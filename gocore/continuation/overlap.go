package continuation

import (
	"errors"
	"sort"
	"strings"
)

// Sentinel errors mirror the ExecutionFailure messages raised by the Python
// overlap policy so golden assertions can pin the exact contract.
var (
	// ErrInconsistent means the continuation rewrote an already observed prefix.
	ErrInconsistent = errors.New(
		"The continuation changed an already observed answer prefix.",
	)
	// ErrRetention means the pending replay exceeded the retained-data limit.
	ErrRetention = errors.New(
		"The continuation replay exceeded the retained-data limit.",
	)
)

const defaultLimit = 1 << 20

// partKey identifies one text part. The first element mirrors Python's
// int|str identity (block index or item id), the second is the part position.
type partKey struct {
	isStr bool
	n     int64
	s     string
	pos   int
}

type order [2]int

func (o order) less(other order) bool {
	return o[0] < other[0] || (o[0] == other[0] && o[1] < other[1])
}

// part is the accumulated replay state of one source text part.
type part struct {
	order   order
	text    string
	seen    int
	closed  bool
	removed int
	fixed   bool

	// cachedSize is the last wireByteSize of the retention tuple
	// (key, order, text, seen); dirty flags the tuple as stale.
	// Only tuple members affect the retained-size accounting, so closed,
	// removed and fixed never flip this bit.
	cachedSize int
	dirty      bool
}

// text is one text-bearing field found in a single feed frame.
type text struct {
	key    partKey
	order  order
	value  map[string]any
	field  string
	delta  bool
	part   *part
	offset int
}

type frame struct {
	data  map[string]any
	texts []text
}

type keyPart struct {
	key  partKey
	part *part
}

var partEventKinds = map[string]struct{}{
	"response.content_part.added": {},
	"response.content_part.done":  {},
	"response.output_text.delta":  {},
	"response.output_text.done":   {},
}

type ident struct {
	isStr bool
	n     int64
	s     string
}

func intIdent(n int64) ident { return ident{n: n} }
func strIdent(s string) ident {
	return ident{isStr: true, s: s}
}
func (i ident) key(pos int) partKey {
	return partKey{isStr: i.isStr, n: i.n, s: i.s, pos: pos}
}

// SizeRecord is one retained-size observation, mirroring the values the
// Python policy computes inside _check_size. It is parity-test diagnostics;
// it is only populated while a replay prefix is still pending.
type SizeRecord struct {
	FrameBytes int `json:"frame_bytes"`
	IDsSize    int `json:"ids_size"`
	PartsSize  int `json:"parts_size"`
	Total      int `json:"total"`
}

// Overlap removes one logical replay prefix. It owns only unpublished replay
// evidence, never the public response transcript.
type Overlap struct {
	expected   string
	pending    bool
	parts      map[partKey]*part
	partOrder  []partKey
	ids        map[int64]string
	frames     []*frame
	frameBytes int
	frontier   *order
	limit      int
	sizeTrace  []SizeRecord

	// Incremental retained-size accounting. Recomputing the wire size of
	// every retained part on each feed would be O(parts*frames); instead
	// only dirty parts and a dirty id table are re-serialized, yielding
	// byte-identical totals at O(changed parts) per check.
	partsSizeAcc int
	idsSizeAcc   int
	idsDirty     bool
}

// SizeTrace returns the retained-size observations gathered so far.
func (o *Overlap) SizeTrace() []SizeRecord {
	return o.sizeTrace
}

// NewOverlap creates an overlap machine expecting the given published prefix.
func NewOverlap(expected string, opts ...Option) *Overlap {
	o := &Overlap{
		expected: expected,
		pending:  bool(expected != ""),
		parts:    map[partKey]*part{},
		ids:      map[int64]string{},
		limit:    defaultLimit,
		idsDirty: true,
	}
	for _, opt := range opts {
		opt(o)
	}
	return o
}

// Option configures an Overlap machine.
type Option func(*Overlap)

// WithLimit overrides the retained-data byte limit, primarily for tests that
// exercise the bound without allocating a megabyte of replay frames.
func WithLimit(limit int) Option {
	return func(o *Overlap) { o.limit = limit }
}

func asMap(value any) map[string]any {
	if m, ok := value.(map[string]any); ok {
		return m
	}
	return nil
}

func asList(value any) []any {
	if l, ok := value.([]any); ok {
		return l
	}
	return nil
}

func stringField(value map[string]any, key string) string {
	if s, ok := value[key].(string); ok {
		return s
	}
	return ""
}

func intField(value map[string]any, key string, fallback int64) int64 {
	switch n := value[key].(type) {
	case int:
		return int64(n)
	case int64:
		return n
	case jsonNumber:
		if i, err := n.Int64(); err == nil {
			return i
		}
	}
	return fallback
}

// jsonNumber is the interface satisfied by encoding/json's Number type
// without importing encoding/json into every signature.
type jsonNumber interface {
	Int64() (int64, error)
}

func deepCopy(value any) any {
	switch v := value.(type) {
	case map[string]any:
		out := make(map[string]any, len(v))
		for key, item := range v {
			out[key] = deepCopy(item)
		}
		return out
	case []any:
		out := make([]any, len(v))
		for i, item := range v {
			out[i] = deepCopy(item)
		}
		return out
	default:
		return value
	}
}

func clip(s string, n int) string {
	if n < 0 {
		return ""
	}
	if n < len(s) {
		return s[:n]
	}
	return s
}

// Feed consumes one source event and returns the frames that are now public.
func (o *Overlap) Feed(payload map[string]any) ([]map[string]any, error) {
	data, ok := deepCopy(payload).(map[string]any)
	if !ok {
		return nil, ErrInconsistent
	}
	kind := stringField(data, "type")
	if kind == "content_block_start" {
		block := asMap(data["content_block"])
		if block != nil && stringField(block, "type") == "text" {
			if eager, isString := block["text"].(string); isString && eager != "" {
				block["text"] = ""
				first, err := o.feed(data)
				if err != nil {
					return nil, err
				}
				synthetic := map[string]any{
					"type":  "content_block_delta",
					"index": data["index"],
					"delta": map[string]any{
						"type": "text_delta",
						"text": eager,
					},
				}
				second, err := o.feed(synthetic)
				if err != nil {
					return nil, err
				}
				return append(first, second...), nil
			}
		}
	}
	return o.feed(data)
}

var terminalKinds = map[string]struct{}{
	"response.completed":  {},
	"response.incomplete": {},
	"message_stop":        {},
}

var errorKinds = map[string]struct{}{
	"error":           {},
	"response.error":  {},
	"response.failed": {},
}

func (o *Overlap) feed(data map[string]any) ([]map[string]any, error) {
	kind := stringField(data, "type")
	if _, isErrorKind := errorKinds[kind]; isErrorKind {
		o.frames = nil
		o.frameBytes = 0
		return []map[string]any{data}, nil
	}
	if _, hasErrorKey := data["error"]; hasErrorKey {
		o.frames = nil
		o.frameBytes = 0
		return []map[string]any{data}, nil
	}
	if o.expected == "" {
		return o.render(&frame{data: data, texts: o.texts(data)}), nil
	}

	texts := o.texts(data)
	f := &frame{data: data, texts: texts}
	terminal := geminiFinished(data)
	if !terminal {
		if _, ok := terminalKinds[kind]; ok {
			terminal = true
		} else if kind == "message_delta" {
			delta := asMap(data["delta"])
			if delta != nil {
				if value, exists := delta["stop_reason"]; exists && value != nil {
					terminal = true
				}
			}
		}
	}

	if !o.pending {
		if kind == "response.completed" || kind == "response.incomplete" {
			if err := o.checkTerminalOrder(texts); err != nil {
				return nil, err
			}
		}
		for i := range texts {
			if err := o.settledText(&texts[i]); err != nil {
				return nil, err
			}
		}
		return o.render(f), nil
	}

	if kind == "response.completed" || kind == "response.incomplete" {
		supplied := map[partKey]struct{}{}
		for _, t := range texts {
			supplied[t.key] = struct{}{}
		}
		for key, part := range o.parts {
			if part.text != "" {
				if _, ok := supplied[key]; !ok {
					return nil, ErrInconsistent
				}
			}
		}
	}

	for i := range texts {
		t := &texts[i]
		p := o.parts[t.key]
		if p == nil {
			p = &part{order: t.order, dirty: true}
			o.parts[t.key] = p
			o.partOrder = append(o.partOrder, t.key)
		}
		p.order = t.order
		t.part = p
		value := stringField(t.value, t.field)
		if t.delta {
			t.offset = p.seen
			p.text = clip(p.text+value, len(o.expected)+1)
			p.seen += len(value)
		} else {
			if !strings.HasPrefix(value, p.text) {
				return nil, ErrInconsistent
			}
			p.text = clip(value, len(o.expected)+1)
			p.seen = len(value)
		}
		if strings.HasSuffix(kind, ".done") {
			p.closed = true
		}
		p.dirty = true
	}
	o.closeParts(data, terminal)

	ordered := make([]keyPart, 0, len(o.parts))
	for _, key := range o.partOrder {
		ordered = append(ordered, keyPart{key: key, part: o.parts[key]})
	}
	sort.SliceStable(ordered, func(i, j int) bool {
		return ordered[i].part.order.less(ordered[j].part.order)
	})

	candidate := ""
	candidates := make([]keyPart, 0, len(ordered))
	for _, entry := range ordered {
		candidates = append(candidates, entry)
		candidate += clip(entry.part.text, len(o.expected)+1-len(candidate))
		if !entry.part.closed || len(candidate) > len(o.expected) {
			break
		}
	}

	if terminal || !strings.Contains(o.expected, candidate) {
		o.resolve(candidate, candidates)
		pending := o.frames
		o.frames = nil
		o.frameBytes = 0
		if err := o.checkSize(); err != nil {
			return nil, err
		}
		// Every buffered frame contributes at least one event at most; size
		// the result up front so the flush never grows its backing array.
		ready := make([]map[string]any, 0, len(pending)+1)
		for _, pendingFrame := range pending {
			ready = append(ready, o.render(pendingFrame)...)
		}
		ready = append(ready, o.render(f)...)
		return ready, nil
	}

	hasText := false
	for _, t := range texts {
		if stringField(t.value, t.field) != "" {
			hasText = true
		}
	}
	if len(o.frames) > 0 || hasText {
		o.frames = append(o.frames, f)
		o.frameBytes += wireByteSize(data)
		if err := o.checkSize(); err != nil {
			return nil, err
		}
		return nil, nil
	}
	if err := o.checkSize(); err != nil {
		return nil, err
	}
	return o.render(f), nil
}

func (o *Overlap) identity(data, item map[string]any) ident {
	index := intField(data, "output_index", 0)
	var result ident
	if id := stringField(item, "id"); id != "" {
		result = strIdent(id)
	} else if id := stringField(data, "item_id"); id != "" {
		result = strIdent(id)
	} else if id, ok := o.ids[index]; ok {
		result = strIdent(id)
	} else {
		result = intIdent(index)
	}
	// Mirror _identity: while the replay is pending, string identities are
	// memorized by output index so later index-only events resolve to them.
	if o.pending && result.isStr {
		if _, present := data["output_index"]; present {
			o.ids[index] = result.s
			o.idsDirty = true
		}
	}
	return result
}

func (o *Overlap) texts(data map[string]any) []text {
	if _, hasCandidates := data["candidates"]; hasCandidates {
		var out []text
		for _, part := range geminiParts(data) {
			if _, hasText := part["text"]; hasText {
				if thought, _ := part["thought"].(bool); thought {
					continue
				}
				out = append(out, text{
					key:   strIdent("gemini").key(0),
					order: order{0, 0},
					value: part,
					field: "text",
					delta: true,
				})
			}
		}
		return out
	}

	kind := stringField(data, "type")
	index := intField(data, "index", 0)
	switch kind {
	case "content_block_start":
		block := asMap(data["content_block"])
		if block != nil && stringField(block, "type") == "text" {
			return []text{{
				key:   intIdent(index).key(0),
				order: order{int(index), 0},
				value: block,
				field: "text",
			}}
		}
		return nil
	case "content_block_delta":
		delta := asMap(data["delta"])
		if delta != nil && stringField(delta, "type") == "text_delta" {
			return []text{{
				key:   intIdent(index).key(0),
				order: order{int(index), 0},
				value: delta,
				field: "text",
				delta: true,
			}}
		}
		return nil
	case "response.completed", "response.incomplete":
		response := asMap(data["response"])
		if response == nil {
			return nil
		}
		var out []text
		for position, raw := range asList(response["output"]) {
			item := asMap(raw)
			if item == nil {
				continue
			}
			identity := item["id"]
			if identity == nil {
				identity = int64(position)
			}
			out = append(out, o.itemTexts(item, toIdent(identity), int64(position))...)
		}
		return out
	}

	item := asMap(data["item"])
	if kind == "response.output_item.added" || kind == "response.output_item.done" {
		if item == nil || stringField(item, "type") != "message" {
			return nil
		}
		identity := o.identity(data, item)
		return o.itemTexts(item, identity, intField(data, "output_index", 0))
	}

	if _, ok := partEventKinds[kind]; !ok {
		return nil
	}
	position := intField(data, "content_index", 0)
	key := o.identity(data, map[string]any{}).key(int(position))
	itemOrder := order{int(intField(data, "output_index", 0)), int(position)}
	if kind == "response.output_text.delta" || kind == "response.output_text.done" {
		isDelta := strings.HasSuffix(kind, ".delta")
		field := "text"
		if isDelta {
			field = "delta"
		}
		return []text{{key: key, order: itemOrder, value: data, field: field, delta: isDelta}}
	}
	part := asMap(data["part"])
	if part != nil && stringField(part, "type") == "output_text" {
		return []text{{key: key, order: itemOrder, value: part, field: "text"}}
	}
	return nil
}

func (o *Overlap) itemTexts(item map[string]any, id ident, index int64) []text {
	if stringField(item, "type") != "message" {
		return nil
	}
	var out []text
	for position, raw := range asList(item["content"]) {
		part := asMap(raw)
		if part == nil || stringField(part, "type") != "output_text" {
			continue
		}
		out = append(out, text{
			key:   id.key(position),
			order: order{int(index), position},
			value: part,
			field: "text",
		})
	}
	return out
}

func toIdent(value any) ident {
	switch v := value.(type) {
	case string:
		return strIdent(v)
	case int64:
		return intIdent(v)
	case int:
		return intIdent(int64(v))
	default:
		return intIdent(0)
	}
}

func (o *Overlap) closeParts(data map[string]any, terminal bool) {
	kind := stringField(data, "type")
	var closing *ident
	switch {
	case kind == "response.output_item.done":
		item := asMap(data["item"])
		if item != nil && stringField(item, "type") == "message" {
			id := o.identity(data, item)
			closing = &id
		}
	case kind == "content_block_stop":
		id := intIdent(intField(data, "index", 0))
		closing = &id
	}
	for key, part := range o.parts {
		if terminal || (closing != nil && key.matchesIdent(*closing)) {
			part.closed = true
		}
	}
}

func (k partKey) matchesIdent(id ident) bool {
	return k.isStr == id.isStr && k.s == id.s && k.n == id.n
}

func (o *Overlap) resolve(candidate string, candidates []keyPart) {
	overlap := o.overlapSize(candidate)
	remaining := len(candidate)
	o.pending = false
	o.parts = map[partKey]*part{}
	o.partOrder = nil
	o.partsSizeAcc = 0
	stringIdentities := map[string]struct{}{}
	for _, entry := range candidates {
		key, part := entry.key, entry.part
		if part.text == "" || remaining == 0 {
			continue
		}
		part.text = clip(part.text, remaining)
		remaining -= len(part.text)
		removed := overlap
		if len(part.text) < removed {
			removed = len(part.text)
		}
		part.removed = removed
		overlap -= removed
		part.fixed = remaining > 0
		// text changed during clipping: the retention tuple must be
		// re-serialized at the next size check. The accumulator was reset
		// above, so the stale cached size must be discarded too.
		part.dirty = true
		part.cachedSize = 0
		o.parts[key] = part
		o.partOrder = append(o.partOrder, key)
		frontier := part.order
		o.frontier = &frontier
		if key.isStr {
			stringIdentities[key.s] = struct{}{}
		}
	}
	filtered := map[int64]string{}
	for index, value := range o.ids {
		if _, ok := stringIdentities[value]; ok {
			filtered[index] = value
		}
	}
	o.ids = filtered
	o.idsDirty = true
}

func (o *Overlap) overlapSize(candidate string) int {
	upper := len(o.expected)
	if len(candidate) < upper {
		upper = len(candidate)
	}
	for size := upper; size > 0; size-- {
		if strings.HasSuffix(o.expected, candidate[:size]) {
			return size
		}
	}
	return 0
}

func (o *Overlap) settledText(t *text) error {
	value := stringField(t.value, t.field)
	part := o.parts[t.key]
	if part == nil {
		if value != "" && o.frontier != nil && t.order.less(*o.frontier) {
			return ErrInconsistent
		}
		return nil
	}
	t.part = part
	if t.delta {
		if value != "" && part.fixed {
			return ErrInconsistent
		}
		t.offset = part.seen
		part.seen += len(value)
		part.dirty = true
		return nil
	}
	if !strings.HasPrefix(value, part.text) ||
		(part.fixed && len(value) != part.seen) {
		return ErrInconsistent
	}
	if len(value) > part.seen {
		part.seen = len(value)
		part.dirty = true
	}
	return nil
}

func (o *Overlap) checkTerminalOrder(texts []text) error {
	keys := o.partOrder
	observed := make([]partKey, 0, len(texts))
	for _, t := range texts {
		if stringField(t.value, t.field) != "" {
			observed = append(observed, t.key)
		}
	}
	if len(observed) < len(keys) {
		return ErrInconsistent
	}
	for i, key := range keys {
		if observed[i] != key {
			return ErrInconsistent
		}
	}
	for i := range texts {
		if _, known := o.parts[texts[i].key]; !known {
			if o.frontier != nil {
				texts[i].order = order{o.frontier[0] + 1, 0}
			}
		}
	}
	return nil
}

func (o *Overlap) render(f *frame) []map[string]any {
	allDelta := len(f.texts) > 0
	anyValue := false
	for i := range f.texts {
		t := &f.texts[i]
		if t.part != nil {
			removed := t.part.removed
			if t.delta {
				removed = t.part.removed - t.offset
				if removed < 0 {
					removed = 0
				}
			}
			current := stringField(t.value, t.field)
			if removed > len(current) {
				removed = len(current)
			}
			t.value[t.field] = current[removed:]
		}
		allDelta = allDelta && t.delta
		if stringField(t.value, t.field) != "" {
			anyValue = true
		}
	}
	if allDelta && !anyValue {
		if candidates := asList(f.data["candidates"]); candidates != nil {
			for _, raw := range candidates {
				candidate := asMap(raw)
				content := asMap(candidate["content"])
				if content == nil {
					continue
				}
				kept := make([]any, 0, len(asList(content["parts"])))
				for _, rawPart := range asList(content["parts"]) {
					part := asMap(rawPart)
					if part == nil {
						continue
					}
					if _, hasText := part["text"]; hasText {
						kept = append(kept, part)
						continue
					}
					exotic := false
					for key := range part {
						if key != "text" && key != "thought" {
							exotic = true
						}
					}
					if exotic {
						kept = append(kept, part)
					}
				}
				content["parts"] = kept
			}
			if geminiFinished(f.data) || len(geminiParts(f.data)) > 0 {
				return []map[string]any{f.data}
			}
		}
		return nil
	}
	return []map[string]any{f.data}
}

// partTupleSize is the wire size of the retention tuple stored per part:
// ((identity, position), (order...), text, seen). The shape mirrors the
// Python policy's _check_size expression exactly.
func partTupleSize(key partKey, part *part) int {
	var builder strings.Builder
	writePartTuple(&builder, key, part)
	return builder.Len()
}

func (o *Overlap) checkSize() error {
	if o.idsDirty {
		o.idsSizeAcc = wireByteSize(o.ids)
		o.idsDirty = false
	}
	for key, part := range o.parts {
		if !part.dirty {
			continue
		}
		o.partsSizeAcc -= part.cachedSize
		part.cachedSize = partTupleSize(key, part)
		o.partsSizeAcc += part.cachedSize
		part.dirty = false
	}
	idsSize := o.idsSizeAcc
	partsSize := o.partsSizeAcc
	total := o.frameBytes + idsSize + partsSize
	o.sizeTrace = append(o.sizeTrace, SizeRecord{
		FrameBytes: o.frameBytes,
		IDsSize:    idsSize,
		PartsSize:  partsSize,
		Total:      total,
	})
	if total > o.limit {
		return ErrRetention
	}
	return nil
}

// geminiParts flattens candidates[].content.parts[].
func geminiParts(data map[string]any) []map[string]any {
	var out []map[string]any
	for _, rawCandidate := range asList(data["candidates"]) {
		candidate := asMap(rawCandidate)
		content := asMap(candidate["content"])
		if content == nil {
			continue
		}
		for _, rawPart := range asList(content["parts"]) {
			if part := asMap(rawPart); part != nil {
				out = append(out, part)
			}
		}
	}
	return out
}

// geminiFinished reports whether any candidate carries a finish reason.
func geminiFinished(data map[string]any) bool {
	for _, rawCandidate := range asList(data["candidates"]) {
		candidate := asMap(rawCandidate)
		if candidate != nil && stringField(candidate, "finishReason") != "" {
			return true
		}
	}
	return false
}
