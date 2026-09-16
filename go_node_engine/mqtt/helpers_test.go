package mqtt

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/json"
	"encoding/pem"
	"math/big"
	"os"
	"path/filepath"
	"testing"
	"time"

	mqtt "github.com/eclipse/paho.mqtt.golang"
	"gotest.tools/v3/assert"
)

// resetForTest gives each test a clean copy of the package-level state that
// InitMqtt mutates. Production code was never written to be re-initialized,
// so tests have to reset it by hand between runs.
func resetForTest(t *testing.T) {
	t.Helper()

	savedNewClient := newClient
	savedNodeIP := nodeIP

	clientID = ""
	brokerUrl = ""
	brokerPort = ""
	mainMqttClient = nil
	TOPICS = make(map[string]mqtt.MessageHandler)

	t.Cleanup(func() {
		if mainMqttClient != nil && mainMqttClient.IsConnected() {
			mainMqttClient.Disconnect(250)
		}
		newClient = savedNewClient
		nodeIP = savedNodeIP
	})
}

// installFakeClient wires mainMqttClient directly to a connected fakeClient,
// for tests that exercise publishToBroker/connectHandler/subscribeHandlerDispatcher
// or the handlers without going through InitMqtt's connection flow.
func installFakeClient(t *testing.T) *fakeClient {
	t.Helper()
	fc := &fakeClient{connected: true}
	mainMqttClient = fc
	return fc
}

// stubNewClient points the newClient seam at a factory that hands back
// fakeClients and reports each one on the returned channel. Use this for
// InitMqtt/runMqttClient, which construct the client on a background
// goroutine, so the test can wait for construction instead of racing it.
func stubNewClient(t *testing.T) <-chan *fakeClient {
	t.Helper()
	created := make(chan *fakeClient, 1)
	newClient = func(opts *mqtt.ClientOptions) mqtt.Client {
		fc := &fakeClient{opts: opts, connectedCh: make(chan struct{}, 1)}
		created <- fc
		return fc
	}
	return created
}

// waitForFakeClient waits for stubNewClient's factory to run and for the
// resulting client to finish connecting, as triggered by code under test
// constructing and connecting the client on another goroutine (e.g. via
// InitMqtt's `go runMqttClient(opts)`). Waiting for the connect signal, not
// just construction, is what makes reading mainMqttClient afterwards
// race-free.
func waitForFakeClient(t *testing.T, created <-chan *fakeClient) *fakeClient {
	t.Helper()
	select {
	case fc := <-created:
		select {
		case <-fc.connectedCh:
		case <-time.After(2 * time.Second):
			t.Fatal("timed out waiting for mqtt client to connect")
		}
		return fc
	case <-time.After(2 * time.Second):
		t.Fatal("timed out waiting for mqtt client to be constructed")
		return nil
	}
}

// awaitPublish polls fc until at least n publishes have been recorded, since
// deployHandler/deleteHandler report status from a background goroutine.
func awaitPublish(t *testing.T, fc *fakeClient, n int, timeout time.Duration) []publishCall {
	t.Helper()
	deadline := time.Now().Add(timeout)
	for {
		calls := fc.publishCalls()
		if len(calls) >= n {
			return calls
		}
		if time.Now().After(deadline) {
			t.Fatalf("timed out waiting for %d publish(es), got %d: %+v", n, len(calls), calls)
		}
		time.Sleep(5 * time.Millisecond)
	}
}

// loadContract reads a shared MQTT contract fixture. Both this suite and the
// cluster-manager Python suite load the same files, so a change to the wire
// format shows up as a failure on both sides.
func loadContract(t *testing.T, name string) []byte {
	t.Helper()
	path := filepath.Join("..", "..", "testdata", "mqtt_contract", name)
	data, err := os.ReadFile(path)
	assert.NilError(t, err, "reading fixture %s", path)
	return data
}

// assertJSONEqual compares two JSON documents semantically (field order and
// exact byte layout don't matter, only the decoded values do).
func assertJSONEqual(t *testing.T, got, want []byte) {
	t.Helper()
	var gotVal, wantVal any
	assert.NilError(t, json.Unmarshal(got, &gotVal), "got is not valid JSON: %s", got)
	assert.NilError(t, json.Unmarshal(want, &wantVal), "want is not valid JSON: %s", want)
	assert.DeepEqual(t, gotVal, wantVal)
}

// writeTestCertPair writes a self-signed ECDSA certificate/key pair into a
// temp dir, for exercising InitMqtt's TLS branch without a real CA.
func writeTestCertPair(t *testing.T) (certPath, keyPath string) {
	t.Helper()

	priv, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	assert.NilError(t, err)

	template := x509.Certificate{
		SerialNumber: big.NewInt(1),
		Subject:      pkix.Name{CommonName: "mqtt-test"},
		NotBefore:    time.Now().Add(-time.Hour),
		NotAfter:     time.Now().Add(time.Hour),
		KeyUsage:     x509.KeyUsageDigitalSignature,
	}
	der, err := x509.CreateCertificate(rand.Reader, &template, &template, &priv.PublicKey, priv)
	assert.NilError(t, err)

	dir := t.TempDir()
	certPath = filepath.Join(dir, "cert.pem")
	keyPath = filepath.Join(dir, "key.pem")

	certOut, err := os.Create(certPath)
	assert.NilError(t, err)
	defer certOut.Close()
	assert.NilError(t, pem.Encode(certOut, &pem.Block{Type: "CERTIFICATE", Bytes: der}))

	keyBytes, err := x509.MarshalECPrivateKey(priv)
	assert.NilError(t, err)
	keyOut, err := os.Create(keyPath)
	assert.NilError(t, err)
	defer keyOut.Close()
	assert.NilError(t, pem.Encode(keyOut, &pem.Block{Type: "EC PRIVATE KEY", Bytes: keyBytes}))

	return certPath, keyPath
}
