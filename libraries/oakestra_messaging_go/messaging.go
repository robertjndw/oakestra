// Package messaging is the pub/sub interface shared by Oakestra's Go
// services. Transport adapters (mqtt, memory) implement Bus.
package messaging

// Message is one inbound or outbound payload on a topic.
type Message struct {
	Topic   string
	Payload []byte
}

// Handler processes one delivered Message. Handlers run on the adapter's
// dispatch path, so a panicking handler must not be allowed to take down
// that path; see Dispatcher.
type Handler func(Message)

// Logger is the subset of *log.Logger the adapters need, so callers can pass
// their own service logger instead of the adapter hard-coding one.
type Logger interface {
	Printf(format string, v ...any)
}

// Bus is implemented by each transport adapter (mqtt, memory).
type Bus interface {
	// Connect blocks until the transport is connected and every pattern
	// registered so far has been acknowledged by the broker.
	Connect() error

	// Publish sends payload on topic. Messages are never retained.
	Publish(topic string, payload []byte) error

	// Subscribe registers h for pattern. It is valid before Connect, and the
	// registration is re-issued on every (re)connect.
	Subscribe(pattern string, h Handler) error

	// Unsubscribe drops every handler registered for pattern.
	Unsubscribe(pattern string) error

	Close() error
}
