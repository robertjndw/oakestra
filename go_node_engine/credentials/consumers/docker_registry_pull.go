package consumers

import (
	"encoding/json"
	"fmt"
	"go_node_engine/credentials"

	"github.com/containerd/containerd"
	docker_remote "github.com/containerd/containerd/remotes/docker"
)

// docker.io is accessed via registry-1.docker.io and index.docker.io; normalise
// all three to a single canonical name so host matching works regardless of which
// form the image reference uses.
var dockerIOAliases = map[string]string{
	"docker.io":           "registry-1.docker.io",
	"index.docker.io":     "registry-1.docker.io",
	"registry-1.docker.io": "registry-1.docker.io",
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

// Apply builds an authenticated resolver and appends it to the PullContext's RemoteOpts.
// Credentials are only sent to the registry host recorded in the credential; any other
// host receives empty credentials so containerd falls back to anonymous auth.
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

	authz := docker_remote.NewDockerAuthorizer(
		docker_remote.WithAuthCreds(func(host string) (string, string, error) {
			if normalizeRegistry(host) == canonicalRegistry {
				return creds.Username, creds.Password, nil
			}
			return "", "", nil
		}),
	)

	resolver := docker_remote.NewResolver(docker_remote.ResolverOptions{
		Hosts: docker_remote.ConfigureDefaultRegistries(
			docker_remote.WithAuthorizer(authz),
		),
	})

	ctx.RemoteOpts = append(ctx.RemoteOpts, containerd.WithResolver(resolver))
	return nil
}
