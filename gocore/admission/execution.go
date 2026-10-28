package admission

import (
	"errors"

	"github.com/NggKhaiz/FCC/gocore/wireuuid"
)

// ExecutionState mirrors ProviderExecutionState.
type ExecutionState string

const (
	StateActive    ExecutionState = "active"
	StateSucceeded ExecutionState = "succeeded"
	StateFailed    ExecutionState = "failed"
	StateAbandoned ExecutionState = "abandoned"
)

// OperationKind mirrors ProviderOperationKind: the safe trace category of
// one physical provider call.
type OperationKind string

const (
	OpModelDiscovery OperationKind = "model_discovery"
	OpGeneration     OperationKind = "generation"
	OpContinuation   OperationKind = "continuation"
	OpToolRepair     OperationKind = "tool_repair"
)

// AttemptError is the portable carrier for an exception's type name and
// message, keeping the admission package free of providerpolicy coupling.
type AttemptError struct {
	Kind    string
	Message string
}

// Error implements the error interface.
func (e *AttemptError) Error() string { return e.Message }

// TerminationHook is invoked exactly on the transitions Python surfaces
// through ProviderAdmissionController._execution_terminated.
type TerminationHook func(*Execution)

// Execution owns one logical provider operation and its physical-attempt
// budget, mirroring ProviderExecution. Concurrency safety is the caller's
// responsibility: the reference object is single-execution, event-loop
// confined as well.
type Execution struct {
	id              string
	maxAttempts     int
	requestID       string
	model           string
	attemptsStarted int
	activeClaim     *attemptClaim
	lastFailure     *AttemptError
	state           ExecutionState
	hook            TerminationHook
}

type attemptClaim struct {
	executionID string
	ordinal     int
	kind        OperationKind
}

// NewExecution starts an active execution. The UUID4 mirrors Python's
// uuid.uuid4 identity used in execution ids and trace events.
func NewExecution(maxAttempts int, requestID, model string, hook TerminationHook) (*Execution, error) {
	id, err := wireuuid.NewV4()
	if err != nil {
		return nil, err
	}
	return &Execution{
		id:          id.String(),
		maxAttempts: maxAttempts,
		requestID:   requestID,
		model:       model,
		state:       StateActive,
		hook:        hook,
	}, nil
}

// ID returns the canonical UUID4 execution identifier.
func (e *Execution) ID() string { return e.id }

// MaxAttempts returns the configured physical-attempt budget.
func (e *Execution) MaxAttempts() int { return e.maxAttempts }

// RequestID returns the associated request identifier, if any.
func (e *Execution) RequestID() string { return e.requestID }

// Model returns the model key; nil in Python becomes the empty string.
func (e *Execution) Model() string { return e.model }

// AttemptsStarted reports how many physical attempts have been claimed.
func (e *Execution) AttemptsStarted() int { return e.attemptsStarted }

// State returns the terminal state of the logical operation.
func (e *Execution) State() ExecutionState { return e.state }

// CanAttempt mirrors the property: active and budget remaining.
func (e *Execution) CanAttempt() bool {
	return e.state == StateActive && e.attemptsStarted < e.maxAttempts
}

// AttemptsRemaining is zero once terminal; otherwise the unused budget.
func (e *Execution) AttemptsRemaining() int {
	if e.state != StateActive {
		return 0
	}
	remaining := e.maxAttempts - e.attemptsStarted
	if remaining < 0 {
		return 0
	}
	return remaining
}

// LastFailure returns the most recently recorded failure while active.
func (e *Execution) LastFailure() *AttemptError { return e.lastFailure }

// TerminalFailure returns the failure only once the execution failed.
func (e *Execution) TerminalFailure() *AttemptError {
	if e.state == StateFailed {
		return e.lastFailure
	}
	return nil
}

// ActiveClaimOrdinal reports the open claim ordinal, or zero when none.
func (e *Execution) ActiveClaimOrdinal() int {
	if e.activeClaim == nil {
		return 0
	}
	return e.activeClaim.ordinal
}

// ClaimAttempt opens the sole active physical call. The error wording
// matches ProviderExecution._claim_attempt exactly.
func (e *Execution) ClaimAttempt(kind OperationKind) (int, error) {
	if !e.CanAttempt() {
		return 0, errors.New("provider execution is terminal or exhausted")
	}
	if e.activeClaim != nil {
		return 0, errors.New("provider execution already has an active attempt")
	}
	e.attemptsStarted++
	e.activeClaim = &attemptClaim{
		executionID: e.id,
		ordinal:     e.attemptsStarted,
		kind:        kind,
	}
	return e.attemptsStarted, nil
}

// CloseAttempt releases the claim when its ordinal matches. A zero ordinal
// mirrors a None claim: when no claim is active the reference comparison
// None == None still matches, so a terminal execution re-fires the
// termination hook. A stale ordinal leaves the active claim untouched.
func (e *Execution) CloseAttempt(ordinal int) {
	if e.activeClaim == nil {
		if ordinal != 0 {
			return
		}
	} else if e.activeClaim.ordinal != ordinal {
		return
	}
	e.activeClaim = nil
	if e.state != StateActive {
		e.fireTermination()
	}
}

// Succeed marks the logical operation successful; terminal calls no-op.
func (e *Execution) Succeed() {
	if e.state != StateActive {
		return
	}
	e.state = StateSucceeded
	e.fireTermination()
}

// Fail records the terminal failure and marks the operation failed.
func (e *Execution) Fail(error *AttemptError) {
	if e.state != StateActive {
		return
	}
	e.lastFailure = error
	e.state = StateFailed
	e.fireTermination()
}

// Abandon marks an unfinished operation abandoned.
func (e *Execution) Abandon() {
	if e.state != StateActive {
		return
	}
	e.state = StateAbandoned
	e.fireTermination()
}

// RecordFailure stores a failure without changing state, and only while
// the execution remains active (mirrors _record_failure).
func (e *Execution) RecordFailure(error *AttemptError) {
	if e.state == StateActive {
		e.lastFailure = error
	}
}

func (e *Execution) fireTermination() {
	if e.hook != nil {
		e.hook(e)
	}
}
