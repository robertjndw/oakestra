package mqtt

import (
	"fmt"
	"net"
	"os"
	"sync"
	"testing"
	"time"

	messaging "github.com/oakestra/oakestra/libraries/oakestra_messaging_go"

	paho "github.com/eclipse/paho.mqtt.golang"
	"gotest.tools/v3/assert"
)

// brokerFromEnv skips the test unless a real broker address was provided, so
// these tests stay out of the default `go test ./...` run.
func brokerFromEnv(t *testing.T) (host, port string) {
	t.Helper()
	addr := os.Getenv("OAKESTRA_TEST_MQTT_ADDR")
	if addr == "" {
		t.Skip("OAKESTRA_TEST_MQTT_ADDR not set; skipping broker-backed integration test")
	}
	host, port, err := net.SplitHostPort(addr)
	assert.NilError(t, err, "OAKESTRA_TEST_MQTT_ADDR must be host:port")
	return host, port
}

// uniqueSuffix gives every test its own topic namespace, so tests can run
// concurrently against a shared broker without cross-talk.
func uniqueSuffix() string {
	return fmt.Sprintf("it-%d", time.Now().UnixNano())
}

// uniqueTopic is uniqueSuffix wired into a single %s in format, for tests
// that only need one topic of their own.
func uniqueTopic(format string) string {
	return fmt.Sprintf(format, uniqueSuffix())
}

// connectedBus builds a real, paho-backed Bus and connects it, failing the
// test on any error.
func connectedBus(t *testing.T, host, port string, qos byte) *Bus {
	t.Helper()
	b, err := NewBus(Config{
		BrokerURL:  host,
		BrokerPort: port,
		ClientID:   uniqueTopic("bus-%s"),
		QoS:        qos,
	})
	assert.NilError(t, err)
	assert.NilError(t, b.Connect())
	t.Cleanup(func() { _ = b.Close() })
	return b
}

// bareSubscriber is a plain paho client, standing in for a peer this library
// doesn't control, used to observe what actually goes over the wire (QoS,
// retained flag) rather than what the Bus claims to have sent.
type bareSubscriber struct {
	client   paho.Client
	messages chan paho.Message
}

func newBareSubscriber(t *testing.T, host, port, topic string, qos byte) *bareSubscriber {
	t.Helper()
	messages := make(chan paho.Message, 16)
	subscribed := make(chan struct{}, 1)

	opts := paho.NewClientOptions()
	opts.AddBroker(fmt.Sprintf("tcp://%s:%s", host, port))
	opts.SetClientID(uniqueTopic("peer-%s"))
	opts.OnConnect = func(c paho.Client) {
		tok := c.Subscribe(topic, qos, func(_ paho.Client, m paho.Message) { messages <- m })
		tok.Wait()
		subscribed <- struct{}{}
	}

	client := paho.NewClient(opts)
	tok := client.Connect()
	assert.Assert(t, tok.WaitTimeout(5*time.Second), "peer timed out connecting")
	assert.NilError(t, tok.Error())

	select {
	case <-subscribed:
	case <-time.After(5 * time.Second):
		t.Fatal("peer timed out subscribing")
	}

	t.Cleanup(func() { client.Disconnect(250) })
	return &bareSubscriber{client: client, messages: messages}
}

func (p *bareSubscriber) expect(t *testing.T, timeout time.Duration) paho.Message {
	t.Helper()
	select {
	case m := <-p.messages:
		return m
	case <-time.After(timeout):
		t.Fatal("timed out waiting for a message")
		return nil
	}
}

func TestIntegration_RoundTripWithPlusPattern(t *testing.T) {
	host, port := brokerFromEnv(t)
	suffix := uniqueSuffix()
	pattern := fmt.Sprintf("oakestra-messaging/%s/+/echo", suffix)
	topic := fmt.Sprintf("oakestra-messaging/%s/leaf/echo", suffix)

	b := connectedBus(t, host, port, 1)
	received := make(chan messaging.Message, 1)
	assert.NilError(t, b.Subscribe(pattern, func(m messaging.Message) { received <- m }))

	assert.NilError(t, b.Publish(topic, []byte("hello")))

	select {
	case m := <-received:
		assert.Equal(t, m.Topic, topic)
		assert.DeepEqual(t, m.Payload, []byte("hello"))
	case <-time.After(5 * time.Second):
		t.Fatal("timed out waiting for round-tripped message")
	}
}

func TestIntegration_QoSObservedOnWire(t *testing.T) {
	host, port := brokerFromEnv(t)

	for _, qos := range []byte{0, 1} {
		qos := qos
		t.Run(fmt.Sprintf("qos%d", qos), func(t *testing.T) {
			topic := uniqueTopic("oakestra-messaging/qos/%s")
			peer := newBareSubscriber(t, host, port, topic, 1)

			b := connectedBus(t, host, port, qos)
			assert.NilError(t, b.Publish(topic, []byte("payload")))

			msg := peer.expect(t, 5*time.Second)
			assert.Equal(t, msg.Qos(), qos)
			assert.Equal(t, msg.Retained(), false)
		})
	}
}

func TestIntegration_PanicInHandlerDoesNotStopLaterDelivery(t *testing.T) {
	host, port := brokerFromEnv(t)
	topic := uniqueTopic("oakestra-messaging/panic/%s")

	b := connectedBus(t, host, port, 1)
	var mu sync.Mutex
	count := 0
	assert.NilError(t, b.Subscribe(topic, func(messaging.Message) {
		mu.Lock()
		count++
		n := count
		mu.Unlock()
		if n == 1 {
			panic("boom")
		}
	}))

	assert.NilError(t, b.Publish(topic, []byte("first")))
	assert.NilError(t, b.Publish(topic, []byte("second")))

	assert.Assert(t, pollUntil(5*time.Second, func() bool {
		mu.Lock()
		defer mu.Unlock()
		return count >= 2
	}), "expected the second message to be delivered despite the first handler panicking")
}

func TestIntegration_UnsubscribeStopsDelivery(t *testing.T) {
	host, port := brokerFromEnv(t)
	topic := uniqueTopic("oakestra-messaging/unsub/%s")

	b := connectedBus(t, host, port, 1)
	received := make(chan messaging.Message, 4)
	assert.NilError(t, b.Subscribe(topic, func(m messaging.Message) { received <- m }))

	assert.NilError(t, b.Publish(topic, []byte("before")))
	select {
	case <-received:
	case <-time.After(5 * time.Second):
		t.Fatal("timed out waiting for the pre-unsubscribe message")
	}

	assert.NilError(t, b.Unsubscribe(topic))
	assert.NilError(t, b.Publish(topic, []byte("after")))

	select {
	case m := <-received:
		t.Fatalf("expected no message after unsubscribe, got one: %s", m.Payload)
	case <-time.After(time.Second):
	}
}

func TestIntegration_PublishImmediatelyAfterConnectIsReceived(t *testing.T) {
	host, port := brokerFromEnv(t)
	topic := uniqueTopic("oakestra-messaging/immediate/%s")

	subscriber, err := NewBus(Config{
		BrokerURL:  host,
		BrokerPort: port,
		ClientID:   uniqueTopic("immediate-sub-%s"),
		QoS:        1,
	})
	assert.NilError(t, err)
	received := make(chan messaging.Message, 1)
	assert.NilError(t, subscriber.Subscribe(topic, func(m messaging.Message) { received <- m }))

	// Connect is documented to block until the broker has acked every
	// registered subscription, so a publish issued the instant it returns
	// must never race the subscribe.
	assert.NilError(t, subscriber.Connect())
	t.Cleanup(func() { _ = subscriber.Close() })

	publisher := connectedBus(t, host, port, 1)
	assert.NilError(t, publisher.Publish(topic, []byte("go")))

	select {
	case m := <-received:
		assert.Equal(t, m.Topic, topic)
	case <-time.After(5 * time.Second):
		t.Fatal("timed out waiting for the immediate publish")
	}
}

func pollUntil(timeout time.Duration, cond func() bool) bool {
	deadline := time.Now().Add(timeout)
	for {
		if cond() {
			return true
		}
		if time.Now().After(deadline) {
			return false
		}
		time.Sleep(10 * time.Millisecond)
	}
}
