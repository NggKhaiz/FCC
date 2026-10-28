package admission

import (
	"context"
	"errors"
	"testing"
	"time"
)

func newBlockingController(t *testing.T, maxConcurrency int) *Controller {
	t.Helper()
	limits, err := NewLimits(40, 60, maxConcurrency)
	if err != nil {
		t.Fatal(err)
	}
	return NewController("blocking", limits, RetryConfig{
		MaxAttempts: 3,
		BaseDelay:   0,
		MaxDelay:    0,
		Jitter:      0,
	})
}

// TestCapacityBlocksUntilRelease: full occupancy parks the acquirer until
// a Release wakes it, without burning the context or busy-waiting.
func TestCapacityBlocksUntilRelease(t *testing.T) {
	limits, err := NewLimits(40, 60, 2)
	if err != nil {
		t.Fatal(err)
	}
	limiter := NewCapacityLimiter(limits)
	ctx := context.Background()
	current := func() bool { return true }

	if !limiter.Acquire(ctx, current) || !limiter.Acquire(ctx, current) {
		t.Fatal("initial admissions must succeed")
	}
	if got := limiter.State().ActiveAttempts; got != 2 {
		t.Fatalf("active slots got %d want 2", got)
	}

	done := make(chan bool, 1)
	go func() { done <- limiter.Acquire(ctx, current) }()

	select {
	case <-done:
		t.Fatal("third acquire must block while occupancy is full")
	case <-time.After(25 * time.Millisecond):
	}

	limiter.Release()
	select {
	case admitted := <-done:
		if !admitted {
			t.Fatal("woken acquire must admit")
		}
	case <-time.After(time.Second):
		t.Fatal("release did not wake the blocked acquirer")
	}
	if got := limiter.State().ActiveAttempts; got != 2 {
		t.Fatalf("active slots got %d want 2", got)
	}
}

// TestCapacityAcquireContextCancel: cancellation leaves without charging.
func TestCapacityAcquireContextCancel(t *testing.T) {
	limits, err := NewLimits(40, 60, 1)
	if err != nil {
		t.Fatal(err)
	}
	limiter := NewCapacityLimiter(limits)
	if !limiter.Acquire(context.Background(), func() bool { return true }) {
		t.Fatal("initial admission must succeed")
	}

	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan bool, 1)
	go func() {
		done <- limiter.Acquire(ctx, func() bool { return true })
	}()
	cancel()

	select {
	case admitted := <-done:
		if admitted {
			t.Fatal("canceled acquire must not admit")
		}
	case <-time.After(time.Second):
		t.Fatal("canceled acquire never returned")
	}
	if got := limiter.State().ActiveAttempts; got != 1 {
		t.Fatalf("canceled acquire charged a slot: got %d", got)
	}
}

// TestCapacityRateTimerUsesDeadline: a rate-bucket wait parks on the timer
// and admits once the real window elapses.
func TestCapacityRateTimerUsesDeadline(t *testing.T) {
	limits, err := NewLimits(1, 0.08, 5)
	if err != nil {
		t.Fatal(err)
	}
	limiter := NewCapacityLimiter(limits)
	if !limiter.Acquire(context.Background(), func() bool { return true }) {
		t.Fatal("first admission must succeed")
	}

	start := time.Now()
	done := make(chan bool, 1)
	go func() {
		done <- limiter.Acquire(context.Background(),
			func() bool { return true })
	}()
	select {
	case admitted := <-done:
		if !admitted {
			t.Fatal("timed acquire must admit after the window")
		}
	case <-time.After(time.Second):
		t.Fatal("rate timer never fired")
	}
	if elapsed := time.Since(start); elapsed < 40*time.Millisecond {
		t.Fatalf("acquire returned early after %v", elapsed)
	}
}

// TestCapacityReconfigureWakes: raising concurrency wakes parked acquirers.
func TestCapacityReconfigureWakes(t *testing.T) {
	limits, err := NewLimits(40, 60, 1)
	if err != nil {
		t.Fatal(err)
	}
	limiter := NewCapacityLimiter(limits)
	if !limiter.Acquire(context.Background(), func() bool { return true }) {
		t.Fatal("initial admission must succeed")
	}

	done := make(chan bool, 1)
	go func() {
		done <- limiter.Acquire(context.Background(),
			func() bool { return true })
	}()
	select {
	case <-done:
		t.Fatal("acquire must block at full occupancy")
	case <-time.After(25 * time.Millisecond):
	}

	raised, err := NewLimits(40, 60, 2)
	if err != nil {
		t.Fatal(err)
	}
	limiter.Reconfigure(raised)
	select {
	case admitted := <-done:
		if !admitted {
			t.Fatal("reconfigure wake must admit")
		}
	case <-time.After(time.Second):
		t.Fatal("reconfigure did not wake the blocked acquirer")
	}
}

// TestOpenAttemptBlocksThenUnblocks: the full attempt path parks on
// capacity and resumes when another attempt closes.
func TestOpenAttemptBlocksThenUnblocks(t *testing.T) {
	controller := newBlockingController(t, 2)
	ctx := context.Background()

	first, err := controller.StartExecution("req-b1", "model-block")
	if err != nil {
		t.Fatal(err)
	}
	second, err := controller.StartExecution("req-b2", "model-block")
	if err != nil {
		t.Fatal(err)
	}
	third, err := controller.StartExecution("req-b3", "model-block")
	if err != nil {
		t.Fatal(err)
	}

	open := func(execution *Execution) (*Attempt, error) {
		return controller.OpenAttempt(ctx, execution, OpGeneration)
	}
	a1, err := open(first)
	if err != nil {
		t.Fatal(err)
	}
	a2, err := open(second)
	if err != nil {
		t.Fatal(err)
	}

	type openResult struct {
		attempt *Attempt
		err     error
	}
	resultCh := make(chan openResult, 1)
	go func() {
		attempt, openErr := open(third)
		resultCh <- openResult{attempt, openErr}
	}()
	select {
	case <-resultCh:
		t.Fatal("third open must block at concurrency 2")
	case <-time.After(25 * time.Millisecond):
	}

	a1.Close()
	select {
	case result := <-resultCh:
		if result.err != nil {
			t.Fatalf("woken open got error %v", result.err)
		}
		if result.attempt.Ordinal() != 1 {
			t.Fatalf("ordinal got %d want 1", result.attempt.Ordinal())
		}
		result.attempt.Close()
	case <-time.After(time.Second):
		t.Fatal("close did not wake the blocked open")
	}
	a2.Close()

	if state := controller.Capacity().State(); state.ActiveAttempts != 0 {
		t.Fatalf("lingering slots: %d", state.ActiveAttempts)
	}
}

// TestOpenAttemptCanceledWhileBlocked abandons without consuming a slot.
func TestOpenAttemptCanceledWhileBlocked(t *testing.T) {
	controller := newBlockingController(t, 1)
	holder, err := controller.StartExecution("req-c1", "model-cancel")
	if err != nil {
		t.Fatal(err)
	}
	waiting, err := controller.StartExecution("req-c2", "model-cancel")
	if err != nil {
		t.Fatal(err)
	}

	held, err := controller.OpenAttempt(context.Background(), holder,
		OpGeneration)
	if err != nil {
		t.Fatal(err)
	}

	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() {
		_, openErr := controller.OpenAttempt(ctx, waiting, OpGeneration)
		done <- openErr
	}()
	cancel()
	select {
	case openErr := <-done:
		if !errors.Is(openErr, context.Canceled) {
			t.Fatalf("got %v want context.Canceled", openErr)
		}
	case <-time.After(time.Second):
		t.Fatal("canceled open never returned")
	}

	held.Close()
	if state := controller.Capacity().State(); state.ActiveAttempts != 0 {
		t.Fatalf("canceled open leaked a slot: %d", state.ActiveAttempts)
	}
	if waiting.ActiveClaimOrdinal() != 0 {
		t.Fatal("canceled open left a claim behind")
	}
}
