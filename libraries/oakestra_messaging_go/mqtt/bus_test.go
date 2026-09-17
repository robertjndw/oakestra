package mqtt

import (
	"errors"
	"testing"
	"time"

	messaging "github.com/oakestra/oakestra/libraries/oakestra_messaging_go"

	"gotest.tools/v3/assert"
)

func TestNewBus_ClientIDPassthrough(t *testing.T) {
	b, _ := newTestBus(t, func(c *Config) { c.ClientID = "worker-7" })
	assert.Equal(t, b.ClientID(), "worker-7")
}

func TestNewBus_RequiredFields(t *testing.T) {
	cases := []Config{
		{BrokerPort: "1883", ClientID: "c"},
		{BrokerURL: "host", ClientID: "c"},
		{BrokerURL: "host", BrokerPort: "1883"},
	}
	for _, cfg := range cases {
		_, err := NewBus(cfg)
		assert.ErrorContains(t, err, "required")
	}
}

func TestNewBus_PlaintextBroker(t *testing.T) {
	b, fc := newTestBus(t, nil)
	_ = b
	assert.Equal(t, len(fc.opts.Servers), 1)
	assert.Equal(t, fc.opts.Servers[0].Scheme, "tcp")
}

func TestNewBus_TLSBroker(t *testing.T) {
	cert, key := writeTestCertPair(t)
	b, fc := newTestBus(t, func(c *Config) {
		c.CertFile = cert
		c.KeyFile = key
	})
	_ = b
	assert.Equal(t, len(fc.opts.Servers), 1)
	assert.Equal(t, fc.opts.Servers[0].Scheme, "tls")
	assert.Assert(t, fc.opts.TLSConfig != nil)
	assert.Equal(t, len(fc.opts.TLSConfig.Certificates), 1)
}

func TestNewBus_BadCertPathErrors(t *testing.T) {
	_, err := NewBus(Config{
		BrokerURL:  "host",
		BrokerPort: "1883",
		ClientID:   "c",
		CertFile:   "/nonexistent/cert.pem",
		KeyFile:    "/nonexistent/key.pem",
	})
	assert.ErrorContains(t, err, "certificate")
}

func TestNewBus_OptionDefaults(t *testing.T) {
	_, fc := newTestBus(t, nil)
	assert.Equal(t, fc.opts.Username, "")
	assert.Equal(t, fc.opts.Password, "")
	assert.Assert(t, fc.opts.DefaultPublishHandler != nil)
	assert.Assert(t, fc.opts.OnConnect != nil)
	assert.Assert(t, fc.opts.OnConnectionLost != nil)
	assert.Equal(t, fc.opts.KeepAlive, int64(30))
	assert.Assert(t, fc.opts.AutoReconnect)
	assert.Assert(t, fc.opts.CleanSession)
	assert.Assert(t, !fc.opts.ConnectRetry)
}

func TestNewBus_KeepAliveOverridable(t *testing.T) {
	_, fc := newTestBus(t, func(c *Config) { c.KeepAlive = 45 * time.Second })
	assert.Equal(t, fc.opts.KeepAlive, int64(45))
}

func TestBus_ConnectErrorPropagates(t *testing.T) {
	wantErr := errors.New("refused")
	b, fc := newTestBus(t, nil)
	fc.connectErr = wantErr

	err := b.Connect()
	assert.ErrorContains(t, err, "refused")
}

func TestBus_ConnectSubscribesAllRegisteredPatterns(t *testing.T) {
	b, fc := newTestBus(t, func(c *Config) { c.QoS = 1 })
	assert.NilError(t, b.Subscribe("nodes/+/job", func(messaging.Message) {}))
	assert.NilError(t, b.Subscribe("nodes/+/information", func(messaging.Message) {}))

	assert.NilError(t, b.Connect())

	calls := fc.subscribeMultipleCalls()
	assert.Equal(t, len(calls), 1)
	assert.DeepEqual(t, calls[0].filters, map[string]byte{
		"nodes/+/job":         1,
		"nodes/+/information": 1,
	})
}

func TestBus_ConnectTimesOutWhenOnConnectNeverFires(t *testing.T) {
	b, fc := newTestBus(t, func(c *Config) { c.SubscribeTimeout = 20 * time.Millisecond })
	fc.blockConnect = true

	err := b.Connect()
	assert.ErrorContains(t, err, "timed out")
}

func TestBus_SubscribeAfterConnectCallsSubscribe(t *testing.T) {
	b, fc := newTestBus(t, func(c *Config) { c.QoS = 1 })
	assert.NilError(t, b.Connect())

	assert.NilError(t, b.Subscribe("nodes/+/job", func(messaging.Message) {}))

	calls := fc.subscribeCalls()
	assert.Equal(t, len(calls), 1)
	assert.Equal(t, calls[0].pattern, "nodes/+/job")
	assert.Equal(t, calls[0].qos, byte(1))
}

func TestBus_SubscribeBeforeConnectDoesNotCallClientSubscribe(t *testing.T) {
	b, fc := newTestBus(t, nil)
	assert.NilError(t, b.Subscribe("nodes/+/job", func(messaging.Message) {}))
	assert.Equal(t, len(fc.subscribeCalls()), 0)
	_ = b
}

func TestBus_Unsubscribe(t *testing.T) {
	b, fc := newTestBus(t, nil)
	called := false
	assert.NilError(t, b.Subscribe("nodes/+/job", func(messaging.Message) { called = true }))

	assert.NilError(t, b.Unsubscribe("nodes/+/job"))

	assert.DeepEqual(t, fc.unsubscribeCalls(), []string{"nodes/+/job"})
	fc.deliver(&fakeMessage{topic: "nodes/n1/job"})
	assert.Assert(t, !called)
}

func TestBus_PublishSendsQoSAndNeverRetained(t *testing.T) {
	b, fc := newTestBus(t, func(c *Config) { c.QoS = 1 })

	assert.NilError(t, b.Publish("nodes/n1/job", []byte("payload")))

	calls := fc.publishCalls()
	assert.Equal(t, len(calls), 1)
	assert.Equal(t, calls[0].topic, "nodes/n1/job")
	assert.Equal(t, calls[0].qos, byte(1))
	assert.Assert(t, !calls[0].retained)
	assert.Equal(t, calls[0].payload, "payload")
}

func TestBus_PublishReturnsTokenError(t *testing.T) {
	wantErr := errors.New("broker gone")
	b, fc := newTestBus(t, nil)
	fc.publishErr = wantErr

	err := b.Publish("topic", nil)
	assert.ErrorContains(t, err, "broker gone")
}

func TestBus_DispatchThroughDefaultHandlerUsesMatches(t *testing.T) {
	b, fc := newTestBus(t, nil)
	var got messaging.Message
	assert.NilError(t, b.Subscribe("nodes/+/job", func(m messaging.Message) { got = m }))

	fc.deliver(&fakeMessage{topic: "nodes/n1/job", payload: []byte("hi")})
	assert.Equal(t, got.Topic, "nodes/n1/job")
	assert.DeepEqual(t, got.Payload, []byte("hi"))

	got = messaging.Message{}
	fc.deliver(&fakeMessage{topic: "nodes/n1/extra/job", payload: []byte("nope")})
	assert.Equal(t, got.Topic, "")
}

func TestBus_HandlerPanicRecoveredNextMessageStillDispatched(t *testing.T) {
	b, fc := newTestBus(t, nil)
	var second bool
	assert.NilError(t, b.Subscribe("a/b", func(messaging.Message) { panic("boom") }))
	assert.NilError(t, b.Subscribe("a/b", func(messaging.Message) { second = true }))

	fc.deliver(&fakeMessage{topic: "a/b"})
	assert.Assert(t, second)

	second = false
	fc.deliver(&fakeMessage{topic: "a/b"})
	assert.Assert(t, second)
}

func TestBus_Close(t *testing.T) {
	b, fc := newTestBus(t, nil)
	assert.NilError(t, b.Connect())
	assert.NilError(t, b.Close())
	assert.Assert(t, !fc.IsConnected())
}
