package delivery

import "encoding/json"

// deepCopy duplicates the JSON-shaped values Python's deepcopy protects:
// maps and slices are rebuilt recursively, while immutable scalars (strings,
// json.Number, bools, nil) are safe to share. Cached payloads must never be
// mutated in place, exactly as in the Python implementation.
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
		// json.Number, string, bool, nil, numeric scalars are immutable.
		return value
	}
}

func asMap(value any) map[string]any {
	if m, ok := value.(map[string]any); ok {
		return m
	}
	return nil
}

func asList(value any) []any {
	if l, ok := value.([]any); ok {
		return l
	}
	return nil
}

func stringField(value map[string]any, key string) string {
	if s, ok := value[key].(string); ok {
		return s
	}
	return ""
}

func isInt(value any) (int, bool) {
	// JSON never yields float integral types with UseNumber; float64 is
	// accepted defensively only when it holds an integral value.
	switch n := value.(type) {
	case int:
		return n, true
	case json.Number:
		i, err := n.Int64()
		if err != nil {
			return 0, false
		}
		return int(i), true
	case float64:
		if n == float64(int64(n)) {
			return int(n), true
		}
	}
	return 0, false
}
