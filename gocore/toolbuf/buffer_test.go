package toolbuf

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"testing"

	"github.com/NggKhaiz/FCC/gocore/sse"
)

type releasedEntry struct {
	Frame   string         `json:"frame"`
	Payload map[string]any `json:"payload"`
}

type toolbufCall struct {
	Frame    string `json:"frame"`
	Released []any  `json:"released"`
}

type toolbufScenario struct {
	Name    string        `json:"name"`
	WireAPI string        `json:"wire_api"`
	Calls   []toolbufCall `json:"calls"`
}

func loadToolbufScenarios(t *testing.T) []toolbufScenario {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join("..", "testdata", "golden_phase2.json"))
	if err != nil {
		t.Fatal(err)
	}
	var wrapper struct {
		Toolbuf []toolbufScenario `json:"toolbuf"`
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	if err := decoder.Decode(&wrapper); err != nil {
		t.Fatal(err)
	}
	return wrapper.Toolbuf
}

func jsonEqual(t *testing.T, got, want any) bool {
	t.Helper()
	return looseEqual(normalize(t, got), normalize(t, want))
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

// looseEqual treats nil and empty slices/maps as equivalent because the
// fixture omits or empties collections depending on the Python path taken.
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

func TestToolCallBufferParity(t *testing.T) {
	for _, scenario := range loadToolbufScenarios(t) {
		t.Run(scenario.Name, func(t *testing.T) {
			wire := WireMessages
			if scenario.WireAPI == "responses" {
				wire = WireResponses
			}
			buf := New(wire, sse.NewPayloadCache())

			for i, call := range scenario.Calls {
				released := buf.Feed(call.Frame)
				if wire == WireMessages {
					// Messages frames pass through verbatim; the fixture
					// stores released frames as bare strings.
					wantFrames := make([]string, 0, len(call.Released))
					for _, entry := range call.Released {
						wantFrames = append(wantFrames, entry.(string))
					}
					if len(released) != len(wantFrames) {
						t.Fatalf("call %d: released count got %d want %d",
							i, len(released), len(wantFrames))
					}
					if len(released) == 0 {
						continue
					}
					if !reflect.DeepEqual(released, wantFrames) {
						t.Fatalf("call %d: released frames mismatch\ngot:  %q\nwant: %q",
							i, released, wantFrames)
					}
					continue
				}

				// Responses snapshots may be reserialized after filtering, so
				// compare the re-parsed payloads rather than byte formatting.
				if len(released) != len(call.Released) {
					t.Fatalf("call %d: released count got %d want %d",
						i, len(released), len(call.Released))
				}
				for j, frame := range released {
					rawEntry, _ := json.Marshal(call.Released[j])
					var entry releasedEntry
					if err := json.Unmarshal(rawEntry, &entry); err != nil {
						t.Fatal(err)
					}
					payload, ok := sse.FrameData(frame)
					if entry.Payload == nil {
						if ok {
							t.Fatalf("call %d frame %d: expected non-object passthrough", i, j)
						}
						continue
					}
					if !ok || !jsonEqual(t, payload, entry.Payload) {
						t.Fatalf("call %d frame %d: payload mismatch\ngot:  %v\nwant: %v",
							i, j, payload, entry.Payload)
					}
				}
			}
		})
	}
}

func TestGeminiAlwaysPassesThrough(t *testing.T) {
	buf := New(WireGemini, nil)
	frame := "event: x\ndata: {}\n\n"
	if got := buf.Feed(frame); len(got) != 1 || got[0] != frame {
		t.Fatalf("gemini frames must pass through untouched: %v", got)
	}
}
