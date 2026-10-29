package openaichat

import (
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"testing"
)

type toolNameFixture struct {
	Schema string `json:"schema"`
	Codecs []struct {
		Label      string   `json:"label"`
		Names      []string `json:"names"`
		HasAliases bool     `json:"has_aliases"`
		Queries    []struct {
			Value     string `json:"value"`
			Encoded   string `json:"encoded"`
			Decoded   string `json:"decoded"`
			Alias     bool   `json:"alias"`
			Unchanged bool   `json:"unchanged"`
			Prefix    bool   `json:"prefix"`
		} `json:"queries"`
	} `json:"codecs"`
	Bodies []struct {
		Label            string         `json:"label"`
		Names            []string       `json:"names"`
		Input            map[string]any `json:"input"`
		Output           map[string]any `json:"output"`
		ChoiceReplaced   bool           `json:"choice_replaced"`
		FunctionReplaced bool           `json:"function_replaced"`
	} `json:"bodies"`
}

func TestToolNameGoldenParity(t *testing.T) {
	data, err := os.ReadFile(filepath.Join("..", "testdata", "golden_openaichat4c4b_toolnames.json"))
	if err != nil {
		t.Fatal(err)
	}
	var fixture toolNameFixture
	if err := json.Unmarshal(data, &fixture); err != nil {
		t.Fatal(err)
	}
	if fixture.Schema != "phase4c4b-toolnames-v1" || len(fixture.Codecs) == 0 || len(fixture.Bodies) == 0 {
		t.Fatal("invalid or empty tool-name fixture")
	}
	// Rebuild repeatedly to catch unintended dependence on map iteration.
	for iteration := 0; iteration < 20; iteration++ {
		for _, tc := range fixture.Codecs {
			codec := NewToolNameCodec(tc.Names)
			if codec.HasAliases() != tc.HasAliases {
				t.Fatalf("%s: HasAliases mismatch", tc.Label)
			}
			for _, q := range tc.Queries {
				if codec.Encode(q.Value) != q.Encoded || codec.Decode(q.Value) != q.Decoded ||
					codec.IsAlias(q.Value) != q.Alias || codec.IsUnchangedName(q.Value) != q.Unchanged ||
					codec.IsAliasPrefix(q.Value) != q.Prefix {
					t.Fatalf("%s: query %q: got encode=%q decode=%q alias=%v unchanged=%v prefix=%v; want %+v",
						tc.Label, q.Value, codec.Encode(q.Value), codec.Decode(q.Value), codec.IsAlias(q.Value), codec.IsUnchangedName(q.Value), codec.IsAliasPrefix(q.Value), q)
				}
			}
		}
	}
	for _, tc := range fixture.Bodies {
		t.Run(tc.Label, func(t *testing.T) {
			// Re-decode so mutating the input cannot affect other cases.
			data, err := json.Marshal(tc.Input)
			if err != nil {
				t.Fatal(err)
			}
			var body map[string]any
			if err := json.Unmarshal(data, &body); err != nil {
				t.Fatal(err)
			}
			oldChoice, _ := body["tool_choice"].(map[string]any)
			oldFunction, _ := oldChoice["function"].(map[string]any)
			EncodeChatToolNames(body, NewToolNameCodec(tc.Names))
			if !reflect.DeepEqual(body, tc.Output) {
				t.Fatalf("got %#v, want %#v", body, tc.Output)
			}
			// Probe map identity without depending on unsafe pointers.
			newChoice, _ := body["tool_choice"].(map[string]any)
			choiceReplaced := false
			if oldChoice != nil {
				oldChoice["__identity_probe__"] = true
				_, shared := newChoice["__identity_probe__"]
				choiceReplaced = !shared
				delete(oldChoice, "__identity_probe__")
			}
			functionReplaced := false
			if oldFunction != nil {
				newFunction, _ := newChoice["function"].(map[string]any)
				oldFunction["__identity_probe__"] = true
				_, shared := newFunction["__identity_probe__"]
				functionReplaced = !shared
				delete(oldFunction, "__identity_probe__")
			}
			if choiceReplaced != tc.ChoiceReplaced || functionReplaced != tc.FunctionReplaced {
				t.Fatalf("replacement: choice=%v function=%v, want %v %v", choiceReplaced, functionReplaced, tc.ChoiceReplaced, tc.FunctionReplaced)
			}
		})
	}
}
