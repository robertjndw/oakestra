package clusterlink

import (
	"errors"
	"testing"
	"time"

	"github.com/oakestra/oakestra/libraries/oakestra_messaging_go/memory"

	"gotest.tools/v3/assert"
)

func TestInit_IsOneShot(t *testing.T) {
	resetForTest(t)
	mb := memory.New()

	provider1 := &fakeProvider{rt: &fakeRuntime{}}
	Init(mb, "node1", provider1)

	// A second call is a no-op guarded by nodeID != "": it must not resubscribe
	// under node2's topics or touch the stored node ID.
	provider2 := &fakeProvider{rt: &fakeRuntime{}}
	Init(mb, "node2", provider2)

	assert.Equal(t, nodeID, "node1")
	assert.DeepEqual(t, mb.Subscriptions(), []string{"nodes/node1/control/deploy", "nodes/node1/control/delete"})

	n := mb.Deliver("nodes/node2/control/deploy", []byte(`{}`))
	assert.Equal(t, n, 0)
	assert.Equal(t, len(provider2.requestedTypes()), 0)
}

func TestInit_SubscribesExactlyControlTopics(t *testing.T) {
	resetForTest(t)
	mb := memory.New()

	Init(mb, "n1", &fakeProvider{rt: &fakeRuntime{}})

	assert.DeepEqual(t, mb.Subscriptions(), []string{"nodes/n1/control/deploy", "nodes/n1/control/delete"})
}

func TestDeliver_ForeignPrefixOrOtherNode_DoesNotInvokeHandlers(t *testing.T) {
	resetForTest(t)
	mb := memory.New()
	provider := &fakeProvider{rt: &fakeRuntime{}}
	Init(mb, "n1", provider)

	n := mb.Deliver("xnodes/n1/control/deploy", []byte(`{}`))
	assert.Equal(t, n, 0)

	n = mb.Deliver("nodes/n2/control/deploy", []byte(`{}`))
	assert.Equal(t, n, 0)

	assert.Equal(t, len(provider.requestedTypes()), 0)
}

func TestPublish_PrefixesTopicWithNodesAndNodeID(t *testing.T) {
	mb := installBus(t)

	publish("job", `{"sname":"a"}`)

	calls := mb.Published()
	assert.Equal(t, len(calls), 1)
	assert.Equal(t, calls[0].Topic, "nodes/n1/job")
	assert.Equal(t, string(calls[0].Payload), `{"sname":"a"}`)
}

func TestPublish_ErrorIsLoggedAndExecutionContinues(t *testing.T) {
	mb := installBus(t)
	mb.SetPublishError(errors.New("broker rejected publish"))

	// publish has no return value; a failing bus must not panic or otherwise
	// stop the caller (ReportServiceStatus etc.) from running.
	publish("job", "first")
	assert.Equal(t, len(mb.Published()), 0)

	mb.SetPublishError(nil)
	publish("job", "second")

	calls := mb.Published()
	assert.Equal(t, len(calls), 1)
	assert.Equal(t, string(calls[0].Payload), "second")
}

func TestPublishBeforeInit_LogsAndReturns(t *testing.T) {
	resetForTest(t)
	assert.Assert(t, bus == nil)

	done := make(chan struct{})
	go func() {
		publish("job", "payload")
		close(done)
	}()

	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("publish did not return when called before Init")
	}
}
