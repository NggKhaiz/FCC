package wirejson

import "testing"

func TestWriteJSONParity(t *testing.T) {
	// Each case is the exact simplejson.dumps(v, use_decimal=True,
	// ensure_ascii=False) output, including the default ", " / ": "
	// separators and the absence of HTML or line-separator escaping.
	ordered := NewObject(2)
	ordered.Set("a", 1)
	ordered.Set("b", []any{2, 3})
	escapes := NewObject(2)
	escapes.Set("q", `a"b\c`)
	escapes.Set("nl", "l\nr\rt\tb\bf\fz")
	html := NewObject(1)
	html.Set("html", "<a>&'")
	cases := []struct {
		name string
		in   any
		want string
	}{
		{"empty array", []any{}, "[]"},
		{"empty object", map[string]any{}, "{}"},
		{"empty ordered object", NewObject(0), "{}"},
		{"scalars", []any{nil, true, false, 1.5, "x"},
			`[null, true, false, 1.5, "x"]`},
		{"ordered object keeps insertion order", ordered,
			`{"a": 1, "b": [2, 3]}`},
		{"plain map renders sorted keys", map[string]any{
			"b": []any{2, 3}, "a": 1,
		}, `{"a": 1, "b": [2, 3]}`},
		{"ordered object value receiver", *ordered,
			`{"a": 1, "b": [2, 3]}`},
		{"escapes", escapes,
			`{"q": "a\"b\\c", "nl": "l\nr\rt\tb\bf\fz"}`},
		{"control escape", "x\x01y", `"x\u0001y"`},
		{"raw unicode", "Hello 世界", `"Hello 世界"`},
		{"html unescaped", html, `{"html": "<a>&'"}`},
		{"line separators raw", "x\u2028y\u2029z", `"x` + "\u2028" + `y` + "\u2029" + `z"`},
		{"int64 key map", map[int64]string{12: "b"}, `{"12": "b"}`},
		{"nested ordered object", []any{ordered},
			`[{"a": 1, "b": [2, 3]}]`},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			// Repeated encoding must be identical: plain maps used to leak
			// Go's randomized iteration order.
			first := Encode(tc.in)
			for range 20 {
				if got := Encode(tc.in); got != first {
					t.Fatalf("non-deterministic encoding: %q then %q", first, got)
				}
			}
			if first != tc.want {
				t.Fatalf("got  %q\nwant %q", first, tc.want)
			}
		})
	}
}

func TestObjectSetReplacesWithoutReordering(t *testing.T) {
	object := NewObject(3)
	object.Set("b", 2)
	object.Set("a", 1)
	object.Set("b", 22)

	if got := Encode(object); got != `{"b": 22, "a": 1}` {
		t.Fatalf("got %q", got)
	}
	if value, ok := object.Get("a"); !ok || value != 1 {
		t.Fatalf("Get(a) = %v, %v", value, ok)
	}
	if object.Len() != 2 {
		t.Fatalf("Len = %d, want 2", object.Len())
	}
}

func TestPlainMapByteLengthIsOrderIndependent(t *testing.T) {
	// Retention/token accounting only needs the size, which must not depend
	// on the randomized map iteration order.
	value := map[string]any{"first_key": "x", "second_key": []any{1, 2}}
	size := Bytes(value)
	for range 20 {
		if Bytes(value) != size {
			t.Fatal("map byte size changed across iterations")
		}
	}
}
