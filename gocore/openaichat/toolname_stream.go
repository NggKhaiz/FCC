package openaichat

import "strings"

// MergeToolName combines incremental and cumulative upstream name fragments.
func MergeToolName(existing, incoming string) string {
	if existing == "" || strings.HasPrefix(incoming, existing) {
		return incoming
	}
	if strings.HasPrefix(existing, incoming) {
		return existing
	}
	return existing + incoming
}

// DecodeStreamedToolName translates an upstream fragment. The second return
// value is false when the fragment is an incomplete alias and was buffered.
// A nil buffer disables buffering; a nil codec disables translation.
func DecodeStreamedToolName(incoming string, index int, codec *ToolNameCodec, buffers map[int]string) (string, bool) {
	if codec == nil || !codec.HasAliases() {
		return incoming, true
	}
	if buffers == nil {
		return codec.Decode(incoming), true
	}
	combined := MergeToolName(buffers[index], incoming)
	if codec.IsAlias(combined) {
		delete(buffers, index)
		return codec.Decode(combined), true
	}
	if codec.IsAliasPrefix(combined) {
		buffers[index] = combined
		return "", false
	}
	delete(buffers, index)
	return codec.Decode(combined), true
}
