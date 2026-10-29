package openaichat

import (
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"testing"
)

func TestStreamedToolNameGoldenParity(t *testing.T) {
	data, err := os.ReadFile(filepath.Join("..", "testdata", "golden_openaichat4c4b_streamed_names.json"))
	if err != nil {
		t.Fatal(err)
	}
	var fixture struct {
		Schema    string                                        `json:"schema"`
		Merge     []struct{ Existing, Incoming, Result string } `json:"merge"`
		Sequences []struct {
			Label      string   `json:"label"`
			Names      []string `json:"names"`
			UseBuffers bool     `json:"use_buffers"`
			Steps      []struct {
				Index    int            `json:"index"`
				Fragment string         `json:"fragment"`
				Result   *string        `json:"result"`
				Buffers  map[int]string `json:"buffers"`
			} `json:"steps"`
		} `json:"sequences"`
	}
	if err := json.Unmarshal(data, &fixture); err != nil {
		t.Fatal(err)
	}
	if fixture.Schema != "phase4c4b-streamed-names-v1" || len(fixture.Merge) == 0 || len(fixture.Sequences) == 0 {
		t.Fatal("invalid fixture")
	}
	for _, tc := range fixture.Merge {
		if got := MergeToolName(tc.Existing, tc.Incoming); got != tc.Result {
			t.Fatalf("merge %q, %q = %q, want %q", tc.Existing, tc.Incoming, got, tc.Result)
		}
	}
	for _, tc := range fixture.Sequences {
		t.Run(tc.Label, func(t *testing.T) {
			var codec *ToolNameCodec
			if tc.Names != nil {
				codec = NewToolNameCodec(tc.Names)
			}
			var buffers map[int]string
			if tc.UseBuffers {
				buffers = make(map[int]string)
			}
			for _, step := range tc.Steps {
				value, ready := DecodeStreamedToolName(step.Fragment, step.Index, codec, buffers)
				if ready != (step.Result != nil) || ready && value != *step.Result {
					t.Fatalf("fragment %q: got %q ready=%v, want %v", step.Fragment, value, ready, step.Result)
				}
				if len(buffers) != 0 || len(step.Buffers) != 0 {
					if !reflect.DeepEqual(buffers, step.Buffers) {
						t.Fatalf("buffers got %#v, want %#v", buffers, step.Buffers)
					}
				}
			}
		})
	}
}
