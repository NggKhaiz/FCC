// Package pipeline holds cross-package integration tests: they drive the
// composed phase-1 and phase-2 packages through the full 2,005-frame fixture
// and compare every observable outcome against Python-generated expectations.
package pipeline

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"github.com/NggKhaiz/FCC/gocore/delivery"
	"github.com/NggKhaiz/FCC/gocore/sse"
	"github.com/NggKhaiz/FCC/gocore/toolbuf"
)

type pipelineExpectation struct {
	FrameCount         int    `json:"frame_count"`
	ReleasedFrameCount int    `json:"released_frame_count"`
	Progress           int    `json:"progress"`
	UnsafeReason       string `json:"unsafe_reason"`
	RetentionExhausted bool   `json:"retention_exhausted"`
	RetainedBytes      int    `json:"retained_bytes"`
	ClosingEventCount  int    `json:"closing_event_count"`
	HandoffEventCount  int    `json:"handoff_event_count"`
	Snapshot           struct {
		Text         string `json:"text"`
		Thinking     string `json:"thinking"`
		HasCalls     bool   `json:"has_calls"`
		Eligible     bool   `json:"eligible"`
		CanHandoff   bool   `json:"can_handoff"`
		UnsafeReason string `json:"unsafe_reason"`
	} `json:"snapshot"`
}

func loadExpectation(t *testing.T) pipelineExpectation {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join("..", "testdata", "golden_phase2.json"))
	if err != nil {
		t.Fatal(err)
	}
	var wrapper struct {
		Pipeline pipelineExpectation `json:"pipeline"`
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	if err := decoder.Decode(&wrapper); err != nil {
		t.Fatal(err)
	}
	return wrapper.Pipeline
}

func TestComposedPipelineParity(t *testing.T) {
	stream, err := os.ReadFile(filepath.Join("..", "testdata", "stream.txt"))
	if err != nil {
		t.Fatal(err)
	}
	want := loadExpectation(t)

	frameDecoder := sse.NewDecoder()
	frames := frameDecoder.FeedFrames(string(stream))
	frames = append(frames, frameDecoder.FinishFrames()...)
	if len(frames) != want.FrameCount {
		t.Fatalf("frame count: got %d want %d", len(frames), want.FrameCount)
	}

	cache := sse.NewPayloadCache()
	delivered := delivery.New(delivery.WireMessages)
	tools := toolbuf.New(toolbuf.WireMessages, cache)

	released := 0
	for _, frame := range frames {
		released += len(tools.Feed(frame))
		if payload, ok := cache.Get(frame); ok {
			delivered.Observe(payload)
		}
	}

	if released != want.ReleasedFrameCount {
		t.Fatalf("released frames: got %d want %d", released, want.ReleasedFrameCount)
	}
	if delivered.Progress() != want.Progress {
		t.Fatalf("progress: got %d want %d", delivered.Progress(), want.Progress)
	}
	if delivered.UnsafeReason() != want.UnsafeReason {
		t.Fatalf("unsafe reason: got %q want %q", delivered.UnsafeReason(), want.UnsafeReason)
	}
	if delivered.RetentionExhausted() != want.RetentionExhausted {
		t.Fatalf("retention exhausted: got %v want %v",
			delivered.RetentionExhausted(), want.RetentionExhausted)
	}
	if delivered.RetainedBytes() != want.RetainedBytes {
		t.Fatalf("retained bytes: got %d want %d",
			delivered.RetainedBytes(), want.RetainedBytes)
	}
	if len(delivered.ClosingEvents()) != want.ClosingEventCount {
		t.Fatalf("closing events: got %d want %d",
			len(delivered.ClosingEvents()), want.ClosingEventCount)
	}
	if len(delivered.HandoffEvents()) != want.HandoffEventCount {
		t.Fatalf("handoff events: got %d want %d",
			len(delivered.HandoffEvents()), want.HandoffEventCount)
	}

	got := delivered.Snapshot()
	if got.Text != want.Snapshot.Text ||
		got.Thinking != want.Snapshot.Thinking ||
		got.HasCalls != want.Snapshot.HasCalls ||
		got.Eligible != want.Snapshot.Eligible ||
		got.CanHandoff != want.Snapshot.CanHandoff ||
		got.UnsafeReason != want.Snapshot.UnsafeReason {
		t.Fatalf("snapshot mismatch:\n got %+v\nwant %+v", got, want.Snapshot)
	}
}
