// Package mqtt is the paho-backed messaging.Bus adapter. It's the only
// package in this module that imports paho.
package mqtt

import (
	"crypto/tls"
	"fmt"
	"log"
	"sync"
	"time"

	messaging "github.com/oakestra/oakestra/libraries/oakestra_messaging_go"

	paho "github.com/eclipse/paho.mqtt.golang"
)

const (
	defaultPublishTimeout   = 5 * time.Second
	defaultSubscribeTimeout = 10 * time.Second
)

// Config configures a Bus. BrokerURL, BrokerPort and ClientID are required;
// everything else has a workable zero value.
type Config struct {
	BrokerURL, BrokerPort, ClientID string

	// CertFile and KeyFile must both be set to enable TLS. An unreadable pair
	// fails NewBus instead of silently falling back to plaintext.
	CertFile, KeyFile string

	// QoS applies to both Subscribe and Publish.
	QoS byte

	// KeepAlive, PublishTimeout and SubscribeTimeout of zero fall back to
	// paho's default (30s), 5s and 10s respectively.
	KeepAlive        time.Duration
	PublishTimeout   time.Duration
	SubscribeTimeout time.Duration

	Logger, ErrorLogger messaging.Logger

	// ClientFactory replaces paho.NewClient; tests substitute a fake here.
	ClientFactory func(*paho.ClientOptions) paho.Client
}

// Bus is a messaging.Bus backed by a paho client. Every subscription
// registers with a nil callback; the client-wide DefaultPublishHandler hands
// each message to the shared messaging.Dispatcher instead.
type Bus struct {
	cfg    Config
	client paho.Client
	disp   *messaging.Dispatcher
	logger messaging.Logger
	errLog messaging.Logger

	mu        sync.Mutex
	connected bool
	signal    chan struct{}
}

var _ messaging.Bus = (*Bus)(nil)

// NewBus builds a Bus and its underlying paho client but does not connect.
func NewBus(cfg Config) (*Bus, error) {
	if cfg.BrokerURL == "" || cfg.BrokerPort == "" || cfg.ClientID == "" {
		return nil, fmt.Errorf("mqtt: BrokerURL, BrokerPort and ClientID are required")
	}
	if cfg.PublishTimeout == 0 {
		cfg.PublishTimeout = defaultPublishTimeout
	}
	if cfg.SubscribeTimeout == 0 {
		cfg.SubscribeTimeout = defaultSubscribeTimeout
	}
	if cfg.Logger == nil {
		cfg.Logger = log.Default()
	}
	if cfg.ErrorLogger == nil {
		cfg.ErrorLogger = log.Default()
	}

	b := &Bus{
		cfg:    cfg,
		disp:   messaging.NewDispatcher(cfg.Logger),
		logger: cfg.Logger,
		errLog: cfg.ErrorLogger,
	}

	opts := paho.NewClientOptions()
	opts.SetClientID(cfg.ClientID)
	opts.SetUsername("")
	opts.SetPassword("")
	opts.SetDefaultPublishHandler(b.dispatch)
	opts.OnConnect = b.onConnect
	opts.OnConnectionLost = b.onConnectionLost
	opts.SetAutoReconnect(true)
	opts.SetCleanSession(true)
	opts.SetConnectRetry(false)
	if cfg.KeepAlive > 0 {
		opts.SetKeepAlive(cfg.KeepAlive)
	}

	if cfg.CertFile != "" && cfg.KeyFile != "" {
		cert, err := tls.LoadX509KeyPair(cfg.CertFile, cfg.KeyFile)
		if err != nil {
			return nil, fmt.Errorf("mqtt: loading certificate: %w", err)
		}
		opts.SetTLSConfig(&tls.Config{Certificates: []tls.Certificate{cert}})
		opts.AddBroker(fmt.Sprintf("tls://%s:%s", cfg.BrokerURL, cfg.BrokerPort))
	} else {
		opts.AddBroker(fmt.Sprintf("tcp://%s:%s", cfg.BrokerURL, cfg.BrokerPort))
	}

	factory := cfg.ClientFactory
	if factory == nil {
		factory = paho.NewClient
	}
	b.client = factory(opts)

	return b, nil
}

// ClientID returns the client ID this bus connects with.
func (b *Bus) ClientID() string {
	return b.cfg.ClientID
}

func (b *Bus) dispatch(_ paho.Client, msg paho.Message) {
	b.disp.Dispatch(messaging.Message{Topic: msg.Topic(), Payload: msg.Payload()})
}

// onConnect fires on every reconnect too, not just the first connect, so it
// doubles as the resubscribe path: replay every pattern registered so far,
// then signal Connect. The signal channel is fresh per Connect call (see
// Connect), so a spurious call after Connect already returned finds no
// listener.
func (b *Bus) onConnect(c paho.Client) {
	b.logger.Printf("mqtt: connected to broker as %s", b.cfg.ClientID)

	// Flip connected before snapshotting patterns: a Subscribe landing after
	// the snapshot issues its own subscribe instead of waiting for the next
	// reconnect. One landing before is covered by the snapshot; a duplicate
	// subscribe in between is harmless.
	b.mu.Lock()
	b.connected = true
	b.mu.Unlock()

	patterns := b.disp.Patterns()
	if len(patterns) > 0 {
		filters := make(map[string]byte, len(patterns))
		for _, p := range patterns {
			filters[p] = b.cfg.QoS
		}
		tok := c.SubscribeMultiple(filters, nil)
		tok.Wait()
		if err := tok.Error(); err != nil {
			b.errLog.Printf("mqtt: resubscribe on connect failed: %v", err)
		}
	}

	b.mu.Lock()
	sig := b.signal
	b.mu.Unlock()

	if sig != nil {
		select {
		case sig <- struct{}{}:
		default:
		}
	}
}

func (b *Bus) onConnectionLost(_ paho.Client, err error) {
	b.mu.Lock()
	b.connected = false
	b.mu.Unlock()
	b.errLog.Printf("mqtt: connection lost: %v", err)
}

// Connect blocks until the broker has acked the client's connection and, via
// onConnect, every pattern registered so far.
func (b *Bus) Connect() error {
	sig := make(chan struct{}, 1)
	b.mu.Lock()
	b.signal = sig
	b.mu.Unlock()

	tok := b.client.Connect()
	tok.Wait()
	if err := tok.Error(); err != nil {
		return fmt.Errorf("mqtt: connect: %w", err)
	}

	select {
	case <-sig:
		return nil
	case <-time.After(b.cfg.SubscribeTimeout):
		return fmt.Errorf("mqtt: timed out after %s waiting for the broker to acknowledge subscriptions", b.cfg.SubscribeTimeout)
	}
}

// Publish never sets the retained flag; nothing here relies on broker-held
// state.
func (b *Bus) Publish(topic string, payload []byte) error {
	tok := b.client.Publish(topic, b.cfg.QoS, false, payload)
	if !tok.WaitTimeout(b.cfg.PublishTimeout) {
		return fmt.Errorf("mqtt: publish to %s timed out after %s", topic, b.cfg.PublishTimeout)
	}
	return tok.Error()
}

// Subscribe registers h in the dispatcher unconditionally, so it survives
// disconnects and is picked up by the next onConnect's SubscribeMultiple.
// If the bus is already connected, it also issues an immediate subscribe -
// otherwise the pattern would sit unacknowledged until the next reconnect.
func (b *Bus) Subscribe(pattern string, h messaging.Handler) error {
	b.disp.Add(pattern, h)

	b.mu.Lock()
	connected := b.connected
	b.mu.Unlock()
	if !connected {
		return nil
	}

	tok := b.client.Subscribe(pattern, b.cfg.QoS, nil)
	if !tok.WaitTimeout(b.cfg.SubscribeTimeout) {
		return fmt.Errorf("mqtt: subscribe to %s timed out after %s", pattern, b.cfg.SubscribeTimeout)
	}
	return tok.Error()
}

// Unsubscribe drops pattern from the dispatcher regardless of the broker
// round trip's outcome, so a failed unsubscribe never leaves a handler that
// silently keeps firing.
func (b *Bus) Unsubscribe(pattern string) error {
	b.disp.Remove(pattern)

	tok := b.client.Unsubscribe(pattern)
	if !tok.WaitTimeout(b.cfg.SubscribeTimeout) {
		return fmt.Errorf("mqtt: unsubscribe from %s timed out after %s", pattern, b.cfg.SubscribeTimeout)
	}
	return tok.Error()
}

func (b *Bus) Close() error {
	b.client.Disconnect(250)
	b.mu.Lock()
	b.connected = false
	b.mu.Unlock()
	return nil
}
