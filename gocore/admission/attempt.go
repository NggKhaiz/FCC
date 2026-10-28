package admission

import (
	"context"
	"errors"
	"sync"

	"github.com/NggKhaiz/FCC/gocore/providerpolicy"
)

// CorrectionAction mirrors ProviderCorrectionAction: whether a deterministic
// correction may consume another attempt.
type CorrectionAction string

const (
	CorrectionRetry CorrectionAction = "retry"
	CorrectionFinal CorrectionAction = "final"
)

// FailureDecision mirrors the immutable ProviderFailureDecision tuple.
type FailureDecision struct {
	Retryable    bool
	RetryAllowed bool
}

// FailureOverride maps a raw provider error to a canonical execution
// failure, mirroring ProviderFailureOverride. A nil result keeps the
// original error as the effective error.
type FailureOverride func(*providerpolicy.ProviderError) *providerpolicy.CanonicalFailure

// Attempt is one admitted upstream call holding a concurrency slot. It
// mirrors ProviderAttempt, including its exactly-once close semantics.
type Attempt struct {
	controller *Controller
	execution  *Execution
	permit     GatePermit
	ordinal    int

	mu       sync.Mutex
	resolved bool
	accepted bool
	closed   bool
}

// Ordinal returns the 1-based position of this attempt in the execution.
func (a *Attempt) Ordinal() int { return a.ordinal }

// Accepted reports whether upstream acceptance resolved this attempt.
func (a *Attempt) Accepted() bool {
	a.mu.Lock()
	defer a.mu.Unlock()
	return a.accepted
}

// Accept records a valid upstream response and closes the recovery
// episode when this attempt was its probe.
func (a *Attempt) Accept() {
	a.mu.Lock()
	if a.resolved || a.closed {
		a.mu.Unlock()
		return
	}
	a.resolved = true
	a.accepted = true
	a.mu.Unlock()

	a.controller.AttemptAccepted(a.execution, a.permit)
}

// Correct resolves a deterministic request correction without provider
// backoff: RETRY while budget remains, FINAL (and a failed execution)
// once the budget is spent.
func (a *Attempt) Correct(err *providerpolicy.ProviderError) CorrectionAction {
	a.mu.Lock()
	if a.resolved || a.closed {
		a.mu.Unlock()
		return CorrectionFinal
	}
	a.resolved = true
	a.mu.Unlock()

	original := toAttemptError(err)
	a.execution.RecordFailure(original)
	if a.execution.CanAttempt() {
		a.controller.AttemptCorrected(a.execution, a.permit)
		return CorrectionRetry
	}
	a.controller.AttemptRejected(a.execution, a.permit)
	a.execution.Fail(original)
	return CorrectionFinal
}

// Fail classifies one failure and returns the immutable retry decision,
// mirroring ProviderAttempt.fail including override handling.
func (a *Attempt) Fail(err *providerpolicy.ProviderError,
	override FailureOverride) FailureDecision {
	a.mu.Lock()
	if a.resolved || a.closed {
		a.mu.Unlock()
		return FailureDecision{}
	}
	a.resolved = true
	a.mu.Unlock()

	effective := err
	if override != nil {
		if canonical := override(err); canonical != nil {
			effective = &providerpolicy.ProviderError{
				Kind:      "ExecutionFailure",
				Message:   canonical.Message,
				Canonical: canonical,
			}
		}
	}
	status, hasStatus := providerpolicy.RetryableUpstreamStatus(effective)
	var statusPtr *int
	if hasStatus {
		statusPtr = &status
	}
	retryable := providerpolicy.IsRetryableProviderError(effective)

	// The recorded failure is always the original error, never the
	// override's canonical replacement.
	original := toAttemptError(err)
	a.execution.RecordFailure(original)

	if !retryable {
		a.controller.AttemptRejected(a.execution, a.permit)
		a.execution.Fail(original)
		return FailureDecision{Retryable: false, RetryAllowed: false}
	}

	a.controller.AttemptFailed(a.execution, a.permit,
		toAttemptError(err), statusPtr)
	retryAllowed := a.execution.CanAttempt()
	if !retryAllowed {
		a.execution.Fail(original)
	}
	return FailureDecision{Retryable: true, RetryAllowed: retryAllowed}
}

// Close releases attempt ownership and its capacity slot exactly once.
// An unresolved attempt is abandoned first, matching aclose's shielded
// recovery cleanup.
func (a *Attempt) Close() {
	a.mu.Lock()
	if a.closed {
		a.mu.Unlock()
		return
	}
	a.closed = true
	unresolved := !a.resolved
	a.resolved = true
	a.mu.Unlock()

	if unresolved {
		a.controller.AttemptAbandoned(a.execution, a.permit)
	}
	a.execution.CloseAttempt(a.ordinal)
	a.controller.Capacity().Release()
}

// RunCall mirrors ProviderExecution.run_call: invoke fn once per admitted
// attempt, retrying retryable failures until success, a terminal failure
// or budget exhaustion. The returned error carries the original provider
// error's kind and message.
func RunCall[T any](ctx context.Context, controller *Controller,
	execution *Execution, kind OperationKind,
	fn func() (T, *providerpolicy.ProviderError)) (T, error) {
	var zero T
	for execution.CanAttempt() {
		attempt, err := controller.OpenAttempt(ctx, execution, kind)
		if err != nil {
			// Mirror run_call's outer handlers: cancellation abandons,
			// any other gate error fails the execution before propagating.
			if ctx.Err() != nil {
				execution.Abandon()
			} else {
				execution.Fail(&AttemptError{
					Kind:    "RuntimeError",
					Message: err.Error(),
				})
			}
			return zero, err
		}
		result, perr := fn()
		if perr != nil {
			decision := attempt.Fail(perr, nil)
			attempt.Close()
			if !decision.RetryAllowed {
				return zero, toAttemptError(perr)
			}
			continue
		}
		attempt.Accept()
		attempt.Close()
		execution.Succeed()
		return result, nil
	}

	if terminal := execution.TerminalFailure(); terminal != nil {
		return zero, terminal
	}
	execution.Abandon()
	return zero, errors.New("provider execution ended without an attempt outcome")
}

func toAttemptError(err *providerpolicy.ProviderError) *AttemptError {
	if err == nil {
		return nil
	}
	return &AttemptError{Kind: err.Kind, Message: err.Message}
}
