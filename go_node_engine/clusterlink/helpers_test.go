package clusterlink

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
	"time"

	messaging "github.com/oakestra/oakestra/libraries/oakestra_messaging_go"
	"github.com/oakestra/oakestra/libraries/oakestra_messaging_go/memory"

	"gotest.tools/v3/assert"
)

// resetForTest gives each test a clean copy of the package-level state that
// Init mutates. Production code was never written to be re-initialized, so
// tests have to reset it by hand between runs.
func resetForTest(t *testing.T) {
	t.Helper()

	savedNodeIP := nodeIP

	bus = nil
	nodeID = ""

	t.Cleanup(func() {
		nodeIP = savedNodeIP
	})
}

// installBus resets package state and wires a fresh in-memory bus through
// Init under node ID "n1", for tests that only care about publish/report
// behavior and drive handlers directly rather than through subscriptions.
func installBus(t *testing.T) *memory.Bus {
	t.Helper()
	resetForTest(t)
	mb := memory.New()
	Init(mb, "n1", &fakeProvider{rt: &fakeRuntime{}})
	return mb
}

// awaitPublish polls mb until at least n publishes have been recorded, since
// deployHandler/deleteHandler report status from a background goroutine.
func awaitPublish(t *testing.T, mb *memory.Bus, n int, timeout time.Duration) []messaging.Message {
	t.Helper()
	deadline := time.Now().Add(timeout)
	for {
		calls := mb.Published()
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
