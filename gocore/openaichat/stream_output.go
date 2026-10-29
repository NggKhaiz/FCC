package openaichat

import (
	"sort"
	"strings"
)

// ChatToolState is the per-tool semantic state of one Chat stream attempt.
type ChatToolState struct {
	ToolID        string
	Name          string
	ExtraContent  map[string]any
	Started       bool
	Open          bool
	PreStartArgs  string
	ArgumentParts []string
}

// Content joins all argument fragments emitted for this tool.
func (s *ChatToolState) Content() string { return strings.Join(s.ArgumentParts, "") }

// ChatOutputSink supplies protocol-specific events for the shared Chat
// lifecycle. Implementations own their protocol ledgers and serialization.
// Tool callbacks receive the live state, matching the Python writer boundary.
type ChatOutputSink interface {
	StartEvents() []string
	StartReasoning(source int) []string
	ReasoningDelta(content string, summary bool, source int) string
	OpaqueReasoning(data string) []string
	AttachReasoningReplay(data string, source int) []string
	StopReasoning(source int) []string
	StartText() []string
	TextDelta(content string) string
	StopText() []string
	StartTool(index int, state *ChatToolState) string
	ToolDelta(index int, state *ChatToolState, partialJSON string) string
	StopTool(index int, state *ChatToolState) []string
	FinishSuccess(stopReason string, usage any) []string
	FinishFailure(failure any) []string
}

// ReasoningReplayLifecycle coordinates provider-specific reasoning replay.
type ReasoningReplayLifecycle interface {
	Active() bool
	BeforeReasoning(output *ChatStreamOutput) []string
	BeforeContent(output *ChatStreamOutput) []string
	Finish(output *ChatStreamOutput, completed bool) []string
}

// ChatStreamOutput is the protocol-neutral lifecycle for a single Chat
// stream attempt. It is not safe for concurrent use by multiple goroutines.
type ChatStreamOutput struct {
	InputTokens    int
	Replay         ReasoningReplayLifecycle
	toolStates     map[int]*ChatToolState
	sink           ChatOutputSink
	toolOrder      []int
	textParts      []string
	reasoningParts []string
	pendingOpaque  []string
	textStarted    bool
	openReasoning  map[int]struct{}
	contentStarted bool
	terminal       bool
}

func NewChatStreamOutput(inputTokens int, sink ChatOutputSink) *ChatStreamOutput {
	return &ChatStreamOutput{InputTokens: inputTokens, sink: sink, toolStates: make(map[int]*ChatToolState), openReasoning: make(map[int]struct{})}
}

func (o *ChatStreamOutput) CommittedOutput() bool        { return o.contentStarted }
func (o *ChatStreamOutput) AccumulatedText() string      { return strings.Join(o.textParts, "") }
func (o *ChatStreamOutput) AccumulatedReasoning() string { return strings.Join(o.reasoningParts, "") }
func (o *ChatStreamOutput) StartEvents() []string        { return o.sink.StartEvents() }

func (o *ChatStreamOutput) EnsureReasoningBlock(source int) []string {
	var events []string
	if o.Replay != nil {
		events = append(events, o.Replay.BeforeReasoning(o)...)
	}
	if o.textStarted {
		events = append(events, o.sink.StopText()...)
		o.textStarted = false
	}
	return append(events, o.BeginReasoningRecord(source)...)
}

func (o *ChatStreamOutput) EmitReasoningDelta(content string, summary bool, source int) string {
	o.reasoningParts = append(o.reasoningParts, content)
	return o.sink.ReasoningDelta(content, summary, source)
}

func (o *ChatStreamOutput) BeginReasoningRecord(source int) []string {
	if _, ok := o.openReasoning[source]; ok {
		return nil
	}
	o.openReasoning[source] = struct{}{}
	o.contentStarted = true
	return o.sink.StartReasoning(source)
}

// CompleteReasoningRecord removes the first opaque value when it is attached
// to an open source, just as the Python method mutates its input list. A nil
// pointer is equivalent to an empty list.
func (o *ChatStreamOutput) CompleteReasoningRecord(opaque *[]string, source int) []string {
	var events []string
	var items []string
	if opaque != nil {
		items = *opaque
	}
	if _, ok := o.openReasoning[source]; ok {
		if len(items) > 0 {
			events = append(events, o.sink.AttachReasoningReplay(items[0], source)...)
			items = items[1:]
			*opaque = items
		}
		events = append(events, o.sink.StopReasoning(source)...)
		delete(o.openReasoning, source)
	}
	for _, data := range items {
		events = append(events, o.EmitOpaqueReasoning(data)...)
	}
	return events
}

func (o *ChatStreamOutput) EmitOpaqueReasoning(data string) []string {
	if data == "" {
		return nil
	}
	o.contentStarted = true
	return o.sink.OpaqueReasoning(data)
}
func (o *ChatStreamOutput) DeferOpaqueReasoning(data string) {
	if data != "" {
		o.pendingOpaque = append(o.pendingOpaque, data)
	}
}
func (o *ChatStreamOutput) FlushReasoningReplay(completed bool) []string {
	var events []string
	if o.Replay != nil {
		events = append(events, o.Replay.Finish(o, completed)...)
	}
	opaque := o.pendingOpaque
	o.pendingOpaque = nil
	if completed {
		for _, data := range opaque {
			events = append(events, o.EmitOpaqueReasoning(data)...)
		}
	}
	return events
}
func (o *ChatStreamOutput) pauseReasoning() []string {
	var events []string
	if o.Replay != nil {
		events = append(events, o.Replay.BeforeContent(o)...)
	}
	if len(o.openReasoning) != 0 && (o.Replay == nil || !o.Replay.Active()) {
		for _, source := range o.reasoningSources() {
			events = append(events, o.CompleteReasoningRecord(nil, source)...)
		}
	}
	return events
}
func (o *ChatStreamOutput) reasoningSources() []int {
	sources := make([]int, 0, len(o.openReasoning))
	for source := range o.openReasoning {
		sources = append(sources, source)
	}
	sort.Ints(sources)
	return sources
}
func (o *ChatStreamOutput) EnsureTextBlock() []string {
	events := o.pauseReasoning()
	if !o.textStarted {
		events = append(events, o.sink.StartText()...)
		o.textStarted = true
		o.contentStarted = true
	}
	return events
}
func (o *ChatStreamOutput) EmitTextDelta(content string) string {
	o.textParts = append(o.textParts, content)
	return o.sink.TextDelta(content)
}
func (o *ChatStreamOutput) CloseContentBlocks() []string {
	events := o.pauseReasoning()
	if o.textStarted {
		events = append(events, o.sink.StopText()...)
		o.textStarted = false
	}
	return events
}
func (o *ChatStreamOutput) FinishReasoningGroup() []string {
	events := o.FlushReasoningReplay(true)
	return append(events, o.closeContentBlocks()...)
}
func (o *ChatStreamOutput) closeContentBlocks() []string {
	var events []string
	for _, source := range o.reasoningSources() {
		events = append(events, o.CompleteReasoningRecord(nil, source)...)
	}
	if o.textStarted {
		events = append(events, o.sink.StopText()...)
		o.textStarted = false
	}
	return events
}

// ToolState returns the existing state, or nil if the tool has not been seen.
func (o *ChatStreamOutput) ToolState(index int) *ChatToolState { return o.toolStates[index] }

func (o *ChatStreamOutput) EnsureToolState(index int) *ChatToolState {
	if state, ok := o.toolStates[index]; ok {
		return state
	}
	state := &ChatToolState{}
	o.toolStates[index] = state
	o.toolOrder = append(o.toolOrder, index)
	return state
}
func (o *ChatStreamOutput) SetToolExtraContent(index int, extra map[string]any) {
	if len(extra) > 0 {
		o.EnsureToolState(index).ExtraContent = extra
	}
}
func (o *ChatStreamOutput) RegisterToolName(index int, name string) {
	state := o.EnsureToolState(index)
	state.Name = MergeToolName(state.Name, name)
}
func (o *ChatStreamOutput) StartToolBlock(index int, id, name string, extra map[string]any) string {
	state := o.EnsureToolState(index)
	state.ToolID = id
	state.Name = name
	if len(extra) > 0 {
		state.ExtraContent = extra
	}
	state.Started = true
	state.Open = true
	o.contentStarted = true
	return o.sink.StartTool(index, state)
}
func (o *ChatStreamOutput) EmitToolDelta(index int, partialJSON string) string {
	state := o.toolStates[index]
	state.ArgumentParts = append(state.ArgumentParts, partialJSON)
	return o.sink.ToolDelta(index, state, partialJSON)
}
func (o *ChatStreamOutput) StopToolBlock(index int) []string {
	state := o.toolStates[index]
	if !state.Open {
		return nil
	}
	state.Open = false
	return o.sink.StopTool(index, state)
}
func (o *ChatStreamOutput) CloseAllBlocks() []string {
	events := o.FlushReasoningReplay(true)
	events = append(events, o.pauseReasoning()...)
	for _, index := range o.toolOrder {
		if o.toolStates[index].Open {
			events = append(events, o.StopToolBlock(index)...)
		}
	}
	if o.textStarted {
		events = append(events, o.sink.StopText()...)
		o.textStarted = false
	}
	return events
}

// CloseUnclosedBlocks is the base lifecycle behavior. Protocol writers whose
// failure cleanup differs must use their own ledger-specific implementation.
func (o *ChatStreamOutput) CloseUnclosedBlocks() []string {
	events := o.FlushReasoningReplay(false)
	events = append(events, o.closeContentBlocks()...)
	for _, index := range o.toolOrder {
		o.toolStates[index].Open = false
	}
	return events
}
func (o *ChatStreamOutput) HasEmittedToolBlock() bool {
	for _, index := range o.toolOrder {
		if o.toolStates[index].Started {
			return true
		}
	}
	return false
}
func (o *ChatStreamOutput) HasContentBlock() bool { return o.contentStarted }
func (o *ChatStreamOutput) FinalStopReason(fallback string) string {
	if o.HasEmittedToolBlock() {
		return "tool_use"
	}
	if fallback == "tool_use" {
		return "end_turn"
	}
	return fallback
}
func (o *ChatStreamOutput) ToolBlockForToolIndex(index int) *ChatToolState {
	state := o.toolStates[index]
	if state != nil && state.Started {
		return state
	}
	return nil
}

// StartedToolIndexes returns started tools in their original insertion order.
func (o *ChatStreamOutput) StartedToolIndexes() []int {
	var indexes []int
	for _, index := range o.toolOrder {
		if o.toolStates[index].Started {
			indexes = append(indexes, index)
		}
	}
	return indexes
}
func (o *ChatStreamOutput) FinishSuccess(stopReason string, usage any) []string {
	if o.terminal {
		return nil
	}
	events := o.CloseAllBlocks()
	events = append(events, o.sink.FinishSuccess(stopReason, usage)...)
	o.terminal = true
	return events
}
func (o *ChatStreamOutput) FinishFailure(failure any) []string {
	if o.terminal {
		return nil
	}
	events := o.CloseUnclosedBlocks()
	events = append(events, o.sink.FinishFailure(failure)...)
	o.terminal = true
	return events
}
