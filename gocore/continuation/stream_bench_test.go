package continuation

import (
	"testing"

	"github.com/NggKhaiz/FCC/gocore/delivery"
	"github.com/NggKhaiz/FCC/gocore/wireuuid"
)

func seedDeliveredForBench() *delivery.Delivered {
	d := delivery.New(delivery.WireResponses)
	d.Observe(map[string]any{
		"type": "response.created",
		"response": map[string]any{
			"id":     "resp_bench",
			"status": "in_progress",
			"usage":  map[string]any{"input_tokens": 10},
		},
	})
	d.Observe(map[string]any{
		"type":         "response.output_item.done",
		"output_index": 0,
		"item": map[string]any{
			"id":     "msg_prefix",
			"type":   "message",
			"status": "completed",
			"role":   "assistant",
			"content": []any{
				map[string]any{"type": "output_text", "text": "Hello "},
			},
		},
	})
	return d
}

func continuationReplay() []map[string]any {
	events := []map[string]any{
		{"type": "response.created"},
		{"type": "response.output_item.added", "output_index": 0,
			"item": map[string]any{"id": "msg_replay", "type": "message",
				"content": []any{}, "role": "assistant", "status": "in_progress"}},
		{"type": "response.content_part.added", "output_index": 0, "content_index": 0,
			"item_id": "msg_replay",
			"part":    map[string]any{"type": "output_text", "text": ""}},
	}
	// The replay re-emits the six prefix characters, then 250 fresh ones.
	full := "Hello " + repeatChar("b", 250)
	for i := 0; i < len(full); i++ {
		events = append(events, map[string]any{
			"type": "response.output_text.delta", "output_index": 0,
			"content_index": 0, "item_id": "msg_replay", "delta": full[i : i+1],
		})
	}
	events = append(events,
		map[string]any{"type": "response.output_text.done", "output_index": 0,
			"content_index": 0, "item_id": "msg_replay", "text": full},
		map[string]any{"type": "response.content_part.done", "output_index": 0,
			"content_index": 0, "item_id": "msg_replay",
			"part": map[string]any{"type": "output_text", "text": full}},
		map[string]any{"type": "response.output_item.done", "output_index": 0,
			"item": map[string]any{"id": "msg_replay", "type": "message",
				"role": "assistant", "status": "completed",
				"content": []any{map[string]any{"type": "output_text", "text": full}}}},
		map[string]any{"type": "response.completed",
			"response": map[string]any{"id": "resp_bench", "status": "completed",
				"output": []any{map[string]any{"id": "msg_replay", "type": "message",
					"content": []any{map[string]any{"type": "output_text", "text": full}}}}}},
	)
	return events
}

func BenchmarkStreamPrepare(b *testing.B) {
	replay := continuationReplay()
	namespace := wireuuid.MustV4()
	b.ReportAllocs()
	b.ResetTimer()
	for n := 0; n < b.N; n++ {
		// Delivered is fresh per attempt because Observe mutates progress.
		stream := NewStream(seedDeliveredForBench(),
			WithNamespaces([]wireuuid.UUID{namespace, namespace}))
		stream.Begin(false)
		stream.Boundary()
		for _, event := range replay {
			if _, err := stream.Prepare(event); err != nil {
				b.Fatal(err)
			}
		}
		// Production hands finalize its own deepcopy; the same frame was
		// already prepared and must stay pristine for later iterations.
		terminal := asMap(delivery.DeepCopy(replay[len(replay)-1]))
		if _, err := stream.Finalize(terminal); err != nil {
			b.Fatal(err)
		}
	}
}
