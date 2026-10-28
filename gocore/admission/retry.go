package admission

import (
	"errors"
	"math"
	"net/http"
	"strconv"
	"strings"
	"time"
)

// Retry timing defaults mirror the admission module constants.
const (
	UpstreamTransientTotalAttempts = 10
	DefaultBaseDelay               = 2.0
	DefaultMaxDelay                = 60.0
	DefaultJitter                  = 1.0
)

// RetryConfig is the validated backoff configuration for one controller.
type RetryConfig struct {
	MaxAttempts int
	BaseDelay   float64
	MaxDelay    float64
	Jitter      float64
}

// NewRetryConfig enforces the same constructor rules as
// ProviderAdmissionController, with identical error wording.
func NewRetryConfig(maxAttempts int, baseDelay, maxDelay, jitter float64) (RetryConfig, error) {
	if maxAttempts <= 0 {
		return RetryConfig{}, errors.New("max_attempts must be > 0")
	}
	if baseDelay < 0 {
		return RetryConfig{}, errors.New("base_delay must be >= 0")
	}
	if maxDelay < baseDelay {
		return RetryConfig{}, errors.New("max_delay must be >= base_delay")
	}
	if jitter < 0 {
		return RetryConfig{}, errors.New("jitter must be >= 0")
	}
	return RetryConfig{
		MaxAttempts: maxAttempts,
		BaseDelay:   baseDelay,
		MaxDelay:    maxDelay,
		Jitter:      jitter,
	}, nil
}

// DefaultRetryConfig returns the production retry configuration.
func DefaultRetryConfig() RetryConfig {
	return RetryConfig{
		MaxAttempts: UpstreamTransientTotalAttempts,
		BaseDelay:   DefaultBaseDelay,
		MaxDelay:    DefaultMaxDelay,
		Jitter:      DefaultJitter,
	}
}

// Delay reproduces _retry_delay: exponential backoff capped at MaxDelay
// before the jitter draw is added, then raised to the Retry-After floor.
func (c RetryConfig) Delay(attempt int, retryAfter *float64, jitterDraw float64) float64 {
	exponent := attempt - 1
	if exponent < 0 {
		exponent = 0
	}
	backoff := c.BaseDelay * math.Pow(2, float64(exponent))
	if backoff > c.MaxDelay {
		backoff = c.MaxDelay
	}
	backoff += jitterDraw
	floor := 0.0
	if retryAfter != nil {
		floor = *retryAfter
	}
	if floor > backoff {
		return floor
	}
	return backoff
}

// RetryAfterSeconds parses a Retry-After header the way
// _retry_after_seconds does: a numeric delta first, then an HTTP date
// measured against now. A tolerant RFC1123Z fallback covers "-0000"
// offsets, which the reference treats as naive UTC. Blank or unparseable
// values yield nil.
func RetryAfterSeconds(header *string, now time.Time) *float64 {
	if header == nil {
		return nil
	}
	stripped := strings.TrimSpace(*header)
	if stripped == "" {
		return nil
	}
	if seconds, err := strconv.ParseFloat(stripped, 64); err == nil {
		if math.IsNaN(seconds) || math.IsInf(seconds, 0) {
			return nil
		}
		return floatPointer(math.Max(0, seconds))
	}
	retryAt, err := http.ParseTime(stripped)
	if err != nil {
		// Python's parsedate_to_datetime accepts a "-0000" local-offset
		// stamp and then assumes UTC; RFC1123Z reproduces that reading.
		retryAt, err = time.Parse(time.RFC1123Z, stripped)
		if err != nil {
			return nil
		}
	}
	seconds := retryAt.Sub(now).Seconds()
	if math.IsNaN(seconds) || math.IsInf(seconds, 0) {
		return nil
	}
	return floatPointer(math.Max(0, seconds))
}

// ExceptionStatus mirrors _exception_status: the exception's own status
// wins; otherwise the response status is used, and neither may be present.
func ExceptionStatus(providerStatus, responseStatus *int) *int {
	if providerStatus != nil {
		return providerStatus
	}
	return responseStatus
}

// FailureLabel mirrors ProviderAdmissionController._failure_label.
func FailureLabel(status *int, errorKind string) string {
	switch {
	case status != nil && *status == 429:
		return "Rate limited (429)"
	case status != nil:
		return "Upstream server error (" + strconv.Itoa(*status) + ")"
	default:
		return "Provider transient error (" + errorKind + ")"
	}
}

func floatPointer(value float64) *float64 {
	return &value
}
