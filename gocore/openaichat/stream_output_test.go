package openaichat

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"testing"
)

type recordingChatSink struct{}

func (recordingChatSink) StartEvents() []string { return []string{"start"} }
func (recordingChatSink) StartReasoning(source int) []string {
	return []string{fmt.Sprintf("reasoning_start:%d", source)}
}
func (recordingChatSink) ReasoningDelta(content string, summary bool, source int) string {
	return fmt.Sprintf("reasoning_delta:%d:%d:%s", source, boolInt(summary), content)
}
func (recordingChatSink) OpaqueReasoning(data string) []string { return []string{"opaque:" + data} }
func (recordingChatSink) AttachReasoningReplay(data string, source int) []string {
	return []string{fmt.Sprintf("attach:%d:%s", source, data)}
}
func (recordingChatSink) StopReasoning(source int) []string {
	return []string{fmt.Sprintf("reasoning_stop:%d", source)}
}
func (recordingChatSink) StartText() []string             { return []string{"text_start"} }
func (recordingChatSink) TextDelta(content string) string { return "text_delta:" + content }
func (recordingChatSink) StopText() []string              { return []string{"text_stop"} }
func (recordingChatSink) StartTool(index int, state *ChatToolState) string {
	return fmt.Sprintf("tool_start:%d:%s:%s", index, state.ToolID, state.Name)
}
func (recordingChatSink) ToolDelta(index int, state *ChatToolState, partial string) string {
	return fmt.Sprintf("tool_delta:%d:%s", index, partial)
}
func (recordingChatSink) StopTool(index int, state *ChatToolState) []string {
	return []string{fmt.Sprintf("tool_stop:%d", index)}
}
func (recordingChatSink) FinishSuccess(reason string, usage any) []string {
	return []string{"success:" + reason}
}
func (recordingChatSink) FinishFailure(failure any) []string { return []string{"failure"} }
func boolInt(value bool) int {
	if value {
		return 1
	}
	return 0
}

type recordingChatReplay struct{ active bool }

func (r *recordingChatReplay) Active() bool { return r.active }
func (r *recordingChatReplay) BeforeReasoning(output *ChatStreamOutput) []string {
	return []string{"before_reasoning"}
}
func (r *recordingChatReplay) BeforeContent(output *ChatStreamOutput) []string {
	return []string{"before_content"}
}
func (r *recordingChatReplay) Finish(output *ChatStreamOutput, completed bool) []string {
	return []string{fmt.Sprintf("replay_finish:%d", boolInt(completed))}
}

type chatOutputState struct {
	Text       string `json:"text"`
	Reasoning  string `json:"reasoning"`
	Committed  bool   `json:"committed"`
	HasTool    bool   `json:"has_tool"`
	StopReason string `json:"stop_reason"`
	Tools      []struct {
		Index   int            `json:"index"`
		ID      string         `json:"id"`
		Name    string         `json:"name"`
		Extra   map[string]any `json:"extra"`
		Started bool           `json:"started"`
		Open    bool           `json:"open"`
		Content string         `json:"content"`
	} `json:"tools"`
}

type chatOutputInput struct {
	Op        string         `json:"op"`
	Index     int            `json:"index"`
	Value     string         `json:"value"`
	ID        string         `json:"id"`
	Extra     map[string]any `json:"extra"`
	Opaque    []string       `json:"opaque"`
	Summary   bool           `json:"summary"`
	Completed *bool          `json:"completed"`
	Active    bool           `json:"active"`
}

func TestChatStreamOutputGoldenParity(t *testing.T) {
	data, err := os.ReadFile(filepath.Join("..", "testdata", "golden_openaichat4c4b_stream_output.json"))
	if err != nil {
		t.Fatal(err)
	}
	var fixture struct {
		Schema    string `json:"schema"`
		Scenarios []struct {
			Name   string `json:"name"`
			Replay bool   `json:"replay"`
			Steps  []struct {
				Input  chatOutputInput `json:"input"`
				Events []string        `json:"events"`
				State  chatOutputState `json:"state"`
			} `json:"steps"`
		} `json:"scenarios"`
	}
	if err := json.Unmarshal(data, &fixture); err != nil {
		t.Fatal(err)
	}
	if fixture.Schema != "phase4c4b-stream-output-v1" || len(fixture.Scenarios) == 0 {
		t.Fatal("invalid fixture")
	}
	for iteration := 0; iteration < 10; iteration++ {
		for _, scenario := range fixture.Scenarios {
			o := NewChatStreamOutput(1, recordingChatSink{})
			var replay *recordingChatReplay
			if scenario.Replay {
				replay = &recordingChatReplay{}
				o.Replay = replay
			}
			for i, step := range scenario.Steps {
				events := runChatOutputStep(t, o, replay, step.Input)
				if len(events) == 0 {
					events = []string{}
				}
				if !reflect.DeepEqual(events, step.Events) {
					t.Fatalf("%s step %d %s: events got %#v, want %#v", scenario.Name, i, step.Input.Op, events, step.Events)
				}
				state := chatOutputState{Text: o.AccumulatedText(), Reasoning: o.AccumulatedReasoning(), Committed: o.CommittedOutput(), HasTool: o.HasEmittedToolBlock(), StopReason: o.FinalStopReason("tool_use")}
				state.Tools = make([]struct {
					Index   int            `json:"index"`
					ID      string         `json:"id"`
					Name    string         `json:"name"`
					Extra   map[string]any `json:"extra"`
					Started bool           `json:"started"`
					Open    bool           `json:"open"`
					Content string         `json:"content"`
				}, 0, len(o.toolOrder))
				for _, index := range o.toolOrder {
					s := o.ToolState(index)
					state.Tools = append(state.Tools, struct {
						Index   int            `json:"index"`
						ID      string         `json:"id"`
						Name    string         `json:"name"`
						Extra   map[string]any `json:"extra"`
						Started bool           `json:"started"`
						Open    bool           `json:"open"`
						Content string         `json:"content"`
					}{index, s.ToolID, s.Name, s.ExtraContent, s.Started, s.Open, s.Content()})
				}
				if !reflect.DeepEqual(state, step.State) {
					t.Fatalf("%s step %d %s: state got %#v, want %#v", scenario.Name, i, step.Input.Op, state, step.State)
				}
			}
		}
	}
}

func runChatOutputStep(t *testing.T, o *ChatStreamOutput, replay *recordingChatReplay, step chatOutputInput) []string {
	t.Helper()
	switch step.Op {
	case "start":
		return o.StartEvents()
	case "reasoning":
		return o.EnsureReasoningBlock(step.Index)
	case "begin":
		return o.BeginReasoningRecord(step.Index)
	case "reasoning_delta":
		return []string{o.EmitReasoningDelta(step.Value, step.Summary, step.Index)}
	case "complete":
		return o.CompleteReasoningRecord(&step.Opaque, step.Index)
	case "opaque":
		return o.EmitOpaqueReasoning(step.Value)
	case "defer":
		o.DeferOpaqueReasoning(step.Value)
	case "flush":
		return o.FlushReasoningReplay(step.Completed == nil || *step.Completed)
	case "text":
		return o.EnsureTextBlock()
	case "text_delta":
		return []string{o.EmitTextDelta(step.Value)}
	case "close_content":
		return o.CloseContentBlocks()
	case "finish_reasoning":
		return o.FinishReasoningGroup()
	case "name":
		o.RegisterToolName(step.Index, step.Value)
	case "extra":
		o.SetToolExtraContent(step.Index, step.Extra)
	case "tool_start":
		return []string{o.StartToolBlock(step.Index, step.ID, step.Value, step.Extra)}
	case "tool_delta":
		return []string{o.EmitToolDelta(step.Index, step.Value)}
	case "tool_stop":
		return o.StopToolBlock(step.Index)
	case "close_all":
		return o.CloseAllBlocks()
	case "close_unclosed":
		return o.CloseUnclosedBlocks()
	case "success":
		return o.FinishSuccess(step.Value, nil)
	case "failure":
		return o.FinishFailure(nil)
	case "active":
		replay.active = step.Active
	default:
		t.Fatalf("unknown op %q", step.Op)
	}
	return nil
}

func TestChatOutputAccessorsAndOpaqueConsumption(t *testing.T) {
	o := NewChatStreamOutput(7, recordingChatSink{})
	if o.InputTokens != 7 || o.ToolState(4) != nil || o.ToolBlockForToolIndex(4) != nil {
		t.Fatal("unexpected initial output state")
	}
	o.RegisterToolName(4, "pending")
	if o.ToolBlockForToolIndex(4) != nil || len(o.StartedToolIndexes()) != 0 {
		t.Fatal("unstarted tool was reported as started")
	}
	o.StartToolBlock(4, "id", "pending", nil)
	if o.ToolBlockForToolIndex(4) != o.ToolState(4) || !reflect.DeepEqual(o.StartedToolIndexes(), []int{4}) {
		t.Fatal("started tool accessors do not preserve identity")
	}
	o.BeginReasoningRecord(1)
	opaque := []string{"signature", "redacted"}
	o.CompleteReasoningRecord(&opaque, 1)
	if !reflect.DeepEqual(opaque, []string{"redacted"}) {
		t.Fatalf("opaque list was not consumed: %#v", opaque)
	}
	unchanged := []string{"other"}
	o.CompleteReasoningRecord(&unchanged, 99)
	if !reflect.DeepEqual(unchanged, []string{"other"}) {
		t.Fatalf("opaque list changed for unopened source: %#v", unchanged)
	}
}
