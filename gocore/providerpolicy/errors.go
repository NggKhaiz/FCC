package providerpolicy

import (
	"bytes"
	"encoding/json"
)

// ProviderError is the data-shaped equivalent of the httpx/openai exception
// hierarchy. Kind carries the original Python class name so classification
// follows the same isinstance ordering as the reference.
type ProviderError struct {
	Kind   string
	Module string

	Message string
	Code    string
	HasCode bool

	HasStatus  bool
	StatusCode int

	Body any

	HasResponseStatus bool
	ResponseStatus    int
	ResponseText      string

	// Wrapped is the retained last_error of a ProviderRecoveryExhausted.
	Wrapped *ProviderError

	// Canonical is populated when the descriptor is an ExecutionFailure.
	Canonical *CanonicalFailure
}

func (e *ProviderError) Error() string { return e.Message }

// Descriptor keys emitted by bench_local/generate_golden_phase4a.py.
type descriptor struct {
	Kind           string         `json:"kind"`
	Module         string         `json:"module"`
	Message        string         `json:"message"`
	StatusCode     json.Number    `json:"status_code"`
	Code           string         `json:"code"`
	Body           any            `json:"body"`
	ResponseStatus json.Number    `json:"response_status"`
	ResponseText   string         `json:"response_text"`
	LastError      map[string]any `json:"last_error"`
	FailureKind    string         `json:"failure_kind"`
	Retryable      bool           `json:"retryable"`
}

// FromDescriptor reconstructs a ProviderError from a golden descriptor.
func FromDescriptor(raw map[string]any) *ProviderError {
	// Round-trip through the typed descriptor so json.Number survives.
	buf, err := json.Marshal(raw)
	if err != nil {
		panic(err)
	}
	var d descriptor
	dec := json.NewDecoder(bytes.NewReader(buf))
	dec.UseNumber()
	if err := dec.Decode(&d); err != nil {
		panic(err)
	}
	return buildError(&d)
}

func buildError(d *descriptor) *ProviderError {
	e := &ProviderError{
		Kind:         d.Kind,
		Module:       d.Module,
		Message:      d.Message,
		Code:         d.Code,
		HasCode:      d.Code != "",
		Body:         d.Body,
		ResponseText: d.ResponseText,
	}
	if n, ok := integral(d.StatusCode); ok {
		e.HasStatus = true
		e.StatusCode = n
	}
	if n, ok := integral(d.ResponseStatus); ok {
		e.HasResponseStatus = true
		e.ResponseStatus = n
	}
	if d.Kind == "ExecutionFailure" {
		e.Canonical = &CanonicalFailure{
			Kind:       FailureKind(d.FailureKind),
			StatusCode: e.StatusCode,
			Message:    d.Message,
			Retryable:  d.Retryable,
		}
	}
	if d.LastError != nil {
		e.Wrapped = FromDescriptor(d.LastError)
	}
	return e
}

func integral(number json.Number) (int, bool) {
	if number == "" {
		return 0, false
	}
	n, err := number.Int64()
	if err != nil {
		return 0, false
	}
	return int(n), true
}

// --- exception-hierarchy groupings, in the same order the Python checks run.

func (e *ProviderError) isRecoveryExhausted() bool {
	return e.Kind == "ProviderRecoveryExhausted"
}

func (e *ProviderError) isCanonical() bool {
	return e.Kind == "ExecutionFailure" || e.Canonical != nil
}

func (e *ProviderError) isHTTPStatusError() bool { return e.Kind == "HTTPStatusError" }

func (e *ProviderError) isProtocolError() bool {
	return e.Kind == "RetryableProviderProtocolError" ||
		e.Kind == "RetryableToolProtocolError"
}

// transportTimeout covers every exception Python retries as a timeout on
// the provider-opening path (httpx and its vendored httpx2 share class names).
func (e *ProviderError) transportTimeout() bool {
	switch e.Kind {
	case "ReadTimeout", "ConnectTimeout", "TimeoutException",
		"TimeoutError", "APITimeoutError":
		return true
	}
	return false
}

// streamTimeout narrows the timeout set to exceptions possible after a
// stream was opened: connect-time failures never reach this path.
func (e *ProviderError) streamTimeout() bool {
	switch e.Kind {
	case "ReadTimeout", "TimeoutError", "APITimeoutError", "SSLWantReadError":
		return true
	}
	return false
}

// transportNetwork mirrors the generic-classify isinstance membership:
// httpx.NetworkError (and its ConnectError subclass), the SSL read stall and
// the openai connection error. RemoteProtocolError is deliberately absent —
// in the installed httpx MRO it derives from ProtocolError, not NetworkError.
func (e *ProviderError) transportNetwork() bool {
	switch e.Kind {
	case "ConnectError", "NetworkError", "APIConnectionError", "SSLWantReadError":
		return true
	}
	return false
}

func (e *ProviderError) remoteProtocol() bool {
	return e.Kind == "RemoteProtocolError"
}
