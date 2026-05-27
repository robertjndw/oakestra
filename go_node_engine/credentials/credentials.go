package credentials

import (
	"encoding/json"
	"fmt"
	"go_node_engine/model"
)

// OpenedCredential holds the decrypted value for one credential.
type OpenedCredential struct {
	UseAs string
	Type  string
	Value json.RawMessage
}

// Consumer applies an opened credential to a pull context.
type Consumer interface {
	Match(useAs, typ string) bool
	Apply(opened OpenedCredential, ctx *PullContext) error
}

// PullContext carries containerd pull options that consumers can augment.
type PullContext struct {
	RemoteOpts []interface{} // containerd.RemoteOpt — typed as interface{} to avoid a circular dep
}

var consumers []Consumer

// RegisterConsumer adds a consumer to the registry.
func RegisterConsumer(c Consumer) {
	consumers = append(consumers, c)
}

// Open converts a slice of plaintext credentials into OpenedCredentials ready
// for dispatch.  Returns an error if any single credential is malformed.
func Open(creds []model.Credential) ([]OpenedCredential, error) {
	var opened []OpenedCredential
	for _, c := range creds {
		if c.Value == nil {
			return nil, fmt.Errorf("credentials: nil value for use_as=%s", c.UseAs)
		}
		opened = append(opened, OpenedCredential{
			UseAs: c.UseAs,
			Type:  c.Type,
			Value: c.Value,
		})
	}
	return opened, nil
}

// Dispatch finds the matching consumer and calls Apply.
// Returns an error if no consumer is registered for the given use_as/type combination.
func Dispatch(opened OpenedCredential, pullCtx *PullContext) error {
	for _, c := range consumers {
		if c.Match(opened.UseAs, opened.Type) {
			return c.Apply(opened, pullCtx)
		}
	}
	return fmt.Errorf("credentials: no consumer registered for use_as=%s type=%s", opened.UseAs, opened.Type)
}
