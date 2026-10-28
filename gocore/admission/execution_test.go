package admission

import (
	"encoding/json"
	"os"
	"path/filepath"
	"regexp"
	"testing"
)

type executionStep struct {
	Label             string            `json:"label"`
	Op                string            `json:"op"`
	Error             *string           `json:"error"`
	ReturnedOrdinal   *int              `json:"returned_ordinal"`
	ErrorInput        *attemptErrorView `json:"error_input"`
	StaleOrdinal      *int              `json:"stale_ordinal"`
	OperationKind     string            `json:"operation_kind"`
	State             string            `json:"state"`
	AttemptsStarted   int               `json:"attempts_started"`
	CanAttempt        bool              `json:"can_attempt"`
	AttemptsRemaining int               `json:"attempts_remaining"`
	ActiveClaim       *int              `json:"active_claim"`
	LastFailure       *attemptErrorView `json:"last_failure"`
	TerminalFailure   *attemptErrorView `json:"terminal_failure"`
	TerminationCalls  []string          `json:"termination_calls"`
}

type executionScenario struct {
	MaxAttempts int             `json:"max_attempts"`
	Steps       []executionStep `json:"steps"`
}

type attemptErrorView struct {
	Kind    string `json:"kind"`
	Message string `json:"message"`
}

type executionFixture struct {
	Enums struct {
		OperationKinds  []string `json:"operation_kinds"`
		ExecutionStates []string `json:"execution_states"`
	} `json:"enums"`
	Identity struct {
		UniqueIDs         bool   `json:"unique_ids"`
		AllUUID4          bool   `json:"all_uuid4"`
		PythonUUIDVersion int    `json:"python_uuid_version"`
		SampleID          string `json:"sample_id"`
	} `json:"identity_contract"`
	Scenarios map[string]executionScenario `json:"scenarios"`
}

var uuid4Pattern = regexp.MustCompile(
	`^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$`)

func loadExecutionFixture(t *testing.T) executionFixture {
	t.Helper()
	raw, err := os.ReadFile(
		filepath.Join("..", "testdata", "golden_execution4c3a.json"))
	if err != nil {
		t.Fatal(err)
	}
	var fixture executionFixture
	if err := json.Unmarshal(raw, &fixture); err != nil {
		t.Fatal(err)
	}
	return fixture
}

func TestExecutionEnumsAndIdentityContract(t *testing.T) {
	fixture := loadExecutionFixture(t)
	wantKinds := []string{
		string(OpModelDiscovery), string(OpGeneration),
		string(OpContinuation), string(OpToolRepair)}
	if !stringSlicesEqual(fixture.Enums.OperationKinds, wantKinds) {
		t.Fatalf("operation kinds got %v want %v",
			fixture.Enums.OperationKinds, wantKinds)
	}
	wantStates := []string{
		string(StateActive), string(StateSucceeded),
		string(StateFailed), string(StateAbandoned)}
	if !stringSlicesEqual(fixture.Enums.ExecutionStates, wantStates) {
		t.Fatalf("states got %v want %v", fixture.Enums.ExecutionStates, wantStates)
	}

	// Mirror the Python identity contract: unique canonical UUID4 strings.
	seen := map[string]bool{}
	for range 8 {
		execution, err := NewExecution(1, "", "", nil)
		if err != nil {
			t.Fatal(err)
		}
		id := execution.ID()
		if !uuid4Pattern.MatchString(id) {
			t.Fatalf("id %q is not canonical uuid4", id)
		}
		if seen[id] {
			t.Fatalf("duplicate execution id %q", id)
		}
		seen[id] = true
	}
	if !fixture.Identity.UniqueIDs || !fixture.Identity.AllUUID4 ||
		fixture.Identity.PythonUUIDVersion != 4 ||
		!uuid4Pattern.MatchString(fixture.Identity.SampleID) {
		t.Fatalf("python identity contract mismatch: %+v", fixture.Identity)
	}
}

func TestExecutionGoldenParity(t *testing.T) {
	fixture := loadExecutionFixture(t)
	// Deterministic scenario order for stable failure output.
	names := make([]string, 0, len(fixture.Scenarios))
	for name := range fixture.Scenarios {
		names = append(names, name)
	}
	for _, name := range names {
		t.Run(name, func(t *testing.T) {
			scenario := fixture.Scenarios[name]
			var terminationCalls []string
			hook := func(execution *Execution) {
				terminationCalls = append(terminationCalls,
					string(execution.State()))
			}
			execution, err := NewExecution(scenario.MaxAttempts, "", "", hook)
			if err != nil {
				t.Fatal(err)
			}
			for _, step := range scenario.Steps {
				replayExecutionStep(t, execution, step)
				assertExecutionSnapshot(t, execution, step, terminationCalls)
			}
		})
	}
}

func replayExecutionStep(t *testing.T, e *Execution, step executionStep) {
	t.Helper()
	switch step.Op {
	case "claim":
		ordinal, err := e.ClaimAttempt(OperationKind(step.OperationKind))
		if step.Error != nil {
			if err == nil || err.Error() != *step.Error {
				t.Fatalf("step %q: got error %v want %q",
					step.Label, err, *step.Error)
			}
			return
		}
		if err != nil {
			t.Fatalf("step %q: unexpected error %v", step.Label, err)
		}
		if step.ReturnedOrdinal == nil || ordinal != *step.ReturnedOrdinal {
			t.Fatalf("step %q: ordinal got %d want %v",
				step.Label, ordinal, step.ReturnedOrdinal)
		}
	case "close":
		e.CloseAttempt(e.ActiveClaimOrdinal())
	case "close_stale":
		if step.StaleOrdinal == nil {
			t.Fatalf("step %q: stale close without ordinal", step.Label)
		}
		e.CloseAttempt(*step.StaleOrdinal)
	case "succeed":
		e.Succeed()
	case "fail":
		if step.ErrorInput == nil {
			t.Fatalf("step %q: fail without error input", step.Label)
		}
		e.Fail(&AttemptError{
			Kind: step.ErrorInput.Kind, Message: step.ErrorInput.Message})
	case "abandon":
		e.Abandon()
	case "record_failure":
		if step.ErrorInput == nil {
			t.Fatalf("step %q: record without error input", step.Label)
		}
		e.RecordFailure(&AttemptError{
			Kind: step.ErrorInput.Kind, Message: step.ErrorInput.Message})
	default:
		t.Fatalf("unknown op %q", step.Op)
	}
}

func assertExecutionSnapshot(t *testing.T, e *Execution, step executionStep,
	terminationCalls []string) {
	t.Helper()
	if e.State() != ExecutionState(step.State) {
		t.Fatalf("step %q: state got %q want %q",
			step.Label, e.State(), step.State)
	}
	if e.AttemptsStarted() != step.AttemptsStarted {
		t.Fatalf("step %q: started got %d want %d",
			step.Label, e.AttemptsStarted(), step.AttemptsStarted)
	}
	if e.CanAttempt() != step.CanAttempt {
		t.Fatalf("step %q: can_attempt got %v want %v",
			step.Label, e.CanAttempt(), step.CanAttempt)
	}
	if e.AttemptsRemaining() != step.AttemptsRemaining {
		t.Fatalf("step %q: remaining got %d want %d",
			step.Label, e.AttemptsRemaining(), step.AttemptsRemaining)
	}
	if (e.ActiveClaimOrdinal() == 0) != (step.ActiveClaim == nil) {
		t.Fatalf("step %q: active claim presence mismatch", step.Label)
	}
	if step.ActiveClaim != nil &&
		e.ActiveClaimOrdinal() != *step.ActiveClaim {
		t.Fatalf("step %q: active claim got %d want %d",
			step.Label, e.ActiveClaimOrdinal(), *step.ActiveClaim)
	}
	assertAttemptError(t, step.Label, "last_failure",
		e.LastFailure(), step.LastFailure)
	assertAttemptError(t, step.Label, "terminal_failure",
		e.TerminalFailure(), step.TerminalFailure)
	if !stringSlicesEqual(terminationCalls, step.TerminationCalls) {
		t.Fatalf("step %q: termination calls got %v want %v",
			step.Label, terminationCalls, step.TerminationCalls)
	}
}

func assertAttemptError(t *testing.T, label, field string,
	got *AttemptError, want *attemptErrorView) {
	t.Helper()
	if want == nil {
		if got != nil {
			t.Fatalf("step %q: %s got %+v want nil", label, field, got)
		}
		return
	}
	if got == nil || got.Kind != want.Kind || got.Message != want.Message {
		t.Fatalf("step %q: %s got %+v want %+v", label, field, got, want)
	}
}

func stringSlicesEqual(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}
