package providerpolicy

import (
	"bytes"
	"encoding/json"
)

// JSONObject preserves insertion order while remaining convenient to query.
// Python's dict-backed bodies keep key order through json.loads/dumps; Go's
// map sorts keys, which would change the exact diagnostic byte stream, so
// fixture bodies decode into this type instead.
type JSONObject struct {
	Keys   []string
	Fields map[string]any
}

// decodeOrdered parses JSON with object order preserved and numbers kept as
// json.Number, mirroring the Python json.loads path used by the diagnostics.
func decodeOrdered(raw []byte) any {
	if len(raw) == 0 {
		return nil
	}
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.UseNumber()
	value, err := decodeOrderedValue(dec)
	if err != nil {
		return nil
	}
	return value
}

func decodeOrderedValue(dec *json.Decoder) (any, error) {
	tok, err := dec.Token()
	if err != nil {
		return nil, err
	}
	switch t := tok.(type) {
	case json.Delim:
		switch t {
		case '{':
			obj := &JSONObject{Fields: map[string]any{}}
			for dec.More() {
				keyTok, err := dec.Token()
				if err != nil {
					return nil, err
				}
				value, err := decodeOrderedValue(dec)
				if err != nil {
					return nil, err
				}
				key := keyTok.(string)
				obj.Keys = append(obj.Keys, key)
				obj.Fields[key] = value
			}
			_, err := dec.Token() // closing brace
			return obj, err
		case '[':
			var arr []any
			for dec.More() {
				value, err := decodeOrderedValue(dec)
				if err != nil {
					return nil, err
				}
				arr = append(arr, value)
			}
			_, err := dec.Token() // closing bracket
			return arr, err
		}
	}
	return tok, nil
}

// compactJSON renders value with compact separators, raw UTF-8 and no HTML
// escaping, matching json.dumps(ensure_ascii=False, separators=(",", ":")).
func compactJSON(value any) string {
	var buf bytes.Buffer
	writeCompact(&buf, value)
	return buf.String()
}

func writeCompact(buf *bytes.Buffer, value any) {
	switch v := value.(type) {
	case nil:
		buf.WriteString("null")
	case bool:
		if v {
			buf.WriteString("true")
		} else {
			buf.WriteString("false")
		}
	case json.Number:
		buf.WriteString(string(v))
	case string:
		raw, _ := json.Marshal(v) // compact string escaping without HTML concerns
		buf.Write(raw)
	case *JSONObject:
		buf.WriteByte('{')
		for i, key := range v.Keys {
			if i > 0 {
				buf.WriteByte(',')
			}
			writeCompact(buf, key)
			buf.WriteByte(':')
			writeCompact(buf, v.Fields[key])
		}
		buf.WriteByte('}')
	case map[string]any:
		buf.WriteByte('{')
		first := true
		for key, item := range v {
			if !first {
				buf.WriteByte(',')
			}
			first = false
			writeCompact(buf, key)
			buf.WriteByte(':')
			writeCompact(buf, item)
		}
		buf.WriteByte('}')
	case []any:
		buf.WriteByte('[')
		for i, item := range v {
			if i > 0 {
				buf.WriteByte(',')
			}
			writeCompact(buf, item)
		}
		buf.WriteByte(']')
	default:
		raw, _ := json.Marshal(v)
		buf.Write(raw)
	}
}

// asObject returns the object view for either the ordered type or a plain
// decoded map, plus whether value is an object at all.
func asObject(value any) (*JSONObject, bool) {
	switch v := value.(type) {
	case *JSONObject:
		return v, true
	case map[string]any:
		obj := &JSONObject{Fields: map[string]any{}}
		for key, item := range v {
			obj.Keys = append(obj.Keys, key)
			obj.Fields[key] = item
		}
		return obj, true
	default:
		return nil, false
	}
}
