package consumers

import (
	"encoding/json"
	"fmt"
	"go_node_engine/credentials"
)

// docker.io is accessed via registry-1.docker.io and index.docker.io; normalise
// both to a single canonical name so host matching works regardless of which
// form the image reference uses.
var dockerIOAliases = map[string]string{
	"docker.io":       "registry-1.docker.io",
	"index.docker.io": "registry-1.docker.io",
}

func normalizeRegistry(r string) string {
	if canon, ok := dockerIOAliases[r]; ok {
		return canon
	}
	return r
}

func init() {
	credentials.RegisterConsumer(&DockerRegistryPullConsumer{})
}

// DockerRegistryPullConsumer handles (image_pull, DockerRegistry) credentials.
type DockerRegistryPullConsumer struct{}

func (c *DockerRegistryPullConsumer) Match(useAs, typ string) bool {
	return useAs == "image_pull" && typ == "DockerRegistry"
}

// Apply chains credentials for this registry into ctx.CredsFn.
// ContainersManagement builds the final resolver from ctx.CredsFn, allowing it
// to apply the same credentials for both normal HTTPS and plain-HTTP fallback pulls.
func (c *DockerRegistryPullConsumer) Apply(opened credentials.OpenedCredential, ctx *credentials.PullContext) error {
	var creds struct {
		Username string `json:"username"`
		Password string `json:"password"`
		Registry string `json:"registry"`
	}
	if err := json.Unmarshal(opened.Value, &creds); err != nil {
		return fmt.Errorf("docker_registry_pull: malformed credential value: %w", err)
	}
	if creds.Username == "" || creds.Password == "" {
		return fmt.Errorf("docker_registry_pull: username or password is empty")
	}
	if creds.Registry == "" {
		return fmt.Errorf("docker_registry_pull: registry field is required")
	}

	canonicalRegistry := normalizeRegistry(creds.Registry)
	prev := ctx.CredsFn
	ctx.CredsFn = func(host string) (string, string, error) {
		if normalizeRegistry(host) == canonicalRegistry {
			return creds.Username, creds.Password, nil
		}
		if prev != nil {
			return prev(host)
		}
		return "", "", nil
	}
	return nil
}
