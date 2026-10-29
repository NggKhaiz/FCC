package openaichat

import "testing"

func BenchmarkChatStreamOutputText(b *testing.B) {
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		output := NewChatStreamOutput(20, recordingChatSink{})
		output.StartEvents()
		output.EnsureTextBlock()
		for j := 0; j < 100; j++ {
			output.EmitTextDelta("example output fragment")
		}
		output.FinishSuccess("end_turn", nil)
	}
}
