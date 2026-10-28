package wirejson

import "testing"

func TestWriteJSONParity(t *testing.T) {
	// Each case is the exact simplejson.dumps(v, use_decimal=True,
	// ensure_ascii=False) output, including the default ", " / ": "
	// separators and the absence of HTML or line-separator escaping.
	cases := []struct {
		name string
		in   any
		want string
	}{
		{"empty array", []any{}, "[]"},
		{"empty object", map[string]any{}, "{}"},
		{"scalars", []any{nil, true, false, 1.5, "x"},
			`[null, true, false, 1.5, "x"]`},
		{"spaced separators", map[string]any{"a": 1, "b": []any{2, 3}},
			`{"a": 1, "b": [2, 3]}`},
		{"escapes", map[string]any{"q": `a"b\c`, "nl": "l\nr\rt\tb\bf\fz"},
			`{"q": "a\"b\\c", "nl": "l\nr\rt\tb\bf\fz"}`},
		{"control escape", "x\x01y", `"x\u0001y"`},
		{"raw unicode", "Hello 世界", `"Hello 世界"`},
		{"html unescaped", map[string]any{"html": "<a>&'"},
			`{"html": "<a>&'"}`},
		{"line separators raw", "x\u2028y\u2029z", `"x` + "\u2028" + `y` + "\u2029" + `z"`},
		{"int64 key map", map[int64]string{12: "b"}, `{"12": "b"}`},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got := Encode(tc.in)
			if got != tc.want {
				t.Fatalf("got  %q\nwant %q", got, tc.want)
			}
		})
	}
}
