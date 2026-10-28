package sse

import (
	"encoding/json"
	"strings"
)

// splitLinesCapturing splits s into content lines and the exact line break
// that followed each, mirroring the Python capturing regex (\r\n|\r|\n).
// The final element is a content line with an empty break.
func splitLinesCapturing(s string) []lineBreak {
	var out []lineBreak
	start := 0
	for i := 0; i < len(s); {
		if s[i] != '\r' && s[i] != '\n' {
			i++
			continue
		}
		br := 1
		if s[i] == '\r' && i+1 < len(s) && s[i+1] == '\n' {
			br = 2
		}
		out = append(out, lineBreak{line: s[start:i], ending: s[i : i+br]})
		i += br
		start = i
	}
	out = append(out, lineBreak{line: s[start:], ending: ""})
	return out
}

type lineBreak struct {
	line   string
	ending string
}

func marshalPayload(payload map[string]any) string {
	var builder strings.Builder
	encoder := json.NewEncoder(&builder)
	encoder.SetEscapeHTML(false)
	// Encoder.Encode appends a newline; drop it so the replacement string
	// matches the inline simplejson.dumps output.
	_ = encoder.Encode(payload)
	return strings.TrimSuffix(builder.String(), "\n")
}

// FormatFrame renders a payload as a public SSE frame. Payloads carrying a
// "type" field use the named event form; others use a bare data frame,
// matching PublicResponseStream._format.
func FormatFrame(payload map[string]any) string {
	data := "data: " + marshalPayload(payload) + "\n\n"
	if typed, ok := payload["type"].(string); ok {
		return "event: " + typed + "\n" + data
	}
	return data
}

// ReplaceFrameData rewrites a frame's data lines while preserving every
// other SSE field and its original line endings. The first data line is
// replaced and any further data lines are dropped, matching
// replace_frame_data. Numbers stay on the wire because payload values are
// json.Number.
func ReplaceFrameData(frame string, payload map[string]any) string {
	parts := splitLinesCapturing(frame)
	replacement := "data: " + marshalPayload(payload)
	var builder strings.Builder
	replaced := false
	for _, piece := range parts {
		if strings.HasPrefix(piece.line, "data:") {
			if replaced {
				continue
			}
			builder.WriteString(replacement)
			replaced = true
			builder.WriteString(piece.ending)
			continue
		}
		builder.WriteString(piece.line)
		builder.WriteString(piece.ending)
	}
	return builder.String()
}

// SSEEvent is the minimal parsed frame used by callers that cannot rely on
// an object payload cache, mirroring parse_sse_text's first event.
type SSEEvent struct {
	Event string
	Data  map[string]any
}

// ParseFirstEvent parses one frame's event and data lines. It returns false
// for comment-only frames or non-object data, matching the Python fallback
// path in ToolCallBuffer._frame_payload.
func ParseFirstEvent(frame string) (SSEEvent, bool) {
	currentEvent := ""
	var dataParts []string
	forEachLine(frame, func(line string) bool {
		if line == "" {
			return true
		}
		if strings.HasPrefix(line, "event:") {
			currentEvent = strings.TrimSpace(line[6:])
		} else if strings.HasPrefix(line, "data:") {
			dataParts = append(dataParts, trimLeadingSpaces(line[5:]))
		}
		return true
	})
	if currentEvent == "" && len(dataParts) == 0 {
		return SSEEvent{}, false
	}
	if len(dataParts) == 0 {
		return SSEEvent{Event: currentEvent, Data: map[string]any{}}, true
	}
	data, ok := decodeObject(strings.Join(dataParts, "\n"))
	if !ok {
		return SSEEvent{}, false
	}
	return SSEEvent{Event: currentEvent, Data: data}, true
}
