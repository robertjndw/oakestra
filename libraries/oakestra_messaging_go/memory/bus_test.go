package memory

import (
	"errors"
	"testing"

	messaging "github.com/oakestra/oakestra/libraries/oakestra_messaging_go"

	"gotest.tools/v3/assert"
)

func TestBus_ConnectAndConnected(t *testing.T) {
	b := New()
	assert.Assert(t, !b.Connected())
	assert.NilError(t, b.Connect())
	assert.Assert(t, b.Connected())
}

func TestBus_PublishRecordsMessage(t *testing.T) {
	b := New()
	assert.NilError(t, b.Publish("nodes/n1/job", []byte("payload")))

	got := b.Published()
	assert.Equal(t, len(got), 1)
	assert.Equal(t, got[0].Topic, "nodes/n1/job")
	assert.DeepEqual(t, got[0].Payload, []byte("payload"))
}

func TestBus_DeliverRoutesToMatchingSubscription(t *testing.T) {
	b := New()
	var got messaging.Message
	assert.NilError(t, b.Subscribe("nodes/+/job", func(m messaging.Message) { got = m }))

	n := b.Deliver("nodes/n1/job", []byte("hi"))

	assert.Equal(t, n, 1)
	assert.Equal(t, got.Topic, "nodes/n1/job")
}

func TestBus_SubscribeBeforeConnect(t *testing.T) {
	b := New()
	called := false
	assert.NilError(t, b.Subscribe("a/b", func(messaging.Message) { called = true }))
	assert.NilError(t, b.Connect())

	b.Deliver("a/b", nil)
	assert.Assert(t, called)
}

func TestBus_PublishLoopsBackToLocalSubscription(t *testing.T) {
	b := New()
	var got messaging.Message
	assert.NilError(t, b.Subscribe("nodes/+/echo", func(m messaging.Message) { got = m }))

	assert.NilError(t, b.Publish("nodes/n1/echo", []byte("loop")))

	assert.Equal(t, got.Topic, "nodes/n1/echo")
	assert.DeepEqual(t, got.Payload, []byte("loop"))
}

func TestBus_Unsubscribe(t *testing.T) {
	b := New()
	calls := 0
	assert.NilError(t, b.Subscribe("a/b", func(messaging.Message) { calls++ }))
	assert.NilError(t, b.Unsubscribe("a/b"))

	b.Deliver("a/b", nil)
	assert.Equal(t, calls, 0)
	assert.Equal(t, len(b.Subscriptions()), 0)
}

func TestBus_SetPublishError(t *testing.T) {
	b := New()
	wantErr := errors.New("boom")
	b.SetPublishError(wantErr)

	err := b.Publish("topic", nil)

	assert.Equal(t, err, wantErr)
	assert.Equal(t, len(b.Published()), 0)
}

func TestBus_SetConnectError(t *testing.T) {
	b := New()
	wantErr := errors.New("no broker")
	b.SetConnectError(wantErr)

	err := b.Connect()

	assert.Equal(t, err, wantErr)
	assert.Assert(t, !b.Connected())
}

func TestBus_HandlerPanicIsIsolated(t *testing.T) {
	b := New()
	ranSecond := false
	assert.NilError(t, b.Subscribe("a/b", func(messaging.Message) { panic("boom") }))
	assert.NilError(t, b.Subscribe("a/b", func(messaging.Message) { ranSecond = true }))

	n := b.Deliver("a/b", nil)

	assert.Equal(t, n, 2)
	assert.Assert(t, ranSecond)
}

func TestBus_Subscriptions(t *testing.T) {
	b := New()
	assert.NilError(t, b.Subscribe("a/b", func(messaging.Message) {}))
	assert.NilError(t, b.Subscribe("c/d", func(messaging.Message) {}))

	assert.DeepEqual(t, b.Subscriptions(), []string{"a/b", "c/d"})
}
