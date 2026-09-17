# Shared libraries

This folder holds the packages shared across Oakestra services: two plain
Python libraries (`oakestra_utils_library`, `resource_abstractor_client`), a
Python pub/sub library (`oakestra_messaging`), and a Go pub/sub module
(`oakestra_messaging_go`).

## Python libraries

### In-repo consumers (system_manager, cluster_manager, resource-abstractor)

These are never fetched from git for services that live in this repo:

- **Docker builds** use the `libraries` named build context (see each
  service's `docker-compose.yml` and `Dockerfile`) instead of a pip install
  from a URL.
- **Local dev / tests**:
  ```py
  pip install ./libraries/oakestra_utils_library ./libraries/resource_abstractor_client ./libraries/oakestra_messaging
  ```
  or, for editable installs while working on a library itself:
  ```py
  pip install -e .
  ```

### Cross-repo consumers (e.g. oakestra-net)

A repo that does not have this checkout available installs a library
straight from git by adding it to `requirements.txt`:

```py
git+https://github.com/{username}/{project}.git@{branch}#subdirectory=libraries/{library_name}
```

For example, `cluster-service-manager` in `oakestra-net` depends on
`oakestra_messaging` this way:

```py
oakestra_messaging @ git+https://github.com/oakestra/oakestra.git@<ref>#subdirectory=libraries/oakestra_messaging
```

Pin `<ref>` to a commit SHA or tag once the branch is stable - a branch name
keeps moving under you.

## Go module: oakestra_messaging_go

`libraries/oakestra_messaging_go` is a Go module (`github.com/oakestra/oakestra/libraries/oakestra_messaging_go`)
with the same "hide the transport" purpose as `oakestra_messaging`, for Go
endpoints. Two consumption modes, depending on whether the consumer has a
local checkout of this repo:

- **In-repo consumers (`go_node_engine`)** require the module and add a local
  `replace` in `go.mod`:
  ```
  require github.com/oakestra/oakestra/libraries/oakestra_messaging_go v0.0.0
  replace github.com/oakestra/oakestra/libraries/oakestra_messaging_go => ../libraries/oakestra_messaging_go
  ```
  so the build always uses the working tree's copy, with no publish step.
- **Cross-repo consumers (e.g. `oakestra-net`)** have nothing to `replace`
  against, so they pin a pseudo-version before a tag exists, or a tag after:
  ```sh
  # before a tag exists, pin to a commit on this repo's default or feature branch
  go get github.com/oakestra/oakestra/libraries/oakestra_messaging_go@<branch-or-commit>

  # after this repo tags a release of the module
  go get github.com/oakestra/oakestra/libraries/oakestra_messaging_go@libraries/oakestra_messaging_go/vX.Y.Z
  ```
  The tag format is `libraries/oakestra_messaging_go/vX.Y.Z` (a subdirectory
  module tag), not a bare `vX.Y.Z`, which would collide with tags on the root
  `oakestra` module. Never run `go get -u` against this dependency from a
  cross-repo consumer: with no tag yet published, `-u` resolves "latest" to
  whatever commit currently sits at the tip of the tracked branch, which can
  silently pull in unrelated changes and bump every other dependency
  alongside it. Use `go get ...@<pinned-ref>` or `go mod download` instead.
