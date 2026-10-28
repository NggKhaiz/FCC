// Package wirejson renders values with the exact byte layout Python's
// simplejson produces with use_decimal=True, ensure_ascii=False and the
// default (", ", ": ") separators. The Go standard library cannot be used
// directly: encoding/json emits compact separators, HTML-escapes a handful
// of characters and always escapes U+2028/U+2029. Retention byte accounting
// and token estimates depend on byte-for-byte parity with the reference.
package wirejson

import (
	"encoding/json"
	"fmt"
	"strconv"
	"strings"
)

// hexChars is the lowercase hex table for control-character escapes.
var hexChars = "0123456789abcdef"

// Bytes returns the wire size (UTF-8 byte count) of value.
func Bytes(value any) int {
	var builder strings.Builder
	WriteJSON(&builder, value)
	return builder.Len()
}

// Encode returns the rendered JSON string. Callers that only need the size
// should use Bytes to avoid keeping the rendered string alive.
func Encode(value any) string {
	var builder strings.Builder
	WriteJSON(&builder, value)
	return builder.String()
}

// WriteJSON writes one JSON value using simplejson-compatible conventions.
func WriteJSON(builder *strings.Builder, value any) {
	switch v := value.(type) {
	case nil:
		builder.WriteString("null")
	case bool:
		if v {
			builder.WriteString("true")
		} else {
			builder.WriteString("false")
		}
	case string:
		WriteJSONString(builder, v)
	case json.Number:
		builder.WriteString(string(v))
	case int:
		builder.WriteString(strconv.Itoa(v))
	case int64:
		builder.WriteString(strconv.FormatInt(v, 10))
	case float64:
		builder.WriteString(strconv.FormatFloat(v, 'g', -1, 64))
	case map[string]any:
		builder.WriteByte('{')
		first := true
		for key, item := range v {
			if !first {
				builder.WriteString(", ")
			}
			first = false
			WriteJSONString(builder, key)
			builder.WriteString(": ")
			WriteJSON(builder, item)
		}
		builder.WriteByte('}')
	case map[int64]string:
		// Python JSON coerces integer dict keys to strings; the overlap id
		// table relies on this key convention.
		builder.WriteByte('{')
		first := true
		for key, item := range v {
			if !first {
				builder.WriteString(", ")
			}
			first = false
			WriteJSONString(builder, strconv.FormatInt(key, 10))
			builder.WriteString(": ")
			WriteJSONString(builder, item)
		}
		builder.WriteByte('}')
	case []any:
		builder.WriteByte('[')
		for i, item := range v {
			if i > 0 {
				builder.WriteString(", ")
			}
			WriteJSON(builder, item)
		}
		builder.WriteByte(']')
	default:
		WriteJSONString(builder, fmt.Sprint(v))
	}
}

// WriteJSONString writes a JSON string. Printable ASCII runs are emitted in
// one WriteString; only escape characters take the slow path. Multi-byte
// UTF-8 passes through raw, matching ensure_ascii=False (continuation and
// lead bytes are always >= 0x80, so chunking cannot split a sequence).
func WriteJSONString(builder *strings.Builder, value string) {
	builder.WriteByte('"')
	start := 0
	for i := 0; i < len(value); i++ {
		b := value[i]
		if b >= 0x20 && b != '"' && b != '\\' {
			continue
		}
		if start < i {
			builder.WriteString(value[start:i])
		}
		switch b {
		case '"':
			builder.WriteString(`\"`)
		case '\\':
			builder.WriteString(`\\`)
		case '\b':
			builder.WriteString(`\b`)
		case '\f':
			builder.WriteString(`\f`)
		case '\n':
			builder.WriteString(`\n`)
		case '\r':
			builder.WriteString(`\r`)
		case '\t':
			builder.WriteString(`\t`)
		default:
			// b < 0x20: mandatory \u00xx escape.
			builder.WriteString(`\u00`)
			builder.WriteByte(hexChars[b>>4])
			builder.WriteByte(hexChars[b&0x0f])
		}
		start = i + 1
	}
	if start < len(value) {
		builder.WriteString(value[start:])
	}
	builder.WriteByte('"')
}
