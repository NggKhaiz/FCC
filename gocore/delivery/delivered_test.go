package delivery

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

type goldenState struct {
	Progress           int    `json:"progress"`
	RetentionExhausted bool   `json:"retention_exhausted"`
	UnsafeReason       string `json:"unsafe_reason"`
	Snapshot           struct {
		Text          string `json:"text"`
		Thinking      string `json:"thinking"`
		HasCalls      bool   `json:"has_calls"`
		Eligible      bool   `json:"eligible"`
		CanHandoff    bool   `json:"can_handoff"`
		UnsafeReason  string `json:"unsafe_reason"`
		RetainedBytes int    `json:"retained_bytes"`
	} `json:"snapshot"`
	ItemsSize int `json:"items_size"`
}

type goldenEvent struct {
	Payload       map[string]any   `json:"payload"`
	CountProgress bool             `json:"count_progress"`
	State         goldenState      `json:"state"`
	ClosingEvents []map[string]any `json:"closing_events"`
}

type deliveryScenario struct {
	Name               string           `json:"name"`
	WireAPI            string           `json:"wire_api"`
	Limit              int              `json:"limit"`
	Events             []goldenEvent    `json:"events"`
	MidClosingEvents   []map[string]any `json:"mid_closing_events"`
	FinalClosingEvents []map[string]any `json:"final_closing_events"`
	HandoffEvents      []map[string]any `json:"handoff_events"`
	Final              goldenState      `json:"final"`
	States             []goldenState    `json:"states"`
}

func loadDeliveryScenarios(t *testing.T) []deliveryScenario {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join("..", "testdata", "golden_phase2.json"))
	if err != nil {
		t.Fatal(err)
	}
	var wrapper struct {
		Delivery []deliveryScenario `json:"delivery"`
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	if err := decoder.Decode(&wrapper); err != nil {
		t.Fatal(err)
	}
	return wrapper.Delivery
}

// jsonEqual normalizes through JSON so int indices compare regardless of
// their concrete Go representation, matching the Python fixture encoding.
// Empty and nil containers are treated as equal.
func jsonEqual(t *testing.T, got any, want any) bool {
	t.Helper()
	gotNorm := normalize(t, got)
	wantNorm := normalize(t, want)
	return looseEqual(gotNorm, wantNorm)
}

func normalize(t *testing.T, value any) any {
	t.Helper()
	raw, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	var out any
	if err := json.Unmarshal(raw, &out); err != nil {
		t.Fatal(err)
	}
	return out
}

// looseEqual compares decoded JSON values while treating nil and empty
// slices/maps as equivalent, since the fixture omits empty collections.
func looseEqual(got, want any) bool {
	switch g := got.(type) {
	case nil:
		return want == nil || isEmptyContainer(want)
	case []any:
		if len(g) == 0 && (want == nil || isEmptyContainer(want)) {
			return true
		}
		w, ok := want.([]any)
		if !ok {
			return false
		}
		if len(g) != len(w) {
			return false
		}
		for i := range g {
			if !looseEqual(g[i], w[i]) {
				return false
			}
		}
		return true
	case map[string]any:
		if len(g) == 0 && (want == nil || isEmptyContainer(want)) {
			return true
		}
		w, ok := want.(map[string]any)
		if !ok || len(g) != len(w) {
			return false
		}
		for key, value := range g {
			other, exists := w[key]
			if !exists || !looseEqual(value, other) {
				return false
			}
		}
		return true
	default:
		return reflect.DeepEqual(got, want)
	}
}

func isEmptyContainer(value any) bool {
	switch v := value.(type) {
	case []any:
		return len(v) == 0
	case map[string]any:
		return len(v) == 0
	default:
		return false
	}
}

func assertState(t *testing.T, d *Delivered, want goldenState, labels ...any) {
	t.Helper()
	got := d.Snapshot()
	if d.Progress() != want.Progress {
		t.Fatalf("progress: got %d want %d ctx=%v", d.Progress(), want.Progress, labels)
	}
	if d.RetentionExhausted() != want.RetentionExhausted {
		t.Fatalf("retention_exhausted: got %v want %v", d.RetentionExhausted(), want.RetentionExhausted)
	}
	if d.UnsafeReason() != want.UnsafeReason {
		t.Fatalf("unsafe_reason: got %q want %q", d.UnsafeReason(), want.UnsafeReason)
	}
	if len(d.items) != want.ItemsSize {
		t.Fatalf("items_size: got %d want %d", len(d.items), want.ItemsSize)
	}
	if got.Text != want.Snapshot.Text || got.Thinking != want.Snapshot.Thinking ||
		got.HasCalls != want.Snapshot.HasCalls || got.Eligible != want.Snapshot.Eligible ||
		got.CanHandoff != want.Snapshot.CanHandoff ||
		got.UnsafeReason != want.Snapshot.UnsafeReason ||
		got.RetainedBytes != want.Snapshot.RetainedBytes {
		t.Fatalf("snapshot mismatch:\n got %+v\nwant %+v", got, want.Snapshot)
	}
}

func TestDeliveredParity(t *testing.T) {
	for _, scenario := range loadDeliveryScenarios(t) {
		t.Run(scenario.Name, func(t *testing.T) {
			d := New(WireMessages, WithRetentionLimit(scenario.Limit))

			if len(scenario.States) > 0 {
				// Retention-limit scenario replays plain deltas and only
				// checks state evolution.
				for stateIndex, want := range scenario.States {
					d.Observe(map[string]any{
						"type":  "content_block_delta",
						"index": json.Number("0"),
						"delta": map[string]any{
							"type": "text_delta",
							"text": strings.Repeat("x", 40),
						},
					})
					assertState(t, d, want, stateIndex)
				}
				assertState(t, d, scenario.Final, "final")
				return
			}

			for i, event := range scenario.Events {
				d.Observe(event.Payload, event.CountProgress)
				assertState(t, d, event.State, i)
				if !jsonEqual(t, d.ClosingEvents(), event.ClosingEvents) {
					t.Fatalf("event %d closing events mismatch:\n got %v\nwant %v",
						i, d.ClosingEvents(), event.ClosingEvents)
				}
			}
			assertState(t, d, scenario.Final, "final")
			if !jsonEqual(t, d.ClosingEvents(), scenario.FinalClosingEvents) {
				t.Fatalf("final closing events mismatch:\n got %v\nwant %v",
					d.ClosingEvents(), scenario.FinalClosingEvents)
			}
			if !jsonEqual(t, d.HandoffEvents(), scenario.HandoffEvents) {
				t.Fatalf("handoff events mismatch:\n got %v\nwant %v",
					d.HandoffEvents(), scenario.HandoffEvents)
			}
		})
	}
}

func TestDeepCopyIsolation(t *testing.T) {
	d := New(WireMessages)
	block := map[string]any{"type": "text", "text": "original"}
	d.Observe(map[string]any{
		"type":          "content_block_start",
		"index":         json.Number("0"),
		"content_block": block,
	})
	block["text"] = "mutated after observe"
	prefix := d.Snapshot()
	if prefix.Text != "original" {
		t.Fatalf("cached payload mutation leaked into retained state: %q", prefix.Text)
	}
}
