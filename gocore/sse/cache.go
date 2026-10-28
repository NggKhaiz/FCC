package sse

// PayloadCache memoizes frame payloads across the delivery stages of one
// response. It mirrors FramePayloadCache: a bounded FIFO window so memory
// stays flat regardless of stream length, while the back-to-back inspections
// of one frame all hit the cache. A nil entry is a valid cached negative
// result for a malformed frame.
type PayloadCache struct {
	entries  map[string]map[string]any
	order    []string
	capacity int
}

// NewPayloadCache creates a cache with the Python default capacity of 64.
func NewPayloadCache() *PayloadCache {
	const defaultCapacity = 64
	return &PayloadCache{
		entries:  make(map[string]map[string]any, defaultCapacity),
		order:    make([]string, 0, defaultCapacity),
		capacity: defaultCapacity,
	}
}

// Get returns the cached payload for frame, parsing and caching it on first
// sight. The boolean reports whether a JSON object payload exists; callers
// must treat the map as read-only, as Python callers deep-copy before edits.
func (c *PayloadCache) Get(frame string) (map[string]any, bool) {
	if value, ok := c.entries[frame]; ok {
		return value, value != nil
	}
	value, ok := FrameData(frame)
	if len(c.order) >= c.capacity {
		oldest := c.order[0]
		c.order = c.order[1:]
		delete(c.entries, oldest)
	}
	c.entries[frame] = value
	c.order = append(c.order, frame)
	return value, ok
}

// Clear drops every cached entry.
func (c *PayloadCache) Clear() {
	clear(c.entries)
	c.order = c.order[:0]
}

// Len reports the number of cached frames, primarily for diagnostics.
func (c *PayloadCache) Len() int {
	return len(c.order)
}
