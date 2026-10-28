package delivery

import "strings"

// Responses-API and Gemini wire observation, ported line-for-line from the
// Python DeliveredResponse._responses / _gemini methods. The messages path
// lives in delivered.go; this file completes the other two dialects so the
// continuation stream can build prefixes, boundaries and usage on any wire.

var (
	responsesNonIndexKinds = map[string]struct{}{
		"response.created": {}, "response.in_progress": {},
		"response.completed": {}, "response.incomplete": {},
		"response.failed": {}, "response.error": {},
		"error": {}, "ping": {},
	}
	responsesItemTypes = map[string]struct{}{
		"message": {}, "reasoning": {},
	}
	responsesPartKinds = map[string]struct{}{
		"response.content_part.added":           {},
		"response.reasoning_summary_part.added": {},
		"response.content_part.done":            {},
		"response.reasoning_summary_part.done":  {},
	}
	responsesPartTypes = map[string]struct{}{
		"output_text": {}, "summary_text": {}, "reasoning_text": {},
	}
	responsesTextKinds = map[string]struct{}{
		"response.output_text.delta":            {},
		"response.reasoning_summary_text.delta": {},
		"response.reasoning_text.delta":         {},
		"response.refusal.delta":                {},
		"response.output_text.done":             {},
		"response.reasoning_summary_text.done":  {},
		"response.reasoning_text.done":          {},
		"response.refusal.done":                 {},
	}
	responsesDeltaExclusions = map[string]struct{}{
		"response.function_call_arguments.delta": {},
		"response.custom_tool_call_input.delta":  {},
	}
	responsesUnknownExclusions = map[string]struct{}{
		"response.function_call_arguments.delta": {},
		"response.function_call_arguments.done":  {},
		"response.custom_tool_call_input.delta":  {},
		"response.custom_tool_call_input.done":   {},
	}
	geminiPartKeys = map[string]struct{}{
		"text": {}, "thought": {}, "thoughtSignature": {}, "functionCall": {},
	}
	clientCallTypes = map[string]struct{}{
		"function_call":    {},
		"custom_tool_call": {},
		"computer_call":    {},
		"local_shell_call": {},
		"apply_patch_call": {},
	}
)

// ClientCall mirrors client_call: tool invocations the client reconstructs
// locally rather than replaying through the provider.
func ClientCall(item map[string]any) bool {
	if item == nil {
		return false
	}
	kind := stringField(item, "type")
	if kind == "tool_search_call" {
		return isClientSearch(item)
	}
	if _, ok := clientCallTypes[kind]; ok {
		return true
	}
	if kind == "shell_call" {
		if env := asMap(item["environment"]); env != nil && stringField(env, "type") == "local" {
			return true
		}
	}
	return false
}

func isClientSearch(item map[string]any) bool {
	if stringField(item, "execution") != "client" {
		return false
	}
	switch stringField(item, "type") {
	case "tool_search", "tool_search_call", "tool_search_output":
		return true
	}
	return false
}

func isStructuredTextFormat(value any) bool {
	m := asMap(value)
	if m == nil {
		return false
	}
	switch stringField(m, "type") {
	case "json_schema", "json_object":
		return true
	}
	return false
}

// ResponsesTextConstraint identifies public text contracts that cannot be
// reconstructed by a synthetic continuation.
func ResponsesTextConstraint(data map[string]any) string {
	response := asMap(data["response"])
	if response == nil {
		response = map[string]any{}
	}
	if text := asMap(response["text"]); text != nil &&
		isStructuredTextFormat(text["format"]) {
		return "structured_output"
	}
	part := asMap(data["part"])
	// Python truthiness: empty lists/dicts and "" are falsy, so an empty
	// logprobs array does not establish the constraint.
	if jsonTruthy(data["logprobs"]) || (part != nil && jsonTruthy(part["logprobs"])) {
		return "text_logprobs"
	}
	items := []any{data["item"]}
	items = append(items, asList(response["output"])...)
	for _, rawItem := range items {
		item := asMap(rawItem)
		if item == nil {
			continue
		}
		for _, rawPart := range asList(item["content"]) {
			part := asMap(rawPart)
			if part != nil && jsonTruthy(part["logprobs"]) {
				return "text_logprobs"
			}
		}
	}
	return ""
}

// jsonTruthy mirrors Python's truth testing for the JSON value types the
// observation layer encounters.
func jsonTruthy(value any) bool {
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

// newSnapshotText reports whether a cumulative snapshot grows any published
// output text, matching _new_snapshot_text.
func (d *Delivered) newSnapshotText(kind string, data map[string]any) bool {
	index, _ := isInt(data["output_index"])
	recorded := d.items[index]
	var previous []any
	if recorded != nil {
		previous = asList(recorded["content"])
	}
	type positioned struct {
		index int
		part  map[string]any
	}
	var parts []positioned
	switch {
	case kind == "response.output_item.added" || kind == "response.output_item.done":
		item := asMap(data["item"])
		if item == nil || stringField(item, "type") != "message" {
			return false
		}
		for i, raw := range asList(item["content"]) {
			if m := asMap(raw); m != nil {
				parts = append(parts, positioned{i, m})
			}
		}
	case kind == "response.content_part.added" || kind == "response.content_part.done":
		pos, _ := isInt(data["content_index"])
		if m := asMap(data["part"]); m != nil {
			parts = append(parts, positioned{pos, m})
		}
	case kind == "response.output_text.done":
		pos, _ := isInt(data["content_index"])
		parts = append(parts, positioned{pos, map[string]any{
			"type": "output_text",
			"text": data["text"],
		}})
	default:
		return false
	}
	for _, p := range parts {
		if stringField(p.part, "type") != "output_text" {
			continue
		}
		newLen := len(stringField(p.part, "text"))
		oldLen := 0
		if p.index < len(previous) {
			if prev := asMap(previous[p.index]); prev != nil {
				oldLen = len(stringField(prev, "text"))
			}
		}
		if newLen > oldLen {
			return true
		}
	}
	return false
}

func (d *Delivered) observeResponses(kind string, data map[string]any) {
	if constraint := ResponsesTextConstraint(data); constraint != "" && d.textConst == "" {
		d.textConst = constraint
	}
	index, hasIndex := isInt(data["output_index"])
	if !hasIndex {
		if _, known := responsesNonIndexKinds[kind]; !known {
			d.unsafe = "unknown_event"
		}
		return
	}
	switch kind {
	case "response.output_item.added", "response.output_item.done":
		item := asMap(data["item"])
		if item == nil {
			item = map[string]any{}
		}
		if !d.retain(item) {
			return
		}
		d.items[index] = asMap(deepCopy(item))
		_, itemTypeOK := responsesItemTypes[stringField(item, "type")]
		if !itemTypeOK && !ClientCall(item) {
			d.unsafe = "native_content"
		}
		if item["encrypted_content"] != nil && !d.isUnencrypted(item["encrypted_content"]) {
			d.unsafe = "opaque_reasoning"
		}
		for _, raw := range asList(item["content"]) {
			if part := asMap(raw); part != nil && stringField(part, "type") == "refusal" {
				d.unsafe = "refusal"
			}
		}
		if kind == "response.output_item.done" {
			d.closed[index] = struct{}{}
		}
	default:
		if _, ok := responsesPartKinds[kind]; ok {
			d.observePart(kind, data, index)
			return
		}
		if _, ok := responsesTextKinds[kind]; ok {
			d.observeText(kind, data, index)
			return
		}
		if strings.HasSuffix(kind, ".delta") {
			if _, excluded := responsesDeltaExclusions[kind]; !excluded {
				d.unsafe = "native_delta"
				return
			}
		}
		if _, excluded := responsesUnknownExclusions[kind]; !excluded {
			d.unsafe = "unknown_event"
		}
	}
}

func (d *Delivered) observePart(kind string, data map[string]any, index int) {
	part := asMap(data["part"])
	if part == nil {
		part = map[string]any{}
	}
	position, _ := isInt(firstPresent(data, "summary_index", "content_index"))
	done := kind == "response.content_part.done" ||
		kind == "response.reasoning_summary_part.done"
	if !d.retain(part) || (done && !d.retain([]any{kind, index, position})) {
		return
	}
	item := d.items[index]
	if item == nil {
		d.unsafe = "missing_item"
		return
	}
	// Python: field = "summary" if "reasoning" in kind else "content".
	// Summary part kinds contain the "reasoning" substring too.
	field := "content"
	if strings.Contains(kind, "reasoning") {
		field = "summary"
	}
	parts := growParts(item, field, position)
	parts[position] = asMap(deepCopy(part))
	if done {
		d.partClosed[partKey{index, field, position}] = struct{}{}
	}
	if stringField(part, "type") == "refusal" {
		d.unsafe = "refusal"
	} else if _, ok := responsesPartTypes[stringField(part, "type")]; !ok {
		d.unsafe = "native_content"
	}
}

func (d *Delivered) observeText(kind string, data map[string]any, index int) {
	done := strings.HasSuffix(kind, ".done")
	textField := "text"
	if containsRefusal(kind) {
		textField = "refusal"
	}
	fieldName := "delta"
	if done {
		fieldName = textField
	}
	delta, _ := data[fieldName].(string)
	position, _ := isInt(firstPresent(data, "summary_index", "content_index"))
	if !d.retain(delta) || (done && !d.retain([]any{kind, index, position})) {
		return
	}
	item := d.items[index]
	if item == nil {
		d.unsafe = "missing_item"
		return
	}
	field := "content"
	if containsReasoningSummary(kind) {
		field = "summary"
	}
	parts := growParts(item, field, position)
	placeholderType := "output_text"
	switch {
	case field == "summary":
		placeholderType = "summary_text"
	case containsReasoning(kind):
		placeholderType = "reasoning_text"
	case textField == "refusal":
		placeholderType = "refusal"
	}
	if parts[position] == nil {
		parts[position] = map[string]any{"type": placeholderType, textField: ""}
	}
	target := asMap(parts[position])
	if done {
		target[textField] = delta
	} else {
		target[textField] = stringField(target, textField) + delta
	}
	if done {
		d.textClosed[partKey{index, field, position}] = struct{}{}
	}
	if textField == "refusal" {
		d.unsafe = "refusal"
	}
}

func firstPresent(data map[string]any, keys ...string) any {
	for _, key := range keys {
		if value, ok := data[key]; ok {
			return value
		}
	}
	return nil
}

func containsReasoning(kind string) bool {
	return strings.Contains(kind, "reasoning") && !strings.Contains(kind, "reasoning_summary")
}

func containsReasoningSummary(kind string) bool {
	return strings.Contains(kind, "reasoning_summary")
}

func containsRefusal(kind string) bool {
	return strings.Contains(kind, "refusal")
}

// growParts fetches item[field], padding it with empty maps up to position
// and writing the grown slice back onto the item.
func growParts(item map[string]any, field string, position int) []any {
	parts := asList(item[field])
	for len(parts) <= position {
		parts = append(parts, map[string]any{})
	}
	item[field] = parts
	return parts
}

func geminiParts(payload map[string]any) []map[string]any {
	var out []map[string]any
	for _, rawCandidate := range asList(payload["candidates"]) {
		candidate := asMap(rawCandidate)
		if candidate == nil {
			continue
		}
		content := asMap(candidate["content"])
		if content == nil {
			continue
		}
		for _, rawPart := range asList(content["parts"]) {
			if part := asMap(rawPart); part != nil {
				out = append(out, part)
			}
		}
	}
	return out
}

func geminiFinished(payload map[string]any) bool {
	for _, rawCandidate := range asList(payload["candidates"]) {
		candidate := asMap(rawCandidate)
		if candidate != nil && candidate["finishReason"] != nil {
			return true
		}
	}
	return false
}

// GeminiFinished reports whether any candidate carries a finish reason,
// mirroring gemini.gemini_finished for callers outside the package.
func GeminiFinished(payload map[string]any) bool { return geminiFinished(payload) }

func (d *Delivered) observeGemini(payload map[string]any, count bool) {
	if len(d.header) == 0 {
		header := map[string]any{}
		for _, key := range []string{"responseId", "modelVersion"} {
			if value, ok := payload[key]; ok {
				header[key] = value
			}
		}
		d.header = header
	}
	parts := geminiParts(payload)
	if count {
		for _, part := range parts {
			if jsonTruthy(part["text"]) || jsonTruthy(part["functionCall"]) {
				d.progress++
				break
			}
		}
	}
	for _, part := range parts {
		if !d.retain(part) {
			d.limitedOut += d.estimate(stringField(part, "text"))
			continue
		}
		if jsonTruthy(part["thoughtSignature"]) {
			d.unsafe = "opaque_reasoning"
		}
		for key := range part {
			if _, known := geminiPartKeys[key]; !known {
				d.unsafe = "unsupported_output"
			}
		}
		index := 0
		if len(d.items) > 0 {
			max := -1
			for k := range d.items {
				if k > max {
					max = k
				}
			}
			index = max + 1
		}
		previous := d.items[index-1]
		_, partHasText := part["text"]
		if previous != nil {
			_, prevText := previous["text"]
			if partHasText && prevText &&
				keysSubset(part, "text", "thought") &&
				keysSubset(previous, "text", "thought") &&
				boolEqual(part["thought"], previous["thought"]) {
				previous["text"] = stringField(previous, "text") + stringField(part, "text")
				continue
			}
		}
		d.items[index] = asMap(deepCopy(part))
		d.closed[index] = struct{}{}
	}
}

func keysSubset(m map[string]any, allowed ...string) bool {
	allow := map[string]struct{}{}
	for _, key := range allowed {
		allow[key] = struct{}{}
	}
	for key := range m {
		if _, ok := allow[key]; !ok {
			return false
		}
	}
	return true
}

func boolEqual(a, b any) bool {
	return truthy(a) == truthy(b)
}

func truthy(value any) bool {
	if b, ok := value.(bool); ok {
		return b
	}
	return false
}
