package mqtt

import (
	"fmt"
	"sync"
	"time"

	paho "github.com/eclipse/paho.mqtt.golang"
)

// fakeToken is an already-resolved paho.Token: Wait/WaitTimeout return
// immediately, Done is pre-closed, and Error reports whatever the fake
// client was told to fail with. Every call in this package waits on its
// token synchronously, so tests never need an async completion.
type fakeToken struct {
	err error
}

func (t *fakeToken) Wait() bool                       { return true }
func (t *fakeToken) WaitTimeout(_ time.Duration) bool { return true }
func (t *fakeToken) Done() <-chan struct{} {
	ch := make(chan struct{})
	close(ch)
	return ch
}
func (t *fakeToken) Error() error { return t.err }

// blockingToken never resolves on its own; a test that wants Connect to time
// out waiting for OnConnect uses this so tok.Wait() still returns quickly
// (the connect handshake itself succeeds) while OnConnect is never invoked.
type blockingToken struct{}

func (t *blockingToken) Wait() bool                       { return true }
func (t *blockingToken) WaitTimeout(_ time.Duration) bool { return false }
func (t *blockingToken) Done() <-chan struct{}            { return make(chan struct{}) }
func (t *blockingToken) Error() error                     { return nil }

type publishCall struct {
	topic    string
	qos      byte
	retained bool
	payload  string
}

type subscribeCall struct {
	pattern string
	qos     byte
}

type subscribeMultipleCall struct {
	filters map[string]byte
}

// fakeClient stands in for the paho client the Bus wraps. The embedded nil
// paho.Client makes any method this fake doesn't implement panic, so a new
// dependency on paho shows up as a test failure instead of doing nothing.
type fakeClient struct {
	paho.Client

	mu sync.Mutex

	opts       *paho.ClientOptions
	connectErr error
	publishErr error
	connected  bool

	published          []publishCall
	subscribes         []subscribeCall
	subscribeMultiples []subscribeMultipleCall
	unsubscribes       []string

	// blockConnect, when set, makes Connect() return a token whose Wait()
	// succeeds without ever invoking opts.OnConnect - simulating a broker
	// that accepts the TCP handshake but never acks a subscription.
	blockConnect bool
}

func newFakeClient(opts *paho.ClientOptions) *fakeClient {
	return &fakeClient{opts: opts}
}

func (c *fakeClient) Connect() paho.Token {
	c.mu.Lock()
	blockConnect := c.blockConnect
	err := c.connectErr
	if err == nil && !blockConnect {
		c.connected = true
	}
	opts := c.opts
	c.mu.Unlock()

	if err != nil {
		return &fakeToken{err: err}
	}
	if blockConnect {
		return &blockingToken{}
	}
	if opts.OnConnect != nil {
		opts.OnConnect(c)
	}
	return &fakeToken{}
}

func (c *fakeClient) Disconnect(quiesce uint) {
	c.mu.Lock()
	c.connected = false
	c.mu.Unlock()
}

func (c *fakeClient) IsConnected() bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.connected
}

func (c *fakeClient) Publish(topic string, qos byte, retained bool, payload interface{}) paho.Token {
	var s string
	switch p := payload.(type) {
	case string:
		s = p
	case []byte:
		s = string(p)
	default:
		s = fmt.Sprintf("%v", p)
	}
	c.mu.Lock()
	c.published = append(c.published, publishCall{topic: topic, qos: qos, retained: retained, payload: s})
	err := c.publishErr
	c.mu.Unlock()
	return &fakeToken{err: err}
}

func (c *fakeClient) Subscribe(pattern string, qos byte, callback paho.MessageHandler) paho.Token {
	c.mu.Lock()
	c.subscribes = append(c.subscribes, subscribeCall{pattern: pattern, qos: qos})
	c.mu.Unlock()
	return &fakeToken{}
}

func (c *fakeClient) SubscribeMultiple(filters map[string]byte, callback paho.MessageHandler) paho.Token {
	cp := make(map[string]byte, len(filters))
	for k, v := range filters {
		cp[k] = v
	}
	c.mu.Lock()
	c.subscribeMultiples = append(c.subscribeMultiples, subscribeMultipleCall{filters: cp})
	c.mu.Unlock()
	return &fakeToken{}
}

func (c *fakeClient) Unsubscribe(topics ...string) paho.Token {
	c.mu.Lock()
	c.unsubscribes = append(c.unsubscribes, topics...)
	c.mu.Unlock()
	return &fakeToken{}
}

// deliver hands msg to the client's configured DefaultPublishHandler, the
// same path a real broker message takes.
func (c *fakeClient) deliver(msg paho.Message) {
	c.opts.DefaultPublishHandler(c, msg)
}

func (c *fakeClient) publishCalls() []publishCall {
	c.mu.Lock()
	defer c.mu.Unlock()
	return append([]publishCall(nil), c.published...)
}

func (c *fakeClient) subscribeCalls() []subscribeCall {
	c.mu.Lock()
	defer c.mu.Unlock()
	return append([]subscribeCall(nil), c.subscribes...)
}

func (c *fakeClient) subscribeMultipleCalls() []subscribeMultipleCall {
	c.mu.Lock()
	defer c.mu.Unlock()
	return append([]subscribeMultipleCall(nil), c.subscribeMultiples...)
}

func (c *fakeClient) unsubscribeCalls() []string {
	c.mu.Lock()
	defer c.mu.Unlock()
	return append([]string(nil), c.unsubscribes...)
}

// fakeMessage implements paho.Message with fixed fields.
type fakeMessage struct {
	topic   string
	payload []byte
	qos     byte
}

func (m *fakeMessage) Duplicate() bool   { return false }
func (m *fakeMessage) Qos() byte         { return m.qos }
func (m *fakeMessage) Retained() bool    { return false }
func (m *fakeMessage) Topic() string     { return m.topic }
func (m *fakeMessage) MessageID() uint16 { return 0 }
func (m *fakeMessage) Payload() []byte   { return m.payload }
func (m *fakeMessage) Ack()              {}
