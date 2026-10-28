// Package recovery ports the provider-independent recovery decision table
// from core/stream_recovery.py. It is pure logic: given what was released,
// what the source looked like, and whether a retry is possible, it decides
// whether to stop, restart, continue, or hand off.
package recovery

// Action mirrors RecoveryAction in the Python reference.
type Action string

const (
	ActionRestart  Action = "restart"
	ActionContinue Action = "continue"
	ActionHandoff  Action = "handoff"
	ActionStop     Action = "stop"
)

// Prefix is the Go counterpart of DeliveredPrefix, limited to the fields the
// assessment reads.
type Prefix struct {
	Text         string
	Thinking     string
	HasCalls     bool
	Eligible     bool
	CanHandoff   bool
	UnsafeReason string
}

// SourceSnapshot mirrors SourceRecoverySnapshot. CanHandoff defaults to true
// in the Python dataclass; constructors here set it explicitly.
type SourceSnapshot struct {
	NormalStopSeen   bool
	StructuredOutput bool
	UnsafeReason     string // empty means None
	CanHandoff       bool
}

// ContinuationSeed is the portable prefix carried into a continuation call.
type ContinuationSeed struct {
	Text     string
	Thinking string
}

// Assessment is the decision result. Seed is non-nil only for continue.
type Assessment struct {
	Action Action
	Reason string
	Seed   *ContinuationSeed
}

// Assess applies the exact branch order of assess_recovery:
// normal stop, released-content gate, prefix presence, tool-call handoff,
// unsafe/structured prefixes, empty prefix, and finally portable continue.
func Assess(contentReleased bool, prefix *Prefix, source SourceSnapshot, retryable bool) Assessment {
	if source.NormalStopSeen {
		return Assessment{Action: ActionStop, Reason: "normal_stop"}
	}
	if !contentReleased {
		if source.UnsafeReason != "" {
			return Assessment{Action: ActionStop, Reason: source.UnsafeReason}
		}
		return Assessment{Action: ActionRestart, Reason: "no_delivered_content"}
	}
	if prefix == nil {
		return Assessment{Action: ActionStop, Reason: "missing_prefix"}
	}
	if prefix.HasCalls {
		if retryable && !source.StructuredOutput && source.CanHandoff && prefix.CanHandoff {
			return Assessment{Action: ActionHandoff, Reason: "completed_tool_calls"}
		}
		return Assessment{Action: ActionStop, Reason: "released_tool_calls"}
	}

	reason := ""
	if source.StructuredOutput {
		reason = "structured_output"
	} else if source.UnsafeReason != "" {
		reason = source.UnsafeReason
	} else if prefix.UnsafeReason != "" {
		reason = prefix.UnsafeReason
	}
	if reason != "" || !prefix.Eligible {
		if reason == "" {
			reason = "unsafe_prefix"
		}
		return Assessment{Action: ActionStop, Reason: reason}
	}
	if prefix.Text == "" && prefix.Thinking == "" {
		return Assessment{Action: ActionStop, Reason: "empty_prefix"}
	}
	return Assessment{
		Action: ActionContinue,
		Reason: "portable_prefix",
		Seed:   &ContinuationSeed{Text: prefix.Text, Thinking: prefix.Thinking},
	}
}
