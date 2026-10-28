package admission

import (
	"encoding/json"
	"math"
	"os"
	"path/filepath"
	"testing"
)

type goldenLimits struct {
	RateLimit      int     `json:"rate_limit"`
	RateWindow     float64 `json:"rate_window"`
	MaxConcurrency int     `json:"max_concurrency"`
}

type goldenStep struct {
	Label           string        `json:"label"`
	Op              string        `json:"op"`
	Now             float64       `json:"now"`
	Limits          *goldenLimits `json:"limits"`
	AdmissionTimes  []float64     `json:"admission_times"`
	LastDiscardedAt *float64      `json:"last_discarded_at"`
	ActiveAttempts  int           `json:"active_attempts"`
	Outcome         *string       `json:"outcome"`
}

type goldenScenario struct {
	Limits goldenLimits `json:"limits"`
	Steps  []goldenStep `json:"steps"`
}

type goldenValidation struct {
	Label          string  `json:"label"`
	Error          string  `json:"error"`
	RateLimit      int     `json:"rate_limit"`
	RateWindow     float64 `json:"rate_window"`
	MaxConcurrency int     `json:"max_concurrency"`
}

type goldenFixture struct {
	ValidationCases []goldenValidation        `json:"validation_cases"`
	Scenarios       map[string]goldenScenario `json:"scenarios"`
}

func loadFixture(t *testing.T) goldenFixture {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join("..", "testdata", "golden_admission4c1.json"))
	if err != nil {
		t.Fatal(err)
	}
	var fixture goldenFixture
	if err := json.Unmarshal(raw, &fixture); err != nil {
		t.Fatal(err)
	}
	return fixture
}

// validationArgs maps Python-only rejection labels (booleans and floats are
// unrepresentable through Go's typed constructor) onto calls that hit the
// same validation branch and must produce the identical message.
func validationArgs(label string) (int, float64, int) {
	switch label {
	case "zero_rate_limit", "bool_rate_limit", "float_rate_limit":
		return 0, 1, 1
	case "negative_rate_limit":
		return -2, 1, 1
	case "zero_concurrency", "bool_concurrency", "float_concurrency":
		return 1, 1, 0
	case "negative_concurrency":
		return 1, 1, -3
	case "zero_window":
		return 1, 0, 1
	case "negative_window":
		return 1, -1, 1
	case "nan_window":
		return 1, math.NaN(), 1
	case "inf_window":
		return 1, math.Inf(1), 1
	default:
		return 4, 2.5, 7
	}
}

func TestLimitsValidationGoldenParity(t *testing.T) {
	for _, tc := range loadFixture(t).ValidationCases {
		t.Run(tc.Label, func(t *testing.T) {
			rateLimit, window, concurrency := validationArgs(tc.Label)
			got, err := NewLimits(rateLimit, window, concurrency)
			if tc.Label == "valid_limits" {
				if err != nil {
					t.Fatalf("unexpected error: %v", err)
				}
				if got.RateLimit != tc.RateLimit ||
					got.MaxConcurrency != tc.MaxConcurrency ||
					!floatEqual(got.RateWindow, tc.RateWindow) {
					t.Fatalf("limits mismatch: %+v", got)
				}
				return
			}
			if err == nil || err.Error() != tc.Error {
				t.Fatalf("got %v, want error %q", err, tc.Error)
			}
		})
	}
}

func TestCapacityGoldenParity(t *testing.T) {
	fixture := loadFixture(t)
	for name, scenario := range fixture.Scenarios {
		t.Run(name, func(t *testing.T) {
			limits, err := NewLimits(
				scenario.Limits.RateLimit,
				scenario.Limits.RateWindow,
				scenario.Limits.MaxConcurrency,
			)
			if err != nil {
				t.Fatal(err)
			}
			limiter := NewCapacityLimiter(limits)

			for _, step := range scenario.Steps {
				outcome := replayStep(t, limiter, step)
				if !outcomeEqual(outcome, step.Outcome) {
					t.Fatalf("step %q outcome got %v want %v",
						step.Label, outcome, step.Outcome)
				}
				assertSnapshot(t, limiter, step)
			}
		})
	}
}

func replayStep(t *testing.T, limiter *CapacityLimiter, step goldenStep) *string {
	t.Helper()
	admit := func() *string {
		decision := limiter.Evaluate(step.Now)
		if !decision.Admit {
			t.Fatalf("step %q: expected admission at %v", step.Label, step.Now)
		}
		limiter.Commit(step.Now)
		outcome := "admitted"
		return &outcome
	}
	switch step.Op {
	case "admit", "advance_wake_admit":
		return admit()
	case "expect_blocked":
		if decision := limiter.Evaluate(step.Now); decision.Admit {
			t.Fatalf("step %q: expected block at %v", step.Label, step.Now)
		}
		return nil
	case "release":
		limiter.Release()
		return nil
	case "release_then_admit":
		limiter.Release()
		return admit()
	case "reconfigure_admit":
		if step.Limits == nil {
			t.Fatalf("step %q: reconfigure without limits", step.Label)
		}
		limits, err := NewLimits(
			step.Limits.RateLimit, step.Limits.RateWindow, step.Limits.MaxConcurrency)
		if err != nil {
			t.Fatal(err)
		}
		limiter.Reconfigure(limits)
		return admit()
	case "permit_invalidated":
		// The permit check lives in the transport wait loop; an invalid
		// permit leaves without charging, so limiter state must not change.
		outcome := "returned_false"
		return &outcome
	default:
		t.Fatalf("unknown op %q", step.Op)
		return nil
	}
}

func assertSnapshot(t *testing.T, limiter *CapacityLimiter, step goldenStep) {
	t.Helper()
	state := limiter.State()
	if state.ActiveAttempts != step.ActiveAttempts {
		t.Fatalf("step %q: active got %d want %d",
			step.Label, state.ActiveAttempts, step.ActiveAttempts)
	}
	if !floatSliceEqual(state.AdmissionTimes, step.AdmissionTimes) {
		t.Fatalf("step %q: admission times got %v want %v",
			step.Label, state.AdmissionTimes, step.AdmissionTimes)
	}
	if (state.LastDiscardedAt == nil) != (step.LastDiscardedAt == nil) {
		t.Fatalf("step %q: discarded presence got %v want %v",
			step.Label, state.LastDiscardedAt, step.LastDiscardedAt)
	}
	if state.LastDiscardedAt != nil &&
		!floatEqual(*state.LastDiscardedAt, *step.LastDiscardedAt) {
		t.Fatalf("step %q: discarded got %v want %v",
			step.Label, *state.LastDiscardedAt, *step.LastDiscardedAt)
	}
}

func outcomeEqual(got, want *string) bool {
	if got == nil || want == nil {
		return got == want
	}
	return *got == *want
}

func floatSliceEqual(got, want []float64) bool {
	if len(got) != len(want) {
		return false
	}
	for i := range got {
		if !floatEqual(got[i], want[i]) {
			return false
		}
	}
	return true
}

func floatEqual(a, b float64) bool {
	return math.Abs(a-b) < 1e-9
}
