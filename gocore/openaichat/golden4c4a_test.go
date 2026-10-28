package openaichat

import (
	"bytes"
	"encoding/json"
	"errors"
	"maps"
	"os"
	"path/filepath"
	"reflect"
	"strconv"
	"testing"

	"github.com/NggKhaiz/FCC/gocore/providerpolicy"
)

type leafFixture struct {
	Schema  string `json:"schema"`
	BaseURL []struct {
		Input  string `json:"input"`
		Output string `json:"output"`
	} `json:"base_url"`
	ExtraBody []struct {
		Name           string         `json:"name"`
		Extra          map[string]any `json:"extra"`
		CanonicalError *string        `json:"canonical_error"`
		ReasoningError *string        `json:"reasoning_error"`
	} `json:"extra_body"`
	StreamUsage struct {
		Request []struct {
			Name string         `json:"name"`
			Body map[string]any `json:"body"`
		} `json:"request"`
		Clone []struct {
			Name           string         `json:"name"`
			Input          map[string]any `json:"input"`
			Output         map[string]any `json:"output"`
			InputUnchanged bool           `json:"input_unchanged"`
		} `json:"clone"`
	} `json:"stream_usage"`
	UsageExtraction struct {
		UsageInt []struct {
			Name   string         `json:"name"`
			Usage  map[string]any `json:"usage"`
			Key    string         `json:"key"`
			Output *int           `json:"output"`
		} `json:"usage_int"`
		NestedUsageInt []struct {
			Name   string         `json:"name"`
			Usage  map[string]any `json:"usage"`
			Parent string         `json:"parent"`
			Key    string         `json:"key"`
			Output *int           `json:"output"`
		} `json:"nested_usage_int"`
	} `json:"usage_extraction"`
	UsageRejection []struct {
		Name     string         `json:"name"`
		Error    map[string]any `json:"error"`
		Expected bool           `json:"expected"`
	} `json:"usage_rejection"`
	OutputCapParse []struct {
		Name     string         `json:"name"`
		Error    map[string]any `json:"error"`
		Expected *int           `json:"expected"`
	} `json:"output_cap_parse"`
	OutputCapClamp []struct {
		Name           string         `json:"name"`
		Input          map[string]any `json:"input"`
		Cap            int            `json:"cap"`
		Output         map[string]any `json:"output"`
		InputUnchanged bool           `json:"input_unchanged"`
	} `json:"output_cap_clamp"`
	Identity []struct {
		Name       string `json:"name"`
		Steps      []identityStep
		FinalCalls []struct {
			Slot     int     `json:"slot"`
			Index    *int    `json:"index"`
			ToolID   *string `json:"tool_id"`
			OrderKey []int   `json:"order_key"`
		} `json:"final_calls"`
	} `json:"identity"`
}

type identityStep struct {
	Op       string  `json:"op"`
	Index    any     `json:"index"`
	ID       any     `json:"id"`
	Slot     *int    `json:"slot"`
	Error    *string `json:"error"`
	ToolID   *string `json:"tool_id"`
	OrderKey []int   `json:"order_key"`
}

func loadLeafFixture(t *testing.T) leafFixture {
	t.Helper()
	raw, err := os.ReadFile(
		filepath.Join("..", "testdata", "golden_openaichat4c4a.json"))
	if err != nil {
		t.Fatalf("read golden fixture: %v", err)
	}
	var fixture leafFixture
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.UseNumber()
	if err := dec.Decode(&fixture); err != nil {
		t.Fatalf("decode golden fixture: %v", err)
	}
	return fixture
}

// canonicalize normalises json.Number values so Go int outputs compare
// against fixture numbers regardless of their decoded representation.
func canonicalize(value any) any {
	switch typed := value.(type) {
	case json.Number:
		if parsed, err := typed.Int64(); err == nil {
			return parsed
		}
		parsed, _ := typed.Float64()
		return parsed
	case int:
		return int64(typed)
	case int64:
		return typed
	case map[string]any:
		clone := make(map[string]any, len(typed))
		for key, item := range typed {
			clone[key] = canonicalize(item)
		}
		return clone
	case []any:
		clone := make([]any, len(typed))
		for i, item := range typed {
			clone[i] = canonicalize(item)
		}
		return clone
	default:
		return value
	}
}

func jsonEqual(t *testing.T, label string, expected, actual any) {
	t.Helper()
	if !reflect.DeepEqual(canonicalize(expected), canonicalize(actual)) {
		t.Errorf("%s: expected %#v, got %#v", label, expected, actual)
	}
}

func intPointerValue(pointer *int) any {
	if pointer == nil {
		return nil
	}
	return *pointer
}

func TestLeafGoldenParity(t *testing.T) {
	fixture := loadLeafFixture(t)
	for iteration := 0; iteration < 20; iteration++ {
		replayLeafFixture(t, &fixture)
	}
}

func replayLeafFixture(t *testing.T, fixture *leafFixture) {
	t.Helper()

	for _, tc := range fixture.BaseURL {
		jsonEqual(t, "base_url/"+tc.Input, tc.Output, CanonicalV1BaseURL(tc.Input))
	}

	for _, tc := range fixture.ExtraBody {
		canonical := errorString(ValidateExtraBody(tc.Extra))
		jsonEqual(t, "extra_body/"+tc.Name+"/canonical",
			stringPointer(tc.CanonicalError), canonical)
		reasoning := errorString(ValidateExtraBodyReasoning(tc.Extra))
		jsonEqual(t, "extra_body/"+tc.Name+"/reasoning",
			stringPointer(tc.ReasoningError), reasoning)
	}

	for _, tc := range fixture.StreamUsage.Request {
		body := maps.Clone(tc.Body)
		RequestStreamUsage(body)
		jsonEqual(t, "stream_usage/request/"+tc.Name, tc.Body, body)
	}
	for _, tc := range fixture.StreamUsage.Clone {
		body := maps.Clone(tc.Input)
		output := CloneWithoutStreamUsage(body)
		jsonEqual(t, "stream_usage/clone/"+tc.Name, tc.Output, output)
		jsonEqual(t, "stream_usage/clone/"+tc.Name+"/input", tc.Input, body)
	}

	for _, tc := range fixture.UsageExtraction.UsageInt {
		got := UsageInt(tc.Usage, tc.Key)
		jsonEqual(t, "usage_int/"+tc.Name,
			intPointerValue(tc.Output), intPointerValue(got))
	}
	for _, tc := range fixture.UsageExtraction.NestedUsageInt {
		got := NestedUsageInt(tc.Usage, tc.Parent, tc.Key)
		jsonEqual(t, "nested_usage_int/"+tc.Name,
			intPointerValue(tc.Output), intPointerValue(got))
	}

	for _, tc := range fixture.UsageRejection {
		err := providerpolicy.FromDescriptor(tc.Error)
		jsonEqual(t, "usage_rejection/"+tc.Name,
			tc.Expected, IsStreamUsageRejection(err))
	}

	for _, tc := range fixture.OutputCapParse {
		err := providerpolicy.FromDescriptor(tc.Error)
		got := ParseOutputTokenCap(err)
		jsonEqual(t, "output_cap_parse/"+tc.Name,
			intPointerValue(tc.Expected), intPointerValue(got))
	}
	for _, tc := range fixture.OutputCapClamp {
		body := maps.Clone(tc.Input)
		output := ClampOutputTokens(body, tc.Cap)
		jsonEqual(t, "output_cap_clamp/"+tc.Name, tc.Output, output)
		jsonEqual(t, "output_cap_clamp/"+tc.Name+"/input", tc.Input, body)
	}

	for _, scenario := range fixture.Identity {
		replayIdentityScenario(t, scenario.Name, scenario.Steps, scenario.FinalCalls)
	}
}

func replayIdentityScenario(
	t *testing.T,
	name string,
	steps []identityStep,
	expectedFinal []struct {
		Slot     int     `json:"slot"`
		Index    *int    `json:"index"`
		ToolID   *string `json:"tool_id"`
		OrderKey []int   `json:"order_key"`
	},
) {
	t.Helper()
	resolver := NewToolCallIdentityResolver()
	for i, step := range steps {
		label := name + "/step" + strconv.Itoa(i)
		switch step.Op {
		case "allocate":
			slot := resolver.Allocate()
			jsonEqual(t, label+"/slot", intPointerValue(step.Slot), slot)
		case "resolve":
			slot, err := resolver.Resolve(step.Index, step.ID)
			if step.Error != nil {
				if err == nil {
					t.Errorf("%s: expected error %q, got slot %d", label, *step.Error, slot)
					continue
				}
				var ambiguous *AmbiguousToolCallIdentityError
				if !errors.As(err, &ambiguous) {
					t.Errorf("%s: error is not AmbiguousToolCallIdentityError: %v", label, err)
				}
				jsonEqual(t, label+"/error", "AmbiguousToolCallIdentityError: "+err.Error(),
					*step.Error)
				continue
			}
			if err != nil {
				t.Errorf("%s: unexpected error: %v", label, err)
				continue
			}
			jsonEqual(t, label+"/slot", intPointerValue(step.Slot), slot)
			jsonEqual(t, label+"/tool_id", stringPointer(step.ToolID),
				stringPointer(resolver.ToolID(slot)))
			orderKey := resolver.OrderKey(slot)
			jsonEqual(t, label+"/order_key", step.OrderKey,
				[]int{orderKey[0], orderKey[1]})
		default:
			t.Fatalf("%s: unknown op %q", label, step.Op)
		}
	}

	snapshot := resolver.Snapshot()
	if len(snapshot) != len(expectedFinal) {
		t.Fatalf("%s: final call count expected %d, got %d",
			name, len(expectedFinal), len(snapshot))
	}
	for i, expected := range expectedFinal {
		got := snapshot[i]
		suffix := strconv.Itoa(i)
		jsonEqual(t, name+"/final/index"+suffix,
			intPointerValue(expected.Index), intPointerValue(got.Index))
		jsonEqual(t, name+"/final/tool_id"+suffix,
			stringPointer(expected.ToolID), stringPointer(got.ToolID))
		jsonEqual(t, name+"/final/order_key"+suffix,
			expected.OrderKey, []int{got.OrderKey[0], got.OrderKey[1]})
	}
}

func TestIdentityErrorSentinels(t *testing.T) {
	resolver := NewToolCallIdentityResolver()
	if _, err := resolver.Resolve(nil, nil); !errors.Is(err, errNoUsableIdentity) {
		t.Fatalf("expected errNoUsableIdentity, got %v", err)
	}
	var ambiguous *AmbiguousToolCallIdentityError
	if _, err := resolver.Resolve(0, "call_a"); err != nil {
		t.Fatalf("first resolve failed: %v", err)
	}
	if _, err := resolver.Resolve(0, "call_b"); err != nil {
		t.Fatalf("second resolve failed: %v", err)
	}
	if _, err := resolver.Resolve(0, nil); !errors.As(err, &ambiguous) {
		t.Fatalf("expected ambiguous identity error, got %v", err)
	}
}

func TestCanonicalV1BaseURLEdges(t *testing.T) {
	cases := map[string]string{
		"":         "/v1",
		"/v1":      "/v1",
		"///":      "/v1",
		"a/v1//":   "a/v1",
		"h.test/x": "h.test/x/v1",
	}
	for input, expected := range cases {
		if got := CanonicalV1BaseURL(input); got != expected {
			t.Errorf("CanonicalV1BaseURL(%q) = %q, want %q", input, got, expected)
		}
	}
}

func errorString(err error) any {
	if err == nil {
		return nil
	}
	return err.Error()
}

func stringPointer(pointer *string) any {
	if pointer == nil {
		return nil
	}
	return *pointer
}
