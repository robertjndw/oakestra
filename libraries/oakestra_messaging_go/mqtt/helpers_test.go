package mqtt

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/pem"
	"math/big"
	"os"
	"path/filepath"
	"testing"
	"time"

	paho "github.com/eclipse/paho.mqtt.golang"

	"gotest.tools/v3/assert"
)

// writeTestCertPair writes a throwaway self-signed ECDSA certificate/key
// pair into a temp dir, for exercising NewBus's TLS branch without a real CA.
func writeTestCertPair(t *testing.T) (certPath, keyPath string) {
	t.Helper()

	priv, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	assert.NilError(t, err)

	template := x509.Certificate{
		SerialNumber: big.NewInt(1),
		Subject:      pkix.Name{CommonName: "oakestra-messaging-test"},
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

// newTestBus builds a Bus wired to a fakeClient via Config.ClientFactory and
// returns both, so tests can drive the fake while exercising the real Bus
// logic (subscription registry, timeouts, error propagation).
func newTestBus(t *testing.T, mutate func(*Config)) (*Bus, *fakeClient) {
	t.Helper()

	var fc *fakeClient
	cfg := Config{
		BrokerURL:  "localhost",
		BrokerPort: "1883",
		ClientID:   "test-client",
	}
	if mutate != nil {
		mutate(&cfg)
	}
	cfg.ClientFactory = func(opts *paho.ClientOptions) paho.Client {
		fc = newFakeClient(opts)
		return fc
	}

	b, err := NewBus(cfg)
	assert.NilError(t, err)
	return b, fc
}
