// Package sse implements byte-faithful incremental SSE framing, ported from
// the Python reference implementation in
// core/anthropic/streaming/decoder.py. The Go port must stay frame-for-frame
// identical to Python, including CR/CRLF handling, split chunks, and unterminated
// trailing events.
package sse

import "strings"

// span is a half-open byte interval inside a probe buffer.
type span struct {
	start int
	end   int
}

// lineBreakLen reports the length of the line break at position i:
// CRLF counts as a single break, matching the Python (?:\r\n|\r|\n) atom.
func lineBreakLen(s string, i int) int {
	if i >= len(s) {
		return 0
	}
	if s[i] == '\r' {
		if i+1 < len(s) && s[i+1] == '\n' {
			return 2
		}
		return 1
	}
	if s[i] == '\n' {
		return 1
	}
	return 0
}

// findBoundaries locates every pair of consecutive line breaks, equivalent to
// the Python atomic regex (?>\r\n|\r|\n){2} applied non-overlapping.
func findBoundaries(s string) []span {
	var out []span
	i := 0
	for i < len(s) {
		first := lineBreakLen(s, i)
		if first == 0 {
			i++
			continue
		}
		second := lineBreakLen(s, i+first)
		if second == 0 {
			i += first
			continue
		}
		out = append(out, span{start: i, end: i + first + second})
		i += first + second
	}
	return out
}

func lastThree(s string) string {
	if len(s) <= 3 {
		return s
	}
	return s[len(s)-3:]
}

// Decoder splits an SSE byte stream into raw frames. A frame retains its
// original line endings and its trailing boundary, so callers can re-emit it
// without normalization.
type Decoder struct {
	parts        []string
	boundaryTail string
	fastPath     bool
}

// NewDecoder creates a framing decoder. Fast path mirrors the Python split
// optimization for the common LF-only stream; tests toggle it for parity
// checks against the general scanner.
func NewDecoder() *Decoder {
	return &Decoder{fastPath: true}
}

// SetFastPath toggles the LF-only fast path. It is intended for parity tests
// that compare both code paths against the same input.
func (d *Decoder) SetFastPath(enabled bool) {
	d.fastPath = enabled
}

// FeedFrames consumes one wire chunk and returns the complete frames it
// completed. Frames include their trailing double-break boundary.
func (d *Decoder) FeedFrames(chunk string) []string {
	if d.fastPath && d.boundaryTail == "" && len(d.parts) == 0 &&
		!strings.ContainsRune(chunk, '\r') && !strings.Contains(chunk, "\n\n\n") {
		// Manual index scan: strings.Split would allocate a slice of every
		// piece, while most callers only need the completed frames.
		frameCount := strings.Count(chunk, "\n\n")
		if frameCount > 0 {
			frames := make([]string, 0, frameCount)
			start := 0
			for {
				relative := strings.Index(chunk[start:], "\n\n")
				if relative < 0 {
					break
				}
				absolute := start + relative
				frames = append(frames, chunk[start:absolute]+"\n\n")
				start = absolute + 2
			}
			remainder := chunk[start:]
			if remainder != "" {
				d.parts = append(d.parts, remainder)
			}
			d.boundaryTail = lastThree(remainder)
			return frames
		}
		if chunk != "" {
			d.parts = append(d.parts, chunk)
		}
		d.boundaryTail = lastThree(chunk)
		return nil
	}

	probe := d.boundaryTail + chunk
	prefixLength := len(d.boundaryTail)
	frames := make([]string, 0)
	chunkStart := 0
	for _, match := range findBoundaries(probe) {
		chunkEnd := match.end - prefixLength
		if chunkEnd < 0 {
			chunkEnd = 0
		}
		d.parts = append(d.parts, chunk[chunkStart:chunkEnd])
		frames = append(frames, strings.Join(d.parts, ""))
		d.parts = d.parts[:0]
		chunkStart = chunkEnd
	}

	remainder := chunk[chunkStart:]
	if remainder != "" {
		d.parts = append(d.parts, remainder)
	}
	if chunkStart != 0 {
		d.boundaryTail = lastThree(remainder)
	} else {
		d.boundaryTail = lastThree(d.boundaryTail + chunk)
	}
	return frames
}

// FinishFrames returns a final unterminated frame, if buffered content remains,
// and resets the decoder for reuse.
func (d *Decoder) FinishFrames() []string {
	remainder := strings.Join(d.parts, "")
	d.parts = d.parts[:0]
	d.boundaryTail = ""
	if remainder == "" {
		return nil
	}
	return []string{remainder}
}
