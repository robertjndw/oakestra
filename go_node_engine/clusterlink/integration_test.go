package clusterlink

import (
	"encoding/json"
	"fmt"
	"os"
	"strings"
	"testing"
	"time"

	"go_node_engine/config"
	"go_node_engine/model"

	mqttbus "github.com/oakestra/oakestra/libraries/oakestra_messaging_go/mqtt"

	mqtt "github.com/eclipse/paho.mqtt.golang"
	"gotest.tools/v3/assert"
)

func TestClientID_AppendsNeSuffix(t *testing.T) {
	assert.Equal(t, ClientID("node1"), "node1-ne")
}

// brokerFromEnv skips the test unless a real broker address was provided,
// keeping these tests out of the default `go test ./clusterlink/` run.
func brokerFromEnv(t *testing.T) (host, port string) {
	t.Helper()
	addr := os.Getenv("OAKESTRA_TEST_MQTT_ADDR")
	if addr == "" {
		t.Skip("OAKESTRA_TEST_MQTT_ADDR not set; skipping broker-backed integration test")
	}
	idx := strings.LastIndex(addr, ":")
	if idx < 0 {
		t.Fatalf("OAKESTRA_TEST_MQTT_ADDR %q must be host:port", addr)
	}
	return addr[:idx], addr[idx+1:]
}

// peer is a real paho client playing cluster_manager. It subscribes to exactly
// the three topics CM subscribes to (information, job, jobs/resources) rather
// than nodes/<id>/#: a wildcard would also deliver the peer's own control/*
// publishes back to it, which the real CM never sees.
type peer struct {
	client   mqtt.Client
	messages chan mqtt.Message
}

func newPeer(t *testing.T, host, port, nodeID string) *peer {
	t.Helper()

	messages := make(chan mqtt.Message, 32)
	subscribed := make(chan struct{}, 1)

	filters := map[string]byte{
		fmt.Sprintf("nodes/%s/information", nodeID):    1,
		fmt.Sprintf("nodes/%s/job", nodeID):            1,
		fmt.Sprintf("nodes/%s/jobs/resources", nodeID): 1,
	}

	opts := mqtt.NewClientOptions()
	opts.AddBroker(fmt.Sprintf("tcp://%s:%s", host, port))
	opts.SetClientID(nodeID + "-peer")
	opts.OnConnect = func(c mqtt.Client) {
		token := c.SubscribeMultiple(filters, func(_ mqtt.Client, m mqtt.Message) {
			messages <- m
		})
		token.Wait()
		subscribed <- struct{}{}
	}

	client := mqtt.NewClient(opts)
	tok := client.Connect()
	assert.Assert(t, tok.WaitTimeout(5*time.Second), "peer timed out connecting to broker")
	assert.NilError(t, tok.Error())

	select {
	case <-subscribed:
	case <-time.After(5 * time.Second):
		t.Fatal("peer timed out subscribing")
	}

	t.Cleanup(func() { client.Disconnect(250) })
	return &peer{client: client, messages: messages}
}

// expect waits for a message on the given topic, ignoring messages on other
// topics (e.g. the node's own information heartbeat) that may interleave.
func (p *peer) expect(t *testing.T, topic string) mqtt.Message {
	t.Helper()
	deadline := time.After(5 * time.Second)
	for {
		select {
		case m := <-p.messages:
			if m.Topic() == topic {
				return m
			}
		case <-deadline:
			t.Fatalf("timed out waiting for a message on %s", topic)
			return nil
		}
	}
}

func (p *peer) expectNone(t *testing.T, timeout time.Duration) {
	t.Helper()
	select {
	case m := <-p.messages:
		t.Fatalf("expected no message, got one on %s: %s", m.Topic(), m.Payload())
	case <-time.After(timeout):
	}
}

func (p *peer) publish(t *testing.T, topic string, payload []byte) {
	t.Helper()
	tok := p.client.Publish(topic, 0, false, payload)
	assert.Assert(t, tok.WaitTimeout(5*time.Second), "publish timed out")
	assert.NilError(t, tok.Error())
}

// startNodeEngineClient builds a real mqtt-backed bus, wires it through Init
// and connects it, blocking until the broker has acked the subscriptions.
func startNodeEngineClient(t *testing.T, host, port, id string, provider RuntimeProvider) {
	t.Helper()
	resetForTest(t)
	nodeIP = func() string { return "10.0.0.7" }

	b, err := mqttbus.NewBus(mqttbus.Config{
		BrokerURL:  host,
		BrokerPort: port,
		ClientID:   ClientID(id),
		QoS:        1,
	})
	assert.NilError(t, err)

	Init(b, id, provider)

	assert.NilError(t, b.Connect())
	t.Cleanup(func() { _ = b.Close() })
}

func TestIntegration_Deploy_ReportsInstantiationThenCreated(t *testing.T) {
	host, port := brokerFromEnv(t)
	nodeID := fmt.Sprintf("it-%d", time.Now().UnixNano())

	proceed := make(chan struct{})
	rt := &fakeRuntime{deployFn: func(model.Service, func(model.Service)) error {
		<-proceed
		return nil
	}}
	startNodeEngineClient(t, host, port, nodeID, &fakeProvider{rt: rt})
	peer := newPeer(t, host, port, nodeID)

	payload, err := json.Marshal(map[string]any{
		"job_name":        "app.ns.svc.inst",
		"instance_number": 1,
		"virtualization":  "docker",
	})
	assert.NilError(t, err)
	peer.publish(t, fmt.Sprintf("nodes/%s/control/deploy", nodeID), payload)

	jobTopic := fmt.Sprintf("nodes/%s/job", nodeID)
	msg := peer.expect(t, jobTopic)
	assert.Equal(t, msg.Qos(), byte(1))
	assert.Equal(t, msg.Retained(), false)
	var status ServiceStatus
	assert.NilError(t, json.Unmarshal(msg.Payload(), &status))
	assert.Equal(t, status.Status, model.SERVICE_INSTANTIATION)

	close(proceed)

	msg = peer.expect(t, jobTopic)
	assert.NilError(t, json.Unmarshal(msg.Payload(), &status))
	assert.Equal(t, status.Status, model.SERVICE_CREATED)
}

func TestIntegration_Deploy_ErrorReportsFailed(t *testing.T) {
	host, port := brokerFromEnv(t)
	nodeID := fmt.Sprintf("it-%d", time.Now().UnixNano())

	rt := &fakeRuntime{deployFn: func(model.Service, func(model.Service)) error {
		return fmt.Errorf("image pull failed")
	}}
	startNodeEngineClient(t, host, port, nodeID, &fakeProvider{rt: rt})
	peer := newPeer(t, host, port, nodeID)

	payload, err := json.Marshal(map[string]any{
		"job_name":        "app.ns.svc.inst",
		"instance_number": 1,
		"virtualization":  "docker",
	})
	assert.NilError(t, err)
	peer.publish(t, fmt.Sprintf("nodes/%s/control/deploy", nodeID), payload)

	jobTopic := fmt.Sprintf("nodes/%s/job", nodeID)
	peer.expect(t, jobTopic) // INSTANTIATION, not under test here

	msg := peer.expect(t, jobTopic)
	var status ServiceStatus
	assert.NilError(t, json.Unmarshal(msg.Payload(), &status))
	assert.Equal(t, status.Status, model.SERVICE_FAILED)
	assert.Equal(t, status.Detail, "image pull failed")
}

func TestIntegration_Delete_ReportsUndeployed(t *testing.T) {
	host, port := brokerFromEnv(t)
	nodeID := fmt.Sprintf("it-%d", time.Now().UnixNano())

	rt := &fakeRuntime{undeployFn: func(string, int) error { return nil }}
	startNodeEngineClient(t, host, port, nodeID, &fakeProvider{rt: rt})
	peer := newPeer(t, host, port, nodeID)

	payload, err := json.Marshal(map[string]any{
		"job_name":        "app.ns.svc.inst",
		"instance_number": 1,
		"virtualization":  "docker",
	})
	assert.NilError(t, err)
	peer.publish(t, fmt.Sprintf("nodes/%s/control/delete", nodeID), payload)

	msg := peer.expect(t, fmt.Sprintf("nodes/%s/job", nodeID))
	var status ServiceStatus
	assert.NilError(t, json.Unmarshal(msg.Payload(), &status))
	assert.Equal(t, status.Status, model.SERVICE_UNDEPLOYED)
}

func TestIntegration_Delete_ErrorPublishesNothing(t *testing.T) {
	host, port := brokerFromEnv(t)
	nodeID := fmt.Sprintf("it-%d", time.Now().UnixNano())

	rt := &fakeRuntime{undeployFn: func(string, int) error { return fmt.Errorf("not found") }}
	startNodeEngineClient(t, host, port, nodeID, &fakeProvider{rt: rt})
	peer := newPeer(t, host, port, nodeID)

	payload, err := json.Marshal(map[string]any{
		"job_name":        "app.ns.svc.inst",
		"instance_number": 1,
		"virtualization":  "docker",
	})
	assert.NilError(t, err)
	peer.publish(t, fmt.Sprintf("nodes/%s/control/delete", nodeID), payload)

	peer.expectNone(t, time.Second)
}

func TestIntegration_MalformedDeploy_PublishesNothing(t *testing.T) {
	host, port := brokerFromEnv(t)
	nodeID := fmt.Sprintf("it-%d", time.Now().UnixNano())

	startNodeEngineClient(t, host, port, nodeID, &fakeProvider{rt: &fakeRuntime{}})
	peer := newPeer(t, host, port, nodeID)

	peer.publish(t, fmt.Sprintf("nodes/%s/control/deploy", nodeID), []byte("not-json"))

	peer.expectNone(t, time.Second)
}

func TestIntegration_ReportNodeInformation_MatchesContract(t *testing.T) {
	host, port := brokerFromEnv(t)
	nodeID := fmt.Sprintf("it-%d", time.Now().UnixNano())

	startNodeEngineClient(t, host, port, nodeID, &fakeProvider{rt: &fakeRuntime{}})
	peer := newPeer(t, host, port, nodeID)

	node := model.Node{
		Id:              "node1",
		Host:            "worker-1",
		Ip:              "10.0.0.7",
		Port:            "",
		SystemInfo:      map[string]string{"kernel_version": "6.1.0", "os": "linux"},
		CpuUsage:        12.5,
		CpuCores:        4,
		CpuArch:         "amd64",
		MemoryUsed:      40.25,
		MemoryMB:        8192,
		DiskInfo:        map[string]string{"/": "50"},
		NetworkInfo:     map[string]string{"eth0": "10.0.0.7"},
		Technology:      []model.RuntimeType{model.CONTAINER_RUNTIME},
		SupportedAddons: []model.AddonType{},
		CSIDrivers:      []config.CSIDriverType{},
		OverlaySocket:   "/etc/netmanager/netmanager.sock",
		LogDirectory:    "/tmp",
		ClusterAddress:  "0.0.0.0",
	}
	ReportNodeInformation(node)

	msg := peer.expect(t, fmt.Sprintf("nodes/%s/information", nodeID))
	assert.Equal(t, msg.Qos(), byte(1))
	assertJSONEqual(t, msg.Payload(), loadContract(t, "node_information.json"))
}

func TestIntegration_ReportServiceResources_MatchesContract(t *testing.T) {
	host, port := brokerFromEnv(t)
	nodeID := fmt.Sprintf("it-%d", time.Now().UnixNano())

	startNodeEngineClient(t, host, port, nodeID, &fakeProvider{rt: &fakeRuntime{}})
	peer := newPeer(t, host, port, nodeID)

	ReportServiceResources([]model.Resources{{
		Cpu: "1.50", Memory: "2.00", Disk: "0", Logs: "hello\n",
		Sname: "app.ns.svc.inst", Runtime: "docker", Instance: 1, Status: "RUNNING",
	}})

	msg := peer.expect(t, fmt.Sprintf("nodes/%s/jobs/resources", nodeID))
	assert.Equal(t, msg.Qos(), byte(1))
	assertJSONEqual(t, msg.Payload(), loadContract(t, "jobs_resources.json"))
}

func TestIntegration_ControlErrorTopicNotSubscribed(t *testing.T) {
	host, port := brokerFromEnv(t)
	nodeID := fmt.Sprintf("it-%d", time.Now().UnixNano())

	provider := &fakeProvider{rt: &fakeRuntime{}}
	startNodeEngineClient(t, host, port, nodeID, provider)
	peer := newPeer(t, host, port, nodeID)

	// NE never subscribes to control/error (only CM produces it, and
	// nothing consumes it); publishing there must not trigger any handler.
	peer.publish(t, fmt.Sprintf("nodes/%s/control/error", nodeID), []byte(`{"message":"ignored"}`))

	peer.expectNone(t, time.Second)
	assert.Equal(t, len(provider.requestedTypes()), 0)
}
