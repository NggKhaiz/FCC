// Package retention accounts retained stream evidence against a fixed byte
// budget. The estimator is a line-by-line port of the Python
// _json_byte_size helper: retention only needs a monotonic approximation
// compared against a one-megabyte threshold, so full serialization is wasted
// work on every observed frame.
package retention

import (
	"encoding/json"
	"fmt"
	"strconv"
)

// LimitBytes is RECOVERY_RETENTION_BYTES in the Python implementation.
const LimitBytes = 1 << 20

// JSONByteSize estimates the UTF-8 JSON size of value without serializing it.
// Structural delimiters are approximated by fixed overhead so the estimate
// stays close to, and never systematically below, the serialized size for
// the payload shapes retained on a stream.
func JSONByteSize(value any) int {
	switch v := value.(type) {
	case nil:
		return 4
	case bool:
		if v {
			return 4
		}
		return 5
	case json.Number:
		return len(v)
	case int:
		return len(strconv.Itoa(v))
	case int64:
		return len(strconv.FormatInt(v, 10))
	case float64:
		return len(strconv.FormatFloat(v, 'g', -1, 64))
	case string:
		return len(v) + 2
	case map[string]any:
		if len(v) == 0 {
			return 2
		}
		total := 2
		for key, item := range v {
			total += len(key) + 4 + JSONByteSize(item)
		}
		return total
	case []any:
		if len(v) == 0 {
			return 2
		}
		total := 2
		for _, item := range v {
			total += JSONByteSize(item) + 1
		}
		return total
	default:
		return len(fmt.Sprint(v))
	}
}

// Budget tracks the retained byte tally for one response. Once the limit is
// exceeded the budget stays limited and every further Retain call is rejected,
// matching DeliveredResponse._retain. The estimate can only trigger retention
// earlier than the serialized-size implementation, never later.
type Budget struct {
	bytes   int
	limited bool
}

// Retain adds value's estimated size and reports whether the budget still
// accepts it. A rejected value is not retained by the caller.
func (b *Budget) Retain(value any) bool {
	if b.limited {
		return false
	}
	b.bytes += JSONByteSize(value)
	if b.bytes > LimitBytes {
		b.limited = true
		return false
	}
	return true
}

// Bytes reports the cumulative estimated tally.
func (b *Budget) Bytes() int {
	return b.bytes
}

// Limited reports whether the retention limit has been crossed.
func (b *Budget) Limited() bool {
	return b.limited
}
