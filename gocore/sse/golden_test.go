package sse

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

func sameStrings(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

type framingCase struct {
	Chunks []string `json:"chunks"`
	Frames []string `json:"frames"`
	Finish []string `json:"finish"`
}

type frameCase struct {
	Frame     string `json:"frame"`
	Event     string `json:"event"`
	Failure   string `json:"failure"`
	DataText  string `json:"data_text"`
	HasObject bool   `json:"has_object_data"`
}

type goldenFile struct {
	FailureEvents []string      `json:"failure_events"`
	Framing       []framingCase `json:"framing"`
	Frames        []frameCase   `json:"frames"`
}

func loadGolden(t *testing.T) goldenFile {
	t.Helper()
	path := filepath.Join("..", "testdata", "golden.json")
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read golden fixture: %v", err)
	}
	var golden goldenFile
	if err := json.Unmarshal(raw, &golden); err != nil {
		t.Fatalf("decode golden fixture: %v", err)
	}
	return golden
}

func failureSet(events []string) map[string]struct{} {
	out := make(map[string]struct{}, len(events))
	for _, event := range events {
		out[event] = struct{}{}
	}
	return out
}

func TestFramingParity(t *testing.T) {
	golden := loadGolden(t)
	for _, fastPath := range []bool{true, false} {
		mode := "fast"
		if !fastPath {
			mode = "scanner"
		}
		t.Run(mode, func(t *testing.T) {
			for i, tc := range golden.Framing {
				decoder := NewDecoder()
				decoder.SetFastPath(fastPath)
				var got []string
				for _, chunk := range tc.Chunks {
					got = append(got, decoder.FeedFrames(chunk)...)
				}
				got = append(got, decoder.FinishFrames()...)
				want := append(append([]string{}, tc.Frames...), tc.Finish...)
				if !sameStrings(got, want) {
					t.Fatalf("case %d: frame mismatch\ngot:  %q\nwant: %q", i, got, want)
				}
			}
		})
	}
}

func TestEventNameParity(t *testing.T) {
	golden := loadGolden(t)
	for i, tc := range golden.Frames {
		if got := EventName(tc.Frame); got != tc.Event {
			t.Fatalf("case %d: event name got %q want %q", i, got, tc.Event)
		}
	}
}

func TestFailureReasonParity(t *testing.T) {
	golden := loadGolden(t)
	failures := failureSet(golden.FailureEvents)
	for i, tc := range golden.Frames {
		if got := FailureReason(tc.Frame, failures); got != tc.Failure {
			t.Fatalf("case %d: failure reason got %q want %q", i, got, tc.Failure)
		}
	}
}

func dataTextOf(frame string) string {
	var parts []string
	for _, line := range splitLineBreaks(frame) {
		if len(line) >= 5 && line[:5] == "data:" {
			parts = append(parts, trimLeadingSpaces(line[5:]))
		}
	}
	return joinWithNewline(parts)
}

func joinWithNewline(parts []string) string {
	if len(parts) == 0 {
		return ""
	}
	out := parts[0]
	for _, part := range parts[1:] {
		out += "\n" + part
	}
	return out
}

func TestFrameDataParity(t *testing.T) {
	golden := loadGolden(t)
	checkedNumbers := false
	for i, tc := range golden.Frames {
		if got := dataTextOf(tc.Frame); got != tc.DataText {
			t.Fatalf("case %d: data text got %q want %q", i, got, tc.DataText)
		}
		payload, ok := FrameData(tc.Frame)
		if ok != tc.HasObject {
			t.Fatalf("case %d: object payload got %v want %v", i, ok, tc.HasObject)
		}
		if !ok {
			continue
		}
		if price, exists := payload["price"]; exists {
			number, isNumber := price.(json.Number)
			if !isNumber || number.String() != "0.00000001" {
				t.Fatalf("case %d: decimal precision lost: %#v", i, price)
			}
			checkedNumbers = true
		}
	}
	if !checkedNumbers {
		t.Fatal("fixture no longer covers decimal-preserving payloads")
	}
}

func TestPayloadCacheFIFO(t *testing.T) {
	cache := NewPayloadCache()
	first, ok := cache.Get("event: message_start\ndata: {\"type\":\"message_start\"}\n\n")
	if !ok || first["type"] != "message_start" {
		t.Fatalf("unexpected first parse: %#v %v", first, ok)
	}
	if cache.Len() != 1 {
		t.Fatalf("cache length = %d, want 1", cache.Len())
	}
	// Repeated reads must return the same entry without growing the cache.
	for range 10 {
		cache.Get("event: message_start\ndata: {\"type\":\"message_start\"}\n\n")
	}
	if cache.Len() != 1 {
		t.Fatalf("cache length = %d, want 1 after repeated reads", cache.Len())
	}
	for i := range 100 {
		cache.Get("event: delta\ndata: {\"seq\":" + itoa(i) + "}\n\n")
	}
	if cache.Len() != 64 {
		t.Fatalf("cache length = %d, want FIFO cap of 64", cache.Len())
	}
	cache.Clear()
	if cache.Len() != 0 {
		t.Fatalf("cache length = %d after clear", cache.Len())
	}
}

func itoa(i int) string {
	if i == 0 {
		return "0"
	}
	var buf [20]byte
	pos := len(buf)
	for i > 0 {
		pos--
		buf[pos] = byte('0' + i%10)
		i /= 10
	}
	return string(buf[pos:])
}
