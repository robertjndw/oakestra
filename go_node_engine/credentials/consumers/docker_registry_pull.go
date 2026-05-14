package consumers

import (
	"encoding/json"
	"fmt"
	"go_node_engine/credentials"

	"github.com/containerd/containerd"
	docker_remote "github.com/containerd/containerd/remotes/docker"
)

func init() {
	credentials.RegisterConsumer(&DockerRegistryPullConsumer{})
}

// DockerRegistryPullConsumer handles (image_pull, DockerRegistry) credentials.
type DockerRegistryPullConsumer struct{}

func (c *DockerRegistryPullConsumer) Match(useAs, typ string) bool {
	return useAs == "image_pull" && typ == "DockerRegistry"
}

// Apply builds an authenticated resolver and appends it to the PullContext's RemoteOpts.
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

	authz := docker_remote.NewDockerAuthorizer(
		docker_remote.WithAuthCreds(func(host string) (string, string, error) {
			return creds.Username, creds.Password, nil
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
