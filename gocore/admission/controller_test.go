package admission

import (
	"encoding/json"
	"os"
	"path/filepath"
	"sort"
	"testing"
)

type recoveryFixture struct {
	JitterDraws []float64 `json:"jitter_draws"`
	RetryConfig struct {
		BaseDelay float64 `json:"base_delay"`
		MaxDelay  float64 `json:"max_delay"`
		Jitter    float64 `json:"jitter"`
	} `json:"retry_config"`
	ExhaustedMessage string `json:"exhausted_message"`
	ClosedMessage    string `json:"closed_message"`
	Scenarios        map[string]struct {
		MaxAttempts int `json:"max_attempts"`
		Executions  []struct {
			Label     string `json:"label"`
			Model     string `json:"model"`
			Claims    int    `json:"claims"`
			OpenClaim bool   `json:"open_claim"`
		} `json:"executions"`
		Steps []recoveryStep `json:"steps"`
	} `json:"scenarios"`
}

type recoveryStep struct {
	Label          string                 `json:"label"`
	Op             string                 `json:"op"`
	Now            float64                `json:"now"`
	Target         string                 `json:"target"`
	Raised         *string                `json:"raised"`
	Permit         *permitView            `json:"permit"`
	ErrorInput     *attemptErrorView      `json:"error_input"`
	Status         *int                   `json:"status"`
	Episodes       map[string]episodeView `json:"episodes"`
	NextGeneration int                    `json:"next_generation"`
	Closed         bool                   `json:"closed"`
	States         map[string]string      `json:"execution_states"`
}

type permitView struct {
	Generation *int `json:"generation"`
	Probe      bool `json:"probe"`
}

type episodeView struct {
	Generation    int               `json:"generation"`
	Leader        *string           `json:"leader"`
	ReadyAt       float64           `json:"ready_at"`
	ProbeActive   bool              `json:"probe_active"`
	TerminalUntil *float64          `json:"terminal_until"`
	Waiters       []string          `json:"waiters"`
	LastError     *attemptErrorView `json:"last_error"`
}

func loadRecoveryFixture(t *testing.T) recoveryFixture {
	t.Helper()
	raw, err := os.ReadFile(
		filepath.Join("..", "testdata", "golden_recovery4c3b.json"))
	if err != nil {
		t.Fatal(err)
	}
	var fixture recoveryFixture
	if err := json.Unmarshal(raw, &fixture); err != nil {
		t.Fatal(err)
	}
	return fixture
}

type virtualClock struct{ now float64 }

func TestRecoveryGoldenParity(t *testing.T) {
	fixture := loadRecoveryFixture(t)
	names := make([]string, 0, len(fixture.Scenarios))
	for name := range fixture.Scenarios {
		names = append(names, name)
	}
	sort.Strings(names)

	for _, name := range names {
		t.Run(name, func(t *testing.T) {
			scenario := fixture.Scenarios[name]
			clock := &virtualClock{}
			limits, err := NewLimits(40, 60, 5)
			if err != nil {
				t.Fatal(err)
			}
			retry := RetryConfig{
				MaxAttempts: scenario.MaxAttempts,
				BaseDelay:   fixture.RetryConfig.BaseDelay,
				MaxDelay:    fixture.RetryConfig.MaxDelay,
				Jitter:      fixture.RetryConfig.Jitter,
			}
			controller := NewController("golden", limits, retry).
				WithClock(func() float64 { return clock.now }).
				WithScriptedJitter(append([]float64(nil), fixture.JitterDraws...))

			executions := map[string]*Execution{}
			for _, spec := range scenario.Executions {
				execution, err := controller.StartExecution("", spec.Model)
				if err != nil {
					t.Fatal(err)
				}
				executions[spec.Label] = execution
				for i := range spec.Claims {
					ordinal, err := execution.ClaimAttempt(OpGeneration)
					if err != nil {
						t.Fatalf("claim %d: %v", i, err)
					}
					if !spec.OpenClaim || i < spec.Claims-1 {
						execution.CloseAttempt(ordinal)
					}
				}
			}

			for _, step := range scenario.Steps {
				clock.now = step.Now
				replayRecoveryStep(t, controller, executions, step)
				assertRecoverySnapshot(t, controller, executions, step)
			}
		})
	}
}

func replayRecoveryStep(t *testing.T, controller *Controller,
	executions map[string]*Execution, step recoveryStep) {
	t.Helper()
	permit := GatePermit{}
	if step.Permit != nil {
		permit = GatePermit{
			Generation: step.Permit.Generation,
			Probe:      step.Permit.Probe,
		}
	}
	switch step.Op {
	case "arm_probe":
		armProbe(controller, executions[step.Target])
	case "add_waiter":
		controller.AddWaiter(executions[step.Target])
	case "attempt_failed":
		if step.ErrorInput == nil {
			t.Fatalf("step %q: missing error input", step.Label)
		}
		controller.AttemptFailed(
			executions[step.Target], permit,
			&AttemptError{
				Kind:    step.ErrorInput.Kind,
				Message: step.ErrorInput.Message,
			}, step.Status)
	case "attempt_accepted":
		controller.AttemptAccepted(executions[step.Target], permit)
	case "attempt_rejected":
		controller.AttemptRejected(executions[step.Target], permit)
	case "attempt_abandoned":
		controller.AttemptAbandoned(executions[step.Target], permit)
	case "attempt_corrected":
		controller.AttemptCorrected(executions[step.Target], permit)
	case "wait_for_gate":
		got, err := controller.WaitForGate(executions[step.Target])
		if step.Raised != nil {
			if err == nil || err.Error() != *step.Raised {
				t.Fatalf("step %q: got error %v want %q",
					step.Label, err, *step.Raised)
			}
			return
		}
		if err != nil {
			t.Fatalf("step %q: unexpected error %v", step.Label, err)
		}
		if got.Probe != permit.Probe {
			t.Fatalf("step %q: probe got %v want %v",
				step.Label, got.Probe, permit.Probe)
		}
		if (got.Generation == nil) != (permit.Generation == nil) {
			t.Fatalf("step %q: generation presence mismatch", step.Label)
		}
		if got.Generation != nil && *got.Generation != *permit.Generation {
			t.Fatalf("step %q: generation got %d want %d",
				step.Label, *got.Generation, *permit.Generation)
		}
	case "maintain_idle":
		controller.MaintainIdleEpisodes()
	case "succeed":
		executions[step.Target].Succeed()
	case "close":
		controller.Close()
	default:
		t.Fatalf("unknown op %q", step.Op)
	}
}

// armProbe reproduces the generator's direct probe_active assignment that
// real code performs inside the blocking gate visit.
func armProbe(controller *Controller, execution *Execution) {
	controller.mu.Lock()
	defer controller.mu.Unlock()
	if episode := controller.episodes[execution.Model()]; episode != nil {
		episode.ProbeActive = true
	}
}

func assertRecoverySnapshot(t *testing.T, controller *Controller,
	executions map[string]*Execution, step recoveryStep) {
	t.Helper()
	if controller.IsClosed() != step.Closed {
		t.Fatalf("step %q: closed got %v want %v",
			step.Label, controller.IsClosed(), step.Closed)
	}
	if controller.NextGeneration() != step.NextGeneration {
		t.Fatalf("step %q: next generation got %d want %d",
			step.Label, controller.NextGeneration(), step.NextGeneration)
	}
	// The fixture only knows executions created before the snapshot; later
	// executions legitimately absent from step.States are not compared.
	for label, want := range step.States {
		execution, ok := executions[label]
		if !ok {
			t.Fatalf("step %q: unknown execution %q", step.Label, label)
		}
		if string(execution.State()) != want {
			t.Fatalf("step %q: execution %s state got %q want %q",
				step.Label, label, execution.State(), want)
		}
	}

	got := controller.Episodes()
	if len(got) != len(step.Episodes) {
		t.Fatalf("step %q: episode count got %d want %d (%v)",
			step.Label, len(got), len(step.Episodes), episodeKeys(got))
	}
	for model, want := range step.Episodes {
		episode, ok := got[model]
		if !ok {
			t.Fatalf("step %q: missing episode %q", step.Label, model)
		}
		assertEpisode(t, step.Label, model, episode, want, executions)
	}
}

func assertEpisode(t *testing.T, label, model string,
	got *RecoveryEpisode, want episodeView,
	executions map[string]*Execution) {
	t.Helper()
	if got.Generation != want.Generation {
		t.Fatalf("step %q ep %q: generation got %d want %d",
			label, model, got.Generation, want.Generation)
	}
	if !floatEqual(got.ReadyAt, want.ReadyAt) {
		t.Fatalf("step %q ep %q: ready got %v want %v",
			label, model, got.ReadyAt, want.ReadyAt)
	}
	if got.ProbeActive != want.ProbeActive {
		t.Fatalf("step %q ep %q: probe got %v want %v",
			label, model, got.ProbeActive, want.ProbeActive)
	}
	if (got.TerminalUntil == nil) != (want.TerminalUntil == nil) {
		t.Fatalf("step %q ep %q: terminal presence mismatch", label, model)
	}
	if got.TerminalUntil != nil &&
		!floatEqual(*got.TerminalUntil, *want.TerminalUntil) {
		t.Fatalf("step %q ep %q: terminal got %v want %v",
			label, model, *got.TerminalUntil, *want.TerminalUntil)
	}
	if (got.Leader == nil) != (want.Leader == nil) {
		t.Fatalf("step %q ep %q: leader presence mismatch", label, model)
	}
	if want.Leader != nil {
		leaderLabel := labelOf(executions, got.Leader)
		if leaderLabel != *want.Leader {
			t.Fatalf("step %q ep %q: leader got %q want %q",
				label, model, leaderLabel, *want.Leader)
		}
	}
	gotWaiters := make([]string, 0, len(got.Waiters))
	for waiter := range got.Waiters {
		gotWaiters = append(gotWaiters, labelOf(executions, waiter))
	}
	sort.Strings(gotWaiters)
	if !stringSlicesEqual(gotWaiters, want.Waiters) {
		t.Fatalf("step %q ep %q: waiters got %v want %v",
			label, model, gotWaiters, want.Waiters)
	}
	assertAttemptError(t, label, "episode last_error",
		got.LastError, want.LastError)
}

func labelOf(executions map[string]*Execution, target *Execution) string {
	for label, execution := range executions {
		if execution == target {
			return label
		}
	}
	return ""
}

func episodeKeys(episodes map[string]*RecoveryEpisode) []string {
	keys := make([]string, 0, len(episodes))
	for key := range episodes {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	return keys
}
