package continuation

import (
	"strings"

	"github.com/NggKhaiz/FCC/gocore/wirejson"
)

// wireByteSize is the retained-data size of value under the simplejson wire
// convention shared with the Python policy. The actual encoder lives in the
// wirejson package so delivery and continuation cannot drift apart.
func wireByteSize(value any) int {
	return wirejson.Bytes(value)
}

// writePartTuple emits the retention tuple ((identity, position),
// (order...), text, seen) directly into the builder, avoiding the
// intermediate slices a generic encode would allocate. The emitted bytes are
// byte-identical to a wirejson encode of the nested arrays.
func writePartTuple(builder *strings.Builder, key partKey, part *part) {
	builder.WriteByte('[')
	builder.WriteByte('[')
	if key.isStr {
		wirejson.WriteJSONString(builder, key.s)
	} else {
		builder.WriteString(itoa64(key.n))
	}
	builder.WriteString(", ")
	builder.WriteString(itoa(key.pos))
	builder.WriteString("], [")
	builder.WriteString(itoa(part.order[0]))
	builder.WriteString(", ")
	builder.WriteString(itoa(part.order[1]))
	builder.WriteString("], ")
	wirejson.WriteJSONString(builder, part.text)
	builder.WriteString(", ")
	builder.WriteString(itoa(part.seen))
	builder.WriteByte(']')
}

func itoa(n int) string {
	return itoa64(int64(n))
}

func itoa64(n int64) string {
	if n == 0 {
		return "0"
	}
	neg := n < 0
	if neg {
		n = -n
	}
	var buf [20]byte
	i := len(buf)
	for n > 0 {
		i--
		buf[i] = byte('0' + n%10)
		n /= 10
	}
	if neg {
		i--
		buf[i] = '-'
	}
	return string(buf[i:])
}
