package openaichat

import (
	"encoding/json"
	"strings"
)

// AmbiguousToolCallIdentityError mirrors the reference exception: a
// fragment lacks sufficient identity to assign its content safely.
type AmbiguousToolCallIdentityError struct {
	message string
}

func (e *AmbiguousToolCallIdentityError) Error() string { return e.message }

var (
	errNoUsableIdentity = &AmbiguousToolCallIdentityError{
		message: "Upstream tool-call fragment has neither a usable index nor an ID.",
	}
	errAmbiguousIdentity = &AmbiguousToolCallIdentityError{
		message: "Upstream tool-call fragment has ambiguous identity.",
	}
)

type toolCallIdentity struct {
	index  *int
	toolID *string
}

// ToolCallIdentityResolver keeps upstream indexes and IDs separate from
// locally allocated slots within one Chat response attempt.
type ToolCallIdentityResolver struct {
	calls []toolCallIdentity
}

// CallSnapshot is an immutable view of one resolved logical slot.
type CallSnapshot struct {
	Slot     int
	Index    *int
	ToolID   *string
	OrderKey [2]int
}

// NewToolCallIdentityResolver returns an empty resolver.
func NewToolCallIdentityResolver() *ToolCallIdentityResolver {
	return &ToolCallIdentityResolver{}
}

// Allocate reserves a new logical slot, including for already-completed calls.
func (r *ToolCallIdentityResolver) Allocate() int {
	slot := len(r.calls)
	r.calls = append(r.calls, toolCallIdentity{})
	return slot
}

// Resolve maps a raw upstream fragment identity to a logical slot,
// learning missing fields and rejecting unidentified or ambiguous fragments.
func (r *ToolCallIdentityResolver) Resolve(rawIndex, rawID any) (int, error) {
	index := validIndex(rawIndex)
	toolID := validToolID(rawID)
	if index == nil && toolID == nil {
		return 0, errNoUsableIdentity
	}

	matches := make([]int, 0)
	for slot, call := range r.calls {
		indexMatches := index == nil || call.index == nil || *call.index == *index
		idMatches := toolID == nil || call.toolID == nil || *call.toolID == *toolID
		if indexMatches && idMatches {
			matches = append(matches, slot)
		}
	}

	// A new identifier starts a call unless a known field links it to one.
	linked := false
	for _, slot := range matches {
		if (index != nil && r.calls[slot].index != nil && *r.calls[slot].index == *index) ||
			(toolID != nil && r.calls[slot].toolID != nil && *r.calls[slot].toolID == *toolID) {
			linked = true
		}
	}
	if !linked {
		matches = nil
	}
	if len(matches) > 1 {
		return 0, errAmbiguousIdentity
	}

	slot := 0
	if len(matches) == 1 {
		slot = matches[0]
	} else {
		slot = r.Allocate()
	}
	if index != nil {
		r.calls[slot].index = index
	}
	if toolID != nil {
		r.calls[slot].toolID = toolID
	}
	return slot, nil
}

// ToolID returns the learned upstream tool ID for a slot, or nil.
func (r *ToolCallIdentityResolver) ToolID(slot int) *string {
	return r.calls[slot].toolID
}

// OrderKey sorts sparse upstream indexes while keeping slot order as the
// tie-breaker; a missing index sorts as zero.
func (r *ToolCallIdentityResolver) OrderKey(slot int) [2]int {
	call := r.calls[slot]
	if call.index != nil {
		return [2]int{*call.index, slot}
	}
	return [2]int{0, slot}
}

// Snapshot returns the current per-slot identity state for golden replay.
func (r *ToolCallIdentityResolver) Snapshot() []CallSnapshot {
	snapshots := make([]CallSnapshot, len(r.calls))
	for slot := range r.calls {
		snapshots[slot] = CallSnapshot{
			Slot:     slot,
			Index:    r.calls[slot].index,
			ToolID:   r.calls[slot].toolID,
			OrderKey: r.OrderKey(slot),
		}
	}
	return snapshots
}

// validIndex accepts only non-negative integral values, rejecting bools,
// floats and strings exactly like the Python isinstance/guard chain.
func validIndex(value any) *int {
	switch typed := value.(type) {
	case int:
		if typed >= 0 {
			return &typed
		}
	case int64:
		if typed >= 0 {
			number := int(typed)
			return &number
		}
	case json.Number:
		parsed, err := typed.Int64()
		if err == nil && parsed >= 0 {
			number := int(parsed)
			return &number
		}
	}
	return nil
}

func validToolID(value any) *string {
	text, ok := value.(string)
	if !ok || strings.TrimSpace(text) == "" {
		return nil
	}
	return &text
}
