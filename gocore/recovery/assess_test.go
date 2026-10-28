package recovery

import (
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"testing"
)

type goldenPrefix struct {
	Text         string `json:"text"`
	Thinking     string `json:"thinking"`
	HasCalls     bool   `json:"has_calls"`
	Eligible     bool   `json:"eligible"`
	CanHandoff   bool   `json:"can_handoff"`
	UnsafeReason string `json:"unsafe_reason"`
}

type goldenSource struct {
	NormalStopSeen   bool   `json:"normal_stop_seen"`
	StructuredOutput bool   `json:"structured_output"`
	UnsafeReason     string `json:"unsafe_reason"`
	CanHandoff       bool   `json:"can_handoff"`
}

type recoveryCase struct {
	Name            string        `json:"name"`
	ContentReleased bool          `json:"content_released"`
	Prefix          *goldenPrefix `json:"prefix"`
	Source          goldenSource  `json:"source"`
	Retryable       bool          `json:"retryable"`
	Expect          struct {
		Action string `json:"action"`
		Reason string `json:"reason"`
		Seed   *struct {
			Text     string `json:"text"`
			Thinking string `json:"thinking"`
		} `json:"seed"`
	} `json:"expect"`
}

func loadPhase2(t *testing.T) map[string]json.RawMessage {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join("..", "testdata", "golden_phase2.json"))
	if err != nil {
		t.Fatalf("read fixture: %v", err)
	}
	var golden map[string]json.RawMessage
	if err := json.Unmarshal(raw, &golden); err != nil {
		t.Fatalf("decode fixture: %v", err)
	}
	return golden
}

func TestAssessParity(t *testing.T) {
	var cases []recoveryCase
	if err := json.Unmarshal(loadPhase2(t)["recovery"], &cases); err != nil {
		t.Fatal(err)
	}
	for _, tc := range cases {
		t.Run(tc.Name, func(t *testing.T) {
			var prefix *Prefix
			if tc.Prefix != nil {
				prefix = &Prefix{
					Text:         tc.Prefix.Text,
					Thinking:     tc.Prefix.Thinking,
					HasCalls:     tc.Prefix.HasCalls,
					Eligible:     tc.Prefix.Eligible,
					CanHandoff:   tc.Prefix.CanHandoff,
					UnsafeReason: tc.Prefix.UnsafeReason,
				}
			}
			source := SourceSnapshot{
				NormalStopSeen:   tc.Source.NormalStopSeen,
				StructuredOutput: tc.Source.StructuredOutput,
				UnsafeReason:     tc.Source.UnsafeReason,
				CanHandoff:       tc.Source.CanHandoff,
			}
			got := Assess(tc.ContentReleased, prefix, source, tc.Retryable)
			if string(got.Action) != tc.Expect.Action || got.Reason != tc.Expect.Reason {
				t.Fatalf("got (%s, %s), want (%s, %s)",
					got.Action, got.Reason, tc.Expect.Action, tc.Expect.Reason)
			}
			if tc.Expect.Seed == nil {
				if got.Seed != nil {
					t.Fatalf("seed should be nil, got %+v", got.Seed)
				}
				return
			}
			if got.Seed == nil {
				t.Fatal("expected continuation seed, got nil")
			}
			wantSeed := ContinuationSeed{Text: tc.Expect.Seed.Text, Thinking: tc.Expect.Seed.Thinking}
			if !reflect.DeepEqual(*got.Seed, wantSeed) {
				t.Fatalf("seed got %+v want %+v", *got.Seed, wantSeed)
			}
		})
	}
}
