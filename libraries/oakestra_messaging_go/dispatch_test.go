package messaging

import (
	"fmt"
	"sync"
	"testing"

	"gotest.tools/v3/assert"
)

// logRecorder is a Logger that records every message, so tests can assert a
// panic was actually logged rather than merely not crashing the test binary.
type logRecorder struct {
	mu   sync.Mutex
	logs []string
}

func (l *logRecorder) Printf(format string, v ...any) {
	l.mu.Lock()
	defer l.mu.Unlock()
	l.logs = append(l.logs, fmt.Sprintf(format, v...))
}

func (l *logRecorder) all() []string {
	l.mu.Lock()
	defer l.mu.Unlock()
	return append([]string(nil), l.logs...)
}

func TestDispatcher_DispatchCallsEveryMatchingHandler(t *testing.T) {
	d := NewDispatcher(nil)

	var calledA, calledB, calledC bool
	d.Add("nodes/+/information", func(Message) { calledA = true })
	d.Add("nodes/n1/#", func(Message) { calledB = true })
	d.Add("nodes/n2/information", func(Message) { calledC = true })

	n := d.Dispatch(Message{Topic: "nodes/n1/information"})

	assert.Equal(t, n, 2)
	assert.Assert(t, calledA)
	assert.Assert(t, calledB)
	assert.Assert(t, !calledC)
}

func TestDispatcher_PanicIsRecoveredAndOthersStillRun(t *testing.T) {
	logs := &logRecorder{}
	d := NewDispatcher(logs)

	var ranAfterPanic bool
	d.Add("nodes/n1/topic", func(Message) { panic("boom") })
	d.Add("nodes/n1/topic", func(Message) { ranAfterPanic = true })

	n := d.Dispatch(Message{Topic: "nodes/n1/topic"})

	assert.Equal(t, n, 2)
	assert.Assert(t, ranAfterPanic)

	found := false
	for _, l := range logs.all() {
		if l != "" {
			found = true
		}
	}
	assert.Assert(t, found, "expected the panic to be logged")
}

func TestDispatcher_Remove(t *testing.T) {
	d := NewDispatcher(nil)
	d.Add("nodes/n1/topic", func(Message) {})

	assert.Assert(t, d.Remove("nodes/n1/topic"))
	assert.Assert(t, !d.Remove("nodes/n1/topic"))

	n := d.Dispatch(Message{Topic: "nodes/n1/topic"})
	assert.Equal(t, n, 0)
}

func TestDispatcher_PatternsUniqueInsertionOrder(t *testing.T) {
	d := NewDispatcher(nil)
	d.Add("b", func(Message) {})
	d.Add("a", func(Message) {})
	d.Add("b", func(Message) {})

	assert.DeepEqual(t, d.Patterns(), []string{"b", "a"})
}
