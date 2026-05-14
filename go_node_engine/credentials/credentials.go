package credentials

import (
	"bytes"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"go_node_engine/keyset"
	"go_node_engine/model"
	"time"
)

const replayWindowSeconds = 300

// OpenedCredential holds the decrypted value for one sealed credential.
type OpenedCredential struct {
	UseAs string
	Type  string
	Value json.RawMessage
}

// DeployContext carries the deployment-level binding fields shared across all sealed credentials.
// Per-credential fields (CredentialID, InstanceNumber, UnixTS) come from each SealedCredential.
type DeployContext struct {
	JobID    string
	WorkerID string
	KeyID    string
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

// Open decrypts all sealed credentials and returns the opened values.
// Returns an error if any single credential cannot be opened; callers must not
// proceed with the pull when an error is returned.
func Open(sealed []model.SealedCredential, deployCtx DeployContext) ([]OpenedCredential, error) {
	dec, err := keyset.GetHybridDecrypt()
	if err != nil {
		return nil, fmt.Errorf("credentials: keyset not available: %w", err)
	}

	now := time.Now().Unix()
	var opened []OpenedCredential

	for _, s := range sealed {
		// Replay window check
		age := now - s.UnixTS
		if age > replayWindowSeconds || age < -60 {
			return nil, fmt.Errorf(
				"credentials: rejecting credential use_as=%s — timestamp age %ds exceeds replay window",
				s.UseAs, age,
			)
		}

		// Check key_id matches what we have loaded
		if s.KeyID != keyset.KeyID {
			return nil, fmt.Errorf(
				"credentials: key_id mismatch for use_as=%s: got %s, have %s — worker key may have rotated",
				s.UseAs, s.KeyID, keyset.KeyID,
			)
		}

		ctx := canonicalContextInfo(
			s.CredentialID,
			deployCtx.JobID,
			s.InstanceNumber,
			deployCtx.WorkerID,
			deployCtx.KeyID,
			s.UnixTS,
		)

		ciphertext, err := base64.StdEncoding.DecodeString(s.CiphertextB64())
		if err != nil {
			return nil, fmt.Errorf("credentials: base64 decode failed for use_as=%s: %w", s.UseAs, err)
		}

		plaintext, err := dec.Decrypt(ciphertext, ctx)
		if err != nil {
			return nil, fmt.Errorf("credentials: HPKE decrypt failed for use_as=%s: %w", s.UseAs, err)
		}

		opened = append(opened, OpenedCredential{
			UseAs: s.UseAs,
			Type:  s.Type,
			Value: json.RawMessage(plaintext),
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

// canonicalContextInfo rebuilds the null-byte-separated binding string that root used as HPKE AAD.
func canonicalContextInfo(credentialID, jobID string, instanceNumber int, workerID, keyID string, unixTS int64) []byte {
	parts := [][]byte{
		[]byte(credentialID),
		[]byte(jobID),
		[]byte(fmt.Sprintf("%d", instanceNumber)),
		[]byte(workerID),
		[]byte(keyID),
		[]byte(fmt.Sprintf("%d", unixTS)),
	}
	return bytes.Join(parts, []byte{0x00})
}
