package wireuuid

import (
	"regexp"
	"testing"
)

var canonicalPattern = regexp.MustCompile(
	`^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$`)

func TestNewV4CanonicalString(t *testing.T) {
	seen := map[string]bool{}
	for range 16 {
		id, err := NewV4()
		if err != nil {
			t.Fatal(err)
		}
		text := id.String()
		if !canonicalPattern.MatchString(text) {
			t.Fatalf("non-canonical uuid4 %q", text)
		}
		if len(text) != 36 {
			t.Fatalf("uuid length got %d want 36", len(text))
		}
		if seen[text] {
			t.Fatalf("duplicate uuid %q", text)
		}
		seen[text] = true
	}
}

func TestParseRoundTrip(t *testing.T) {
	id, err := NewV4()
	if err != nil {
		t.Fatal(err)
	}
	parsed, err := Parse(id.String())
	if err != nil {
		t.Fatal(err)
	}
	if parsed != id {
		t.Fatalf("round trip mismatch: %q vs %q", parsed, id)
	}
}

func TestHexIsThirtyTwoChars(t *testing.T) {
	id, err := NewV4()
	if err != nil {
		t.Fatal(err)
	}
	if hex := id.Hex(); len(hex) != 32 {
		t.Fatalf("hex length got %d want 32", len(hex))
	}
}
