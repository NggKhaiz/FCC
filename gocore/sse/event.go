package sse

import (
	"encoding/json"
	"strings"
)

// forEachLine invokes fn for every CR/LF/CRLF-delimited line, including a
// trailing empty line. Lines are substrings of the input, so the scan
// allocates nothing. Returning false stops iteration early.
func forEachLine(s string, fn func(line string) bool) {
	start := 0
	for i := 0; i < len(s); {
		if s[i] != '\r' && s[i] != '\n' {
			i++
			continue
		}
		if !fn(s[start:i]) {
			return
		}
		if s[i] == '\r' && i+1 < len(s) && s[i+1] == '\n' {
			i++
		}
		i++
		start = i
	}
	fn(s[start:])
}

// splitLineBreaks splits on CRLF, CR, or LF, treating CRLF as one break.
// It is the equivalent of the Python compiled regex (?:\r\n|\r|\n) and is
// retained for callers and tests that need the materialized slice.
func splitLineBreaks(s string) []string {
	var lines []string
	forEachLine(s, func(line string) bool {
		lines = append(lines, line)
		return true
	})
	return lines
}

// EventName returns a frame's named event without touching its JSON payload.
// Mirrors sse_event_name: only event:-prefixed lines are consulted, so JSON
// text containing exotic Unicode line separators cannot influence the result.
func EventName(frame string) string {
	name := ""
	forEachLine(frame, func(line string) bool {
		if strings.HasPrefix(line, "event:") {
			name = strings.TrimSpace(line[6:])
			return false
		}
		return true
	})
	return name
}

// trimLeadingSpaces mirrors Python's str.lstrip(" "): only ASCII spaces are
// removed, never tabs or other whitespace.
func trimLeadingSpaces(s string) string {
	i := 0
	for i < len(s) && s[i] == ' ' {
		i++
	}
	return s[i:]
}

func decodeObject(text string) (map[string]any, bool) {
	decoder := json.NewDecoder(strings.NewReader(text))
	decoder.UseNumber()
	var value any
	if err := decoder.Decode(&value); err != nil {
		return nil, false
	}
	// Matches frame_data: a non-object payload is a negative result, not a
	// wrapped value; callers cache it as a failed parse.
	if object, ok := value.(map[string]any); ok {
		return object, true
	}
	return nil, false
}

// FrameData extracts and decodes a frame's data lines. Numbers are decoded as
// json.Number so no precision is lost, matching the Python use_decimal path.
// The second result is false when the frame has no data line or its payload
// is not a JSON object.
func FrameData(frame string) (map[string]any, bool) {
	var first string
	dataLines := 0
	forEachLine(frame, func(line string) bool {
		if strings.HasPrefix(line, "data:") {
			if dataLines == 0 {
				first = trimLeadingSpaces(line[5:])
			}
			dataLines++
		}
		return true
	})
	if dataLines == 0 {
		return nil, false
	}
	// The common frame carries exactly one data line, so decode the
	// zero-copy substring instead of joining a one-element slice.
	if dataLines == 1 {
		return decodeObject(first)
	}
	var builder strings.Builder
	forEachLine(frame, func(line string) bool {
		if strings.HasPrefix(line, "data:") {
			if builder.Len() > 0 {
				builder.WriteByte('\n')
			}
			builder.WriteString(trimLeadingSpaces(line[5:]))
		}
		return true
	})
	return decodeObject(builder.String())
}

// FailureReason inspects a frame for a terminal failure event. Healthy frames
// are rejected by a plain substring scan before any line splitting or JSON
// decoding, exactly like failure_reason_from_frame. It returns "" when the
// frame is not a failure event.
func FailureReason(frame string, failureEvents map[string]struct{}) string {
	if !strings.Contains(frame, "error") && !strings.Contains(frame, "response.failed") {
		return ""
	}

	eventName := ""
	var dataBuilder strings.Builder
	forEachLine(frame, func(line string) bool {
		if strings.HasPrefix(line, "event:") {
			eventName = strings.TrimSpace(line[6:])
		} else if strings.HasPrefix(line, "data:") {
			if dataBuilder.Len() > 0 {
				dataBuilder.WriteByte('\n')
			}
			dataBuilder.WriteString(trimLeadingSpaces(line[5:]))
		}
		return true
	})

	if eventName != "" {
		if _, ok := failureEvents[eventName]; !ok {
			return ""
		}
	} else if dataBuilder.Len() == 0 {
		return ""
	}

	var parsed any
	if dataBuilder.Len() > 0 {
		decoder := json.NewDecoder(strings.NewReader(dataBuilder.String()))
		decoder.UseNumber()
		if err := decoder.Decode(&parsed); err != nil {
			return ""
		}
	} else {
		// Python substitutes an empty object when a named failure event
		// carries no data line.
		parsed = map[string]any{}
	}

	data, ok := parsed.(map[string]any)
	if !ok {
		data = map[string]any{"value": parsed}
	}

	kind := eventName
	if kind == "" {
		if typed, isString := data["type"].(string); isString {
			kind = typed
		} else if _, hasError := data["error"]; hasError {
			kind = "error"
		}
	}
	if kind == "" {
		return ""
	}
	if _, ok := failureEvents[kind]; !ok {
		return ""
	}

	payload := data
	if response, isMap := data["response"].(map[string]any); isMap {
		payload = response
	}
	errorObject := payload
	if nested, isMap := payload["error"].(map[string]any); isMap {
		errorObject = nested
	}
	if code, isString := errorObject["code"].(string); isString && code != "" {
		return code
	}
	if typ, isString := errorObject["type"].(string); isString && typ != "" {
		return typ
	}
	return kind
}
