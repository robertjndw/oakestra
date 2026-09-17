package messaging

import (
	"testing"

	"gotest.tools/v3/assert"
)

func TestBackendFromEnv_DefaultsToMqtt(t *testing.T) {
	t.Setenv(messagingBackendEnv, "")

	b, err := BackendFromEnv()

	assert.NilError(t, err)
	assert.Equal(t, b, BackendMQTT)
}

func TestBackendFromEnv_ExplicitMqtt(t *testing.T) {
	t.Setenv(messagingBackendEnv, "mqtt")

	b, err := BackendFromEnv()

	assert.NilError(t, err)
	assert.Equal(t, b, BackendMQTT)
}

func TestBackendFromEnv_UnknownValueErrors(t *testing.T) {
	t.Setenv(messagingBackendEnv, "nats")

	_, err := BackendFromEnv()

	assert.ErrorContains(t, err, "nats")
	assert.ErrorContains(t, err, string(BackendMQTT))
}
