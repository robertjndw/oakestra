package credentials

import (
	"encoding/json"
	"go_node_engine/model"
	"testing"
)

type fakeConsumer struct {
	matchUseAs string
	matchType  string
	applied    *OpenedCredential
}

func (f *fakeConsumer) Match(useAs, typ string) bool {
	return useAs == f.matchUseAs && typ == f.matchType
}

func (f *fakeConsumer) Apply(opened OpenedCredential, ctx *PullContext) error {
	o := opened
	f.applied = &o
	return nil
}

// withConsumers swaps the package-global registry for the duration of a test.
func withConsumers(t *testing.T, replacement []Consumer) {
	t.Helper()
	saved := consumers
	consumers = replacement
	t.Cleanup(func() { consumers = saved })
}

func TestOpenCopiesFields(t *testing.T) {
	creds := []model.Credential{
		{UseAs: "image_pull", Type: "DockerRegistry", Value: json.RawMessage(`{"a":1}`)},
	}
	opened, err := Open(creds)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(opened) != 1 {
		t.Fatalf("expected 1 opened credential, got %d", len(opened))
	}
	if opened[0].UseAs != "image_pull" || opened[0].Type != "DockerRegistry" {
		t.Fatalf("fields not copied: %+v", opened[0])
	}
	if string(opened[0].Value) != `{"a":1}` {
		t.Fatalf("value not copied: %s", opened[0].Value)
	}
}

func TestOpenRejectsNilValue(t *testing.T) {
	creds := []model.Credential{{UseAs: "image_pull", Type: "DockerRegistry", Value: nil}}
	if _, err := Open(creds); err == nil {
		t.Fatal("expected error for nil value, got nil")
	}
}

func TestDispatchCallsMatchingConsumer(t *testing.T) {
	fake := &fakeConsumer{matchUseAs: "image_pull", matchType: "DockerRegistry"}
	withConsumers(t, []Consumer{fake})

	opened := OpenedCredential{UseAs: "image_pull", Type: "DockerRegistry", Value: json.RawMessage(`{}`)}
	if err := Dispatch(opened, &PullContext{}); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if fake.applied == nil {
		t.Fatal("expected consumer.Apply to be called")
	}
}

func TestDispatchErrorsWhenNoConsumerMatches(t *testing.T) {
	withConsumers(t, []Consumer{&fakeConsumer{matchUseAs: "other", matchType: "Other"}})

	opened := OpenedCredential{UseAs: "image_pull", Type: "DockerRegistry", Value: json.RawMessage(`{}`)}
	if err := Dispatch(opened, &PullContext{}); err == nil {
		t.Fatal("expected error when no consumer matches, got nil")
	}
}
