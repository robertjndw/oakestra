package mqtt

import (
	"errors"
	"fmt"
	"testing"
	"time"

	mqtt "github.com/eclipse/paho.mqtt.golang"
	"gotest.tools/v3/assert"
)

func TestSubscribeHandlerDispatcher(t *testing.T) {
	resetForTest(t)

	var deployCalls, deleteCalls int
	TOPICS["nodes/n1/control/deploy"] = func(mqtt.Client, mqtt.Message) { deployCalls++ }
	TOPICS["nodes/n1/control/delete"] = func(mqtt.Client, mqtt.Message) { deleteCalls++ }

	cases := []struct {
		name       string
		topic      string
		wantDeploy int
		wantDelete int
	}{
		{"exact topic matches", "nodes/n1/control/deploy", 1, 0},
		{"topic with suffix still matches", "nodes/n1/control/deploy/extra", 1, 0},
		// Dispatch uses strings.Contains, so any topic containing the key anywhere
		// fires the handler. Not a prefix or suffix match.
		{"topic with foreign prefix still matches", "xnodes/n1/control/deploy", 1, 0},
		{"truncated key does not match", "nodes/n1/control/depl", 0, 0},
		{"other node id does not match", "nodes/n2/control/deploy", 0, 0},
		{"topic containing both keys fires both handlers", "nodes/n1/control/deploy;nodes/n1/control/delete", 1, 1},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			deployCalls, deleteCalls = 0, 0
			subscribeHandlerDispatcher(nil, &fakeMessage{topic: tc.topic})
			assert.Equal(t, deployCalls, tc.wantDeploy)
			assert.Equal(t, deleteCalls, tc.wantDelete)
		})
	}
}

func TestInitMqtt_RegistersHandlersAndOptions(t *testing.T) {
	resetForTest(t)
	created := stubNewClient(t)

	provider := &fakeProvider{rt: &fakeRuntime{}}
	InitMqtt("node1", "10.0.0.1", "10003", "", "", provider)

	fc := waitForFakeClient(t, created)

	assert.Equal(t, len(TOPICS), 2)
	_, hasDeploy := TOPICS["nodes/node1/control/deploy"]
	_, hasDelete := TOPICS["nodes/node1/control/delete"]
	assert.Assert(t, hasDeploy)
	assert.Assert(t, hasDelete)

	assert.Equal(t, fc.opts.ClientID, "node1-ne")
	assert.Equal(t, len(fc.opts.Servers), 1)
	assert.Equal(t, fc.opts.Servers[0].String(), "tcp://10.0.0.1:10003")
	assert.Equal(t, fc.opts.Username, "")
	assert.Equal(t, fc.opts.Password, "")
	assert.Assert(t, fc.opts.DefaultPublishHandler != nil)
	assert.Assert(t, fc.opts.OnConnect != nil)
	assert.Assert(t, fc.opts.OnConnectionLost != nil)
	assert.Assert(t, fc.opts.TLSConfig == nil)

	// We rely on these paho defaults. If an upgrade changes them the broker
	// connection behavior changes with it, so lock them down here.
	assert.Equal(t, fc.opts.KeepAlive, int64(30))
	assert.Equal(t, fc.opts.AutoReconnect, true)
	assert.Equal(t, fc.opts.CleanSession, true)
	assert.Equal(t, fc.opts.ConnectRetry, false)
}

func TestInitMqtt_TLS_AddsTlsBrokerAfterTcp(t *testing.T) {
	resetForTest(t)
	created := stubNewClient(t)
	certPath, keyPath := writeTestCertPair(t)

	provider := &fakeProvider{rt: &fakeRuntime{}}
	InitMqtt("node1", "10.0.0.1", "10003", certPath, keyPath, provider)

	fc := waitForFakeClient(t, created)

	assert.Equal(t, len(fc.opts.Servers), 2)
	assert.Equal(t, fc.opts.Servers[0].String(), "tcp://10.0.0.1:10003")
	assert.Equal(t, fc.opts.Servers[1].String(), "tls://10.0.0.1:10003")
	assert.Assert(t, fc.opts.TLSConfig != nil)
	assert.Equal(t, len(fc.opts.TLSConfig.Certificates), 1)
}

func TestInitMqtt_TLS_BadCertPathStillSetsTlsConfig(t *testing.T) {
	resetForTest(t)
	created := stubNewClient(t)

	provider := &fakeProvider{rt: &fakeRuntime{}}
	// tls.LoadX509KeyPair fails for a nonexistent path; InitMqtt only logs the
	// error and keeps going, so the client ends up with a TLSConfig holding a
	// zero-value, unusable certificate.
	InitMqtt("node1", "10.0.0.1", "10003", "/nonexistent/cert.pem", "/nonexistent/key.pem", provider)

	fc := waitForFakeClient(t, created)

	assert.Assert(t, fc.opts.TLSConfig != nil)
	assert.Equal(t, len(fc.opts.TLSConfig.Certificates), 1)
	assert.Assert(t, fc.opts.TLSConfig.Certificates[0].Certificate == nil)
}

func TestInitMqtt_IsOneShot(t *testing.T) {
	resetForTest(t)
	created := stubNewClient(t)

	provider := &fakeProvider{rt: &fakeRuntime{}}
	InitMqtt("node1", "10.0.0.1", "10003", "", "", provider)
	waitForFakeClient(t, created)

	// A second call is a no-op guarded by clientID != "": it must not touch
	// brokerUrl/brokerPort/TOPICS or spawn another runMqttClient.
	InitMqtt("node2", "10.0.0.2", "20003", "", "", provider)

	select {
	case <-created:
		t.Fatal("second InitMqtt call constructed another client")
	case <-time.After(100 * time.Millisecond):
	}

	assert.Equal(t, clientID, "node1")
	assert.Equal(t, brokerUrl, "10.0.0.1")
	assert.Equal(t, brokerPort, "10003")
	assert.Equal(t, len(TOPICS), 2)
	_, hasNode2Deploy := TOPICS["nodes/node2/control/deploy"]
	assert.Assert(t, !hasNode2Deploy)
}

func TestConnectHandler_SubscribesAllTopicsAtQoS1(t *testing.T) {
	resetForTest(t)
	fc := installFakeClient(t)

	TOPICS["nodes/n1/control/deploy"] = func(mqtt.Client, mqtt.Message) {}
	TOPICS["nodes/n1/control/delete"] = func(mqtt.Client, mqtt.Message) {}

	connectHandler(fc)

	assert.Equal(t, len(fc.subscribed), 2)
	assert.Equal(t, fc.subscribed["nodes/n1/control/deploy"], byte(1))
	assert.Equal(t, fc.subscribed["nodes/n1/control/delete"], byte(1))
	assert.Equal(t, fmt.Sprintf("%p", fc.dispatcher), fmt.Sprintf("%p", subscribeHandlerDispatcher))
}

func TestRunMqttClient_PanicsWhenConnectFails(t *testing.T) {
	resetForTest(t)
	connectErr := errors.New("connection refused")
	newClient = func(opts *mqtt.ClientOptions) mqtt.Client {
		return &fakeClient{connectErr: connectErr}
	}

	panicked := func() (r any) {
		defer func() { r = recover() }()
		runMqttClient(mqtt.NewClientOptions())
		return nil
	}()

	assert.Assert(t, panicked != nil, "expected runMqttClient to panic on connect failure")
	assert.ErrorIs(t, panicked.(error), connectErr)
}

func TestPublishToBroker_PrefixesTopicQoS1NotRetained(t *testing.T) {
	resetForTest(t)
	fc := installFakeClient(t)
	clientID = "n1"

	publishToBroker("job", `{"sname":"a"}`)

	calls := fc.publishCalls()
	assert.Equal(t, len(calls), 1)
	assert.Equal(t, calls[0].topic, "nodes/n1/job")
	assert.Equal(t, calls[0].qos, byte(1))
	assert.Equal(t, calls[0].retained, false)
	assert.Equal(t, calls[0].payload, `{"sname":"a"}`)
}

func TestPublishToBroker_LogsAndContinuesOnTokenError(t *testing.T) {
	resetForTest(t)
	fc := installFakeClient(t)
	fc.publishErr = errors.New("broker rejected publish")
	clientID = "n1"

	// publishToBroker's token-error branch only logs; it never propagates the
	// error, retries, or panics, so a broker rejecting the publish is invisible
	// to every caller (ReportServiceStatus etc.).
	publishToBroker("job", "payload")

	calls := fc.publishCalls()
	assert.Equal(t, len(calls), 1)
	assert.Equal(t, calls[0].payload, "payload")
}
