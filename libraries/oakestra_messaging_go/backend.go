package messaging

import (
	"fmt"
	"os"
)

// Backend names a messaging transport. mqtt is the only one implemented.
type Backend string

const BackendMQTT Backend = "mqtt"

const messagingBackendEnv = "MESSAGING_BACKEND"

// BackendFromEnv reads MESSAGING_BACKEND, defaulting to mqtt when unset, and
// fails fast on anything this library doesn't implement rather than letting
// a typo silently fall back to a default.
func BackendFromEnv() (Backend, error) {
	v, ok := os.LookupEnv(messagingBackendEnv)
	if !ok || v == "" {
		return BackendMQTT, nil
	}

	b := Backend(v)
	switch b {
	case BackendMQTT:
		return b, nil
	default:
		return "", fmt.Errorf("unsupported MESSAGING_BACKEND %q, valid values: %s", v, BackendMQTT)
	}
}
