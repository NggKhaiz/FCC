package admission

import "sync"

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
// ProviderCapacityLimiter; the blocking wait loop lives in the transport
// layer and drives Evaluate/Commit under an injectable clock.
type CapacityLimiter struct {
	mu sync.Mutex

	limits          Limits
	admissionTimes  []float64
	lastDiscardedAt *float64
	activeAttempts  int
}

// NewCapacityLimiter starts a limiter with empty history and occupancy.
func NewCapacityLimiter(limits Limits) *CapacityLimiter {
	return &CapacityLimiter{limits: limits}
}

// Limits returns the policy currently in force.
func (l *CapacityLimiter) Limits() Limits {
	l.mu.Lock()
	defer l.mu.Unlock()
	return l.limits
}

// Reconfigure swaps limits without resetting occupancy or history, matching
// ProviderCapacityLimiter.reconfigure. Identical limits are a no-op.
func (l *CapacityLimiter) Reconfigure(limits Limits) {
	l.mu.Lock()
	defer l.mu.Unlock()
	if l.limits == limits {
		return
	}
	l.limits = limits
}

// Evaluate runs one pass of the acquire loop at now: stale admissions are
// evicted in timestamp order, then readiness is computed without mutating
// occupancy. Call Commit only when Decision.Admit is true.
func (l *CapacityLimiter) Evaluate(now float64) CapacityDecision {
	l.mu.Lock()
	defer l.mu.Unlock()

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
	l.admissionTimes = append(l.admissionTimes, now)
	l.activeAttempts++
}

// Release frees one concurrency slot after an attempt finishes.
func (l *CapacityLimiter) Release() {
	l.mu.Lock()
	defer l.mu.Unlock()
	l.activeAttempts--
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
