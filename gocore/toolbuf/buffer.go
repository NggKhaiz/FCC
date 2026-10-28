// Package toolbuf ports api/tool_call_buffer.py: it withholds incomplete
// client tool calls at the public SSE boundary and releases each call group
// atomically, in source order, once every member is complete.
package toolbuf

import (
	"encoding/json"

	"github.com/NggKhaiz/FCC/gocore/sse"
)

// asInt mirrors Python's isinstance(index, int): JSON numbers arrive as
// json.Number through the UseNumber decoder, and booleans must not qualify.
func asInt(value any) (int, bool) {
	switch n := value.(type) {
	case int:
		return n, true
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

type wireAPI string

const (
	WireMessages  wireAPI = "messages"
	WireResponses wireAPI = "responses"
	WireGemini    wireAPI = "gemini"
)

var (
	unfinished = map[string]struct{}{
		"in_progress": {}, "incomplete": {}, "failed": {},
	}
	failures = map[string]struct{}{
		"error": {}, "response.error": {}, "response.failed": {},
	}
	responsesTerminals = map[string]struct{}{
		"response.completed": {}, "response.incomplete": {},
	}
	argumentEvents = map[string]struct{}{
		"response.function_call_arguments.delta": {},
		"response.function_call_arguments.done":  {},
		"response.custom_tool_call_input.delta":  {},
		"response.custom_tool_call_input.done":   {},
	}
	clientCallTypes = map[string]struct{}{
		"function_call":    {},
		"custom_tool_call": {},
		"computer_call":    {},
		"local_shell_call": {},
		"apply_patch_call": {},
	}
	searchTypes = map[string]struct{}{
		"tool_search": {}, "tool_search_call": {}, "tool_search_output": {},
	}
)

// Buffer is the Go ToolCallBuffer. Keys are int (messages indices) or string
// (responses item ids), matching Python's int | str CallKey.
type Buffer struct {
	wire      wireAPI
	payloads  *sse.PayloadCache
	frames    []string
	calls     map[any]struct{}
	pending   map[any]struct{}
	delivered map[any]struct{}
	discarded map[any]struct{}
	ids       map[string]any
	indices   map[int]any
}

// New creates a buffer. A shared payload cache from the delivery stream can
// be passed so frame parses are memoized across stages.
func New(wire wireAPI, cache *sse.PayloadCache) *Buffer {
	if cache == nil {
		cache = sse.NewPayloadCache()
	}
	return &Buffer{
		wire:      wire,
		payloads:  cache,
		calls:     map[any]struct{}{},
		pending:   map[any]struct{}{},
		delivered: map[any]struct{}{},
		discarded: map[any]struct{}{},
		ids:       map[string]any{},
		indices:   map[int]any{},
	}
}

func isClientSearch(item map[string]any) bool {
	return item["execution"] == "client" && contains(searchTypes, asString(item["type"]))
}

func contains(set map[string]struct{}, value string) bool {
	_, ok := set[value]
	return ok
}

func asString(value any) string {
	if s, ok := value.(string); ok {
		return s
	}
	return ""
}

func asMap(value any) map[string]any {
	if m, ok := value.(map[string]any); ok {
		return m
	}
	return nil
}

func clientCall(item map[string]any) bool {
	kind := asString(item["type"])
	if contains(clientCallTypes, kind) {
		return true
	}
	if kind == "tool_search_call" {
		return isClientSearch(item)
	}
	environment := asMap(item["environment"])
	return kind == "shell_call" && environment != nil && asString(environment["type"]) == "local"
}

// framePayload resolves a frame's payload and event kind through the shared
// parse memo; malformed frames fall back to the general SSE parser.
func (b *Buffer) framePayload(frame string) (map[string]any, any) {
	if cached, ok := b.payloads.Get(frame); ok {
		kind := sse.EventName(frame)
		if kind == "" {
			kind = asString(cached["type"])
		}
		return cached, kind
	}
	event, ok := sse.ParseFirstEvent(frame)
	if !ok {
		return nil, nil
	}
	kind := event.Event
	if kind == "" {
		kind = asString(event.Data["type"])
	}
	return event.Data, kind
}

// Feed processes one frame and returns the frames releasable now.
func (b *Buffer) Feed(frame string) []string {
	if b.wire == WireGemini {
		return []string{frame}
	}
	data, kindAny := b.framePayload(frame)
	if data == nil {
		return b.publish(frame)
	}
	kind := asString(kindAny)
	if contains(failures, kind) {
		b.discardGroup()
		return []string{b.filterSnapshot(frame, true)}
	}

	if b.wire == WireMessages {
		index, hasIndex := asInt(data["index"])
		block := asMap(data["content_block"])
		if hasIndex {
			if kind == "content_block_start" && block != nil && asString(block["type"]) == "tool_use" {
				b.beginCall(index)
			} else if kind == "content_block_stop" {
				delete(b.pending, index)
			}
		}
		if kind == "message_stop" && len(b.pending) > 0 {
			b.discardGroup()
		}
	} else {
		item := asMap(data["item"])
		if item == nil {
			item = map[string]any{}
		}
		if clientCall(item) || contains(argumentEvents, kind) {
			if key := b.key(data, item); key != nil {
				if kind == "response.output_item.done" {
					b.calls[key] = struct{}{}
					if contains(unfinished, asString(item["status"])) {
						b.pending[key] = struct{}{}
					} else {
						delete(b.pending, key)
					}
				} else {
					b.beginCall(key)
				}
			}
		}
		if contains(responsesTerminals, kind) {
			response := asMap(data["response"])
			if response != nil {
				if output, ok := response["output"].([]any); ok {
					for _, value := range output {
						item := asMap(value)
						if item == nil || !clientCall(item) {
							continue
						}
						key := b.key(map[string]any{}, item)
						if !b.withheldItem(item, asString(response["status"]), false) {
							delete(b.pending, key)
						}
					}
				}
			}
			if len(b.pending) > 0 {
				b.discardGroup()
			}
		}
		if asMap(data["response"]) != nil {
			frame = b.filterSnapshot(frame, false)
		}
	}
	return b.publish(frame)
}

func (b *Buffer) beginCall(key any) {
	if _, seen := b.calls[key]; !seen {
		b.calls[key] = struct{}{}
		b.pending[key] = struct{}{}
	}
}

func (b *Buffer) key(data, item map[string]any) any {
	var ids []string
	for _, value := range []any{data["item_id"], item["id"], item["call_id"]} {
		if s := asString(value); s != "" {
			ids = append(ids, s)
		}
	}
	var index *int
	if raw, ok := asInt(data["output_index"]); ok {
		index = &raw
	}
	var key any
	for _, value := range ids {
		if existing, ok := b.ids[value]; ok {
			key = existing
			break
		}
	}
	if key == nil && index != nil {
		if existing, ok := b.indices[*index]; ok {
			key = existing
		} else {
			key = *index
		}
	}
	if key == nil && len(ids) > 0 {
		key = ids[0]
	}
	if key != nil {
		for _, value := range ids {
			b.ids[value] = key
		}
		if index != nil {
			b.indices[*index] = key
		}
	}
	return key
}

func (b *Buffer) publish(frame string) []string {
	if len(b.calls) == 0 {
		return []string{frame}
	}
	b.frames = append(b.frames, frame)
	if len(b.pending) > 0 {
		return nil
	}
	frames := b.frames
	b.frames = nil
	for key := range b.calls {
		b.delivered[key] = struct{}{}
	}
	clear(b.calls)
	return frames
}

func (b *Buffer) discardGroup() {
	for key := range b.calls {
		b.discarded[key] = struct{}{}
	}
	b.frames = nil
	clear(b.calls)
	clear(b.pending)
}

func (b *Buffer) filterSnapshot(frame string, failed bool) string {
	value, ok := b.payloads.Get(frame)
	if !ok {
		return frame
	}
	response := asMap(value["response"])
	if response == nil {
		return frame
	}
	output, ok := response["output"].([]any)
	if !ok {
		return frame
	}
	retained := make([]any, 0, len(output))
	for _, raw := range output {
		item := asMap(raw)
		if item != nil && clientCall(item) &&
			b.withheldItem(item, asString(response["status"]), failed) {
			continue
		}
		retained = append(retained, raw)
	}
	if len(retained) == len(output) {
		return frame
	}
	edited := deepCopy(value).(map[string]any)
	asMap(edited["response"])["output"] = retained
	return sse.ReplaceFrameData(frame, edited)
}

func (b *Buffer) withheldItem(item map[string]any, responseStatus string, failed bool) bool {
	key := b.key(map[string]any{}, item)
	if _, discarded := b.discarded[key]; discarded {
		return true
	}
	status := asString(item["status"])
	if contains(unfinished, status) {
		return true
	}
	if failed {
		if _, ok := b.delivered[key]; !ok {
			return true
		}
	}
	if item["status"] == nil && responseStatus != "completed" {
		if _, ok := b.delivered[key]; !ok {
			return true
		}
	}
	return false
}

func deepCopy(value any) any {
	switch v := value.(type) {
	case map[string]any:
		out := make(map[string]any, len(v))
		for key, item := range v {
			out[key] = deepCopy(item)
		}
		return out
	case []any:
		out := make([]any, len(v))
		for i, item := range v {
			out[i] = deepCopy(item)
		}
		return out
	default:
		return value
	}
}
