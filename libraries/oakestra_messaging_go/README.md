# oakestra_messaging_go

Technology-neutral pub/sub interface shared by Oakestra's Go endpoints (NodeEngine, NetManager).
It exists so that swapping the transport - MQTT today, NATS in a later migration step - touches
only an adapter package, never call sites. Routing (pattern matching, panic isolation) lives in
this library, not in paho callbacks: both the `mqtt` and `memory` adapters hand every inbound
message to the same `Dispatcher`, so a unit test against the in-memory bus exercises the same
routing code that runs in production.

## API

Root package `messaging`:

```go
type Message struct{ Topic string; Payload []byte }
type Handler func(Message)
type Logger interface{ Printf(format string, v ...any) }   // *log.Logger satisfies it

type Bus interface {
    Connect() error                              // blocks until connected and every registered pattern is acked
    Publish(topic string, payload []byte) error  // never retained
    Subscribe(pattern string, h Handler) error   // allowed before Connect; re-issued on every (re)connect
    Unsubscribe(pattern string) error            // drops every handler for pattern
    Close() error
}

func Matches(pattern, topic string) bool
func NewDispatcher(log Logger) *Dispatcher   // nil log falls back to log.Default()
func BackendFromEnv() (Backend, error)       // reads MESSAGING_BACKEND
```

`mqtt.NewBus(mqtt.Config) (*mqtt.Bus, error)` and `memory.New() *memory.Bus` both implement
`messaging.Bus`. `memory.Bus` additionally exposes `Deliver`, `Published`, `Subscriptions`,
`SetPublishError`, `SetConnectError` and `Connected`, for tests that need to inject inbound
messages or assert on what a caller published, without a broker.

The package is named `messaging`, not `oakestra_messaging_go`, so every consumer imports it with
an explicit alias to avoid clashing with the standard library's own naming conventions and with
this repo's other packages:

```go
import messaging "github.com/oakestra/oakestra/libraries/oakestra_messaging_go"
```

## Topic syntax

Canonical topics are `/`-separated. `+` matches exactly one segment. `#` matches the rest of the
topic, including zero segments, and is only meaningful as the last segment of a pattern - a `#`
anywhere else makes the pattern invalid, so `Matches` never matches it against anything.

```
nodes/+/information        matches nodes/n1/information, not nodes/n1/extra/information
nodes/n1/#                 matches nodes/n1, nodes/n1/a, nodes/n1/a/b
```

A later NATS adapter maps `/` to `.`, `+` to `*` and `#` to `>`. Job names (`app.ns.svc.inst`) and
service IPs contain `.`, which becomes the NATS token separator. Exact-match subjects such as
`jobs/<job>/updates_available` survive that mapping unchanged, but a `+` wildcard must never sit
over a segment that may itself contain a `.` - anything derived from a job name or an IP is unsafe
there. Node IDs are ObjectId hex, so `nodes/+/...` patterns are safe today and will remain safe
once NATS is in the mix.

## Backend selection

`BackendFromEnv()` reads `MESSAGING_BACKEND`, defaulting to `mqtt` when unset, and returns an
error naming the invalid value and the supported list when it doesn't recognize it. Callers are
expected to treat that error as fatal at startup rather than falling back silently.

## Running the tests

```sh
cd libraries/oakestra_messaging_go
go mod tidy
gofmt -l .
go vet ./...
go test -race ./...
```

`mqtt/integration_test.go` also has broker-backed tests, skipped unless `OAKESTRA_TEST_MQTT_ADDR`
points at a live broker:

```sh
docker run -d --rm --name oakestra-test-mqtt \
  -p 11883:10003 \
  -v "$PWD/../../cluster_orchestrator/mosquitto/mosquitto.conf:/mosquitto/config/mosquitto.conf:ro" \
  eclipse-mosquitto:2.0
OAKESTRA_TEST_MQTT_ADDR=127.0.0.1:11883 go test -race -run Integration ./mqtt/
docker stop oakestra-test-mqtt
```

## How consumers import this module

**NodeEngine** (lives in this repo, `go_node_engine/`) uses a local `replace` directive so it
always builds against the working tree's copy, with no publish step:

```
require github.com/oakestra/oakestra/libraries/oakestra_messaging_go v0.0.0
replace github.com/oakestra/oakestra/libraries/oakestra_messaging_go => ../libraries/oakestra_messaging_go
```

**oakestra-net** (a separate repository) has no local checkout of this module to replace against,
so it depends on a pseudo-version or, after a release, a tag:

```sh
# before a tag exists, pin to a commit on this repo's default or feature branch
go get github.com/oakestra/oakestra/libraries/oakestra_messaging_go@<branch-or-commit>

# after this repo tags a release of the module
go get github.com/oakestra/oakestra/libraries/oakestra_messaging_go@libraries/oakestra_messaging_go/vX.Y.Z
```

The tag format is `libraries/oakestra_messaging_go/vX.Y.Z` (a subdirectory module tag), not a
bare `vX.Y.Z` - the latter would collide with tags on the root `oakestra` module. Never run
`go get -u` against this dependency in oakestra-net: with no tag yet published, `-u` resolves
"latest" to whatever commit currently sits at the tip of the tracked branch, which can silently
pull in unrelated changes and bump every other dependency alongside it. Use `go get
...@<pinned-ref>` or `go mod download` instead.
