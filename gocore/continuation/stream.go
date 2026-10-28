package continuation

import (
	"encoding/json"
	"sort"
	"strings"

	"github.com/NggKhaiz/FCC/gocore/delivery"
	"github.com/NggKhaiz/FCC/gocore/wireuuid"
)

// Sentinel failures mirror the exact public messages the Python stream
// raises. Both are non-retryable upstream 502s in the calling layer.
var (
	// ErrNativeContract fires when a continuation returns public text under
	// a structured-output or logprobs contract the synthesis cannot rebuild.
	ErrNativeContract = streamError{
		"The continuation returned text with a native output contract that cannot be reconstructed.",
	}
	// ErrNoProgress fires when a finalized continuation added no output at
	// all, leaving the published answer with nothing new to present.
	ErrNoProgress = streamError{
		"The provider continuation completed without adding output.",
	}
)

type streamError struct{ message string }

func (e streamError) Error() string { return e.message }

// Stream maps continuation events into an existing public response
// lifecycle. It owns only recovery-time mappings; ordinary frames pass
// through untouched, matching ContinuationStream in the Python reference.
type Stream struct {
	Delivered *delivery.Delivered

	Active  bool
	Handoff bool

	offset          int
	namespace       wireuuid.UUID
	namespaces      []wireuuid.UUID
	namespaceCursor int
	boundary        []map[string]any
	expected        string
	overlap         *Overlap
	initialProgress int
	prefixItems     []map[string]any
}

// StreamOption configures a Stream at construction.
type StreamOption func(*Stream)

// WithNamespaces pins the attempt namespaces handed out in order whenever a
// new attempt begins (construction and RestartAttempt). Golden tests use it
// to reproduce Python's uuid4 sequence exactly; once exhausted the stream
// falls back to random v4 UUIDs.
func WithNamespaces(ids []wireuuid.UUID) StreamOption {
	return func(s *Stream) { s.namespaces = append([]wireuuid.UUID(nil), ids...) }
}

// NewStream wraps a delivered response state machine.
func NewStream(delivered *delivery.Delivered, opts ...StreamOption) *Stream {
	s := &Stream{Delivered: delivered, overlap: NewOverlap("")}
	for _, opt := range opts {
		opt(s)
	}
	s.namespace = s.nextNamespace()
	return s
}

func (s *Stream) nextNamespace() wireuuid.UUID {
	if s.namespaceCursor < len(s.namespaces) {
		id := s.namespaces[s.namespaceCursor]
		s.namespaceCursor++
		return id
	}
	return wireuuid.MustV4()
}

// MadeProgress reports whether the delivered response grew since the last
// attempt began, the gate that distinguishes a real continuation from a
// provider-completed empty replay.
func (s *Stream) MadeProgress() bool {
	return s.Delivered.Progress() > s.initialProgress
}

// Begin opens a continuation attempt. Handoff mode bypasses overlap removal
// because the prefix is already finalized through tool-call handoff.
func (s *Stream) Begin(handoff bool) {
	s.Active = true
	s.Handoff = handoff
	maxIndex := -1
	for index := range s.Delivered.Items() {
		if index > maxIndex {
			maxIndex = index
		}
	}
	s.offset = maxIndex + 1
	s.boundary = s.Delivered.ClosingEvents()
	s.expected = s.Delivered.Snapshot().Text
	s.restartAttempt()

	finalized := map[int]map[string]any{}
	for index, item := range s.Delivered.Items() {
		finalized[index] = asMap(delivery.DeepCopy(item))
	}
	for _, event := range s.boundary {
		if stringField(event, "type") != "response.output_item.done" {
			continue
		}
		if outputIndex, ok := isJSONInt(event["output_index"]); ok {
			finalized[outputIndex] = asMap(delivery.DeepCopy(event["item"]))
		}
	}
	indices := make([]int, 0, len(finalized))
	for index := range finalized {
		indices = append(indices, index)
	}
	sort.Ints(indices)
	s.prefixItems = make([]map[string]any, 0, len(indices))
	for _, index := range indices {
		s.prefixItems = append(s.prefixItems, finalized[index])
	}
}

// RestartAttempt replaces an invisible attempt without losing the finalized
// prefix: a fresh namespace remaps ids and a new overlap machine re-expects
// the original published text.
func (s *Stream) RestartAttempt() { s.restartAttempt() }

func (s *Stream) restartAttempt() {
	s.namespace = s.nextNamespace()
	s.overlap = NewOverlap(s.expected)
	s.initialProgress = s.Delivered.Progress()
}

// Boundary drains the synthetic closing events captured at Begin, stamping
// sequence numbers on the responses wire exactly as _boundary/_sequence do.
func (s *Stream) Boundary() []map[string]any {
	events := s.boundary
	s.boundary = nil
	for _, event := range events {
		s.sequence(event)
	}
	return events
}

func (s *Stream) sequence(event map[string]any) {
	if s.Delivered.Wire() == delivery.WireResponses {
		event["sequence_number"] = s.Delivered.BumpSequence()
	}
}

// itemID mirrors f"{value.split('_')[0]}_{uuid5(namespace, value).hex}".
func (s *Stream) itemID(value string) string {
	prefix, _, _ := strings.Cut(value, "_")
	return prefix + "_" + wireuuid.NewV5(s.namespace, value).Hex()
}

// Prepare maps one incoming continuation payload into zero or more public
// events. The error is one of the package sentinels or an overlap failure.
func (s *Stream) Prepare(payload map[string]any) ([]map[string]any, error) {
	if !s.Active {
		return []map[string]any{payload}, nil
	}
	kind := stringField(payload, "type")
	if s.Delivered.Wire() == delivery.WireResponses {
		if _, isError := streamErrorKinds[kind]; !isError &&
			delivery.ResponsesTextConstraint(payload) != "" {
			return nil, ErrNativeContract
		}
	}
	if _, suppressed := lifecycleKinds[kind]; suppressed {
		return nil, nil
	}
	var events []map[string]any
	if s.Handoff {
		events = []map[string]any{asMap(delivery.DeepCopy(payload))}
	} else {
		ready, err := s.overlap.Feed(payload)
		if err != nil {
			return nil, err
		}
		events = ready
	}
	mapped := make([]map[string]any, 0, len(events))
	for _, event := range events {
		mapped = append(mapped, s.mapEvent(event))
	}
	return mapped, nil
}

var streamErrorKinds = map[string]struct{}{
	"response.failed": {}, "response.error": {}, "error": {},
}

var lifecycleKinds = map[string]struct{}{
	"message_start": {}, "response.created": {}, "response.in_progress": {},
}

func (s *Stream) mapEvent(data map[string]any) map[string]any {
	if s.Delivered.Wire() == delivery.WireGemini {
		header := s.Delivered.Header()
		for _, key := range []string{"responseId", "modelVersion"} {
			if value, ok := header[key]; ok {
				data[key] = value
			}
		}
		return data
	}
	if !s.Handoff {
		bumpIndex(data, "index", s.offset)
		bumpIndex(data, "output_index", s.offset)
		if id, ok := data["item_id"].(string); ok {
			data["item_id"] = s.itemID(id)
		}
		if item := asMap(data["item"]); item != nil {
			if id, ok := item["id"].(string); ok {
				item["id"] = s.itemID(id)
			}
		}
	}
	if _, present := data["response_id"]; present {
		data["response_id"] = s.headerID(data["response_id"])
	}
	if response := asMap(data["response"]); response != nil {
		response["id"] = s.headerID(response["id"])
		header := s.Delivered.Header()
		if createdAt, ok := header["created_at"]; ok {
			response["created_at"] = createdAt
		}
		if !s.Handoff {
			for _, rawItem := range asList(response["output"]) {
				item := asMap(rawItem)
				if item == nil {
					continue
				}
				if id, ok := item["id"].(string); ok {
					item["id"] = s.itemID(id)
				}
			}
		}
	}
	s.sequence(data)
	return data
}

func (s *Stream) headerID(fallback any) any {
	if id, ok := s.Delivered.Header()["id"]; ok {
		return id
	}
	return fallback
}

// bumpIndex adds the prefix offset to an integer index field. JSON-decoded
// numbers arrive as json.Number while programmatically built frames use int.
func bumpIndex(data map[string]any, key string, offset int) {
	if n, ok := isJSONInt(data[key]); ok {
		data[key] = n + offset
	}
}

func isJSONInt(value any) (int, bool) {
	switch n := value.(type) {
	case int:
		return n, true
	case int64:
		return int(n), true
	case json.Number:
		i, err := n.Int64()
		if err != nil {
			return 0, false
		}
		return int(i), true
	default:
		return 0, false
	}
}

// Finalize finishes the response only after preceding frames have been
// published, merging prefix items and recombining usage.
func (s *Stream) Finalize(data map[string]any) (map[string]any, error) {
	if !s.Active {
		return data, nil
	}
	if s.Delivered.Wire() == delivery.WireGemini {
		if delivery.GeminiFinished(data) && !s.Handoff && !s.MadeProgress() {
			return nil, ErrNoProgress
		}
		// Per-attempt usage cannot describe a synthesized multi-attempt answer.
		delete(data, "usageMetadata")
		return data, nil
	}
	kind := stringField(data, "type")
	response := asMap(data["response"])
	output := []any{}
	if response != nil {
		output = asList(response["output"])
	}
	terminal := kind == "response.completed" ||
		kind == "response.incomplete" || kind == "message_stop"
	if kind == "message_delta" {
		if delta := asMap(data["delta"]); delta != nil {
			if value, present := delta["stop_reason"]; present && value != nil {
				terminal = true
			}
		}
	}
	if terminal && !s.Handoff && !(s.MadeProgress() || hasOutput(output)) {
		return nil, ErrNoProgress
	}
	if response != nil {
		merged := make([]any, 0, len(s.prefixItems)+len(output))
		for _, item := range s.prefixItems {
			merged = append(merged, delivery.DeepCopy(item))
		}
		if !s.Handoff {
			merged = append(merged, output...)
		}
		response["output"] = merged
		response["usage"] = s.Delivered.Usage(merged)
	} else if kind == "message_delta" {
		if _, present := data["usage"]; present {
			data["usage"] = map[string]any{
				"output_tokens": s.Delivered.OutputTokens(),
			}
		}
	}
	return data, nil
}

// hasOutput mirrors _has_output: client tool calls or any text/refusal part
// in content or summary means the continuation produced real output.
func hasOutput(items []any) bool {
	for _, rawItem := range items {
		item := asMap(rawItem)
		if item == nil {
			continue
		}
		if delivery.ClientCall(item) {
			return true
		}
		for _, field := range []string{"content", "summary"} {
			for _, rawPart := range asList(item[field]) {
				part := asMap(rawPart)
				if part == nil {
					continue
				}
				if jsonTruthyLocal(part["text"]) || jsonTruthyLocal(part["refusal"]) {
					return true
				}
			}
		}
	}
	return false
}

func jsonTruthyLocal(value any) bool {
	switch v := value.(type) {
	case nil:
		return false
	case bool:
		return v
	case string:
		return v != ""
	case []any:
		return len(v) > 0
	case map[string]any:
		return len(v) > 0
	default:
		return true
	}
}
