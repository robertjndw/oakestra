package consumers

import (
	"encoding/json"
	"go_node_engine/credentials"
	"testing"
)

func TestNormalizeRegistry(t *testing.T) {
	cases := map[string]string{
		"docker.io":            "registry-1.docker.io",
		"index.docker.io":      "registry-1.docker.io",
		"registry-1.docker.io": "registry-1.docker.io",
		"ghcr.io":              "ghcr.io",
	}
	for in, want := range cases {
		if got := normalizeRegistry(in); got != want {
			t.Errorf("normalizeRegistry(%q) = %q, want %q", in, got, want)
		}
	}
}

func TestMatch(t *testing.T) {
	c := &DockerRegistryPullConsumer{}
	if !c.Match("image_pull", "DockerRegistry") {
		t.Error("expected match for (image_pull, DockerRegistry)")
	}
	if c.Match("image_push", "DockerRegistry") {
		t.Error("did not expect match for image_push")
	}
	if c.Match("image_pull", "OtherType") {
		t.Error("did not expect match for OtherType")
	}
}

func opened(registry string) credentials.OpenedCredential {
	value, _ := json.Marshal(map[string]string{
		"username": "user", "password": "pass", "registry": registry,
	})
	return credentials.OpenedCredential{UseAs: "image_pull", Type: "DockerRegistry", Value: value}
}

func TestApplyMatchesNormalizedHost(t *testing.T) {
	c := &DockerRegistryPullConsumer{}
	ctx := &credentials.PullContext{}
	if err := c.Apply(opened("docker.io"), ctx); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	// docker.io credential must resolve for the canonical access host.
	u, p, err := ctx.CredsFn("registry-1.docker.io")
	if err != nil || u != "user" || p != "pass" {
		t.Fatalf("got (%q,%q,%v), want (user,pass,nil)", u, p, err)
	}

	// Unrelated host yields no credentials rather than an error.
	u, p, err = ctx.CredsFn("ghcr.io")
	if err != nil || u != "" || p != "" {
		t.Fatalf("got (%q,%q,%v), want empty creds", u, p, err)
	}
}

func TestApplyChainsAndPrioritisesLatestHost(t *testing.T) {
	c := &DockerRegistryPullConsumer{}
	ctx := &credentials.PullContext{}
	if err := c.Apply(opened("docker.io"), ctx); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	// Second credential for a different registry must chain, not clobber.
	ghcr := credentials.OpenedCredential{
		UseAs: "image_pull", Type: "DockerRegistry",
		Value: json.RawMessage(`{"username":"gh","password":"ghpass","registry":"ghcr.io"}`),
	}
	if err := c.Apply(ghcr, ctx); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	if u, _, _ := ctx.CredsFn("ghcr.io"); u != "gh" {
		t.Errorf("ghcr.io resolved to %q, want gh", u)
	}
	if u, _, _ := ctx.CredsFn("registry-1.docker.io"); u != "user" {
		t.Errorf("docker host resolved to %q, want user (chained fallthrough)", u)
	}
}

func TestApplyRejectsInvalidCredentials(t *testing.T) {
	c := &DockerRegistryPullConsumer{}
	cases := map[string]credentials.OpenedCredential{
		"malformed json":   {Value: json.RawMessage(`not json`)},
		"empty password":   {Value: json.RawMessage(`{"username":"u","password":"","registry":"docker.io"}`)},
		"missing registry": {Value: json.RawMessage(`{"username":"u","password":"p"}`)},
	}
	for name, oc := range cases {
		if err := c.Apply(oc, &credentials.PullContext{}); err == nil {
			t.Errorf("%s: expected error, got nil", name)
		}
	}
}
