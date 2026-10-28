// Package delivery ports the per-request evidence state machine from
// core/delivered_response.py. It records what has genuinely been released to
// a stream consumer, enforces the retention byte budget, and produces
// recovery snapshots and synthetic closing/handoff events.
package delivery

import (
	"sort"
	"strings"

	"github.com/NggKhaiz/FCC/gocore/retention"
	"github.com/NggKhaiz/FCC/gocore/sse"
	"github.com/NggKhaiz/FCC/gocore/wirejson"
)

// WireAPI selects the provider wire dialect the state machine understands.
type WireAPI string

const (
	WireMessages  WireAPI = "messages"
	WireResponses WireAPI = "responses"
	WireGemini    WireAPI = "gemini"
)

// ReplayInspector identifies local reasoning carriers that can be decrypted
// instead of treated as opaque. The Python default returns nil for ordinary
// signatures; a nil inspector reproduces that safe default.
type ReplayInspector interface {
	// Unencrypted reports whether value carries a readable replay record
	// and returns its decoded native payload when it does.
	Unencrypted(value any) (map[string]any, bool)
}

// TokenEstimator maps serialized output to a token count. The Go default is
// the Python fallback max(1, len/4); parity fixtures assert the rest of the
// state machine and leave exact tiktoken counts to the Python reference.
type TokenEstimator func(text string) int

// DefaultTokenEstimator is the documented fallback of estimate_text_tokens.
func DefaultTokenEstimator(text string) int {
	if text == "" {
		return 0
	}
	if n := len(text) / 4; n >= 1 {
		return n
	}
	return 1
}

// Prefix is the Go DeliveredPrefix projection.
type Prefix struct {
	Text          string
	Thinking      string
	HasCalls      bool
	Eligible      bool
	CanHandoff    bool
	UnsafeReason  string
	RetainedBytes int
}

var (
	messagesNonIndexKinds = map[string]struct{}{
		"message_start": {}, "message_delta": {}, "message_stop": {},
		"ping": {}, "error": {},
	}
	messagesBlockTypes = map[string]struct{}{
		"text": {}, "thinking": {}, "redacted_thinking": {}, "tool_use": {},
	}
	reasoningTypes = map[string]struct{}{
		"thinking": {}, "redacted_thinking": {}, "reasoning": {},
	}
	deltaFields = map[string]string{
		"text_delta":       "text",
		"thinking_delta":   "thinking",
		"input_json_delta": "partial_json",
	}
	headerKinds = map[string]struct{}{
		"message_start": {}, "response.created": {},
	}
)

// Delivered tracks one response. Instances are single-goroutine state
// machines; the caller serializes frames as the Python async loop does.
// partKey identifies one content or summary part of a responses-wire item.
type partKey struct {
	index    int
	field    string
	position int
}

type Delivered struct {
	wire       WireAPI
	items      map[int]map[string]any
	closed     map[int]struct{}
	textClosed map[partKey]struct{}
	partClosed map[partKey]struct{}
	header     map[string]any
	sequence   int
	unsafe     string
	textConst  string
	budget     *retention.Budget
	limitBytes int
	progress   int
	limitedOut int
	inspector  ReplayInspector
	estimate   TokenEstimator
}

// New creates a state machine for the given wire dialect. Retention uses the
// same 1 MiB default as the Python reference.
func New(wire WireAPI, opts ...Option) *Delivered {
	d := &Delivered{
		wire:       wire,
		items:      map[int]map[string]any{},
		closed:     map[int]struct{}{},
		textClosed: map[partKey]struct{}{},
		partClosed: map[partKey]struct{}{},
		header:     map[string]any{},
		sequence:   -1,
		budget:     &retention.Budget{},
		limitBytes: retention.LimitBytes,
		estimate:   DefaultTokenEstimator,
	}
	for _, opt := range opts {
		opt(d)
	}
	// A custom limit rebuilds the budget with that threshold.
	if d.limitBytes != retention.LimitBytes {
		d.budget = retention.NewBudget(d.limitBytes)
	}
	return d
}

// Option configures a Delivered state machine.
type Option func(*Delivered)

// WithRetentionLimit overrides the retention threshold, primarily so golden
// tests can exercise the limit path without allocating a megabyte of input.
func WithRetentionLimit(bytes int) Option {
	return func(d *Delivered) { d.limitBytes = bytes }
}

// WithReplayInspector injects a reasoning replay decoder.
func WithReplayInspector(inspector ReplayInspector) Option {
	return func(d *Delivered) { d.inspector = inspector }
}

// WithTokenEstimator injects a token counter.
func WithTokenEstimator(estimator TokenEstimator) Option {
	return func(d *Delivered) { d.estimate = estimator }
}

// Progress reports how many output-bearing events were observed.
func (d *Delivered) Progress() int { return d.progress }

// RetentionExhausted reports whether the retention limit was crossed.
func (d *Delivered) RetentionExhausted() bool { return d.budget.Limited() }

// UnsafeReason exposes the current safety classification.
func (d *Delivered) UnsafeReason() string {
	if d.textConst != "" {
		return d.textConst
	}
	return d.unsafe
}

// RetainedBytes exposes the estimated retained tally.
func (d *Delivered) RetainedBytes() int { return d.budget.Bytes() }

func (d *Delivered) retain(value any) bool {
	if d.budget.Retain(value) {
		return true
	}
	d.limitedOut = d.OutputTokens()
	d.items = map[int]map[string]any{}
	d.closed = map[int]struct{}{}
	d.textClosed = map[partKey]struct{}{}
	d.partClosed = map[partKey]struct{}{}
	d.unsafe = "retention_limit"
	return false
}

// Wire exposes the dialect this machine records.
func (d *Delivered) Wire() WireAPI { return d.wire }

// Header exposes the captured response/message header map.
func (d *Delivered) Header() map[string]any { return d.header }

// Sequence exposes the newest observed sequence number.
func (d *Delivered) Sequence() int { return d.sequence }

// BumpSequence advances and returns the public sequence counter, matching
// ContinuationStream._sequence on the responses wire.
func (d *Delivered) BumpSequence() int {
	d.sequence++
	return d.sequence
}

// Items exposes the retained item map keyed by output index.
func (d *Delivered) Items() map[int]map[string]any { return d.items }

// TextConstraint reports the first public text contract that forbids a
// synthetic continuation ("structured_output" or "text_logprobs").
func (d *Delivered) TextConstraint() string { return d.textConst }

// ClosedItems returns the finalized output indices in ascending order.
func (d *Delivered) ClosedItems() []int {
	out := make([]int, 0, len(d.closed))
	for index := range d.closed {
		out = append(out, index)
	}
	sort.Ints(out)
	return out
}

// TextClosedKeys returns the finalized text-part keys in deterministic order.
func (d *Delivered) TextClosedKeys() []PartKey { return sortedPartKeys(d.textClosed) }

// PartClosedKeys returns the finalized content/summary-part keys in order.
func (d *Delivered) PartClosedKeys() []PartKey { return sortedPartKeys(d.partClosed) }

func sortedPartKeys(set map[partKey]struct{}) []PartKey {
	out := make([]PartKey, 0, len(set))
	for key := range set {
		out = append(out, PartKey{Index: key.index, Field: key.field, Position: key.position})
	}
	sort.Slice(out, func(i, j int) bool {
		if out[i].Index != out[j].Index {
			return out[i].Index < out[j].Index
		}
		if out[i].Field != out[j].Field {
			return out[i].Field < out[j].Field
		}
		return out[i].Position < out[j].Position
	})
	return out
}

// PartKey is the exported identity of one content or summary part.
type PartKey struct {
	Index    int    `json:"index"`
	Field    string `json:"field"`
	Position int    `json:"position"`
}

// Observe processes one decoded payload. The boolean countProgress mirrors
// the Python argument: synthetic frames are recorded without advancing the
// progress counter.
func (d *Delivered) Observe(payload map[string]any, countProgress ...bool) {
	count := true
	if len(countProgress) > 0 {
		count = countProgress[0]
	}
	if d.wire == WireGemini {
		d.observeGemini(payload, count)
		return
	}

	kind := stringField(payload, "type")
	if raw, exists := payload["type"]; exists && kind == "" {
		if _, isString := raw.(string); !isString {
			d.unsafe = "unknown_event"
			return
		}
	}
	if seq, ok := isInt(payload["sequence_number"]); ok {
		if seq > d.sequence {
			d.sequence = seq
		}
	}
	if _, headerKind := headerKinds[kind]; headerKind && len(d.header) == 0 {
		if message := asMap(payload["message"]); message != nil {
			d.header = asMap(deepCopy(message))
		} else if response := asMap(payload["response"]); response != nil {
			d.header = asMap(deepCopy(response))
		} else {
			d.header = map[string]any{}
		}
	}

	text := d.deltaText(payload)
	block := asMap(payload["content_block"])
	if kind == "content_block_start" && block != nil {
		text += stringField(block, "text") + stringField(block, "thinking")
		if stringField(block, "type") == "redacted_thinking" {
			if d.inspector == nil {
				// Safe default: no readable text is synthesized.
			} else if native, ok := d.inspector.Unencrypted(block["data"]); ok {
				text += strings.Join(readableReasoning(native), "")
			}
		}
	}

	// Progress mirrors DeliveredResponse.observe: emitted text, a newly
	// opened tool block, a cumulative snapshot that grew, a refusal, or a
	// finalized client tool call each publish observable output.
	part := asMap(payload["part"])
	if count && (text != "" ||
		d.newSnapshotText(kind, payload) ||
		(part != nil && jsonTruthy(part["refusal"])) ||
		(kind == "response.refusal.done" && jsonTruthy(payload["refusal"])) ||
		(kind == "content_block_start" && block != nil && stringField(block, "type") == "tool_use") ||
		(kind == "response.output_item.done" && ClientCall(asMap(payload["item"])))) {
		d.progress++
	}

	if !d.budget.Limited() {
		if d.wire == WireMessages {
			d.observeMessages(kind, payload)
		} else {
			d.observeResponses(kind, payload)
		}
	}
	if d.budget.Limited() {
		d.limitedOut += d.estimate(text)
	}
}

func (d *Delivered) deltaText(payload map[string]any) string {
	switch delta := payload["delta"].(type) {
	case string:
		return delta
	case map[string]any:
		return stringField(delta, "text") + stringField(delta, "thinking") +
			stringField(delta, "partial_json")
	default:
		return ""
	}
}

func (d *Delivered) observeMessages(kind string, payload map[string]any) {
	index, hasIndex := isInt(payload["index"])
	if !hasIndex {
		if _, known := messagesNonIndexKinds[kind]; !known {
			d.unsafe = "unknown_event"
		}
		return
	}

	switch kind {
	case "content_block_start":
		block := asMap(payload["content_block"])
		if block == nil {
			block = map[string]any{}
		}
		if !d.retain(block) {
			return
		}
		d.items[index] = asMap(deepCopy(block))
		if _, allowed := messagesBlockTypes[stringField(block, "type")]; !allowed {
			d.unsafe = "native_content"
		}
		blockType := stringField(block, "type")
		if block["signature"] != nil || blockType == "redacted_thinking" {
			value := block["signature"]
			if blockType == "redacted_thinking" {
				value = block["data"]
			}
			if !d.isUnencrypted(value) {
				d.unsafe = "opaque_reasoning"
			}
		}
	case "content_block_delta":
		delta := asMap(payload["delta"])
		if delta == nil {
			delta = map[string]any{}
		}
		if !d.retain(delta) {
			return
		}
		block := d.items[index]
		if block == nil {
			d.unsafe = "missing_block"
			return
		}
		deltaType := stringField(delta, "type")
		if field, known := deltaFields[deltaType]; known {
			block[field] = stringField(block, field) + stringField(delta, field)
		} else if deltaType == "signature_delta" {
			block["signature"] = stringField(block, "signature") + stringField(delta, "signature")
			if !d.isUnencrypted(block["signature"]) {
				d.unsafe = "opaque_delta"
			}
		} else {
			d.unsafe = "opaque_delta"
		}
	case "content_block_stop":
		d.closed[index] = struct{}{}
	}
}

func (d *Delivered) isUnencrypted(value any) bool {
	if d.inspector == nil {
		return false
	}
	_, ok := d.inspector.Unencrypted(value)
	return ok
}

// readableReasoning extracts thinking text from a decoded replay native
// payload, matching the messages snapshot's readable_reasoning use.
func readableReasoning(native map[string]any) []string {
	if stringField(native, "type") == "thinking" {
		if text := stringField(native, "thinking"); text != "" {
			return []string{text}
		}
		return nil
	}
	var out []string
	for _, key := range []string{"content", "summary"} {
		parts := asList(native[key])
		var text []string
		for _, part := range parts {
			partMap := asMap(part)
			if partMap == nil {
				continue
			}
			if t, ok := partMap["text"].(string); ok && t != "" {
				text = append(text, t)
			}
		}
		joined := strings.Join(text, "\n\n")
		if joined != "" {
			out = append(out, joined)
		}
	}
	return out
}

// Snapshot projects the retained evidence into the recovery prefix.
func (d *Delivered) Snapshot() Prefix {
	var textParts, thinkingParts []string
	hasCalls := false
	indices := make([]int, 0, len(d.items))
	for index := range d.items {
		indices = append(indices, index)
	}
	sort.Ints(indices)
	for _, index := range indices {
		item := d.items[index]
		kind := stringField(item, "type")
		if d.wire == WireGemini {
			if _, hasText := item["text"]; hasText {
				if jsonTruthy(item["thought"]) {
					thinkingParts = append(thinkingParts, stringField(item, "text"))
				} else {
					textParts = append(textParts, stringField(item, "text"))
				}
			}
			if _, hasCall := item["functionCall"]; hasCall {
				hasCalls = true
			}
			continue
		}
		if d.wire == WireResponses {
			switch kind {
			case "message":
				for _, raw := range asList(item["content"]) {
					if part := asMap(raw); part != nil {
						textParts = append(textParts, stringField(part, "text"))
					}
				}
			case "reasoning":
				for _, field := range []string{"summary", "content"} {
					for _, raw := range asList(item[field]) {
						if part := asMap(raw); part != nil {
							thinkingParts = append(thinkingParts, stringField(part, "text"))
						}
					}
				}
			default:
				if _, closed := d.closed[index]; closed && ClientCall(item) {
					hasCalls = true
				}
			}
			continue
		}
		switch kind {
		case "text":
			textParts = append(textParts, stringField(item, "text"))
		case "thinking":
			thinkingParts = append(thinkingParts, stringField(item, "thinking"))
		case "redacted_thinking":
			if d.inspector != nil {
				if native, ok := d.inspector.Unencrypted(item["data"]); ok {
					thinkingParts = append(thinkingParts, readableReasoning(native)...)
				}
			}
		case "tool_use":
			if _, closed := d.closed[index]; closed {
				hasCalls = true
			}
		}
	}
	reason := d.UnsafeReason()
	return Prefix{
		Text:          strings.Join(textParts, ""),
		Thinking:      strings.Join(thinkingParts, ""),
		HasCalls:      hasCalls,
		Eligible:      reason == "",
		CanHandoff:    d.CanHandoff(),
		UnsafeReason:  reason,
		RetainedBytes: d.budget.Bytes(),
	}
}

// CanHandoff mirrors the Python property: reasoning blocks must all be
// closed and the response must carry no disqualifying unsafe reason.
func (d *Delivered) CanHandoff() bool {
	if d.textConst != "" {
		return false
	}
	switch d.unsafe {
	case "", "opaque_reasoning", "opaque_delta":
	default:
		return false
	}
	for index, item := range d.items {
		if _, reasoning := reasoningTypes[stringField(item, "type")]; reasoning {
			if _, closed := d.closed[index]; !closed {
				return false
			}
		}
	}
	return true
}

// ClosingEvents synthesizes stop events for blocks the source never closed.
func (d *Delivered) ClosingEvents() []map[string]any {
	if d.wire == WireGemini {
		return nil
	}
	indices := make([]int, 0, len(d.items))
	for index := range d.items {
		indices = append(indices, index)
	}
	sort.Ints(indices)
	var events []map[string]any
	for _, index := range indices {
		if _, closed := d.closed[index]; closed {
			continue
		}
		item := d.items[index]
		if d.wire == WireMessages {
			events = append(events, map[string]any{
				"type":  "content_block_stop",
				"index": index,
			})
			continue
		}
		for _, field := range []string{"content", "summary"} {
			summary := field == "summary"
			parts := asList(item[field])
			for position, raw := range parts {
				part := asMap(raw)
				if part == nil {
					continue
				}
				pk := partKey{index: index, field: field, position: position}
				if _, partClosed := d.partClosed[pk]; partClosed {
					continue
				}
				indexKey := "content_index"
				if summary {
					indexKey = "summary_index"
				}
				identity := map[string]any{
					"item_id":      item["id"],
					"output_index": index,
					indexKey:       position,
				}
				if _, textClosed := d.textClosed[pk]; !textClosed {
					textKind := "response.output_text.done"
					if summary {
						textKind = "response.reasoning_summary_text.done"
					} else if stringField(part, "type") == "reasoning_text" {
						textKind = "response.reasoning_text.done"
					}
					textEvent := map[string]any{
						"type": textKind,
						"text": stringField(part, "text"),
					}
					for key, value := range identity {
						textEvent[key] = value
					}
					events = append(events, textEvent)
				}
				if stringField(part, "type") != "reasoning_text" {
					partKind := "response.content_part.done"
					if summary {
						partKind = "response.reasoning_summary_part.done"
					}
					partEvent := map[string]any{
						"type": partKind,
						"part": asMap(deepCopy(part)),
					}
					for key, value := range identity {
						partEvent[key] = value
					}
					events = append(events, partEvent)
				}
			}
		}
		closedItem := asMap(deepCopy(item))
		closedItem["status"] = "completed"
		events = append(events, map[string]any{
			"type":         "response.output_item.done",
			"output_index": index,
			"item":         closedItem,
		})
	}
	return events
}

// HandoffEvents returns the terminal events used when tool calls are handed
// to another attempt on the messages wire.
func (d *Delivered) HandoffEvents() []map[string]any {
	switch d.wire {
	case WireMessages:
		return []map[string]any{
			{
				"type":  "message_delta",
				"delta": map[string]any{"stop_reason": "tool_use", "stop_sequence": nil},
				"usage": map[string]any{},
			},
			{"type": "message_stop"},
		}
	case WireGemini:
		header := asMap(deepCopy(d.header))
		if header == nil {
			header = map[string]any{}
		}
		return []map[string]any{
			mergeMaps(header, map[string]any{
				"candidates": []any{map[string]any{"index": 0, "finishReason": "STOP"}},
			}),
		}
	default:
		header := asMap(deepCopy(d.header))
		if header == nil {
			header = map[string]any{}
		}
		return []map[string]any{{
			"type":     "response.completed",
			"response": mergeMaps(header, map[string]any{"status": "completed"}),
		}}
	}
}

func mergeMaps(base, extra map[string]any) map[string]any {
	out := make(map[string]any, len(base)+len(extra))
	for key, value := range base {
		out[key] = value
	}
	for key, value := range extra {
		out[key] = value
	}
	return out
}

// OutputTokens estimates the serialized retained output. When retention has
// been exhausted, the frozen limited-output tally is returned, matching
// output_tokens() in the Python reference.
func (d *Delivered) OutputTokens() int {
	if d.budget.Limited() && len(d.items) == 0 {
		return d.limitedOut
	}
	values := make([]any, 0, len(d.items))
	indices := make([]int, 0, len(d.items))
	for index := range d.items {
		indices = append(indices, index)
	}
	sort.Ints(indices)
	for _, index := range indices {
		values = append(values, d.items[index])
	}
	return d.OutputTokensFor(values)
}

// OutputTokensFor estimates the token cost of an explicit output list, as
// output_tokens(output) does for synthesized continuation terminals. The
// frozen tally only applies when no explicit output is supplied.
func (d *Delivered) OutputTokensFor(output []any) int {
	// Python estimates over simplejson.dumps(output, ensure_ascii=False)
	// with its default spaced separators; wirejson reproduces those bytes.
	var builder strings.Builder
	wirejson.WriteJSON(&builder, output)
	return d.estimate(builder.String())
}

// Usage builds the terminal usage block: header input tokens are preserved
// and recombined with an output estimate over the finalized output.
func (d *Delivered) Usage(output []any) map[string]any {
	initial := map[string]any{}
	if headerUsage := asMap(d.header["usage"]); headerUsage != nil {
		for key, value := range headerUsage {
			initial[key] = value
		}
	}
	inputTokens := 0
	if v, ok := isInt(initial["input_tokens"]); ok {
		inputTokens = v
	}
	outputTokens := d.OutputTokensFor(output)
	if output == nil {
		// Python usage(None) estimates the retained items, honoring the
		// frozen limited-output tally when retention is exhausted.
		outputTokens = d.OutputTokens()
	}
	initial["input_tokens"] = inputTokens
	initial["output_tokens"] = outputTokens
	initial["total_tokens"] = inputTokens + outputTokens
	return initial
}

// FormatFrame renders a payload as a public SSE frame, matching
// PublicResponseStream._format for use by callers that emit synthetic events.
func FormatFrame(payload map[string]any) string {
	return sse.FormatFrame(payload)
}
