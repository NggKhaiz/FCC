package sse_test

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/NggKhaiz/FCC/gocore/delivery"
	"github.com/NggKhaiz/FCC/gocore/retention"
	"github.com/NggKhaiz/FCC/gocore/sse"
	"github.com/NggKhaiz/FCC/gocore/toolbuf"
)

var failureEvents = map[string]struct{}{
	"error":           {},
	"response.error":  {},
	"response.failed": {},
}

func BenchmarkHotPath(b *testing.B) {
	raw, err := os.ReadFile(filepath.Join("..", "testdata", "stream.txt"))
	if err != nil {
		b.Fatal(err)
	}
	stream := string(raw)
	b.ReportAllocs()
	b.ResetTimer()
	for range b.N {
		decoder := sse.NewDecoder()
		frames := decoder.FeedFrames(stream)
		frames = append(frames, decoder.FinishFrames()...)
		cache := sse.NewPayloadCache()
		budget := &retention.Budget{}
		delivered := delivery.New(delivery.WireMessages)
		tools := toolbuf.New(toolbuf.WireMessages, cache)
		for _, frame := range frames {
			_ = sse.EventName(frame)
			if payload, ok := cache.Get(frame); ok {
				budget.Retain(payload)
				delivered.Observe(payload)
			}
			_ = sse.FailureReason(frame, failureEvents)
			_ = tools.Feed(frame)
		}
		_ = delivered.Snapshot()
		_ = delivered.HandoffEvents()
	}
}
