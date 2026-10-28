package delivery

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"testing"
)

type d3Trace struct {
	Progress int    `json:"progress"`
	Unsafe   string `json:"unsafe"`
	Retained int    `json:"retained"`
	Limited  bool   `json:"limited"`
}

type d3Snapshot struct {
	Text         string `json:"text"`
	Thinking     string `json:"thinking"`
	HasCalls     bool   `json:"has_calls"`
	Eligible     bool   `json:"eligible"`
	CanHandoff   bool   `json:"can_handoff"`
	UnsafeReason string `json:"unsafe_reason"`
}

type d3Scenario struct {
	Name           string                    `json:"name"`
	Wire           string                    `json:"wire"`
	Limit          int                       `json:"limit"`
	Events         []map[string]any          `json:"events"`
	Trace          []d3Trace                 `json:"trace"`
	Items          map[string]map[string]any `json:"items"`
	Closed         []int                     `json:"closed"`
	TextClosed     [][]any                   `json:"text_closed"`
	PartClosed     [][]any                   `json:"part_closed"`
	Header         map[string]any            `json:"header"`
	Sequence       int                       `json:"sequence"`
	TextConstraint string                    `json:"text_constraint"`
	Snapshot       d3Snapshot                `json:"snapshot"`
	ClosingEvents  []map[string]any          `json:"closing_events"`
	Usage          map[string]any            `json:"usage"`
	OutputTokens   int                       `json:"output_tokens"`
	CanHandoff     bool                      `json:"can_handoff"`
}

func loadDelivery3(t *testing.T) []d3Scenario {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join("..", "testdata", "golden_delivery3.json"))
	if err != nil {
		t.Fatal(err)
	}
	var doc struct {
		Scenarios []d3Scenario `json:"scenarios"`
	}
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.UseNumber()
	if err := dec.Decode(&doc); err != nil {
		t.Fatal(err)
	}
	return doc.Scenarios
}

// d3Normalize round-trips through JSON with UseNumber so ints in the Go
// state and the fixture compare without float/int type noise.
func d3Normalize(t *testing.T, value any) any {
	t.Helper()
	raw, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	var out any
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.UseNumber()
	if err := dec.Decode(&out); err != nil {
		t.Fatal(err)
	}
	return out
}

// canonical replaces nil slices/maps with empty ones so Go's nil semantics
// match Python's empty lists/dicts after JSON round-tripping.
func canonical(value any) any {
	switch v := value.(type) {
	case nil:
		// Fixture comparisons only involve arrays or objects, so a null
		// slice/map normalizes to the empty Python list/dict counterpart.
		return []any{}
	case []any:
		if v == nil {
			return []any{}
		}
		for i := range v {
			v[i] = canonical(v[i])
		}
		return v
	case map[string]any:
		for key, item := range v {
			v[key] = canonical(item)
		}
		return v
	default:
		return value
	}
}

func d3LooseEqual(a, b any) bool {
	if reflect.DeepEqual(a, b) {
		return true
	}
	as, aOK := a.([]any)
	bs, bOK := b.([]any)
	if aOK && bOK && len(as) == 0 && len(bs) == 0 {
		return true
	}
	am, aOK := a.(map[string]any)
	bm, bOK := b.(map[string]any)
	if aOK && bOK {
		if len(am) != len(bm) {
			return false
		}
		for key, av := range am {
			bv, ok := bm[key]
			if !ok || !d3LooseEqual(av, bv) {
				return false
			}
		}
		return true
	}
	if aOK && bOK {
		if len(as) != len(bs) {
			return false
		}
		for i := range as {
			if !d3LooseEqual(as[i], bs[i]) {
				return false
			}
		}
		return true
	}
	return false
}

func TestDeliveryGoldenParity(t *testing.T) {
	for _, sc := range loadDelivery3(t) {
		t.Run(sc.Name, func(t *testing.T) {
			d := New(WireAPI(sc.Wire), WithRetentionLimit(sc.Limit))
			for i, event := range sc.Events {
				d.Observe(event)
				got := d3Trace{
					Progress: d.Progress(),
					Retained: d.RetainedBytes(),
					Limited:  d.RetentionExhausted(),
				}
				// Python trace records unsafe_reason before text_constraint.
				got.Unsafe = d.unsafe
				if got.Unsafe == "" {
					got.Unsafe = d.textConst
				}
				want := sc.Trace[i]
				if got != want {
					t.Fatalf("trace %d: got %+v want %+v", i, got, want)
				}
			}

			gotItems := map[string]map[string]any{}
			indices := make([]int, 0, len(d.items))
			for index := range d.items {
				indices = append(indices, index)
			}
			sort.Ints(indices)
			for _, index := range indices {
				gotItems[itoa(index)] = d.items[index]
			}
			if !d3LooseEqual(canonical(d3Normalize(t, gotItems)), canonical(d3Normalize(t, sc.Items))) {
				t.Fatalf("items mismatch\ngot:  %v\nwant: %v",
					canonical(d3Normalize(t, gotItems)), canonical(d3Normalize(t, sc.Items)))
			}

			if !reflect.DeepEqual(d.ClosedItems(), sc.Closed) {
				t.Fatalf("closed got %v want %v", d.ClosedItems(), sc.Closed)
			}
			if !d3LooseEqual(canonical(d3Normalize(t, partKeyArrays(d.TextClosedKeys()))), canonical(d3Normalize(t, sc.TextClosed))) ||
				!d3LooseEqual(canonical(d3Normalize(t, partKeyArrays(d.PartClosedKeys()))), canonical(d3Normalize(t, sc.PartClosed))) {
				t.Fatalf("part-key sets mismatch:\ntext got %v want %v\npart got %v want %v",
					d.TextClosedKeys(), sc.TextClosed, d.PartClosedKeys(), sc.PartClosed)
			}
			if !d3LooseEqual(canonical(d3Normalize(t, d.header)), canonical(d3Normalize(t, sc.Header))) {
				t.Fatalf("header got %v want %v", d.header, sc.Header)
			}
			if d.sequence != sc.Sequence {
				t.Fatalf("sequence got %d want %d", d.sequence, sc.Sequence)
			}
			if d.textConst != sc.TextConstraint {
				t.Fatalf("text constraint got %q want %q", d.textConst, sc.TextConstraint)
			}

			snap := d.Snapshot()
			gotSnap := d3Snapshot{
				Text:         snap.Text,
				Thinking:     snap.Thinking,
				HasCalls:     snap.HasCalls,
				Eligible:     snap.Eligible,
				CanHandoff:   snap.CanHandoff,
				UnsafeReason: snap.UnsafeReason,
			}
			if gotSnap != sc.Snapshot {
				t.Fatalf("snapshot got %+v want %+v", gotSnap, sc.Snapshot)
			}

			if !d3LooseEqual(canonical(d3Normalize(t, d.ClosingEvents())), canonical(d3Normalize(t, sc.ClosingEvents))) {
				t.Fatalf("closing events got %v want %v",
					canonical(d3Normalize(t, d.ClosingEvents())), canonical(d3Normalize(t, sc.ClosingEvents)))
			}
			if !d3LooseEqual(canonical(d3Normalize(t, d.Usage(nil))), canonical(d3Normalize(t, sc.Usage))) {
				t.Fatalf("usage got %v want %v", canonical(d3Normalize(t, d.Usage(nil))), canonical(d3Normalize(t, sc.Usage)))
			}
			if d.OutputTokens() != sc.OutputTokens {
				t.Fatalf("output tokens got %d want %d", d.OutputTokens(), sc.OutputTokens)
			}
			if d.CanHandoff() != sc.CanHandoff {
				t.Fatalf("can_handoff got %v want %v", d.CanHandoff(), sc.CanHandoff)
			}
		})
	}
}

func partKeyArrays(keys []PartKey) []any {
	out := make([]any, 0, len(keys))
	for _, key := range keys {
		out = append(out, []any{key.Index, key.Field, key.Position})
	}
	return out
}

func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	neg := n < 0
	if neg {
		n = -n
	}
	var buf [20]byte
	i := len(buf)
	for n > 0 {
		i--
		buf[i] = byte('0' + n%10)
		n /= 10
	}
	if neg {
		i--
		buf[i] = '-'
	}
	return string(buf[i:])
}
