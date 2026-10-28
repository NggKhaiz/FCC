package providerpolicy

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"testing"
)

type goldenCase struct {
	Name                    string               `json:"name"`
	Descriptor              map[string]any       `json:"descriptor"`
	ReadTimeoutS            *float64             `json:"read_timeout_s"`
	RetryableProvider       bool                 `json:"retryable_provider"`
	RetryableStream         bool                 `json:"retryable_stream"`
	TransientStatus         *int                 `json:"transient_status"`
	RetryableUpstreamStatus *int                 `json:"retryable_upstream_status"`
	TransientOverload       bool                 `json:"transient_overload"`
	AuthenticationStatus    *int                 `json:"authentication_status"`
	ProviderErrorMessage    string               `json:"provider_error_message"`
	Classification          goldenClassification `json:"classification"`
}

type goldenClassification struct {
	Kind       string `json:"kind"`
	StatusCode int    `json:"status_code"`
	Retryable  bool   `json:"retryable"`
}

type goldenContextEvent struct {
	Name      string         `json:"name"`
	EventType string         `json:"event_type"`
	Payload   map[string]any `json:"payload"`
	Expected  bool           `json:"expected"`
}

type goldenFile struct {
	Cases               []goldenCase         `json:"cases"`
	ContextWindowEvents []goldenContextEvent `json:"context_window_events"`
}

func loadGolden(t *testing.T) goldenFile {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join("..", "testdata", "golden_failure4a.json"))
	if err != nil {
		t.Fatal(err)
	}
	var doc goldenFile
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.UseNumber()
	if err := dec.Decode(&doc); err != nil {
		t.Fatal(err)
	}
	return doc
}

func TestFailurePolicyGoldenParity(t *testing.T) {
	for _, tc := range loadGolden(t).Cases {
		t.Run(tc.Name, func(t *testing.T) {
			err := FromDescriptor(tc.Descriptor)

			if got := IsRetryableProviderError(err); got != tc.RetryableProvider {
				t.Fatalf("retryable provider: got %v want %v", got, tc.RetryableProvider)
			}
			if got := IsRetryableStreamError(err); got != tc.RetryableStream {
				t.Fatalf("retryable stream: got %v want %v", got, tc.RetryableStream)
			}
			if got, ok := RetryableTransientStatus(err); ok != (tc.TransientStatus != nil) ||
				(tc.TransientStatus != nil && got != *tc.TransientStatus) {
				t.Fatalf("transient status: got (%d,%v) want %v", got, ok, tc.TransientStatus)
			}
			if got, ok := RetryableUpstreamStatus(err); ok != (tc.RetryableUpstreamStatus != nil) ||
				(tc.RetryableUpstreamStatus != nil && got != *tc.RetryableUpstreamStatus) {
				t.Fatalf("upstream status: got (%d,%v) want %v", got, ok, tc.RetryableUpstreamStatus)
			}
			if got := IsTransientOverloadError(err); got != tc.TransientOverload {
				t.Fatalf("overload: got %v want %v", got, tc.TransientOverload)
			}
			if got, ok := ProviderAuthenticationStatus(err); ok != (tc.AuthenticationStatus != nil) ||
				(tc.AuthenticationStatus != nil && got != *tc.AuthenticationStatus) {
				t.Fatalf("auth status: got (%d,%v) want %v", got, ok, tc.AuthenticationStatus)
			}
			if got := ProviderErrorMessage(err, tc.ReadTimeoutS); got != tc.ProviderErrorMessage {
				t.Fatalf("error message:\ngot  %q\nwant %q", got, tc.ProviderErrorMessage)
			}
			got := Classify(err, tc.ReadTimeoutS)
			want := &CanonicalFailure{
				Kind:       FailureKind(tc.Classification.Kind),
				StatusCode: tc.Classification.StatusCode,
				Retryable:  tc.Classification.Retryable,
			}
			// Message wording is locked separately through the stable
			// wording constants; compare the decision tuple fields here.
			if got.Kind != want.Kind || got.StatusCode != want.StatusCode ||
				got.Retryable != want.Retryable {
				t.Fatalf("classification: got %+v want %+v", got, want)
			}
		})
	}
}

func TestContextWindowEventsGolden(t *testing.T) {
	for _, tc := range loadGolden(t).ContextWindowEvents {
		t.Run(tc.Name, func(t *testing.T) {
			if got := ReportsContextWindowIncomplete(tc.EventType, tc.Payload); got != tc.Expected {
				t.Fatalf("got %v want %v", got, tc.Expected)
			}
		})
	}
}

func TestCanonicalFailures(t *testing.T) {
	if got := OverloadedFailure(); !reflect.DeepEqual(*got, CanonicalFailure{
		Kind: KindOverloaded, StatusCode: 529, Message: MessageOverloaded, Retryable: true,
	}) {
		t.Fatalf("overloaded failure mismatch: %+v", got)
	}
	if got := ContextWindowFailure(); !reflect.DeepEqual(*got, CanonicalFailure{
		Kind: KindContextWindowExceeded, StatusCode: 400, Message: MessageContextWindow, Retryable: false,
	}) {
		t.Fatalf("context window failure mismatch: %+v", got)
	}
}
