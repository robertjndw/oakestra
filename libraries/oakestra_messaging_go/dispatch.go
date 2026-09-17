package messaging

import (
	"log"
	"sync"
)

// Dispatcher routes an inbound Message to every handler whose pattern
// matches it. mqtt and memory adapters share one Dispatcher, so panic
// isolation only needs testing once, not per adapter.
type Dispatcher struct {
	mu       sync.Mutex
	order    []string
	handlers map[string][]Handler
	log      Logger
}

// NewDispatcher creates a Dispatcher that logs recovered handler panics
// through log. A nil log falls back to log.Default().
func NewDispatcher(l Logger) *Dispatcher {
	if l == nil {
		l = log.Default()
	}
	return &Dispatcher{handlers: make(map[string][]Handler), log: l}
}

// Add registers h for pattern. A pattern may carry more than one handler;
// all of them run when a message matches.
func (d *Dispatcher) Add(pattern string, h Handler) {
	d.mu.Lock()
	defer d.mu.Unlock()
	if _, ok := d.handlers[pattern]; !ok {
		d.order = append(d.order, pattern)
	}
	d.handlers[pattern] = append(d.handlers[pattern], h)
}

// Remove drops every handler registered for pattern and reports whether the
// pattern had any.
func (d *Dispatcher) Remove(pattern string) bool {
	d.mu.Lock()
	defer d.mu.Unlock()
	if _, ok := d.handlers[pattern]; !ok {
		return false
	}
	delete(d.handlers, pattern)
	for i, p := range d.order {
		if p == pattern {
			d.order = append(d.order[:i], d.order[i+1:]...)
			break
		}
	}
	return true
}

// Patterns returns the currently registered patterns, unique, in the order
// they were first added. Adapters use this to (re-)subscribe on connect.
func (d *Dispatcher) Patterns() []string {
	d.mu.Lock()
	defer d.mu.Unlock()
	return append([]string(nil), d.order...)
}

// Dispatch calls every handler whose pattern matches m.Topic and returns how
// many handlers ran. A handler panic is recovered and logged with the topic
// so the rest keep running - on the mqtt adapter an unrecovered panic would
// take down the shared network goroutine along with every other subscriber.
func (d *Dispatcher) Dispatch(m Message) int {
	d.mu.Lock()
	var matched []Handler
	for _, pattern := range d.order {
		if !Matches(pattern, m.Topic) {
			continue
		}
		matched = append(matched, d.handlers[pattern]...)
	}
	d.mu.Unlock()

	for _, h := range matched {
		d.runHandler(h, m)
	}
	return len(matched)
}

func (d *Dispatcher) runHandler(h Handler, m Message) {
	defer func() {
		if r := recover(); r != nil {
			d.log.Printf("messaging: handler for topic %s panicked: %v", m.Topic, r)
		}
	}()
	h(m)
}
