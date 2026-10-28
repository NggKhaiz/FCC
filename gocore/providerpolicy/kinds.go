// Package providerpolicy ports the provider-owned SDK classification and
// retry qualification from providers/failure_policy.py. The Python code
// branches on httpx/openai exception hierarchies; the Go transport layer
// instead constructs ProviderError values from recorded error descriptors,
// so every decision stays pure data and is locked by golden fixtures.
package providerpolicy

// FailureKind mirrors the stable canonical categories in core/failures.py.
type FailureKind string

const (
	KindInvalidRequest        FailureKind = "invalid_request"
	KindContextWindowExceeded FailureKind = "context_window_exceeded"
	KindAuthentication        FailureKind = "authentication"
	KindPermission            FailureKind = "permission"
	KindRateLimit             FailureKind = "rate_limit"
	KindOverloaded            FailureKind = "overloaded"
	KindTimeout               FailureKind = "timeout"
	KindUpstream              FailureKind = "upstream"
	KindUnavailable           FailureKind = "unavailable"
)

// CanonicalFailure is the neutral, provider-independent failure model. It
// also satisfies the error interface so already-canonical failures flow
// through the same predicates as raw provider errors.
type CanonicalFailure struct {
	Kind       FailureKind `json:"kind"`
	StatusCode int         `json:"status_code"`
	Message    string      `json:"message"`
	Retryable  bool        `json:"retryable"`
}

func (f *CanonicalFailure) Error() string { return f.Message }

// Stable customer-facing wording, matching the failure_policy constants.
const (
	MessageAuthentication  = "Provider authentication failed. Check API key."
	MessagePermission      = "Provider denied access. Check credential permissions and model access."
	MessageBilling         = "Provider requires payment or additional credits. Add credits or resolve billing."
	MessageRateLimit       = "Provider rate limit reached. Please retry shortly."
	MessageInvalidRequest  = "Invalid request sent to provider."
	MessageRequestTooLarge = "Provider rejected the request as too large."
	MessageContextWindow   = "Provider input exceeds the model context window."
	MessageOverloaded      = "Provider is currently overloaded. Please retry."
	MessageUnavailable     = "Provider is temporarily unavailable. Please retry."
	MessageGenericUpstream = "Provider API request failed."
	MessageUnexpected      = "Provider request failed unexpectedly."
	MessageConnect         = "Could not connect to provider."
	MessageInterrupted     = "Provider connection was interrupted before a response was received."
	MessageSSLRead         = "Could not read the provider response."
	MessageProviderTimeout = "Provider request timed out."
	MessageRequestTimeout  = "Request timed out."
	contextWindowErrorCode = "context_length_exceeded"
	contextWindowFinish    = "model_context_window_exceeded"
)

// anthropicErrorStatus maps Anthropic error type strings to HTTP statuses,
// mirroring _ANTHROPIC_ERROR_STATUS_CODES.
var anthropicErrorStatus = map[string]int{
	"invalid_request_error": 400,
	"authentication_error":  401,
	"billing_error":         402,
	"permission_error":      403,
	"not_found_error":       404,
	"request_too_large":     413,
	"rate_limit_error":      429,
	"api_error":             500,
	"timeout_error":         504,
	"overloaded_error":      529,
}
