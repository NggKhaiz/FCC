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

	// Causes models the Python __cause__/__context__ chain used by the
	// diagnostics formatter. Only kind and message are retained.
	Causes []*ProviderError

	// AttachedBody is a bounded streamed body captured by the transport,
	// taking precedence over Body during diagnostic extraction.
	AttachedBody      string
	HasAttachedBody   bool
	AttachedTruncated bool

	// Canonical is populated when the descriptor is an ExecutionFailure.
	Canonical *CanonicalFailure
}

func (e *ProviderError) Error() string { return e.Message }

// Descriptor keys emitted by bench_local/generate_golden_phase4a.py.
type descriptor struct {
	Kind              string          `json:"kind"`
	Module            string          `json:"module"`
	Message           string          `json:"message"`
	StatusCode        json.Number     `json:"status_code"`
	Code              string          `json:"code"`
	Body              json.RawMessage `json:"body"`
	ResponseStatus    json.Number     `json:"response_status"`
	ResponseText      string          `json:"response_text"`
	LastError         json.RawMessage `json:"last_error"`
	FailureKind       string          `json:"failure_kind"`
	Retryable         bool            `json:"retryable"`
	Causes            []causeEntry    `json:"causes"`
	AttachedBody      string          `json:"attached_body"`
	AttachedTruncated bool            `json:"attached_truncated"`
}

type causeEntry struct {
	Kind    string `json:"kind"`
	Message string `json:"message"`
}

// FromDescriptor reconstructs a ProviderError from a golden descriptor.
func FromDescriptor(raw map[string]any) *ProviderError {
	if raw == nil {
		return nil
	}
	// Round-trip through the typed descriptor so json.Number survives and
	// body objects keep their raw bytes for order-preserving decode.
	buf, err := json.Marshal(raw)
	if err != nil {
		panic(err)
	}
	return fromRawDescriptor(json.RawMessage(buf))
}

func fromRawDescriptor(raw json.RawMessage) *ProviderError {
	if len(raw) == 0 {
		return nil
	}
	var d descriptor
	dec := json.NewDecoder(bytes.NewReader(raw))
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
		ResponseText: d.ResponseText,
	}
	if n, ok := integral(d.StatusCode); ok {
		e.HasStatus = true
		e.StatusCode = n
	}
	if len(d.Body) > 0 {
		e.Body = decodeOrdered(d.Body)
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
	if len(d.LastError) > 0 {
		e.Wrapped = fromRawDescriptor(d.LastError)
	}
	for _, cause := range d.Causes {
		e.Causes = append(e.Causes, &ProviderError{
			Kind: cause.Kind, Message: cause.Message,
		})
	}
	if d.AttachedBody != "" {
		e.AttachedBody = d.AttachedBody
		e.HasAttachedBody = true
		e.AttachedTruncated = d.AttachedTruncated
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
