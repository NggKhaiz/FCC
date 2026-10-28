package openaichat

import (
	"encoding/json"
	"maps"
	"strings"

	"github.com/NggKhaiz/FCC/gocore/providerpolicy"
)

var usageOptionKeys = []string{"stream_options", "include_usage"}

var usageRejectionWords = []string{
	"unsupported",
	"not supported",
	"unknown",
	"unrecognized",
	"unexpected",
	"invalid",
	"extra",
	"forbidden",
	"not permitted",
}

// RequestStreamUsage asks an OpenAI-compatible streaming endpoint for its
// final usage chunk. An existing stream_options dict gains include_usage;
// any non-object value is left untouched, matching the reference.
func RequestStreamUsage(body map[string]any) {
	option, ok := body["stream_options"]
	if !ok || option == nil {
		body["stream_options"] = map[string]any{"include_usage": true}
		return
	}
	if options, ok := option.(map[string]any); ok {
		options["include_usage"] = true
	}
}

// CloneWithoutStreamUsage returns a shallow clone with only include_usage
// removed from stream_options, or nil when there is nothing to remove.
func CloneWithoutStreamUsage(body map[string]any) map[string]any {
	option, ok := body["stream_options"].(map[string]any)
	if !ok {
		return nil
	}
	if _, ok := option["include_usage"]; !ok {
		return nil
	}
	retryBody := maps.Clone(body)
	retryOptions := maps.Clone(option)
	delete(retryOptions, "include_usage")
	if len(retryOptions) == 0 {
		delete(retryBody, "stream_options")
	} else {
		retryBody["stream_options"] = retryOptions
	}
	return retryBody
}

// IsStreamUsageRejection reports whether upstream rejected the optional
// streamed-usage request: a bad-request-like error whose text names one of
// the usage option keys together with a rejection word.
func IsStreamUsageRejection(err *providerpolicy.ProviderError) bool {
	if !isBadRequestLike(err) {
		return false
	}
	text := errorText(err)
	if !containsAny(text, usageOptionKeys) {
		return false
	}
	return containsAny(text, usageRejectionWords)
}

func isBadRequestLike(err *providerpolicy.ProviderError) bool {
	if err.Kind == "BadRequestError" {
		return true
	}
	if err.HasStatus && (err.StatusCode == 400 || err.StatusCode == 422) {
		return true
	}
	return err.HasResponseStatus &&
		(err.ResponseStatus == 400 || err.ResponseStatus == 422)
}

// errorText joins the message, the JSON-encoded body and the response text,
// then lowercases the result, mirroring the reference _error_text helper.
func errorText(err *providerpolicy.ProviderError) string {
	parts := []string{err.Message}
	if err.Body != nil {
		if encoded, marshalErr := json.Marshal(plainValue(err.Body)); marshalErr == nil {
			parts = append(parts, string(encoded))
		}
	}
	if err.ResponseText != "" {
		parts = append(parts, err.ResponseText)
	}
	return strings.ToLower(strings.Join(parts, " "))
}

// UsageInt extracts an integer usage field from a decoded JSON mapping.
// Booleans, floats, strings and nulls are rejected, matching the reference
// isinstance(value, int) and not isinstance(value, bool) guard.
func UsageInt(info map[string]any, key string) *int {
	if info == nil {
		return nil
	}
	return asInt(info[key])
}

// NestedUsageInt extracts one nested integer from a decoded usage mapping.
func NestedUsageInt(info map[string]any, parent, key string) *int {
	if info == nil {
		return nil
	}
	details, ok := info[parent].(map[string]any)
	if !ok {
		return nil
	}
	return asInt(details[key])
}

func asInt(value any) *int {
	switch typed := value.(type) {
	case int:
		return &typed
	case int64:
		number := int(typed)
		return &number
	case json.Number:
		parsed, err := typed.Int64()
		if err != nil {
			return nil
		}
		number := int(parsed)
		return &number
	default:
		return nil
	}
}

func containsAny(haystack string, needles []string) bool {
	for _, needle := range needles {
		if strings.Contains(haystack, needle) {
			return true
		}
	}
	return false
}

// plainValue converts ordered JSON objects into plain maps so the standard
// encoder can serialise them; key order is irrelevant for substring checks.
func plainValue(value any) any {
	switch typed := value.(type) {
	case *providerpolicy.JSONObject:
		return orderedToPlain(typed)
	case map[string]any:
		return orderedToPlainMap(typed)
	case []any:
		plain := make([]any, len(typed))
		for i, item := range typed {
			plain[i] = plainValue(item)
		}
		return plain
	default:
		return value
	}
}

func orderedToPlain(obj *providerpolicy.JSONObject) map[string]any {
	plain := make(map[string]any, len(obj.Fields))
	for key, value := range obj.Fields {
		plain[key] = plainValue(value)
	}
	return plain
}

func orderedToPlainMap(source map[string]any) map[string]any {
	plain := make(map[string]any, len(source))
	for key, value := range source {
		plain[key] = plainValue(value)
	}
	return plain
}
