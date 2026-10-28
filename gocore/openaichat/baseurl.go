// Package openaichat ports the OpenAI-compatible chat adapter family from
// the Python reference. Phase 4c-4a covers the transport-free leaf
// utilities: base URL policy, extra-body guards, streamed-usage helpers,
// output-token cap recovery and per-attempt tool-call identity.
package openaichat

import "strings"

// CanonicalV1BaseURL returns the canonical /v1 API base for a server root
// or API base, mirroring openai_v1_base_url: trailing slashes are stripped
// and /v1 is appended only when not already the final path segment.
func CanonicalV1BaseURL(baseURL string) string {
	normalized := strings.TrimRight(baseURL, "/")
	if strings.HasSuffix(normalized, "/v1") {
		return normalized
	}
	return normalized + "/v1"
}
