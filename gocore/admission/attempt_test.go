package admission

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"sort"
	"testing"

	"github.com/NggKhaiz/FCC/gocore/providerpolicy"
)

type attemptFixture struct {
	ExhaustedMessage  string   `json:"exhausted_message"`
	CorrectionActions []string `json:"correction_actions"`
	Scenarios         map[string]struct {
		Executions []struct {
			Label       string `json:"label"`
			Model       string `json:"model"`
			RequestID   string `json:"request_id"`
			MaxAttempts int    `json:"max_attempts"`
			Controller  int    `json:"controller"`
		} `json:"executions"`
		Steps []attemptStep `json:"steps"`
	} `json:"scenarios"`
}

type attemptStep struct {
	Label          string                 `json:"label"`
	Op             string                 `json:"op"`
	Now            float64                `json:"now"`
	Controller     int                    `json:"controller"`
	Execution      string                 `json:"execution"`
	Raised         *string                `json:"raised"`
	RaisedType     *string                `json:"raised_type"`
	Decision       map[string]any         `json:"decision"`
	Correction     *string                `json:"correction"`
	Result         any                    `json:"result"`
	Episodes       map[string]episodeView `json:"episodes"`
	NextGeneration int                    `json:"next_generation"`
	Closed         bool                   `json:"closed"`
	Capacity       struct {
		ActiveAttempts int `json:"active_attempts"`
		AdmissionCount int `json:"admission_count"`
	} `json:"capacity"`
	ExecutionState *struct {
		Label             string            `json:"label"`
		State             string            `json:"state"`
		AttemptsStarted   int               `json:"attempts_started"`
		CanAttempt        bool              `json:"can_attempt"`
		AttemptsRemaining int               `json:"attempts_remaining"`
		ActiveClaim       *int              `json:"active_claim"`
		LastFailure       *attemptErrorView `json:"last_failure"`
	} `json:"execution_state"`
}

func loadAttemptFixture(t *testing.T) attemptFixture {
	t.Helper()
	raw, err := os.ReadFile(
		filepath.Join("..", "testdata", "golden_attempt4c3c.json"))
	if err != nil {
		t.Fatal(err)
	}
	var fixture attemptFixture
	if err := json.Unmarshal(raw, &fixture); err != nil {
		t.Fatal(err)
	}
	return fixture
}

func TestCorrectionActionConstants(t *testing.T) {
	fixture := loadAttemptFixture(t)
	want := []string{string(CorrectionRetry), string(CorrectionFinal)}
	if !stringSlicesEqual(fixture.CorrectionActions, want) {
		t.Fatalf("correction actions got %v want %v",
			fixture.CorrectionActions, want)
	}
}

func TestAttemptLifecycleGoldenParity(t *testing.T) {
	fixture := loadAttemptFixture(t)
	names := make([]string, 0, len(fixture.Scenarios))
	for name := range fixture.Scenarios {
		names = append(names, name)
	}
	sort.Strings(names)

	for _, name := range names {
		t.Run(name, func(t *testing.T) {
			scenario := fixture.Scenarios[name]
			controllers := map[int]*Controller{}
			executions := map[string]*Execution{}
			openAttempts := map[string]*Attempt{}
			ctx := context.Background()
			clock := &virtualClock{}

			for _, spec := range scenario.Executions {
				controller, ok := controllers[spec.Controller]
				if !ok {
					limits, err := NewLimits(40, 60, 5)
					if err != nil {
						t.Fatal(err)
					}
					retry := RetryConfig{
						MaxAttempts: spec.MaxAttempts,
						BaseDelay:   0,
						MaxDelay:    0,
						Jitter:      0,
					}
					controller = NewController("golden", limits, retry).
						WithClock(func() float64 { return clock.now })
					controllers[spec.Controller] = controller
				}
				execution, err := controller.StartExecution(
					spec.RequestID, spec.Model)
				if err != nil {
					t.Fatal(err)
				}
				executions[spec.Label] = execution
			}

			for _, step := range scenario.Steps {
				clock.now = step.Now
				controller := controllers[step.Controller]
				replayAttemptStep(t, ctx, controller, executions,
					openAttempts, step)
				assertAttemptSnapshot(t, controller, executions, step)
			}
		})
	}
}

func replayAttemptStep(t *testing.T, ctx context.Context,
	controller *Controller, executions map[string]*Execution,
	openAttempts map[string]*Attempt, step attemptStep) {
	t.Helper()
	switch step.Op {
	case "open_attempt":
		execution := executions[step.Execution]
		attempt, err := controller.OpenAttempt(ctx, execution, OpGeneration)
		if step.Raised != nil {
			if err == nil || err.Error() != *step.Raised {
				t.Fatalf("step %q: got %v want %q", step.Label, err, *step.Raised)
			}
			if step.RaisedType != nil {
				_, isExhausted := err.(*RecoveryExhaustedError)
				wantExhausted := *step.RaisedType == "ProviderRecoveryExhausted"
				if isExhausted != wantExhausted {
					t.Fatalf("step %q: error type mismatch exhausted=%v want %q",
						step.Label, isExhausted, *step.RaisedType)
				}
			}
			return
		}
		if err != nil {
			t.Fatalf("step %q: unexpected error %v", step.Label, err)
		}
		openAttempts[step.Execution] = attempt
		if permitView, ok := step.Decision["permit"].(map[string]any); ok {
			assertPermit(t, step.Label, attempt.permit, permitView)
		}
	case "attempt_fail":
		attempt := openAttempts[step.Execution]
		descriptor := step.Decision["error_descriptor"].(map[string]any)
		perr := providerpolicy.FromDescriptor(descriptor)
		decision := attempt.Fail(perr, nil)
		wantRetryable := step.Decision["retryable"].(bool)
		wantAllowed := step.Decision["retry_allowed"].(bool)
		if decision.Retryable != wantRetryable ||
			decision.RetryAllowed != wantAllowed {
			t.Fatalf("step %q: decision got %+v want (%v,%v)",
				step.Label, decision, wantRetryable, wantAllowed)
		}
	case "attempt_accept":
		openAttempts[step.Execution].Accept()
	case "attempt_correct":
		attempt := openAttempts[step.Execution]
		descriptor := step.Decision["error_descriptor"].(map[string]any)
		action := attempt.Correct(providerpolicy.FromDescriptor(descriptor))
		if string(action) != *step.Correction {
			t.Fatalf("step %q: correction got %q want %q",
				step.Label, action, *step.Correction)
		}
	case "controller_attempt_failed":
		// Bypasses Attempt.Fail exactly like the Python harness, exercising
		// the unresolved-close abandonment path.
		attempt := openAttempts[step.Execution]
		descriptor := step.Decision["error_descriptor"].(map[string]any)
		perr := providerpolicy.FromDescriptor(descriptor)
		status := perr.StatusCode
		controller.AttemptFailed(executions[step.Execution], attempt.permit,
			toAttemptError(perr), &status)
	case "attempt_close":
		// Kept in the map on purpose: double-close is an asserted no-op.
		openAttempts[step.Execution].Close()
	case "controller_close":
		controller.Close()
	case "run_call":
		replayRunCall(t, ctx, controller, executions, step)
	default:
		t.Fatalf("unknown op %q", step.Op)
	}
}

func assertPermit(t *testing.T, label string, got GatePermit,
	view map[string]any) {
	t.Helper()
	wantProbe, _ := view["probe"].(bool)
	if got.Probe != wantProbe {
		t.Fatalf("step %q: probe got %v want %v", label, got.Probe, wantProbe)
	}
	wantGen, hasGen := view["generation"]
	if hasGen && wantGen != nil {
		if got.Generation == nil ||
			float64(*got.Generation) != wantGen.(float64) {
			t.Fatalf("step %q: generation got %v want %v",
				label, got.Generation, wantGen)
		}
	}
}

func replayRunCall(t *testing.T, ctx context.Context,
	controller *Controller, executions map[string]*Execution,
	step attemptStep) {
	t.Helper()
	execution := executions[step.Execution]
	script := step.Decision["script"].([]any)
	index := 0
	fn := func() (string, *providerpolicy.ProviderError) {
		entry := script[index].(map[string]any)
		index++
		if raw, ok := entry["descriptor"]; ok {
			return "", providerpolicy.FromDescriptor(raw.(map[string]any))
		}
		return entry["result"].(string), nil
	}
	result, err := RunCall(ctx, controller, execution, OpGeneration, fn)
	if calls := int(step.Decision["calls"].(float64)); index != calls {
		t.Fatalf("step %q: calls got %d want %d", step.Label, index, calls)
	}
	if step.Raised != nil {
		if err == nil || err.Error() != *step.Raised {
			t.Fatalf("step %q: got err %v want %q",
				step.Label, err, *step.Raised)
		}
		if step.RaisedType != nil {
			attemptErr, ok := err.(*AttemptError)
			if !ok || attemptErr.Kind != *step.RaisedType {
				t.Fatalf("step %q: raised kind got %v want %q",
					step.Label, err, *step.RaisedType)
			}
		}
		return
	}
	if err != nil {
		t.Fatalf("step %q: unexpected error %v", step.Label, err)
	}
	if result != step.Result.(string) {
		t.Fatalf("step %q: result got %q want %q",
			step.Label, result, step.Result)
	}
}

func assertAttemptSnapshot(t *testing.T, controller *Controller,
	executions map[string]*Execution, step attemptStep) {
	t.Helper()
	if controller.IsClosed() != step.Closed {
		t.Fatalf("step %q: closed got %v want %v",
			step.Label, controller.IsClosed(), step.Closed)
	}
	if controller.NextGeneration() != step.NextGeneration {
		t.Fatalf("step %q: next generation got %d want %d",
			step.Label, controller.NextGeneration(), step.NextGeneration)
	}
	state := controller.Capacity().State()
	if state.ActiveAttempts != step.Capacity.ActiveAttempts {
		t.Fatalf("step %q: active slots got %d want %d",
			step.Label, state.ActiveAttempts, step.Capacity.ActiveAttempts)
	}
	if len(state.AdmissionTimes) != step.Capacity.AdmissionCount {
		t.Fatalf("step %q: admission count got %d want %d",
			step.Label, len(state.AdmissionTimes),
			step.Capacity.AdmissionCount)
	}
	if step.ExecutionState != nil {
		execution := executions[step.ExecutionState.Label]
		want := step.ExecutionState
		if string(execution.State()) != want.State ||
			execution.AttemptsStarted() != want.AttemptsStarted ||
			execution.CanAttempt() != want.CanAttempt ||
			execution.AttemptsRemaining() != want.AttemptsRemaining {
			t.Fatalf("step %q: execution state got (%s,%d,%v,%d) want (%s,%d,%v,%d)",
				step.Label, execution.State(), execution.AttemptsStarted(),
				execution.CanAttempt(), execution.AttemptsRemaining(),
				want.State, want.AttemptsStarted, want.CanAttempt,
				want.AttemptsRemaining)
		}
		if (execution.ActiveClaimOrdinal() == 0) != (want.ActiveClaim == nil) {
			t.Fatalf("step %q: active claim presence mismatch", step.Label)
		}
		if want.ActiveClaim != nil &&
			execution.ActiveClaimOrdinal() != *want.ActiveClaim {
			t.Fatalf("step %q: active claim got %d want %d",
				step.Label, execution.ActiveClaimOrdinal(), *want.ActiveClaim)
		}
		assertAttemptError(t, step.Label, "last_failure",
			execution.LastFailure(), want.LastFailure)
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
