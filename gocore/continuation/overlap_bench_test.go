package continuation

import "testing"

// buildDeltaStream synthesizes a responses-wire style replay where the first
// 64 output characters overlap the already-observed answer prefix, followed
// by 192 new characters delivered as one-char deltas plus lifecycle frames.
func buildDeltaStream() ([]map[string]any, string) {
	const overlap = 64
	const fresh = 192
	prefix := repeatChar("a", overlap)
	full := prefix + repeatChar("b", fresh)

	events := []map[string]any{
		{"type": "response.created"},
		{"type": "response.output_item.added", "output_index": 0,
			"item": map[string]any{"id": "msg_piece_0", "type": "message",
				"content": []any{}, "role": "assistant", "status": "in_progress"}},
		{"type": "response.content_part.added", "output_index": 0, "content_index": 0,
			"item_id": "msg_piece_0", "part": map[string]any{"type": "output_text", "text": ""}},
	}
	for i := 0; i < len(full); i++ {
		events = append(events, map[string]any{
			"type": "response.output_text.delta", "output_index": 0,
			"content_index": 0, "item_id": "msg_piece_0", "delta": full[i : i+1],
		})
	}
	events = append(events,
		map[string]any{"type": "response.output_text.done", "output_index": 0,
			"content_index": 0, "item_id": "msg_piece_0", "text": full},
		map[string]any{"type": "response.content_part.done", "output_index": 0,
			"content_index": 0, "item_id": "msg_piece_0",
			"part": map[string]any{"type": "output_text", "text": full}},
		map[string]any{"type": "response.output_item.done", "output_index": 0,
			"item": map[string]any{"id": "msg_piece_0", "type": "message", "role": "assistant",
				"status": "completed",
				"content": []any{map[string]any{"type": "output_text",
					"text": full, "annotations": []any{}}}}},
		map[string]any{"type": "response.completed",
			"response": map[string]any{"output": []any{
				map[string]any{"id": "msg_piece_0", "type": "message", "role": "assistant",
					"status": "completed",
					"content": []any{map[string]any{"type": "output_text",
						"text": full, "annotations": []any{}}}},
			}}},
	)
	return events, prefix
}

func repeatChar(ch string, n int) string {
	out := make([]byte, 0, n)
	for i := 0; i < n; i++ {
		out = append(out, ch[0])
	}
	return string(out)
}

func BenchmarkOverlapFeed(b *testing.B) {
	stream, prefix := buildDeltaStream()
	b.ReportAllocs()
	b.ResetTimer()
	for n := 0; n < b.N; n++ {
		o := NewOverlap(prefix)
		for _, event := range stream {
			if _, err := o.Feed(event); err != nil {
				b.Fatal(err)
			}
		}
	}
}
