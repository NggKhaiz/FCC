// Package admission ports the provider admission policy: validated rate and
// concurrency limits plus the per-provider capacity gate that enforces them.
package admission

import (
	"errors"
	"math"
)

// Limits is the validated capacity policy for one provider, mirroring
// ProviderAdmissionLimits. Construction is the only way to obtain values,
// so receivers can assume every field is usable.
type Limits struct {
	RateLimit      int
	RateWindow     float64
	MaxConcurrency int
}

// NewLimits validates the policy before it can reach a limiter. The error
// wording matches the Python dataclass __post_init__ checks exactly.
func NewLimits(rateLimit int, rateWindow float64, maxConcurrency int) (Limits, error) {
	if rateLimit <= 0 {
		return Limits{}, errors.New("rate_limit must be a positive integer")
	}
	if maxConcurrency <= 0 {
		return Limits{}, errors.New("max_concurrency must be a positive integer")
	}
	if math.IsNaN(rateWindow) || math.IsInf(rateWindow, 0) || rateWindow <= 0 {
		return Limits{}, errors.New("rate_window must be finite and > 0")
	}
	return Limits{
		RateLimit:      rateLimit,
		RateWindow:     rateWindow,
		MaxConcurrency: maxConcurrency,
	}, nil
}
