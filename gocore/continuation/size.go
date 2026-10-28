// Package continuation ports the replay overlap removal and continuation
// stream mapping from core/continuation_overlap.py and
// core/continuation_stream.py. It strips one logical replay prefix from a
// retried provider response while preserving every source event and part.
package continuation

import (
	"encoding/json"
	"fmt"
	"strconv"
	"strings"
)

// wireByteSize returns the UTF-8 byte length of the simplejson encoding used
// by the Python reference: default separators (", " and ": "), no ASCII
// escaping, numbers kept on the wire. The retention bound checks compare
// against this exact convention, so parity is locked by golden size probes.
func wireByteSize(value any) int {
	var builder strings.Builder
	writeJSON(&builder, value)
	return len(builder.String())
}

func writeJSON(builder *strings.Builder, value any) {
	switch v := value.(type) {
	case nil:
		builder.WriteString("null")
	case bool:
		if v {
			builder.WriteString("true")
		} else {
			builder.WriteString("false")
		}
	case json.Number:
		builder.WriteString(string(v))
	case int:
		builder.WriteString(strconv.Itoa(v))
	case int64:
		builder.WriteString(strconv.FormatInt(v, 10))
	case float64:
		builder.WriteString(strconv.FormatFloat(v, 'g', -1, 64))
	case string:
		writeJSONString(builder, v)
	case map[string]any:
		builder.WriteByte('{')
		first := true
		for key, item := range v {
			if !first {
				builder.WriteString(", ")
			}
			first = false
			writeJSONString(builder, key)
			builder.WriteString(": ")
			writeJSON(builder, item)
		}
		builder.WriteByte('}')
	case map[int64]string:
		// Python JSON coerces integer dict keys to strings; preserve the
		// simplejson key convention used by the overlap id table.
		builder.WriteByte('{')
		first := true
		for key, item := range v {
			if !first {
				builder.WriteString(", ")
			}
			first = false
			writeJSONString(builder, strconv.FormatInt(key, 10))
			builder.WriteString(": ")
			writeJSONString(builder, item)
		}
		builder.WriteByte('}')
	case []any:
		builder.WriteByte('[')
		for i, item := range v {
			if i > 0 {
				builder.WriteString(", ")
			}
			writeJSON(builder, item)
		}
		builder.WriteByte(']')
	default:
		writeJSONString(builder, fmt.Sprint(v))
	}
}

// writePartTuple emits the retention tuple ((identity, position),
// (order...), text, seen) directly into the builder, avoiding the
// intermediate slices a generic wireByteSize call would allocate.
// The emitted bytes are byte-identical to writeJSON of the nested arrays.
func writePartTuple(builder *strings.Builder, key partKey, part *part) {
	builder.WriteByte('[')
	builder.WriteByte('[')
	if key.isStr {
		writeJSONString(builder, key.s)
	} else {
		builder.WriteString(strconv.FormatInt(key.n, 10))
	}
	builder.WriteString(", ")
	builder.WriteString(strconv.Itoa(key.pos))
	builder.WriteString("], [")
	builder.WriteString(strconv.Itoa(part.order[0]))
	builder.WriteString(", ")
	builder.WriteString(strconv.Itoa(part.order[1]))
	builder.WriteString("], ")
	writeJSONString(builder, part.text)
	builder.WriteString(", ")
	builder.WriteString(strconv.Itoa(part.seen))
	builder.WriteByte(']')
}

// writeJSONString emits a JSON string with simplejson semantics
// (ensure_ascii=False): only the JSON-mandatory control set is escaped;
// U+2028/U+2029, HTML-significant characters, and multi-byte text stay raw
// UTF-8. Go's encoding/json would escape U+2028/U+2029 unconditionally, so
// a hand-rolled escaper is required for byte-size parity.
// hexChars mirrors encoding/json's lowercase hex table without the
// import cost; control-character escapes use \u00xx only.
var hexChars = "0123456789abcdef"

func writeJSONString(builder *strings.Builder, value string) {
	builder.WriteByte('"')
	// Fast path: emit runs of printable, non-escaping ASCII bytes in one
	// WriteString. Only escape characters and multi-byte runes take the
	// per-element slow path. simplejson with ensure_ascii=False writes raw
	// UTF-8 for the latter, so WriteRune reproduces the same byte count.
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
