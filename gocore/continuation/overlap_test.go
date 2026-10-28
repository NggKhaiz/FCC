package continuation

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"testing"
)

type goldenStep struct {
	OK      bool             `json:"ok"`
	Events  []map[string]any `json:"events"`
	Message string           `json:"message"`
}

type goldenSizeRecord struct {
	FrameBytes int `json:"frame_bytes"`
	IDsSize    int `json:"ids_size"`
	PartsSize  int `json:"parts_size"`
	Total      int `json:"total"`
}

type goldenScenario struct {
	Name            string             `json:"name"`
	Wire            string             `json:"wire"`
	Prefix          string             `json:"prefix"`
	Limit           int                `json:"limit"`
	Feeds           []map[string]any   `json:"feeds"`
	Steps           []goldenStep       `json:"steps"`
	SizeTrace       []goldenSizeRecord `json:"size_trace"`
	SourceUnchanged bool               `json:"source_unchanged"`
}

type goldenProbe struct {
	Value any `json:"value"`
	Size  int `json:"size"`
}

type goldenFile struct {
	SizeProbes []goldenProbe    `json:"size_probes"`
	Scenarios  []goldenScenario `json:"scenarios"`
}

func loadOverlapGolden(t *testing.T) goldenFile {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join("..", "testdata", "golden_phase3.json"))
	if err != nil {
		t.Fatal(err)
	}
	var golden goldenFile
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	if err := decoder.Decode(&golden); err != nil {
		t.Fatal(err)
	}
	return golden
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

func looseEqual(got, want any) bool {
	switch g := got.(type) {
	case nil:
		return want == nil || isEmptyContainer(want)
	case []any:
		if len(g) == 0 && (want == nil || isEmptyContainer(want)) {
			return true
		}
		w, ok := want.([]any)
		if !ok || len(g) != len(w) {
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

func TestWireByteSizeParity(t *testing.T) {
	for _, probe := range loadOverlapGolden(t).SizeProbes {
		if got := wireByteSize(probe.Value); got != probe.Size {
			t.Fatalf("size of %v: got %d want %d", probe.Value, got, probe.Size)
		}
	}
}

func TestOverlapGoldenParity(t *testing.T) {
	golden := loadOverlapGolden(t)
	for _, scenario := range golden.Scenarios {
		t.Run(scenario.Name, func(t *testing.T) {
			o := NewOverlap(scenario.Prefix, WithLimit(scenario.Limit))
			feeds := scenario.Feeds
			if len(feeds) != len(scenario.Steps) {
				t.Fatalf("feed/step count mismatch: %d feeds vs %d steps",
					len(feeds), len(scenario.Steps))
			}
			for i, step := range scenario.Steps {
				before := normalize(t, feeds[i])
				events, err := o.Feed(feeds[i])
				after := normalize(t, feeds[i])
				if scenario.SourceUnchanged && !reflect.DeepEqual(before, after) {
					t.Fatalf("step %d: Feed mutated its input event", i)
				}
				if !step.OK {
					if err == nil {
						t.Fatalf("step %d: expected error %q, got success", i, step.Message)
					}
					if err.Error() != step.Message {
						t.Fatalf("step %d: error got %q want %q", i, err.Error(), step.Message)
					}
					// The failure is non-fatal: later feeds stay replayable.
					continue
				}
				if err != nil {
					t.Fatalf("step %d: unexpected error %v", i, err)
				}
				wantEvents := make([]any, len(step.Events))
				for j, event := range step.Events {
					wantEvents[j] = event
				}
				gotEvents := make([]any, len(events))
				for j, event := range events {
					gotEvents[j] = event
				}
				if !looseEqual(normalize(t, gotEvents), normalize(t, wantEvents)) {
					t.Fatalf("step %d: events mismatch\ngot:  %v\nwant: %v",
						i, normalize(t, gotEvents), normalize(t, wantEvents))
				}
			}
			assertSizeTrace(t, o.SizeTrace(), scenario.SizeTrace)
		})
	}
}

func assertSizeTrace(t *testing.T, got []SizeRecord, want []goldenSizeRecord) {
	t.Helper()
	if len(got) != len(want) {
		t.Fatalf("size trace length: got %d want %d (%v vs %v)", len(got), len(want), got, want)
	}
	for i := range got {
		if got[i].FrameBytes != want[i].FrameBytes ||
			got[i].IDsSize != want[i].IDsSize ||
			got[i].PartsSize != want[i].PartsSize ||
			got[i].Total != want[i].Total {
			t.Fatalf("size trace %d: got %+v want %+v", i, got[i], want[i])
		}
	}
}
