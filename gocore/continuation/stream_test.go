package continuation

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"github.com/NggKhaiz/FCC/gocore/delivery"
	"github.com/NggKhaiz/FCC/gocore/wireuuid"
)

type streamGoldenFile struct {
	Namespaces []string           `json:"namespaces"`
	Probes     []streamUUID5Probe `json:"uuid5_probes"`
	Scenarios  []streamGoldenCase `json:"scenarios"`
}

type streamUUID5Probe struct {
	Namespace string `json:"namespace"`
	Name      string `json:"name"`
	Hex       string `json:"hex"`
}

type streamGoldenCase struct {
	Name       string           `json:"name"`
	Wire       string           `json:"wire"`
	SeedEvents []map[string]any `json:"seed_events"`
	Namespaces []string         `json:"namespaces"`
	Steps      []streamStep     `json:"steps"`
}

type streamStep struct {
	Op      string           `json:"op"`
	Handoff bool             `json:"handoff"`
	Payload map[string]any   `json:"payload"`
	Events  []map[string]any `json:"events"`
	Event   map[string]any   `json:"event"`
	Failure string           `json:"failure"`
}

func loadStreamGolden(t *testing.T) streamGoldenFile {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join("..", "testdata", "golden_stream3.json"))
	if err != nil {
		t.Fatal(err)
	}
	var doc streamGoldenFile
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.UseNumber()
	if err := dec.Decode(&doc); err != nil {
		t.Fatal(err)
	}
	return doc
}

func parseNamespaces(t *testing.T, values []string) []wireuuid.UUID {
	t.Helper()
	out := make([]wireuuid.UUID, 0, len(values))
	for _, value := range values {
		id, err := wireuuid.Parse(value)
		if err != nil {
			t.Fatalf("parse namespace %q: %v", value, err)
		}
		out = append(out, id)
	}
	return out
}

func TestUUID5GoldenProbes(t *testing.T) {
	for _, probe := range loadStreamGolden(t).Probes {
		namespace, err := wireuuid.Parse(probe.Namespace)
		if err != nil {
			t.Fatal(err)
		}
		if got := wireuuid.NewV5(namespace, probe.Name).Hex(); got != probe.Hex {
			t.Fatalf("uuid5(%s, %s): got %s want %s",
				probe.Namespace, probe.Name, got, probe.Hex)
		}
	}
}

func TestStreamGoldenParity(t *testing.T) {
	for _, sc := range loadStreamGolden(t).Scenarios {
		t.Run(sc.Name, func(t *testing.T) {
			d := delivery.New(delivery.WireAPI(sc.Wire))
			for _, event := range sc.SeedEvents {
				d.Observe(event)
			}
			stream := NewStream(d, WithNamespaces(parseNamespaces(t, sc.Namespaces)))

			for i, step := range sc.Steps {
				switch step.Op {
				case "begin":
					stream.Begin(step.Handoff)
				case "restart":
					stream.RestartAttempt()
				case "observe":
					// The Python generator hands a deepcopy to each method;
					// finalize/observe may mutate that owned frame, while the
					// fixture keeps the pristine payload for comparison.
					owned := asMap(delivery.DeepCopy(step.Payload))
					before := normalize(t, step.Payload)
					d.Observe(owned)
					if !looseEqual(normalize(t, step.Payload), before) {
						t.Fatalf("step %d: Observe mutated its input", i)
					}
				case "boundary":
					got := stream.Boundary()
					if !looseEqual(normalize(t, eventsToAny(got)),
						normalize(t, eventsToAny(step.Events))) {
						t.Fatalf("step %d boundary mismatch\ngot:  %v\nwant: %v",
							i, normalize(t, eventsToAny(got)),
							normalize(t, eventsToAny(step.Events)))
					}
				case "prepare":
					owned := asMap(delivery.DeepCopy(step.Payload))
					before := normalize(t, step.Payload)
					got, err := stream.Prepare(owned)
					if !looseEqual(normalize(t, step.Payload), before) {
						t.Fatalf("step %d: Prepare mutated its input", i)
					}
					assertPrepareResult(t, i, got, err, step)
				case "finalize":
					owned := asMap(delivery.DeepCopy(step.Payload))
					before := normalize(t, step.Payload)
					got, err := stream.Finalize(owned)
					if !looseEqual(normalize(t, step.Payload), before) {
						t.Fatalf("step %d: Finalize mutated its input", i)
					}
					assertFinalizeResult(t, i, got, err, step)
				default:
					t.Fatalf("step %d: unknown op %q", i, step.Op)
				}
			}
		})
	}
}

func assertPrepareResult(t *testing.T, i int, got []map[string]any, err error, step streamStep) {
	t.Helper()
	if step.Failure != "" {
		if err == nil || err.Error() != step.Failure {
			t.Fatalf("step %d: failure got %v want %q", i, err, step.Failure)
		}
		return
	}
	if err != nil {
		t.Fatalf("step %d: unexpected error %v", i, err)
	}
	if !looseEqual(normalize(t, eventsToAny(got)),
		normalize(t, eventsToAny(step.Events))) {
		t.Fatalf("step %d prepare events mismatch\ngot:  %v\nwant: %v",
			i, normalize(t, eventsToAny(got)),
			normalize(t, eventsToAny(step.Events)))
	}
}

func assertFinalizeResult(t *testing.T, i int, got map[string]any, err error, step streamStep) {
	t.Helper()
	if step.Failure != "" {
		if err == nil || err.Error() != step.Failure {
			t.Fatalf("step %d: failure got %v want %q", i, err, step.Failure)
		}
		return
	}
	if err != nil {
		t.Fatalf("step %d: unexpected error %v", i, err)
	}
	if !looseEqual(normalize(t, got), normalize(t, step.Event)) {
		t.Fatalf("step %d finalize mismatch\ngot:  %v\nwant: %v",
			i, normalize(t, got), normalize(t, step.Event))
	}
}

func eventsToAny(events []map[string]any) []any {
	out := make([]any, 0, len(events))
	for _, event := range events {
		out = append(out, event)
	}
	return out
}
