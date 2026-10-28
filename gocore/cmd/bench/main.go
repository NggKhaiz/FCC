// Command bench exercises the Go hot path on the same 2005-frame SSE fixture
// used by the Python benchmark, so the two implementations can be compared
// like for like: framing, event naming, cached payload parsing, failure
// scanning, and retention accounting per frame.
package main

import (
	"fmt"
	"os"
	"runtime"
	"time"

	"github.com/NggKhaiz/FCC/gocore/delivery"
	"github.com/NggKhaiz/FCC/gocore/retention"
	"github.com/NggKhaiz/FCC/gocore/sse"
	"github.com/NggKhaiz/FCC/gocore/toolbuf"
)

const iterations = 100

func main() {
	raw, err := os.ReadFile("testdata/stream.txt")
	if err != nil {
		fmt.Fprintf(os.Stderr, "read fixture: %v\n", err)
		os.Exit(1)
	}
	stream := string(raw)
	failureEvents := map[string]struct{}{
		"error":           {},
		"response.error":  {},
		"response.failed": {},
	}

	// Warm up so first-run code paths and allocator behavior settle.
	for range 5 {
		runPipeline(stream, failureEvents)
	}

	var before, after runtime.MemStats
	runtime.GC()
	runtime.ReadMemStats(&before)
	start := time.Now()
	for range iterations {
		runPipeline(stream, failureEvents)
	}
	elapsed := time.Since(start)
	runtime.ReadMemStats(&after)

	// Live heap after a forced GC: the steady-state footprint of one pipeline
	// run, comparable to a Python tracemalloc peak rather than to cumulative
	// allocation churn.
	var live runtime.MemStats
	runtime.GC()
	runtime.ReadMemStats(&live)

	frames := countFrames(stream)
	perRequest := elapsed / iterations
	fmt.Printf("frames/request:   %d\n", frames)
	fmt.Printf("total:            %v over %d requests\n", elapsed, iterations)
	fmt.Printf("per-request:      %.2f ms\n", float64(perRequest.Microseconds())/1000)
	fmt.Printf("throughput:       %.1f req/s\n", float64(iterations)/elapsed.Seconds())
	fmt.Printf("allocs/request:   %d\n", (after.Mallocs-before.Mallocs)/iterations)
	fmt.Printf("alloc churn/req:  %.1f KiB\n",
		float64(after.TotalAlloc-before.TotalAlloc)/float64(iterations)/1024)
	fmt.Printf("live heap after GC: %.1f KiB\n", float64(live.HeapAlloc)/1024)
}

func runPipeline(stream string, failureEvents map[string]struct{}) int {
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
	return len(frames)
}

func countFrames(stream string) int {
	decoder := sse.NewDecoder()
	frames := decoder.FeedFrames(stream)
	return len(frames) + len(decoder.FinishFrames())
}
