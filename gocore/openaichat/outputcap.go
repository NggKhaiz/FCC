package openaichat

import (
	"maps"
	"regexp"
	"strconv"
	"strings"

	"github.com/NggKhaiz/FCC/gocore/providerpolicy"
)

// Body keys that carry the output-token budget across OpenAI-compatible
// policies, in the order the reference inspects them.
var outputTokenFields = []string{"max_completion_tokens", "max_tokens"}

var outputTokenFieldSet = map[string]struct{}{
	"max_completion_tokens": {},
	"max_tokens":            {},
}

var structuredTextFields = []string{"message", "detail", "error"}

var structuredContainerFields = []string{"error", "errors", "detail", "details"}

// The reference allows optional backtick/quote wrapping around the field
// name and the cap number. RE2 cannot express the reference's lookaround
// guards around those quotes, so parseCap strips adjacent quotes first;
// gluing a quote to a word then collapses into an ordinary word, which
// the \b boundaries reject. After normalization the patterns are plain.
var quotedFieldPattern = regexp.MustCompile("[`'\"]*(max_completion_tokens|max_tokens)[`'\"]*")
var quotedCapPattern = regexp.MustCompile("[`'\"]*(\\d+)[`'\"]*")

const capValuePattern = "(\\d+)"

const outputTokenFieldPattern = "\\b(?:max_completion_tokens|max_tokens)\\b"

var fieldBoundCapPatterns = []*regexp.Regexp{
	regexp.MustCompile(
		`range\s+of\s+` + outputTokenFieldPattern + `\s+should\s+be\s+` +
			`\[\s*1\s*,\s*` + capValuePattern + `\s*\]`),
	regexp.MustCompile(
		outputTokenFieldPattern + `\s*(?::\s*)?` +
			`(?:` +
			`(?:(?:must|should)\s+be\s+)?(?:less|smaller)\s+than\s+or\s+equal\s+to` +
			`|(?:(?:must|should)\s+be\s*)?<=` +
			`|(?:(?:must|should)\s+be\s+)?at\s+most` +
			`|(?:must|should)\s+not\s+exceed` +
			`|maximum(?:\s+allowed)?(?:\s+value)?\s+(?:is|of)` +
			`)\s*` + capValuePattern),
	regexp.MustCompile(
		`maximum(?:\s+allowed)?(?:\s+value)?\s+for\s+` +
			outputTokenFieldPattern + `\s+is\s+` + capValuePattern),
	regexp.MustCompile(
		`maximum(?:\s+allowed)?(?:\s+value)?\s+of\s+` +
			capValuePattern + `\s+for\s+` + outputTokenFieldPattern),
}

// ParseOutputTokenCap returns the allowed output-token maximum named in a
// 400 rejection, or nil when the error does not bind such a cap.
func ParseOutputTokenCap(err *providerpolicy.ProviderError) *int {
	if !isCapBadRequest(err) {
		return nil
	}
	for _, text := range capErrorTexts(err) {
		if cap := parseCap(text); cap != nil {
			return cap
		}
	}
	return nil
}

// ClampOutputTokens returns a shallow clone with output-token fields
// clamped to cap, or nil when nothing exceeds the cap.
func ClampOutputTokens(body map[string]any, cap int) map[string]any {
	var clamped map[string]any
	for _, field := range outputTokenFields {
		value := asInt(body[field])
		if value != nil && *value > cap {
			if clamped == nil {
				clamped = maps.Clone(body)
			}
			clamped[field] = cap
		}
	}
	return clamped
}

func isCapBadRequest(err *providerpolicy.ProviderError) bool {
	return err.Kind == "BadRequestError" || (err.HasStatus && err.StatusCode == 400)
}

func parseCap(rawText string) *int {
	text := quotedCapPattern.ReplaceAllString(
		quotedFieldPattern.ReplaceAllString(rawText, "$1"), "$1")
	for _, pattern := range fieldBoundCapPatterns {
		match := pattern.FindStringSubmatch(text)
		if match == nil {
			continue
		}
		cap, err := strconv.Atoi(match[1])
		if err == nil && cap > 0 {
			return &cap
		}
	}
	return nil
}

// capErrorTexts keeps unstructured text separate from structured
// validation messages, matching the reference _error_texts helper.
func capErrorTexts(err *providerpolicy.ProviderError) []string {
	var texts []string
	switch body := err.Body.(type) {
	case nil:
		texts = []string{err.Message}
	case string:
		texts = []string{body}
	case *providerpolicy.JSONObject, map[string]any, []any:
		texts = structuredErrorTexts(err.Body)
	default:
		texts = nil
	}
	lowered := make([]string, len(texts))
	for i, text := range texts {
		lowered[i] = strings.ToLower(text)
	}
	return lowered
}

// structuredErrorTexts walks structured error bodies without letting text
// nested below an explicitly scoped validation error escape that scope.
func structuredErrorTexts(body any) []string {
	texts := make([]string, 0)
	pending := []any{body}
	for len(pending) > 0 {
		value := pending[len(pending)-1]
		pending = pending[:len(pending)-1]

		if items, ok := value.([]any); ok {
			for _, item := range items {
				switch item.(type) {
				case string:
					texts = append(texts, item.(string))
				case *providerpolicy.JSONObject, map[string]any, []any:
					pending = append(pending, item)
				}
			}
			continue
		}

		obj := asObject(value)
		if obj == nil {
			continue
		}
		paramValue := obj["param"]
		outputField := normalizeOutputField(paramValue)
		hasParam := paramValue != nil
		messages := make([]string, 0)
		for _, field := range structuredTextFields {
			if message, ok := obj[field].(string); ok {
				messages = append(messages, message)
			}
		}
		if outputField != "" {
			for _, message := range messages {
				texts = append(texts, outputField+" "+message)
			}
		}
		if hasParam {
			continue
		}
		texts = append(texts, messages...)
		for _, field := range structuredContainerFields {
			switch nested := obj[field].(type) {
			case *providerpolicy.JSONObject, map[string]any, []any:
				pending = append(pending, nested)
			}
		}
	}
	return texts
}

func normalizeOutputField(value any) string {
	text, ok := value.(string)
	if !ok {
		return ""
	}
	field := strings.ToLower(strings.Trim(text, "`'\" "))
	if _, ok := outputTokenFieldSet[field]; ok {
		return field
	}
	return ""
}

// asObject accepts both decoded fixture maps and ordered JSON objects.
func asObject(value any) map[string]any {
	switch typed := value.(type) {
	case map[string]any:
		return typed
	case *providerpolicy.JSONObject:
		return typed.Fields
	default:
		return nil
	}
}
