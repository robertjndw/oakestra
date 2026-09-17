// Package memory is an in-process messaging.Bus for tests: no broker, no
// goroutines racing a network socket, synchronous dispatch on Deliver. It
// runs inbound messages through the same messaging.Dispatcher the mqtt
// adapter uses, so a test exercising routing here exercises production
// routing too.
package memory

import (
	"sync"

	messaging "github.com/oakestra/oakestra/libraries/oakestra_messaging_go"
)

// Bus is an in-memory messaging.Bus. It records every Publish call and, on
// Deliver, also loops published messages back to any local subscription
// that matches - useful for tests that publish and expect to observe their
// own message routed like a broker echo would.
type Bus struct {
	mu   sync.Mutex
	disp *messaging.Dispatcher

	connected     bool
	connectErr    error
	publishErr    error
	published     []messaging.Message
	subscriptions []string
}

var _ messaging.Bus = (*Bus)(nil)

// New creates a ready-to-use Bus. Connect still has to be called; Subscribe
// works beforehand, matching the messaging.Bus contract.
func New() *Bus {
	return &Bus{disp: messaging.NewDispatcher(nil)}
}

func (b *Bus) Connect() error {
	b.mu.Lock()
	defer b.mu.Unlock()
	if b.connectErr != nil {
		return b.connectErr
	}
	b.connected = true
	return nil
}

// Publish records the message and delivers it to any local subscription
// whose pattern matches topic, the way a broker would echo a publish back to
// the publisher's own subscriptions.
func (b *Bus) Publish(topic string, payload []byte) error {
	b.mu.Lock()
	err := b.publishErr
	if err == nil {
		b.published = append(b.published, messaging.Message{Topic: topic, Payload: payload})
	}
	b.mu.Unlock()
	if err != nil {
		return err
	}
	b.disp.Dispatch(messaging.Message{Topic: topic, Payload: payload})
	return nil
}

func (b *Bus) Subscribe(pattern string, h messaging.Handler) error {
	b.mu.Lock()
	b.subscriptions = append(b.subscriptions, pattern)
	b.mu.Unlock()
	b.disp.Add(pattern, h)
	return nil
}

func (b *Bus) Unsubscribe(pattern string) error {
	b.mu.Lock()
	for i, p := range b.subscriptions {
		if p == pattern {
			b.subscriptions = append(b.subscriptions[:i], b.subscriptions[i+1:]...)
			break
		}
	}
	b.mu.Unlock()
	b.disp.Remove(pattern)
	return nil
}

func (b *Bus) Close() error {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.connected = false
	return nil
}

// Deliver injects an inbound message, as if it arrived from a broker, and
// dispatches it synchronously. It returns how many handlers ran.
func (b *Bus) Deliver(topic string, payload []byte) int {
	return b.disp.Dispatch(messaging.Message{Topic: topic, Payload: payload})
}

// Published returns a copy of every message passed to Publish so far.
func (b *Bus) Published() []messaging.Message {
	b.mu.Lock()
	defer b.mu.Unlock()
	return append([]messaging.Message(nil), b.published...)
}

// Subscriptions returns a copy of the currently registered patterns.
func (b *Bus) Subscriptions() []string {
	b.mu.Lock()
	defer b.mu.Unlock()
	return append([]string(nil), b.subscriptions...)
}

// SetPublishError makes every subsequent Publish call fail with err.
func (b *Bus) SetPublishError(err error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.publishErr = err
}

// SetConnectError makes the next Connect call fail with err.
func (b *Bus) SetConnectError(err error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.connectErr = err
}

func (b *Bus) Connected() bool {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.connected
}
