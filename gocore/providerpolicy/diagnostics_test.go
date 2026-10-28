package providerpolicy

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

type redactionCase struct {
	Input    string `json:"input"`
	Expected string `json:"expected"`
}

type capGolden struct {
	InputLength int    `json:"input_length"`
	Expected    string `json:"expected"`
	Truncated   bool   `json:"truncated"`
	CapBytes    int    `json:"cap_bytes"`
}

type formatGoldenCase struct {
	Name         string          `json:"name"`
	Descriptor   json.RawMessage `json:"descriptor"`
	UpstreamName string          `json:"upstream_name"`
	RequestID    string          `json:"request_id"`
	ReadTimeoutS *float64        `json:"read_timeout_s"`
	Expected     string          `json:"expected_message"`
}

type diagnosticsGolden struct {
	DisplayCapBytes int                `json:"display_cap_bytes"`
	RedactionCases  []redactionCase    `json:"redaction_cases"`
	CapCase         capGolden          `json:"cap_case"`
	FormatCases     []formatGoldenCase `json:"format_cases"`
}

func loadDiagnosticsGolden(t *testing.T) diagnosticsGolden {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join("..", "testdata", "golden_diagnostics4b.json"))
	if err != nil {
		t.Fatal(err)
	}
	var doc diagnosticsGolden
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.UseNumber()
	if err := dec.Decode(&doc); err != nil {
		t.Fatal(err)
	}
	return doc
}

func TestRedactionGoldenParity(t *testing.T) {
	for _, tc := range loadDiagnosticsGolden(t).RedactionCases {
		t.Run(tc.Input, func(t *testing.T) {
			if got := RedactSensitiveErrorText(tc.Input); got != tc.Expected {
				t.Fatalf("\ngot:  %q\nwant: %q", got, tc.Expected)
			}
		})
	}
}

func TestCapTextBytesGolden(t *testing.T) {
	golden := loadDiagnosticsGolden(t).CapCase
	if ErrorDetailDisplayCapBytes != golden.CapBytes {
		t.Fatalf("cap constant got %d want %d", ErrorDetailDisplayCapBytes, golden.CapBytes)
	}
	text := strings.Repeat("x", golden.InputLength)
	got, truncated := capTextBytes(text)
	if truncated != golden.Truncated || got != golden.Expected {
		t.Fatalf("cap mismatch\ntruncated got %v want %v", truncated, golden.Truncated)
	}
}

func TestFormatExecutionFailureGoldenParity(t *testing.T) {
	for _, tc := range loadDiagnosticsGolden(t).FormatCases {
		t.Run(tc.Name, func(t *testing.T) {
			providerErr := fromRawDescriptor(tc.Descriptor)
			canonical := Classify(providerErr, tc.ReadTimeoutS)
			detail := ExtractUpstreamErrorDetail(providerErr)
			got := FormatExecutionFailureMessage(
				canonical, detail, tc.UpstreamName, tc.RequestID)
			if got != tc.Expected {
				t.Fatalf("\ngot:\n%s\n\nwant:\n%s", got, tc.Expected)
			}
		})
	}
}
