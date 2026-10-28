package providerpolicy

import (
	"regexp"
	"strconv"
	"strings"
)

// ErrorDetailDisplayCapBytes matches ERROR_DETAIL_DISPLAY_CAP_BYTES.
const ErrorDetailDisplayCapBytes = 16_384

// maxCauseChainDepth matches _MAX_CAUSE_CHAIN_DEPTH.
const maxCauseChainDepth = 4

var (
	// keyPrefixPattern captures an authorization-style key, its optional
	// quotes, the separator and the opening value quote. RE2 does not
	// backtrack optional capturing groups the way Python does, so the key
	// quotes use character classes; closing-quote pairing is enforced by
	// redactKeyedSecrets manually (RE2 has no backreferences either).
	keyPrefixPattern = regexp.MustCompile(
		`(?i)['"]?(authorization|api[_-]?key|access[_-]?token|refresh[_-]?token|` +
			`token|client[_-]?secret|secret|password)['"]?\s*[:=]\s*(['"]?)`)
	bearerPattern = regexp.MustCompile(`(?i)(bearer\s+)[^\s,;]+`)
	// The Python reference guards tokens with a negative lookahead; RE2 lacks
	// lookarounds, so the trailing alphanumeric guard runs in code instead.
	tokenPattern = regexp.MustCompile(
		`(?i)(?:sk-[a-z0-9._-]{8,}|nvapi-[a-z0-9._-]{8,}|hf_[a-z0-9_-]{8,}|` +
			`gsk_[a-z0-9_-]{8,}|github_pat_[a-z0-9_]{8,}|gh[pousr]_[a-z0-9]{8,}|` +
			`AIza[a-z0-9_-]{20,})`)
	schemePattern = regexp.MustCompile(`(?i)^(bearer|basic)(\s+)`)
)

// RedactSensitiveErrorText mirrors redact_sensitive_error_text: recognizable
// credentials are replaced while the surrounding diagnostic context survives.
func RedactSensitiveErrorText(text string) string {
	text = redactKeyedSecrets(text)
	text = bearerPattern.ReplaceAllString(text, "${1}<redacted>")
	text = redactBareTokens(text)
	return text
}

// redactKeyedSecrets handles the two key/value patterns whose Python versions
// rely on a backreference between the opening and closing value quotes.
func redactKeyedSecrets(text string) string {
	matches := keyPrefixPattern.FindAllStringSubmatchIndex(text, -1)
	if len(matches) == 0 {
		return text
	}
	var builder strings.Builder
	prev := 0
	for _, match := range matches {
		start, end := match[0], match[1]
		// Submatch layout: pair [4,5] is group 2 (the opening value quote).
		valueQuote := text[match[4]:match[5]]
		cursor := end
		// Skip an optional bearer/basic scheme plus its required whitespace.
		// loc[5] is the end of the (\s+) group, not loc[3] (the scheme word).
		if loc := schemePattern.FindStringSubmatchIndex(text[cursor:]); loc != nil && loc[0] == 0 {
			cursor += loc[5]
		}
		valueStart := cursor
		for cursor < len(text) && !isValueDelimiter(text[cursor]) {
			cursor++
		}
		if cursor == valueStart {
			// Python requires at least one secret character; leave as-is.
			continue
		}
		builder.WriteString(text[prev:start])
		builder.WriteString(text[start:end])
		builder.WriteString("<redacted>")
		builder.WriteString(valueQuote)
		if valueQuote != "" && cursor < len(text) && string(text[cursor]) == valueQuote {
			cursor++ // consume the paired closing quote
		}
		prev = cursor
	}
	builder.WriteString(text[prev:])
	return builder.String()
}

func isValueDelimiter(b byte) bool {
	switch b {
	case '"', '\'', ' ', '\t', '\n', '\r', ',', ';', '&', '}', ']':
		return true
	}
	return false
}

func redactBareTokens(text string) string {
	matches := tokenPattern.FindAllStringIndex(text, -1)
	if len(matches) == 0 {
		return text
	}
	var builder strings.Builder
	prev := 0
	for _, loc := range matches {
		start, end := loc[0], loc[1]
		if end < len(text) && isLowerAlnum(text[end]) {
			continue // negative-lookahead guard: token continues, not a match
		}
		builder.WriteString(text[prev:start])
		builder.WriteString("<redacted>")
		prev = end
	}
	builder.WriteString(text[prev:])
	return builder.String()
}

func isLowerAlnum(b byte) bool {
	return (b >= 'a' && b <= 'z') || (b >= '0' && b <= '9')
}

// SafeExceptionMessage returns a redacted, non-empty exception message.
func SafeExceptionMessage(message string) string {
	if cleaned := RedactSensitiveErrorText(strings.TrimSpace(message)); cleaned != "" {
		return cleaned
	}
	return MessageUnexpected
}

// UpstreamErrorDetail is the Go form of diagnostics.UpstreamErrorDetail.
type UpstreamErrorDetail struct {
	StatusCode     int
	HasStatus      bool
	BodyText       string
	HasBodyText    bool
	ExceptionText  string
	HasException   bool
	CauseChainText string
	HasCauseChain  bool
	CategoryHint   string
	HasCategory    bool
	BodyTruncated  bool
}

// ExtractUpstreamErrorDetail reproduces extract_upstream_error_detail for the
// descriptor-backed provider error model.
func ExtractUpstreamErrorDetail(e *ProviderError) UpstreamErrorDetail {
	var rawBody any
	switch {
	case e.HasAttachedBody:
		rawBody = e.AttachedBody
	case e.Body != nil:
		rawBody = e.Body
	case e.ResponseText != "":
		rawBody = e.ResponseText
	}
	detail := UpstreamErrorDetail{}
	if e.HasStatus {
		detail.StatusCode, detail.HasStatus = e.StatusCode, true
	} else if e.HasResponseStatus {
		detail.StatusCode, detail.HasStatus = e.ResponseStatus, true
	}
	if bodyText := normalizeBodyText(rawBody); bodyText != "" {
		redacted := RedactSensitiveErrorText(bodyText)
		capped, truncated := capTextBytes(redacted)
		detail.BodyText, detail.HasBodyText = capped, true
		detail.BodyTruncated = e.AttachedTruncated || truncated
	}
	if exception := strings.TrimSpace(e.Message); exception != "" {
		capped, _ := capTextBytes(RedactSensitiveErrorText(exception))
		detail.ExceptionText, detail.HasException = capped, true
	}
	if chain := e.causeChainText(); chain != "" {
		detail.CauseChainText, detail.HasCauseChain = chain, true
	}
	if hint, ok := categoryHint(rawBody); ok {
		detail.CategoryHint, detail.HasCategory = hint, true
	}
	return detail
}

func (e *ProviderError) causeChainText() string {
	lines := make([]string, 0, len(e.Causes))
	for i, cause := range e.Causes {
		if i >= maxCauseChainDepth {
			break
		}
		raw := strings.TrimSpace(cause.Message)
		if raw != "" {
			lines = append(lines, cause.Kind+": "+RedactSensitiveErrorText(raw))
		} else {
			lines = append(lines, cause.Kind)
		}
	}
	if len(lines) == 0 {
		return ""
	}
	text, _ := capTextBytes(strings.Join(lines, "\n"))
	return text
}

// FormatExecutionFailureMessage builds the copyable redacted diagnostic
// envelope, matching format_execution_failure_message line for line.
func FormatExecutionFailureMessage(
	failure *CanonicalFailure,
	detail UpstreamErrorDetail,
	upstreamName string,
	requestID string,
) string {
	stableMessage := failure.Message
	hasUpstreamDetail := detail.HasStatus || detail.HasBodyText
	if !hasUpstreamDetail {
		lines := []string{stableMessage}
		if detail.HasException && detail.ExceptionText != stableMessage {
			lines = append(lines, "", "Provider exception:", detail.ExceptionText)
		}
		if detail.HasCauseChain {
			lines = append(lines, "", "Caused by:", detail.CauseChainText)
		}
		appendRequestID(&lines, requestID)
		return strings.Join(lines, "\n")
	}

	var first string
	switch {
	case detail.HasStatus && detail.StatusCode == 405:
		first = "Upstream provider " + upstreamName +
			" rejected the request method or endpoint (HTTP 405)."
	case detail.HasStatus:
		first = "Upstream provider " + upstreamName +
			" returned HTTP " + itoa(detail.StatusCode) + "."
	default:
		first = "Upstream provider " + upstreamName + " returned an error."
	}
	lines := []string{first}

	category := string(failure.Kind)
	if detail.HasCategory {
		category = detail.CategoryHint
	}
	lines = append(lines, "Category: "+category)
	if stableMessage != "" && stableMessage != first {
		lines = append(lines, "Mapped message: "+stableMessage)
	}
	lines = append(lines, "", "Upstream error:")
	if detail.HasBodyText {
		lines = append(lines, detail.BodyText)
	} else {
		lines = append(lines, "(empty upstream error body)")
	}
	if bodyTruncationLineNeeded(detail) {
		lines = append(lines, "... [truncated after "+itoa(ErrorDetailDisplayCapBytes)+" bytes]")
	}
	appendRequestID(&lines, requestID)
	return strings.Join(lines, "\n")
}

func bodyTruncationLineNeeded(detail UpstreamErrorDetail) bool {
	if !detail.BodyTruncated {
		return false
	}
	if !detail.HasBodyText {
		return true
	}
	return !strings.Contains(detail.BodyText,
		"truncated after "+itoa(ErrorDetailDisplayCapBytes)+" bytes")
}

func appendRequestID(lines *[]string, requestID string) {
	if requestID != "" {
		*lines = append(*lines, "", "Request ID: "+requestID)
	}
}

// normalizeBodyText mirrors _normalize_body_text: stripped strings are
// re-encoded compactly when they parse as JSON; structured values use compact
// JSON; blank input disappears.
func normalizeBodyText(body any) string {
	switch v := body.(type) {
	case nil:
		return ""
	case string:
		stripped := strings.TrimSpace(v)
		if stripped == "" {
			return ""
		}
		// decodeOrdered preserves key order so output matches Python dumps.
		if parsed, ok := decodeOrdered([]byte(stripped)).(*JSONObject); ok {
			return compactJSON(parsed)
		}
		if parsed, ok := decodeOrdered([]byte(stripped)).([]any); ok {
			return compactJSON(parsed)
		}
		return stripped
	default:
		if v == nil {
			return ""
		}
		return compactJSON(v)
	}
}

// capTextBytes caps on UTF-8 byte length, replacing a split multibyte
// sequence like Python's errors="replace" decode.
func capTextBytes(text string) (string, bool) {
	if len(text) <= ErrorDetailDisplayCapBytes {
		return text, false
	}
	capped := strings.ToValidUTF8(text[:ErrorDetailDisplayCapBytes], "\ufffd")
	return capped + "\n... [truncated after " + itoa(ErrorDetailDisplayCapBytes) + " bytes]", true
}

// categoryHint mirrors _category_hint_from_body.
func categoryHint(body any) (string, bool) {
	parsed := body
	if text, ok := body.(string); ok {
		parsed = decodeOrdered([]byte(text))
	}
	if mapping, ok := asObject(parsed); ok {
		if errorMap, ok := asObject(mapping.Fields["error"]); ok {
			if hint, ok := stringTypeCode(errorMap); ok {
				return hint, true
			}
		}
		if hint, ok := stringTypeCode(mapping); ok {
			return hint, true
		}
	}
	if text, ok := body.(string); ok {
		lower := strings.ToLower(text)
		if strings.Contains(lower, "model") && strings.Contains(lower, "unsupported") {
			return "upstream_model_error", true
		}
	}
	return "", false
}

func stringTypeCode(mapping *JSONObject) (string, bool) {
	for _, key := range []string{"type", "code"} {
		if value, ok := mapping.Fields[key].(string); ok && strings.TrimSpace(value) != "" {
			return strings.TrimSpace(value), true
		}
	}
	return "", false
}

func itoa(n int) string {
	return strconv.Itoa(n)
}
