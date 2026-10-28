package admission

import (
	"context"
	"sync"
	"time"
)

// CapacityDecision is the outcome of one non-blocking admission evaluation.
type CapacityDecision struct {
	// Admit reports whether an attempt may charge capacity at the given time.
	Admit bool
	// ReadyAt is the earliest virtual time at which the rate window allows
	// another admission; it equals now when no rate timer is pending.
	ReadyAt float64
	// RateBlocked reports whether the rate bucket (rather than occupancy)
	// contributed the pending timer.
	RateBlocked bool
}

// CapacityLimiter owns one provider's rate history, active slots and the
// timestamp of the most recently evicted admission. It mirrors
// ProviderCapacityLimiter, including its blocking acquire loop and wake
// semantics.
type CapacityLimiter struct {
	mu sync.Mutex

	limits          Limits
	admissionTimes  []float64
	lastDiscardedAt *float64
	activeAttempts  int

	now  func() float64
	wake chan struct{}
}

// NewCapacityLimiter starts a limiter with empty history and occupancy and
// a real process-relative monotonic clock.
func NewCapacityLimiter(limits Limits) *CapacityLimiter {
	return &CapacityLimiter{
		limits: limits,
		now:    defaultMonotonic,
		wake:   make(chan struct{}),
	}
}

// WithClock replaces the time source (golden tests use a virtual clock).
func (l *CapacityLimiter) WithClock(now func() float64) *CapacityLimiter {
	l.mu.Lock()
	l.now = now
	l.mu.Unlock()
	return l
}

// Limits returns the policy currently in force.
func (l *CapacityLimiter) Limits() Limits {
	l.mu.Lock()
	defer l.mu.Unlock()
	return l.limits
}

// Reconfigure swaps limits without resetting occupancy or history, matching
// ProviderCapacityLimiter.reconfigure. Identical limits are a no-op; any
// actual change wakes blocked acquirers.
func (l *CapacityLimiter) Reconfigure(limits Limits) {
	l.mu.Lock()
	if l.limits == limits {
		l.mu.Unlock()
		return
	}
	l.limits = limits
	l.wakeLocked()
	l.mu.Unlock()
}

// Evaluate runs one pass of the acquire loop at now: stale admissions are
// evicted in timestamp order, then readiness is computed without mutating
// occupancy. Call Commit only when Decision.Admit is true.
func (l *CapacityLimiter) Evaluate(now float64) CapacityDecision {
	l.mu.Lock()
	defer l.mu.Unlock()
	return l.evaluateLocked(now)
}

func (l *CapacityLimiter) evaluateLocked(now float64) CapacityDecision {
	cutoff := now - l.limits.RateWindow
	for len(l.admissionTimes) > 0 && l.admissionTimes[0] <= cutoff {
		discarded := l.admissionTimes[0]
		l.admissionTimes = l.admissionTimes[1:]
		l.lastDiscardedAt = &discarded
	}

	readyAt := now
	if l.lastDiscardedAt != nil {
		if candidate := *l.lastDiscardedAt + l.limits.RateWindow; candidate > readyAt {
			readyAt = candidate
		}
	}
	rateBlocked := len(l.admissionTimes) >= l.limits.RateLimit
	if rateBlocked {
		oldest := l.admissionTimes[len(l.admissionTimes)-l.limits.RateLimit]
		if candidate := oldest + l.limits.RateWindow; candidate > readyAt {
			readyAt = candidate
		}
	}

	return CapacityDecision{
		Admit:       l.activeAttempts < l.limits.MaxConcurrency && readyAt <= now,
		ReadyAt:     readyAt,
		RateBlocked: rateBlocked,
	}
}

// Commit charges one admitted attempt, recording its admission time and
// occupying a concurrency slot.
func (l *CapacityLimiter) Commit(now float64) {
	l.mu.Lock()
	defer l.mu.Unlock()
	l.commitLocked(now)
}

func (l *CapacityLimiter) commitLocked(now float64) {
	l.admissionTimes = append(l.admissionTimes, now)
	l.activeAttempts++
}

// Release frees one concurrency slot after an attempt finishes and wakes
// every blocked acquirer.
func (l *CapacityLimiter) Release() {
	l.mu.Lock()
	l.activeAttempts--
	l.wakeLocked()
	l.mu.Unlock()
}

// Acquire mirrors ProviderCapacityLimiter.acquire: it charges capacity only
// while the caller's recovery permit stays valid, parking on wake signals
// between evaluations. A rate-bucket wait carries a deadline; an
// occupancy-only wait parks until Release/Reconfigure. Context
// cancellation makes the caller leave without charging, like an invalidated
// permit.
func (l *CapacityLimiter) Acquire(ctx context.Context,
	permitIsCurrent func() bool) bool {
	for permitIsCurrent() {
		l.mu.Lock()
		now := l.now()
		decision := l.evaluateLocked(now)
		if decision.Admit {
			l.commitLocked(now)
			l.mu.Unlock()
			return true
		}
		var timer <-chan time.Time
		// Python only arms a timer while a concurrency slot remains free;
		// full occupancy waits for the wake event without a deadline.
		if l.activeAttempts < l.limits.MaxConcurrency {
			if delay := decision.ReadyAt - now; delay > 0 {
				timer = time.After(time.Duration(delay * float64(time.Second)))
			}
		}
		wait := l.wake
		l.mu.Unlock()

		select {
		case <-wait:
		case <-timer:
		case <-ctx.Done():
			return false
		}
	}
	return false
}

// wakeLocked broadcasts to blocked acquirers by replacing the signal
// channel; callers must hold mu.
func (l *CapacityLimiter) wakeLocked() {
	close(l.wake)
	l.wake = make(chan struct{})
}

// Snapshot is a point-in-time view used by golden parity tests.
type Snapshot struct {
	AdmissionTimes  []float64
	LastDiscardedAt *float64
	ActiveAttempts  int
}

// State copies the internal timing state without aliasing it.
func (l *CapacityLimiter) State() Snapshot {
	l.mu.Lock()
	defer l.mu.Unlock()
	times := make([]float64, len(l.admissionTimes))
	copy(times, l.admissionTimes)
	var discarded *float64
	if l.lastDiscardedAt != nil {
		value := *l.lastDiscardedAt
		discarded = &value
	}
	return Snapshot{
		AdmissionTimes:  times,
		LastDiscardedAt: discarded,
		ActiveAttempts:  l.activeAttempts,
	}
}
