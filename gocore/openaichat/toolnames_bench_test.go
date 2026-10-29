package openaichat

import (
	"strings"
	"testing"
)

var benchmarkCodec *ToolNameCodec
var benchmarkPrefix bool

func BenchmarkToolNameCodec(b *testing.B) {
	names := []string{"read_file", "mcp.server/" + strings.Repeat("long-name", 12), "工具名称"}
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		benchmarkCodec = NewToolNameCodec(names)
	}
}

func BenchmarkToolNameAliasPrefix(b *testing.B) {
	codec := NewToolNameCodec([]string{"mcp.server/" + strings.Repeat("long-name", 12), "工具名称"})
	prefix := codec.Encode("工具名称")[:8]
	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		benchmarkPrefix = codec.IsAliasPrefix(prefix)
	}
}
