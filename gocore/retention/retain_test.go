package retention

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

type retentionCase struct {
	Value any `json:"value"`
	Size  int `json:"size"`
}

type retentionGolden struct {
	Retention []retentionCase `json:"retention"`
}

func TestJSONByteSizeParity(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join("..", "testdata", "golden.json"))
	if err != nil {
		t.Fatalf("read golden fixture: %v", err)
	}
	var golden retentionGolden
	if err := json.Unmarshal(raw, &golden); err != nil {
		t.Fatalf("decode golden fixture: %v", err)
	}
	for i, tc := range golden.Retention {
		if got := JSONByteSize(tc.Value); got != tc.Size {
			t.Fatalf("case %d (%v): size got %d want %d", i, tc.Value, got, tc.Size)
		}
	}
}

func TestBudgetMatchesRetainSemantics(t *testing.T) {
	budget := &Budget{}
	// Small payloads accumulate without limiting.
	if !budget.Retain(map[string]any{"type": "text", "text": "hello"}) {
		t.Fatal("small payload should be retained")
	}
	if budget.Limited() {
		t.Fatal("budget should not be limited below the threshold")
	}

	// One oversized payload crosses the limit and is rejected.
	huge := map[string]any{"text": string(make([]byte, LimitBytes))}
	if budget.Retain(huge) {
		t.Fatal("oversized payload should be rejected")
	}
	if !budget.Limited() {
		t.Fatal("budget should be limited after crossing the threshold")
	}
	if budget.Bytes() <= LimitBytes {
		t.Fatalf("tally %d should exceed %d", budget.Bytes(), LimitBytes)
	}
	// Once limited, everything is rejected, matching DeliveredResponse._retain.
	if budget.Retain(map[string]any{"type": "ping"}) {
		t.Fatal("limited budget must reject every further payload")
	}
}

func TestEstimatorIsMonotonic(t *testing.T) {
	first := JSONByteSize(map[string]any{"text": "abc"})
	second := JSONByteSize(map[string]any{"text": "abcdef"})
	if second <= first {
		t.Fatalf("longer payload must estimate larger: %d vs %d", second, first)
	}
}
