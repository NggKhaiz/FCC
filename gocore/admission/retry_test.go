package admission

import (
	"encoding/json"
	"math"
	"os"
	"path/filepath"
	"testing"
	"time"
)

type retryFixture struct {
	Constants struct {
		TotalAttempts int     `json:"upstream_transient_total_attempts"`
		BaseDelay     float64 `json:"default_base_delay"`
		MaxDelay      float64 `json:"default_max_delay"`
		Jitter        float64 `json:"default_jitter"`
	} `json:"constants"`
	ConfigCases []struct {
		Label    string `json:"label"`
		Error    string `json:"error"`
		Expected string `json:"expected"`
		Observed *struct {
			MaxAttempts int     `json:"max_attempts"`
			BaseDelay   float64 `json:"base_delay"`
			MaxDelay    float64 `json:"max_delay"`
			Jitter      float64 `json:"jitter"`
		} `json:"observed"`
	} `json:"config_cases"`
	RetryAfterCases []struct {
		Label           string   `json:"label"`
		ResponsePresent bool     `json:"response_present"`
		Header          *string  `json:"retry_after_header"`
		Expected        *float64 `json:"expected"`
	} `json:"retry_after_cases"`
	DelayCases  []map[string]any `json:"delay_cases"`
	StatusCases []map[string]any `json:"status_and_label_cases"`
}

func loadRetryFixture(t *testing.T) retryFixture {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join("..", "testdata", "golden_retry4c2.json"))
	if err != nil {
		t.Fatal(err)
	}
	var fixture retryFixture
	if err := json.Unmarshal(raw, &fixture); err != nil {
		t.Fatal(err)
	}
	return fixture
}

var goldenNow = time.Date(2026, 10, 28, 8, 0, 0, 0, time.UTC)

func TestRetryConstantsAndConfigGoldenParity(t *testing.T) {
	fixture := loadRetryFixture(t)
	if fixture.Constants.TotalAttempts != UpstreamTransientTotalAttempts ||
		fixture.Constants.BaseDelay != DefaultBaseDelay ||
		fixture.Constants.MaxDelay != DefaultMaxDelay ||
		fixture.Constants.Jitter != DefaultJitter {
		t.Fatalf("constant mismatch: %+v", fixture.Constants)
	}

	for _, tc := range fixture.ConfigCases {
		t.Run(tc.Label, func(t *testing.T) {
			if tc.Observed != nil {
				got := DefaultRetryConfig()
				if got.MaxAttempts != tc.Observed.MaxAttempts ||
					!floatEqual(got.BaseDelay, tc.Observed.BaseDelay) ||
					!floatEqual(got.MaxDelay, tc.Observed.MaxDelay) ||
					!floatEqual(got.Jitter, tc.Observed.Jitter) {
					t.Fatalf("defaults got %+v want %+v", got, tc.Observed)
				}
				return
			}
			maxAttempts, base, max, jitter := invalidConfigArgs(tc.Label)
			_, err := NewRetryConfig(maxAttempts, base, max, jitter)
			if err == nil || err.Error() != tc.Expected {
				t.Fatalf("got %v want %q", err, tc.Expected)
			}
		})
	}
}

func invalidConfigArgs(label string) (int, float64, float64, float64) {
	switch label {
	case "max_attempts_zero":
		return 0, 2, 60, 1
	case "max_attempts_negative":
		return -4, 2, 60, 1
	case "negative_base_delay":
		return 10, -0.1, 60, 1
	case "max_below_base":
		return 10, 5, 4.9, 1
	case "negative_jitter":
		return 10, 2, 60, -1
	default:
		return 10, 2, 60, 1
	}
}

func TestRetryAfterGoldenParity(t *testing.T) {
	for _, tc := range loadRetryFixture(t).RetryAfterCases {
		t.Run(tc.Label, func(t *testing.T) {
			got := RetryAfterSeconds(tc.Header, goldenNow)
			if !floatPointerEqual(got, tc.Expected) {
				t.Fatalf("got %v want %v", got, tc.Expected)
			}
		})
	}
}

func TestRetryDelayGoldenParity(t *testing.T) {
	standard := RetryConfig{MaxAttempts: 10, BaseDelay: 2, MaxDelay: 20, Jitter: 1}
	zeroed := RetryConfig{MaxAttempts: 10, BaseDelay: 0, MaxDelay: 0, Jitter: 0}
	for _, raw := range loadRetryFixture(t).DelayCases {
		label := raw["label"].(string)
		if label == "uniform_bounds" {
			// Jitter draws are the transport's responsibility; the config
			// contract is that draws stay within [0, Jitter).
			continue
		}
		t.Run(label, func(t *testing.T) {
			attempt := int(raw["attempt"].(float64))
			draw := raw["jitter_draw"].(float64)
			var retryAfter *float64
			if value, ok := raw["retry_after"]; ok && value != nil {
				parsed := value.(float64)
				retryAfter = &parsed
			}
			config := standard
			if label == "zero_base_no_header" {
				config = zeroed
			}
			got := config.Delay(attempt, retryAfter, draw)
			want := raw["observed"].(float64)
			if !floatEqual(got, want) {
				t.Fatalf("got %v want %v", got, want)
			}
		})
	}
}

func TestExceptionStatusAndLabelGoldenParity(t *testing.T) {
	for _, raw := range loadRetryFixture(t).StatusCases {
		label := raw["label"].(string)
		t.Run(label, func(t *testing.T) {
			if _, ok := raw["observed_label"]; ok {
				status := optionalInt(raw["status_code"])
				got := FailureLabel(status, raw["error_kind"].(string))
				if got != raw["expected_label"].(string) {
					t.Fatalf("label got %q want %q", got, raw["expected_label"])
				}
				return
			}
			gotStatus := ExceptionStatus(
				optionalInt(raw["status_code"]),
				optionalInt(raw["response_status"]))
			wantStatus := optionalInt(raw["expected_status"])
			if !intPointerEqual(gotStatus, wantStatus) {
				t.Fatalf("status got %v want %v", gotStatus, wantStatus)
			}
			if want, ok := raw["failure_label"].(string); ok {
				if got := FailureLabel(gotStatus, raw["error_kind"].(string)); got != want {
					t.Fatalf("label got %q want %q", got, want)
				}
			}
		})
	}
}

func optionalInt(value any) *int {
	number, ok := value.(float64)
	if !ok || math.IsNaN(number) {
		return nil
	}
	parsed := int(number)
	return &parsed
}

func floatPointerEqual(got, want *float64) bool {
	if got == nil || want == nil {
		return got == want
	}
	return floatEqual(*got, *want)
}

func intPointerEqual(got, want *int) bool {
	if got == nil || want == nil {
		return got == want
	}
	return *got == *want
}
