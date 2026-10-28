package admission

import (
	"context"
	"errors"
	"math"
	"math/rand"
	"sync"
	"time"
)

// ErrGateWouldWait is returned when WaitForGate would block on another
// execution's leadership. The blocking transport loop (a later slice)
// parks on the controller's wake signal instead; golden scenarios never
// reach this branch.
var ErrGateWouldWait = errors.New("provider gate requires waiting")

// RecoveryExhaustedError mirrors ProviderRecoveryExhausted.
type RecoveryExhaustedError struct {
	LastError *AttemptError
}

// Error implements the error interface with the reference wording.
func (e *RecoveryExhaustedError) Error() string {
	return "Provider recovery retry budget was exhausted."
}

// GatePermit mirrors _GatePermit: a nil Generation is the normal permit;
// a probe permit carries the recovery generation it belongs to.
type GatePermit struct {
	Generation *int
	Probe      bool
}

// RecoveryEpisode mirrors _RecoveryEpisode. Waiters is a set keyed by
// execution pointer, matching the Python object-identity set.
type RecoveryEpisode struct {
	Model         string
	Generation    int
	Leader        *Execution
	ReadyAt       float64
	LastError     *AttemptError
	Waiters       map[*Execution]bool
	ProbeActive   bool
	TerminalUntil *float64
}

// processStart anchors the default monotonic clock at process start, the
// Go equivalent of time.monotonic's unspecified but stable origin.
var processStart = time.Now()

func defaultMonotonic() float64 {
	return time.Since(processStart).Seconds()
}

// Controller coordinates per-model recovery for one credential inside
// shared provider capacity, mirroring ProviderAdmissionController. The
// blocking wait and timer machinery lives in the transport slice; this
// type owns the decision state machine and is fully deterministic under
// an injected clock and jitter source.
type Controller struct {
	mu sync.Mutex

	providerName string
	limits       Limits
	capacity     *CapacityLimiter
	retry        RetryConfig

	episodes       map[string]*RecoveryEpisode
	nextGeneration int
	closed         bool

	now      func() float64
	jitter   func() float64
	gateWake chan struct{}
}

// NewController builds a controller with its own capacity limiter, real
// monotonic clock and randomized jitter.
func NewController(providerName string, limits Limits, retry RetryConfig) *Controller {
	c := &Controller{
		providerName:   providerName,
		limits:         limits,
		capacity:       NewCapacityLimiter(limits),
		retry:          retry,
		episodes:       map[string]*RecoveryEpisode{},
		nextGeneration: 1,
		now:            defaultMonotonic,
		gateWake:       make(chan struct{}),
	}
	c.jitter = c.defaultJitter
	return c
}

// WithClock replaces the time source (golden tests use a virtual clock)
// on both the gate and the shared capacity limiter.
func (c *Controller) WithClock(now func() float64) *Controller {
	c.mu.Lock()
	c.now = now
	c.mu.Unlock()
	c.capacity.WithClock(now)
	return c
}

// WithScriptedJitter replaces random jitter draws with a fixed sequence;
// the sequence wraps rather than runs out, matching generator harnesses.
func (c *Controller) WithScriptedJitter(draws []float64) *Controller {
	index := 0
	c.jitter = func() float64 {
		draw := draws[index%len(draws)]
		index++
		return draw
	}
	return c
}

func (c *Controller) defaultJitter() float64 {
	if c.retry.Jitter <= 0 {
		return 0
	}
	return rand.Float64() * c.retry.Jitter
}

// Capacity exposes the shared capacity gate.
func (c *Controller) Capacity() *CapacityLimiter { return c.capacity }

// IsClosed reports whether Close ran.
func (c *Controller) IsClosed() bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.closed
}

// Reconfigure forwards validated limits without disturbing recovery.
func (c *Controller) Reconfigure(limits Limits) {
	c.mu.Lock()
	c.limits = limits
	c.mu.Unlock()
	c.capacity.Reconfigure(limits)
}

// StartExecution opens one logical operation whose termination hook is
// wired to this controller.
func (c *Controller) StartExecution(requestID, model string) (*Execution, error) {
	return NewExecution(c.retry.MaxAttempts, requestID, model,
		c.OnExecutionTerminated)
}

// retryDelay mirrors _retry_delay: capped exponential backoff plus jitter.
func (c *Controller) retryDelay(error *AttemptError, attemptsStarted int) float64 {
	exponent := attemptsStarted - 1
	if exponent < 0 {
		exponent = 0
	}
	backoff := c.retry.BaseDelay * math.Pow(2, float64(exponent))
	if backoff > c.retry.MaxDelay {
		backoff = c.retry.MaxDelay
	}
	return backoff + c.jitter()
}

// StartRecoveryEpisode mirrors _start_recovery_episode.
func (c *Controller) startRecoveryEpisode(model string, leader *Execution,
	readyAt float64, lastError *AttemptError) *RecoveryEpisode {
	episode := &RecoveryEpisode{
		Model:      model,
		Generation: c.nextGeneration,
		Leader:     leader,
		ReadyAt:    readyAt,
		LastError:  lastError,
		Waiters:    map[*Execution]bool{},
	}
	c.nextGeneration++
	c.episodes[model] = episode
	return episode
}

// MatchingProbe mirrors _matching_probe.
func (c *Controller) MatchingProbe(execution *Execution,
	permit GatePermit) *RecoveryEpisode {
	if !permit.Probe || permit.Generation == nil {
		return nil
	}
	episode := c.episodes[execution.Model()]
	if episode == nil ||
		episode.Generation != *permit.Generation ||
		episode.Leader != execution ||
		!episode.ProbeActive {
		return nil
	}
	return episode
}

// AddWaiter registers an execution waiting on another leader's probe. The
// blocking gate performs this itself; the method exists for parity replays.
func (c *Controller) AddWaiter(execution *Execution) {
	c.mu.Lock()
	defer c.mu.Unlock()
	episode := c.episodes[execution.Model()]
	if episode == nil {
		return
	}
	episode.Waiters[execution] = true
}

// AttemptFailed mirrors _attempt_failed: open, continue, coalesce or
// exhaust the recovery episode for the execution's model.
func (c *Controller) AttemptFailed(execution *Execution, permit GatePermit,
	error *AttemptError, status *int) {
	c.mu.Lock()
	defer c.mu.Unlock()

	canRetry := execution.CanAttempt()
	// The reference always computes the delay, consuming one jitter draw
	// even when the episode ends up terminal.
	delay := c.retryDelay(error, execution.AttemptsStarted())

	c.maintainIdleEpisodesLocked()
	episode := c.episodes[execution.Model()]
	matchingProbe := c.matchingProbeLocked(execution, permit)
	now := c.now()

	if canRetry {
		switch {
		case episode == nil:
			c.startRecoveryEpisode(execution.Model(), execution, now+delay, error)
		case matchingProbe != nil:
			episode.LastError = error
			episode.ProbeActive = false
			episode.ReadyAt = now + delay
		case episode.TerminalUntil != nil:
			execution.Fail(episode.LastError)
		default:
			episode.Waiters[execution] = true
		}
		// Python notifies waiters, which also runs idle maintenance.
		c.notifyRecoveryLocked()
		return
	}

	if episode == nil || matchingProbe != nil {
		terminalDelay := c.retryDelay(error, execution.AttemptsStarted())
		if episode == nil {
			episode = c.startRecoveryEpisode(
				execution.Model(), nil, now, error)
		}
		episode.LastError = error
		episode.Leader = nil
		episode.ProbeActive = false
		terminalUntil := now + terminalDelay
		episode.TerminalUntil = &terminalUntil
		waiters := make([]*Execution, 0, len(episode.Waiters))
		for waiter := range episode.Waiters {
			waiters = append(waiters, waiter)
		}
		episode.Waiters = map[*Execution]bool{}
		for _, waiter := range waiters {
			waiter.Fail(error)
		}
	}
	c.notifyRecoveryLocked()
}

// AttemptAccepted mirrors _attempt_accepted: a successful probe closes
// the recovery episode.
func (c *Controller) AttemptAccepted(execution *Execution, permit GatePermit) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if !permit.Probe {
		return
	}
	if c.matchingProbeLocked(execution, permit) == nil {
		return
	}
	delete(c.episodes, execution.Model())
	c.notifyRecoveryLocked()
}

// AttemptRejected mirrors _attempt_rejected: a final probe rejection
// closes the episode too.
func (c *Controller) AttemptRejected(execution *Execution, permit GatePermit) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if !permit.Probe {
		return
	}
	if c.matchingProbeLocked(execution, permit) == nil {
		return
	}
	delete(c.episodes, execution.Model())
	c.notifyRecoveryLocked()
}

// AttemptAbandoned mirrors _attempt_abandoned: leadership is released and
// the probe slot returned without advancing the generation.
func (c *Controller) AttemptAbandoned(execution *Execution, permit GatePermit) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if !permit.Probe {
		return
	}
	episode := c.matchingProbeLocked(execution, permit)
	if episode == nil {
		return
	}
	episode.Leader = nil
	episode.ProbeActive = false
	episode.ReadyAt = c.now()
	// A leaderless, waiterless episode is immediately collectable.
	c.notifyRecoveryLocked()
}

// AttemptCorrected mirrors _attempt_corrected: the probe slot is released
// but the episode stays open and becomes immediately ready.
func (c *Controller) AttemptCorrected(execution *Execution, permit GatePermit) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if !permit.Probe {
		return
	}
	episode := c.matchingProbeLocked(execution, permit)
	if episode == nil {
		return
	}
	episode.ProbeActive = false
	episode.ReadyAt = c.now()
	c.notifyRecoveryLocked()
}

func (c *Controller) matchingProbeLocked(execution *Execution,
	permit GatePermit) *RecoveryEpisode {
	if !permit.Probe || permit.Generation == nil {
		return nil
	}
	episode := c.episodes[execution.Model()]
	if episode == nil ||
		episode.Generation != *permit.Generation ||
		episode.Leader != execution ||
		!episode.ProbeActive {
		return nil
	}
	return episode
}

// WaitForGate mirrors the decision portion of _wait_for_gate. It returns
// the normal permit, a probe permit after leader election, or a terminal
// exhaustion/closed error. A path that would block on another leader
// returns ErrGateWouldWait for the transport loop to park.
func (c *Controller) WaitForGate(execution *Execution) (GatePermit, error) {
	permit, _, err := c.waitForGateOnce(execution)
	return permit, err
}

// waitForGateOnce performs one gate evaluation. Besides the permit/error,
// it reports how long the caller must sleep when ErrGateWouldWait comes
// from a leader backoff: zero means park until the next wake instead.
func (c *Controller) waitForGateOnce(execution *Execution) (GatePermit, float64, error) {
	c.mu.Lock()
	defer c.mu.Unlock()

	c.maintainIdleEpisodesLocked()
	if c.closed {
		return GatePermit{}, 0, errors.New(
			"provider admission controller is closed")
	}
	if terminal := execution.TerminalFailure(); terminal != nil {
		return GatePermit{}, 0, &RecoveryExhaustedError{LastError: terminal}
	}
	if !execution.CanAttempt() {
		return GatePermit{}, 0, errors.New(
			"provider execution is terminal or exhausted")
	}

	episode := c.episodes[execution.Model()]
	if episode == nil {
		return GatePermit{Probe: false}, 0, nil
	}
	if episode.TerminalUntil != nil {
		return GatePermit{}, 0, &RecoveryExhaustedError{
			LastError: episode.LastError}
	}

	if episode.Leader == nil {
		episode.Leader = execution
		delete(episode.Waiters, execution)
		c.maintainIdleEpisodesLocked()
	}
	if episode.Leader == execution && !episode.ProbeActive {
		// Leader backoff: sleep until the episode becomes ready.
		if episode.ReadyAt > c.now() {
			return GatePermit{}, episode.ReadyAt - c.now(), ErrGateWouldWait
		}
		if c.episodes[execution.Model()] == episode &&
			episode.Leader == execution &&
			episode.TerminalUntil == nil &&
			!episode.ProbeActive &&
			execution.CanAttempt() {
			episode.ProbeActive = true
			generation := episode.Generation
			return GatePermit{Generation: &generation, Probe: true}, 0, nil
		}
		return GatePermit{}, 0, ErrGateWouldWait
	}

	// Another execution owns the probe; wait without a private deadline.
	episode.Waiters[execution] = true
	return GatePermit{}, 0, ErrGateWouldWait
}

// notifyRecoveryLocked mirrors _notify_recovery_waiters: expire idle
// episodes, then wake both the gate and capacity waiters. Callers hold mu.
func (c *Controller) notifyRecoveryLocked() {
	c.maintainIdleEpisodesLocked()
	c.gateWakeLocked()
	c.capacity.wakeLocked()
}

// gateWakeLocked broadcasts to parked gate visitors. Callers hold mu.
func (c *Controller) gateWakeLocked() {
	close(c.gateWake)
	c.gateWake = make(chan struct{})
}

// waitGateBlocking drives the asyncio event loop of _wait_for_gate:
// evaluate, park on the wake signal or a leader backoff deadline, then
// re-evaluate until a permit is issued or the context/terminal state ends
// the wait.
func (c *Controller) waitGateBlocking(ctx context.Context,
	execution *Execution) (GatePermit, error) {
	for {
		permit, wait, err := c.waitForGateOnce(execution)
		if !errors.Is(err, ErrGateWouldWait) {
			return permit, err
		}
		var timer <-chan time.Time
		if wait > 0 {
			timer = time.After(time.Duration(wait * float64(time.Second)))
		}
		c.mu.Lock()
		signal := c.gateWake
		c.mu.Unlock()
		select {
		case <-signal:
		case <-timer:
		case <-ctx.Done():
			return GatePermit{}, ctx.Err()
		}
	}
}

// PermitIsCurrent mirrors _permit_is_current: whether the recovery state
// that issued permit still owns this execution's admission path.
func (c *Controller) PermitIsCurrent(execution *Execution, permit GatePermit) bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.closed || !execution.CanAttempt() {
		return false
	}
	episode := c.episodes[execution.Model()]
	if !permit.Probe {
		return episode == nil
	}
	return episode != nil &&
		permit.Generation != nil &&
		episode.Generation == *permit.Generation &&
		episode.TerminalUntil == nil &&
		episode.Leader == execution &&
		episode.ProbeActive
}

// abandonProbePermit mirrors _abandon_probe_permit: a probe that lost its
// capacity race releases leadership without advancing the generation.
func (c *Controller) abandonProbePermit(execution *Execution, permit GatePermit) {
	if !permit.Probe {
		return
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.matchingProbeLocked(execution, permit) == nil {
		return
	}
	if episode := c.episodes[execution.Model()]; episode != nil {
		episode.Leader = nil
		episode.ProbeActive = false
		episode.ReadyAt = c.now()
	}
	c.notifyRecoveryLocked()
}

// OpenAttempt mirrors _open_attempt: wait for gate admission, charge
// shared capacity while the permit stays current, then claim the
// execution's sole active physical call.
func (c *Controller) OpenAttempt(ctx context.Context, execution *Execution,
	kind OperationKind) (*Attempt, error) {
	// Mirror Python's pre-loop guards: reject before touching the gate or
	// taking a capacity slot, so a double open leaves no admission record.
	if terminal := execution.TerminalFailure(); terminal != nil {
		return nil, &RecoveryExhaustedError{LastError: terminal}
	}
	if !execution.CanAttempt() {
		return nil, errors.New(
			"provider execution is terminal or exhausted")
	}
	if execution.ActiveClaimOrdinal() != 0 {
		return nil, errors.New(
			"provider execution already has an active attempt")
	}
	for {
		permit, err := c.waitGateBlocking(ctx, execution)
		if err != nil {
			return nil, err
		}
		admitted := c.capacity.Acquire(ctx, func() bool {
			return c.PermitIsCurrent(execution, permit)
		})
		if !admitted {
			c.abandonProbePermit(execution, permit)
			if ctx.Err() != nil {
				return nil, ctx.Err()
			}
			continue
		}
		ordinal, err := execution.ClaimAttempt(kind)
		if err != nil {
			c.capacity.Release()
			c.abandonProbePermit(execution, permit)
			return nil, err
		}
		return &Attempt{
			controller: c,
			execution:  execution,
			permit:     permit,
			ordinal:    ordinal,
		}, nil
	}
}

// OnExecutionTerminated mirrors _execution_terminated: drop waiter
// membership and step down a finished leader.
func (c *Controller) OnExecutionTerminated(execution *Execution) {
	c.mu.Lock()
	defer c.mu.Unlock()
	episode := c.episodes[execution.Model()]
	if episode == nil {
		return
	}
	delete(episode.Waiters, execution)
	if episode.Leader == execution &&
		(!episode.ProbeActive || execution.ActiveClaimOrdinal() == 0) {
		episode.Leader = nil
		episode.ProbeActive = false
	}
	c.notifyRecoveryLocked()
}

// MaintainIdleEpisodes expires leaderless, waiterless, inactive episodes
// whose ready/terminal deadline has passed. Timer scheduling is the
// transport layer's responsibility.
func (c *Controller) MaintainIdleEpisodes() {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.maintainIdleEpisodesLocked()
}

func (c *Controller) maintainIdleEpisodesLocked() {
	if c.closed {
		return
	}
	now := c.now()
	for model, episode := range c.episodes {
		if episode.Leader != nil || len(episode.Waiters) > 0 || episode.ProbeActive {
			continue
		}
		expiresAt := episode.ReadyAt
		if episode.TerminalUntil != nil {
			expiresAt = *episode.TerminalUntil
		}
		if expiresAt <= now {
			delete(c.episodes, model)
		}
	}
}

// Close releases recovery state after runtimes have drained and wakes
// every parked gate visitor and capacity acquirer.
func (c *Controller) Close() {
	c.mu.Lock()
	c.closed = true
	c.episodes = map[string]*RecoveryEpisode{}
	c.gateWakeLocked()
	c.capacity.wakeLocked()
	c.mu.Unlock()
}

// Episodes returns a snapshot copy of the current recovery state for
// golden assertions; callers must not mutate the returned episodes beyond
// test scaffolding.
func (c *Controller) Episodes() map[string]*RecoveryEpisode {
	c.mu.Lock()
	defer c.mu.Unlock()
	out := make(map[string]*RecoveryEpisode, len(c.episodes))
	for model, episode := range c.episodes {
		out[model] = episode
	}
	return out
}

// NextGeneration reports the next recovery generation number.
func (c *Controller) NextGeneration() int {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.nextGeneration
}
