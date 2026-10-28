// Package delivery ports the per-request evidence state machine from
// core/delivered_response.py. It records what has genuinely been released to
// a stream consumer, enforces the retention byte budget, and produces
// recovery snapshots and synthetic closing/handoff events.
package delivery

import (
	"encoding/json"
	"sort"
	"strings"

	"github.com/NggKhaiz/FCC/gocore/retention"
	"github.com/NggKhaiz/FCC/gocore/sse"
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
type Delivered struct {
	wire       WireAPI
	items      map[int]map[string]any
	closed     map[int]struct{}
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
	d.unsafe = "retention_limit"
	return false
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

	// Messages-wire progress: any emitted text or a newly opened tool block.
	// Responses-API cumulative snapshot checks arrive with the responses port.
	if count && (text != "" ||
		(kind == "content_block_start" && block != nil && stringField(block, "type") == "tool_use")) {
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

// observeResponses is intentionally minimal until the responses phase: the
// messages wire path is the current hot path and has full golden coverage.
func (d *Delivered) observeResponses(kind string, payload map[string]any) {
	_ = kind
	_ = payload
	d.unsafe = "unknown_event"
}

func (d *Delivered) observeGemini(payload map[string]any, count bool) {
	_ = payload
	_ = count
	d.unsafe = "unknown_event"
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
	if d.wire != WireMessages {
		return nil
	}
	var events []map[string]any
	indices := make([]int, 0, len(d.items))
	for index := range d.items {
		indices = append(indices, index)
	}
	sort.Ints(indices)
	for _, index := range indices {
		if _, closed := d.closed[index]; closed {
			continue
		}
		events = append(events, map[string]any{
			"type":  "content_block_stop",
			"index": index,
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
	raw, err := json.Marshal(values)
	if err != nil {
		return 0
	}
	return d.estimate(string(raw))
}

// FormatFrame renders a payload as a public SSE frame, matching
// PublicResponseStream._format for use by callers that emit synthetic events.
func FormatFrame(payload map[string]any) string {
	return sse.FormatFrame(payload)
}
