package openaichat

import (
	"fmt"
	"sort"
	"strings"
)

// canonicalBodyKeys names the chat-completion fields FCC owns. Caller
// extra_body entries must never replace them.
var canonicalBodyKeys = map[string]struct{}{
	"model":                  {},
	"messages":               {},
	"tools":                  {},
	"tool_choice":            {},
	"stream":                 {},
	"max_tokens":             {},
	"max_completion_tokens":  {},
	"temperature":            {},
	"top_p":                  {},
	"metadata":               {},
	"stop":                   {},
	"stop_sequences":         {},
	"stream_options":         {},
	"reasoning":              {},
	"reasoning_effort":       {},
	"reasoning_tokens":       {},
	"thinking":               {},
	"thinking_budget_tokens": {},
	"enable_thinking":        {},
	"thinking_budget":        {},
}

// reasoningBodyKeys is the subset that controls provider reasoning; the
// profile's reasoning translation stays authoritative over caller extras.
var reasoningBodyKeys = map[string]struct{}{
	"reasoning":              {},
	"reasoning_effort":       {},
	"reasoning_tokens":       {},
	"thinking":               {},
	"thinking_budget_tokens": {},
	"enable_thinking":        {},
	"thinking_budget":        {},
}

// ValidateExtraBody rejects extras that would replace FCC-owned
// chat-completion fields. The message reproduces the Python reference
// exactly, including Python's sorted list repr with single quotes.
func ValidateExtraBody(extra map[string]any) error {
	return rejectOverrides(extra, canonicalBodyKeys,
		"extra_body must not override canonical request fields:")
}

// ValidateExtraBodyReasoning rejects extras that would replace the
// provider reasoning translation.
func ValidateExtraBodyReasoning(extra map[string]any) error {
	return rejectOverrides(extra, reasoningBodyKeys,
		"extra_body must not override reasoning fields:")
}

func rejectOverrides(extra map[string]any, allowed map[string]struct{}, prefix string) error {
	bad := make([]string, 0)
	for key := range extra {
		if _, ok := allowed[key]; ok {
			bad = append(bad, key)
		}
	}
	if len(bad) == 0 {
		return nil
	}
	sort.Strings(bad)
	quoted := make([]string, len(bad))
	for i, key := range bad {
		quoted[i] = "'" + key + "'"
	}
	return fmt.Errorf("%s [%s]", prefix, strings.Join(quoted, ", "))
}
