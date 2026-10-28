package providerpolicy

import (
	"encoding/json"
	"strconv"
	"strings"
)

// Markers mirror the Python frozensets; matching is substring based on the
// lowercased combination of exception, body and response text.
var (
	rateLimitMarkers = []string{"rate_limit", "rate limit", "too many requests"}
	overloadMarkers  = []string{
		"resourceexhausted", "resource exhausted", "limit reached",
		"overloaded", "capacity",
	}
	internalErrorMarkers = []string{"internal_server_error", "internal server error"}
)

// Underlying unwraps ProviderRecoveryExhausted wrappers, matching
// underlying_provider_error.
func Underlying(e *ProviderError) *ProviderError {
	for e != nil && e.isRecoveryExhausted() && e.Wrapped != nil {
		e = e.Wrapped
	}
	return e
}

func isRetryableStatus(status int) bool {
	return status == 429 || (status >= 500 && status <= 599)
}

// RetryableTransientStatus infers a retryable HTTP-like status.
func RetryableTransientStatus(e *ProviderError) (int, bool) {
	if e.isRecoveryExhausted() {
		return 0, false
	}
	if e.isCanonical() {
		if e.Canonical.Retryable && isRetryableStatus(e.Canonical.StatusCode) {
			return e.Canonical.StatusCode, true
		}
		return 0, false
	}
	if reportsContextWindowExceeded(e) {
		return 0, false
	}
	if status, ok := e.reportedStatus(); ok && status == 413 {
		return 0, false
	}
	if e.Kind == "RateLimitError" {
		return 429, true
	}
	if e.isHTTPStatusError() && e.HasResponseStatus && isRetryableStatus(e.ResponseStatus) {
		return e.ResponseStatus, true
	}
	if status, ok := e.exceptionStatus(); ok && isRetryableStatus(status) {
		return status, true
	}
	if status, ok := statusFromBody(e.Body); ok && isRetryableStatus(status) {
		return status, true
	}
	text := transientErrorText(e)
	if hasMarker(text, rateLimitMarkers) {
		return 429, true
	}
	if hasMarker(text, overloadMarkers) {
		return 503, true
	}
	if hasMarker(text, internalErrorMarkers) {
		return 500, true
	}
	return 0, false
}

// RetryableUpstreamStatus returns a status eligible for opening backoff.
func RetryableUpstreamStatus(e *ProviderError) (int, bool) {
	status, ok := RetryableTransientStatus(e)
	if !ok || !isRetryableStatus(status) {
		return 0, false
	}
	return status, true
}

// IsTransientOverloadError reports overload or capacity pressure.
func IsTransientOverloadError(e *ProviderError) bool {
	if e.isRecoveryExhausted() {
		return false
	}
	if e.isCanonical() {
		return e.Canonical.Kind == KindOverloaded
	}
	return hasMarker(transientErrorText(e), overloadMarkers)
}

// IsRetryableProviderError mirrors is_retryable_provider_error.
func IsRetryableProviderError(e *ProviderError) bool {
	if e.isRecoveryExhausted() {
		return false
	}
	if e.isCanonical() {
		return e.Canonical.Retryable
	}
	switch e.Kind {
	case "AuthenticationError", "PermissionDeniedError", "BadRequestError":
		return false
	}
	if _, ok := RetryableTransientStatus(e); ok {
		return true
	}
	return e.transportTimeout() || e.transportNetwork() ||
		e.remoteProtocol() || e.isProtocolError()
}

// IsRetryableStreamError mirrors is_retryable_stream_error, which only
// accepts post-open failures: connect-time errors are excluded.
func IsRetryableStreamError(e *ProviderError) bool {
	if e.isProtocolError() {
		return true
	}
	if e.isCanonical() {
		return e.Canonical.Retryable
	}
	if e.Kind == "AuthenticationError" || e.Kind == "BadRequestError" {
		return false
	}
	if _, ok := RetryableTransientStatus(e); ok {
		return true
	}
	return e.streamTimeout() || e.streamNetwork()
}

func (e *ProviderError) streamNetwork() bool {
	// httpx ConnectError and RemoteProtocolError are NetworkError
	// subclasses, so isinstance membership includes all three names.
	switch e.Kind {
	case "ConnectError", "RemoteProtocolError", "NetworkError",
		"APIConnectionError", "SSLWantReadError":
		return true
	}
	return false
}

// ProviderAuthenticationStatus recognizes 401/403 failures from statuses
// and structured discriminators, never prose.
func ProviderAuthenticationStatus(e *ProviderError) (int, bool) {
	if status, ok := e.reportedStatus(); ok {
		if status == 401 || status == 403 {
			return status, true
		}
	}
	codes := []string{}
	if e.HasCode {
		codes = append(codes, e.Code)
	}
	for _, item := range bodyCandidates(e.Body) {
		if m, ok := item.(map[string]any); ok {
			if code, ok := m["code"].(string); ok {
				codes = append(codes, code)
			}
			if typ, ok := m["type"].(string); ok {
				codes = append(codes, typ)
			}
		}
	}
	for _, code := range codes {
		if status, ok := anthropicErrorStatus[code]; ok && (status == 401 || status == 403) {
			return status, true
		}
	}
	for _, code := range codes {
		switch code {
		case "invalid_api_key", "unauthorized":
			return 401, true
		case "permission_denied", "forbidden":
			return 403, true
		}
	}
	return 0, false
}

// IsContextWindowErrorCode reports one structured discriminator value.
func IsContextWindowErrorCode(value any) bool {
	code, ok := value.(string)
	return ok && strings.EqualFold(strings.TrimSpace(code), contextWindowErrorCode)
}

// IsContextWindowFinishReason reports one terminal reason value.
func IsContextWindowFinishReason(value any) bool {
	reason, ok := value.(string)
	return ok && strings.EqualFold(strings.TrimSpace(reason), contextWindowFinish)
}

// ReportsContextWindowIncomplete mirrors the Responses terminal check.
func ReportsContextWindowIncomplete(eventType string, payload map[string]any) bool {
	if eventType != "response.incomplete" {
		return false
	}
	response, ok := payload["response"].(map[string]any)
	if !ok {
		return false
	}
	details, ok := response["incomplete_details"].(map[string]any)
	if !ok {
		return false
	}
	return IsContextWindowFinishReason(details["reason"])
}

func reportsContextWindowExceeded(e *ProviderError) bool {
	if e.HasCode && IsContextWindowErrorCode(e.Code) {
		return true
	}
	for _, body := range contextBodies(e) {
		for _, item := range bodyCandidates(body) {
			m, ok := item.(map[string]any)
			if !ok {
				continue
			}
			if IsContextWindowErrorCode(m["code"]) || IsContextWindowErrorCode(m["type"]) {
				return true
			}
		}
	}
	return false
}

func contextBodies(e *ProviderError) []any {
	bodies := []any{e.Body}
	// Python reads response.content; the descriptor carries its text form,
	// which may itself be a JSON body worth scanning.
	if e.ResponseText != "" {
		bodies = append(bodies, e.ResponseText)
	}
	return bodies
}

// Classify returns the canonical failure tuple (kind/status/retryable and
// stable wording) from _classify_provider_failure. Full customer-facing
// message formatting with redaction arrives in the diagnostics phase.
func Classify(e *ProviderError, readTimeoutS *float64) *CanonicalFailure {
	// Note: unlike classify_provider_failure, the reference
	// _classify_provider_failure does not unwrap recovery wrappers; only
	// ProviderErrorMessage unwraps, so the wrapper itself classifies as a
	// generic upstream 502 while its wording still reflects the inner error.
	if e.isCanonical() {
		return e.Canonical
	}
	if reportsContextWindowExceeded(e) {
		return failure(KindContextWindowExceeded, 400, MessageContextWindow, false)
	}
	if status, ok := e.reportedStatus(); ok && status == 413 {
		return failure(KindInvalidRequest, 413, MessageRequestTooLarge, false)
	}
	if status, ok := ProviderAuthenticationStatus(e); ok {
		if status == 401 {
			return failure(KindAuthentication, 401, MessageAuthentication, false)
		}
		return failure(KindPermission, 403, MessagePermission, false)
	}
	switch e.Kind {
	case "RateLimitError":
		return failure(KindRateLimit, 429, MessageRateLimit, true)
	case "BadRequestError":
		return failure(KindInvalidRequest, 400, MessageInvalidRequest, false)
	case "APITimeoutError":
		return failure(KindTimeout, 500, stableUpstream(500), true)
	case "APIConnectionError":
		return failure(KindUnavailable, 500, stableUpstream(500), true)
	case "InternalServerError":
		return classifyInternalServer(e)
	case "APIError", "APIStatusError",
		"AuthenticationError", "PermissionDeniedError":
		return classifyGenericOpenAI(e)
	}
	if e.isHTTPStatusError() {
		return classifyHTTPStatus(e)
	}
	kind := KindUpstream
	if e.transportTimeout() {
		kind = KindTimeout
	} else if e.transportNetwork() {
		kind = KindUnavailable
	}
	return failure(kind, 502, ProviderErrorMessage(e, readTimeoutS),
		IsRetryableProviderError(e))
}

func classifyInternalServer(e *ProviderError) *CanonicalFailure {
	status, hasTransient := RetryableTransientStatus(e)
	if !hasTransient {
		status = e.StatusCode
	}
	if IsTransientOverloadError(e) {
		return OverloadedFailure()
	}
	if status >= 500 && status <= 599 {
		return failure(KindUpstream, status, stableUpstream(status), true)
	}
	return failure(KindUpstream, 500, stableUpstream(500), true)
}

func classifyGenericOpenAI(e *ProviderError) *CanonicalFailure {
	status, hasTransient := RetryableTransientStatus(e)
	if hasTransient && status == 429 {
		return failure(KindRateLimit, 429, MessageRateLimit, true)
	}
	if IsTransientOverloadError(e) {
		return OverloadedFailure()
	}
	effective := status
	if effective == 0 {
		effective = e.StatusCode
	}
	if effective == 0 {
		effective = 500
	}
	switch effective {
	case 401:
		return failure(KindAuthentication, 401, MessageAuthentication, false)
	case 402:
		return failure(KindPermission, 402, MessageBilling, false)
	case 403:
		return failure(KindPermission, 403, MessagePermission, false)
	}
	return failure(KindUpstream, effective, stableUpstream(effective),
		IsRetryableProviderError(e))
}

func classifyHTTPStatus(e *ProviderError) *CanonicalFailure {
	status := e.ResponseStatus
	switch status {
	case 401:
		return failure(KindAuthentication, 401, MessageAuthentication, false)
	case 402:
		return failure(KindPermission, 402, MessageBilling, false)
	case 403:
		return failure(KindPermission, 403, MessagePermission, false)
	case 429:
		return failure(KindRateLimit, 429, MessageRateLimit, true)
	case 400:
		return failure(KindInvalidRequest, 400, MessageInvalidRequest, false)
	case 502, 503, 504:
		return OverloadedFailure()
	}
	return failure(KindUpstream, status, stableUpstream(status), isRetryableStatus(status))
}

// OverloadedFailure returns the canonical overload meaning.
func OverloadedFailure() *CanonicalFailure {
	return failure(KindOverloaded, 529, MessageOverloaded, true)
}

// ContextWindowFailure returns the canonical non-retryable context failure.
func ContextWindowFailure() *CanonicalFailure {
	return failure(KindContextWindowExceeded, 400, MessageContextWindow, false)
}

func stableUpstream(status int) string {
	if status == 502 || status == 503 || status == 504 {
		return MessageUnavailable
	}
	return MessageGenericUpstream
}

func failure(kind FailureKind, status int, message string, retryable bool) *CanonicalFailure {
	return &CanonicalFailure{Kind: kind, StatusCode: status, Message: message, Retryable: retryable}
}

// ProviderErrorMessage maps raw exception types to stable wording.
func ProviderErrorMessage(e *ProviderError, readTimeoutS *float64) string {
	if e != nil {
		e = Underlying(e)
	}
	if e.isCanonical() {
		return e.Canonical.Message
	}
	switch e.Kind {
	case "ReadTimeout":
		if readTimeoutS != nil {
			return "Provider request timed out after " + formatSeconds(*readTimeoutS) + "s."
		}
		return MessageProviderTimeout
	case "ConnectTimeout", "ConnectError":
		return MessageConnect
	case "RemoteProtocolError":
		return MessageInterrupted
	case "SSLWantReadError":
		return MessageSSLRead
	case "TimeoutError", "TimeoutException":
		if readTimeoutS != nil {
			return "Provider request timed out after " + formatSeconds(*readTimeoutS) + "s."
		}
		return MessageRequestTimeout
	case "RateLimitError":
		return MessageRateLimit
	case "AuthenticationError":
		return MessageAuthentication
	case "PermissionDeniedError":
		return MessagePermission
	case "BadRequestError":
		return MessageInvalidRequest
	}
	if trimmed := strings.TrimSpace(e.Message); trimmed != "" {
		// Credential redaction arrives with the diagnostics phase; golden
		// inputs are already clean and this keeps wording byte-identical.
		return trimmed
	}
	return MessageUnexpected
}

// formatSeconds reproduces Python's %g rendering for the timeout hint.
func formatSeconds(value float64) string {
	return strconv.FormatFloat(value, 'g', -1, 64)
}

// --- body and text helpers -------------------------------------------------

func (e *ProviderError) reportedStatus() (int, bool) {
	if e.HasStatus {
		return e.StatusCode, true
	}
	if e.HasResponseStatus {
		return e.ResponseStatus, true
	}
	return statusFromBody(e.Body)
}

func (e *ProviderError) exceptionStatus() (int, bool) {
	if e.HasStatus {
		return e.StatusCode, true
	}
	return 0, false
}

func transientErrorText(e *ProviderError) string {
	parts := make([]string, 0, 3)
	if e.Message != "" {
		parts = append(parts, e.Message)
	}
	if text := bodyToText(e.Body); text != "" {
		parts = append(parts, text)
	}
	if e.ResponseText != "" {
		parts = append(parts, e.ResponseText)
	}
	return strings.ToLower(strings.Join(parts, " "))
}

func hasMarker(text string, markers []string) bool {
	for _, marker := range markers {
		if strings.Contains(text, marker) {
			return true
		}
	}
	return false
}

func bodyToText(body any) string {
	switch v := body.(type) {
	case nil:
		return ""
	case string:
		return v
	case []byte:
		return string(v)
	default:
		// Compact JSON; only marker substring matching consumes this text.
		raw, err := json.Marshal(v)
		if err != nil {
			return ""
		}
		return string(raw)
	}
}

// bodyCandidates mirrors _body_candidates: strings are parsed as JSON,
// mappings contribute their nested "error" object when present.
func bodyCandidates(body any) []any {
	switch v := body.(type) {
	case nil:
		return nil
	case string:
		var parsed any
		if err := json.Unmarshal([]byte(v), &parsed); err == nil {
			return bodyCandidates(parsed)
		}
		return []any{v}
	case map[string]any:
		if nested, ok := v["error"].(map[string]any); ok {
			return []any{v, nested}
		}
		return []any{v}
	default:
		return []any{v}
	}
}

func statusFromBody(body any) (int, bool) {
	for _, item := range bodyCandidates(body) {
		m, ok := item.(map[string]any)
		if !ok {
			continue
		}
		for _, key := range []string{"status", "status_code", "code"} {
			if status, ok := coerceStatus(m[key]); ok {
				return status, true
			}
		}
		if status, ok := statusFromTypeFields(m); ok {
			return status, true
		}
	}
	return 0, false
}

func coerceStatus(value any) (int, bool) {
	switch n := value.(type) {
	case int:
		return n, true
	case int64:
		return int(n), true
	case float64:
		return int(n), true
	case json.Number:
		if i, err := n.Int64(); err == nil {
			return int(i), true
		}
	case string:
		if i, err := strconv.Atoi(n); err == nil {
			return i, true
		}
	}
	return 0, false
}

func statusFromTypeFields(item map[string]any) (int, bool) {
	values := []string{}
	for _, key := range []string{"type", "code"} {
		if value, ok := item[key].(string); ok {
			values = append(values, strings.ToLower(value))
		}
	}
	text := strings.Join(values, " ")
	if hasMarker(text, rateLimitMarkers) {
		return 429, true
	}
	if hasMarker(text, overloadMarkers) {
		return 503, true
	}
	if hasMarker(text, internalErrorMarkers) {
		return 500, true
	}
	return 0, false
}
