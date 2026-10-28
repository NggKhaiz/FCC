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
	"sort"
	"strconv"
	"strings"
)

// hexChars is the lowercase hex table for control-character escapes.
var hexChars = "0123456789abcdef"

// Object is a JSON object with explicit key order, mirroring Python dict
// insertion order under json.loads/dumps. Go maps randomize iteration, so
// byte-for-byte wire parity must route ordered payloads through this type.
// Plain maps remain supported but render with sorted keys: their byte
// length is order-independent, which keeps retention/token accounting
// deterministic even when callers only need the size.
type Object struct {
	Keys   []string
	Fields map[string]any
}

// NewObject returns an ordered object with the given initial capacity.
func NewObject(capacity int) *Object {
	return &Object{
		Keys:   make([]string, 0, capacity),
		Fields: make(map[string]any, capacity),
	}
}

// Set inserts or replaces one field, appending the key on first insertion.
func (o *Object) Set(key string, value any) {
	if _, exists := o.Fields[key]; !exists {
		o.Keys = append(o.Keys, key)
	}
	o.Fields[key] = value
}

// Len reports the number of fields.
func (o *Object) Len() int { return len(o.Keys) }

// Get returns the field and whether the key exists.
func (o *Object) Get(key string) (any, bool) {
	value, ok := o.Fields[key]
	return value, ok
}

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
	case *Object:
		writeObject(builder, v)
	case Object:
		object := v
		writeObject(builder, &object)
	case map[string]any:
		keys := make([]string, 0, len(v))
		for key := range v {
			keys = append(keys, key)
		}
		sort.Strings(keys)
		builder.WriteByte('{')
		for i, key := range keys {
			if i > 0 {
				builder.WriteString(", ")
			}
			WriteJSONString(builder, key)
			builder.WriteString(": ")
			WriteJSON(builder, v[key])
		}
		builder.WriteByte('}')
	case map[int64]string:
		// Python JSON coerces integer dict keys to strings; the overlap id
		// table relies on this key convention.
		keys := make([]int64, 0, len(v))
		for key := range v {
			keys = append(keys, key)
		}
		sort.Slice(keys, func(i, j int) bool { return keys[i] < keys[j] })
		builder.WriteByte('{')
		for i, key := range keys {
			if i > 0 {
				builder.WriteString(", ")
			}
			WriteJSONString(builder, strconv.FormatInt(key, 10))
			builder.WriteString(": ")
			WriteJSON(builder, v[key])
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

// writeObject emits an ordered object, honoring its explicit key sequence.
func writeObject(builder *strings.Builder, object *Object) {
	builder.WriteByte('{')
	for i, key := range object.Keys {
		if i > 0 {
			builder.WriteString(", ")
		}
		WriteJSONString(builder, key)
		builder.WriteString(": ")
		WriteJSON(builder, object.Fields[key])
	}
	builder.WriteByte('}')
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
