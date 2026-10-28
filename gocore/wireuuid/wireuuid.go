// Package wireuuid implements the subset of RFC 4122 the continuation
// stream needs: name-based version-5 UUIDs (SHA-1) for deterministic item id
// remapping, and random version-4 UUIDs for attempt namespaces. The standard
// library supplies SHA-1 and crypto/rand, so no external module is required.
package wireuuid

import (
	"crypto/rand"
	"crypto/sha1"
	"encoding/hex"
	"errors"
)

// ErrInvalidUUID is returned when a canonical UUID string cannot be parsed.
var ErrInvalidUUID = errors.New("invalid UUID format")

// UUID is a 128-bit UUID stored in network byte order, matching the layout
// Python's uuid module uses for bytes and time fields.
type UUID [16]byte

// NewV5 derives the version-5 UUID of name under namespace, exactly as
// Python's uuid.uuid5: SHA-1(namespace bytes || name), then the version and
// variant bits are overwritten.
func NewV5(namespace UUID, name string) UUID {
	h := sha1.New()
	h.Write(namespace[:])
	h.Write([]byte(name))
	sum := h.Sum(nil)
	var out UUID
	copy(out[:], sum[:16])
	out[6] = (out[6] & 0x0f) | 0x50 // version 5
	out[8] = (out[8] & 0x3f) | 0x80 // RFC 4122 variant
	return out
}

// NewV4 returns a random version-4 UUID.
func NewV4() (UUID, error) {
	var out UUID
	if _, err := rand.Read(out[:]); err != nil {
		return UUID{}, err
	}
	out[6] = (out[6] & 0x0f) | 0x40 // version 4
	out[8] = (out[8] & 0x3f) | 0x80 // RFC 4122 variant
	return out, nil
}

// MustV4 panics when the system random source fails; a failing /dev/urandom
// makes every later crypto operation unusable anyway.
func MustV4() UUID {
	id, err := NewV4()
	if err != nil {
		panic(err)
	}
	return id
}

// Parse accepts the canonical 8-4-4-4-12 hyphenated form used in fixtures.
func Parse(value string) (UUID, error) {
	raw, err := hex.DecodeString(stripHyphens(value))
	if err != nil || len(raw) != 16 {
		return UUID{}, ErrInvalidUUID
	}
	var out UUID
	copy(out[:], raw)
	return out, nil
}

// Hex renders the 32 lowercase hex characters Python's UUID.hex produces.
func (u UUID) Hex() string {
	return hex.EncodeToString(u[:])
}

// String renders the canonical 8-4-4-4-12 hyphenated form.
func (u UUID) String() string {
	hexText := u.Hex()
	out := make([]byte, 0, 36)
	bounds := [5]int{0, 8, 12, 16, 20}
	ends := [5]int{8, 12, 16, 20, 32}
	for i := 0; i < 5; i++ {
		if i > 0 {
			out = append(out, '-')
		}
		out = append(out, hexText[bounds[i]:ends[i]]...)
	}
	return string(out)
}

func stripHyphens(value string) string {
	out := make([]byte, 0, len(value))
	for i := 0; i < len(value); i++ {
		if value[i] != '-' {
			out = append(out, value[i])
		}
	}
	return string(out)
}
