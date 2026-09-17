package mqtt

import (
	"fmt"
	"sync"
	"time"

	"go_node_engine/model"

	mqtt "github.com/eclipse/paho.mqtt.golang"
)

// fakeToken is an already-resolved mqtt.Token: every call in this package
// waits on the token synchronously, so tests never need an async completion.
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

// fakeMessage implements mqtt.Message with fixed fields, enough to drive the
// package's handlers without a real broker.
type fakeMessage struct {
	topic    string
	payload  []byte
	qos      byte
	retained bool
	dup      bool
	id       uint16
}

func (m *fakeMessage) Duplicate() bool   { return m.dup }
func (m *fakeMessage) Qos() byte         { return m.qos }
func (m *fakeMessage) Retained() bool    { return m.retained }
func (m *fakeMessage) Topic() string     { return m.topic }
func (m *fakeMessage) MessageID() uint16 { return m.id }
func (m *fakeMessage) Payload() []byte   { return m.payload }
func (m *fakeMessage) Ack()              {}

// publishCall records one call to fakeClient.Publish, in the shape the
// package's own tests care about.
type publishCall struct {
	topic    string
	qos      byte
	retained bool
	payload  string
}

// fakeClient stands in for mainMqttClient. The embedded nil mqtt.Client makes
// any method we don't implement panic, so a new dependency on paho shows up
// immediately instead of silently doing nothing.
type fakeClient struct {
	mqtt.Client

	mu         sync.Mutex
	opts       *mqtt.ClientOptions
	connectErr error
	publishErr error
	connected  bool
	published  []publishCall
	subscribed map[string]byte
	dispatcher mqtt.MessageHandler

	// connectedCh, when set, gets one value right after a successful Connect().
	// runMqttClient assigns mainMqttClient before connecting, so waiting on this
	// instead of polling the global is race-free.
	connectedCh chan struct{}
}

func (c *fakeClient) Connect() mqtt.Token {
	c.mu.Lock()
	err := c.connectErr
	if err == nil {
		c.connected = true
	}
	ch := c.connectedCh
	c.mu.Unlock()

	if err == nil && ch != nil {
		select {
		case ch <- struct{}{}:
		default:
		}
	}
	if err != nil {
		return &fakeToken{err: err}
	}
	return &fakeToken{}
}

func (c *fakeClient) Publish(topic string, qos byte, retained bool, payload interface{}) mqtt.Token {
	var payloadStr string
	switch p := payload.(type) {
	case string:
		payloadStr = p
	case []byte:
		payloadStr = string(p)
	default:
		payloadStr = fmt.Sprintf("%v", p)
	}
	c.mu.Lock()
	c.published = append(c.published, publishCall{topic: topic, qos: qos, retained: retained, payload: payloadStr})
	err := c.publishErr
	c.mu.Unlock()
	return &fakeToken{err: err}
}

func (c *fakeClient) SubscribeMultiple(filters map[string]byte, callback mqtt.MessageHandler) mqtt.Token {
	c.mu.Lock()
	c.subscribed = filters
	c.dispatcher = callback
	c.mu.Unlock()
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

func (c *fakeClient) publishCalls() []publishCall {
	c.mu.Lock()
	defer c.mu.Unlock()
	return append([]publishCall(nil), c.published...)
}

// fakeRuntime is a ServiceRuntime double whose Deploy/Undeploy behavior the
// test controls per case.
type fakeRuntime struct {
	deployFn   func(service model.Service, statusChangeNotificationHandler func(service model.Service)) error
	undeployFn func(sname string, instance int) error
}

func (r *fakeRuntime) Deploy(service model.Service, statusChangeNotificationHandler func(service model.Service)) error {
	if r.deployFn == nil {
		return nil
	}
	return r.deployFn(service, statusChangeNotificationHandler)
}

func (r *fakeRuntime) Undeploy(sname string, instance int) error {
	if r.undeployFn == nil {
		return nil
	}
	return r.undeployFn(sname, instance)
}

// fakeProvider is a RuntimeProvider double that always returns rt and
// records which runtime types were requested, so tests can assert
// deployHandler/deleteHandler forward service.Runtime correctly.
type fakeProvider struct {
	rt ServiceRuntime

	mu        sync.Mutex
	requested []model.RuntimeType
}

func (p *fakeProvider) GetRuntime(rt model.RuntimeType) ServiceRuntime {
	p.mu.Lock()
	p.requested = append(p.requested, rt)
	p.mu.Unlock()
	return p.rt
}

func (p *fakeProvider) requestedTypes() []model.RuntimeType {
	p.mu.Lock()
	defer p.mu.Unlock()
	return append([]model.RuntimeType(nil), p.requested...)
}
