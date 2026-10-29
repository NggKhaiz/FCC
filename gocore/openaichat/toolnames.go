package openaichat

import (
	"crypto/sha256"
	"encoding/hex"
	"maps"
	"sort"
	"strconv"
	"strings"
)

const maxToolNameLength = 64

// ToolNameCodec maps non-portable client tool names to deterministic,
// reversible OpenAI-compatible aliases. Construct one per request, including
// declared tools, forced choices, and historical assistant tool calls.
type ToolNameCodec struct {
	originalToAlias map[string]string
	aliasToOriginal map[string]string
	unchanged       map[string]struct{}
}

// NewToolNameCodec builds order-independent aliases without shadowing any
// portable names present in the request. Empty names are left unchanged.
func NewToolNameCodec(names []string) *ToolNameCodec {
	codec := &ToolNameCodec{
		originalToAlias: make(map[string]string),
		aliasToOriginal: make(map[string]string),
		unchanged:       make(map[string]struct{}),
	}
	unique := make(map[string]struct{}, len(names))
	for _, name := range names {
		if name != "" {
			unique[name] = struct{}{}
		}
	}
	var nonportable []string
	reserved := make(map[string]struct{}, len(unique))
	for name := range unique {
		if portableToolName(name) {
			codec.unchanged[name] = struct{}{}
			reserved[name] = struct{}{}
		} else {
			nonportable = append(nonportable, name)
		}
	}
	sort.Strings(nonportable)
	for _, name := range nonportable {
		alias := uniqueToolAlias(name, reserved)
		reserved[alias] = struct{}{}
		codec.originalToAlias[name] = alias
		codec.aliasToOriginal[alias] = name
	}
	return codec
}

func portableToolName(name string) bool {
	if len(name) == 0 || len(name) > maxToolNameLength {
		return false
	}
	for i := 0; i < len(name); i++ {
		if !toolNameByte(name[i]) {
			return false
		}
	}
	return true
}

func toolNameByte(c byte) bool {
	return c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c >= '0' && c <= '9' || c == '_' || c == '-'
}

func uniqueToolAlias(name string, reserved map[string]struct{}) string {
	var readable strings.Builder
	invalid := false
	for i := 0; i < len(name); i++ {
		if toolNameByte(name[i]) {
			readable.WriteByte(name[i])
			invalid = false
		} else if !invalid {
			readable.WriteByte('_')
			invalid = true
		}
	}
	prefix := strings.Trim(readable.String(), "_-")
	if prefix == "" {
		prefix = "tool"
	}
	if len(prefix) > 47 {
		prefix = prefix[:47]
	}
	prefix = strings.TrimRight(prefix, "_-")
	if prefix == "" {
		prefix = "tool"
	}
	for attempt := 0; ; attempt++ {
		input := name
		if attempt > 0 {
			input += "\x00" + strconv.Itoa(attempt)
		}
		digest := sha256.Sum256([]byte(input))
		alias := prefix + "_" + hex.EncodeToString(digest[:8])
		if _, exists := reserved[alias]; !exists {
			return alias
		}
	}
}

// HasAliases reports whether translation is required.
func (c *ToolNameCodec) HasAliases() bool { return len(c.originalToAlias) != 0 }

// Encode returns the wire name, leaving unknown names untouched.
func (c *ToolNameCodec) Encode(name string) string {
	if alias, ok := c.originalToAlias[name]; ok {
		return alias
	}
	return name
}

// Decode returns the original client name, leaving unknown names untouched.
func (c *ToolNameCodec) Decode(name string) string {
	if original, ok := c.aliasToOriginal[name]; ok {
		return original
	}
	return name
}

// IsAlias reports whether value is a complete generated alias.
func (c *ToolNameCodec) IsAlias(value string) bool {
	_, ok := c.aliasToOriginal[value]
	return ok
}

// IsUnchangedName reports whether value was a portable request name.
func (c *ToolNameCodec) IsUnchangedName(value string) bool {
	_, ok := c.unchanged[value]
	return ok
}

// IsAliasPrefix reports whether value is a nonempty, incomplete alias prefix.
func (c *ToolNameCodec) IsAliasPrefix(value string) bool {
	if value == "" {
		return false
	}
	for alias := range c.aliasToOriginal {
		if alias != value && strings.HasPrefix(alias, value) {
			return true
		}
	}
	return false
}

// EncodeChatToolNames modifies canonical Chat locations in place, matching
// the Python boundary's shallow-copy behavior for an encoded tool_choice.
func EncodeChatToolNames(body map[string]any, codec *ToolNameCodec) {
	if codec == nil || !codec.HasAliases() {
		return
	}
	if tools, ok := body["tools"].([]any); ok {
		for _, tool := range tools {
			encodeToolFunction(tool, codec)
		}
	}
	if messages, ok := body["messages"].([]any); ok {
		for _, raw := range messages {
			message, ok := raw.(map[string]any)
			if !ok {
				continue
			}
			if calls, ok := message["tool_calls"].([]any); ok {
				for _, call := range calls {
					encodeToolFunction(call, codec)
				}
			}
		}
	}
	choice, ok := body["tool_choice"].(map[string]any)
	if !ok {
		return
	}
	function, ok := choice["function"].(map[string]any)
	if !ok {
		return
	}
	name, ok := function["name"].(string)
	if !ok {
		return
	}
	encoded := codec.Encode(name)
	if encoded != name {
		newChoice := maps.Clone(choice)
		newFunction := maps.Clone(function)
		newFunction["name"] = encoded
		newChoice["function"] = newFunction
		body["tool_choice"] = newChoice
	}
}

func encodeToolFunction(value any, codec *ToolNameCodec) {
	object, ok := value.(map[string]any)
	if !ok {
		return
	}
	function, ok := object["function"].(map[string]any)
	if !ok {
		return
	}
	if name, ok := function["name"].(string); ok {
		function["name"] = codec.Encode(name)
	}
}
