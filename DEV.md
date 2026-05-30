# Local Development Guide

## Quickstart

```bash
cp .env.dev .env   # once — adjust values if needed
make up            # build from local source and start root + cluster
make status        # confirm all containers are up
make open          # open API docs at localhost:10000/api/docs
```

First `make up` takes ~3 minutes (builds all images). Subsequent starts are ~10 seconds because Docker reuses the layer cache.

---

## Daily workflow

| Goal | Command |
|---|---|
| Start everything | `make up` |
| Stop everything | `make down` |
| Start/stop one stack | `make up-root` / `make down-cluster` |
| Show running containers | `make status` |
| Tail all root logs | `make logs-root` |
| Tail all cluster logs | `make logs-cluster` |
| Follow one container | `make log s=system_manager` |
| Rebuild after a change | `make rebuild s=system_manager` |
| Rebuild a cluster service | `make rebuild-cluster s=cluster_manager` |
| Restart without rebuild | `make restart s=system_manager` |
| Wipe volumes (fresh start) | `make clean` |

---

## Changing a Python service

Both `system_manager` and `cluster_manager` run under gunicorn, so there is no hot-reload — each change needs a rebuild. The pip layer is cached, so this takes ~10–15 seconds.

```bash
# edit root_orchestrator/system-manager-python/...
make rebuild s=system_manager
make log s=system_manager        # watch startup and request logs
curl http://localhost:10000/api/...
```

Same pattern for `cluster_manager`, `root_resource_abstractor`, `cluster_resource_abstractor`.

### Optional: enable hot-reload for system_manager

If you're iterating fast on Flask code, add a dev override that mounts the source
and passes `--reload` to gunicorn. Edits then take effect in ~1 second without a rebuild.

```yaml
# root_orchestrator/override-dev-reload.yml
services:
  system_manager:
    volumes:
      - ./system-manager-python:/app
    command: gunicorn --bind [::]:10000 --reload --chdir /app system_manager:app
```

Add it to the `ROOT_COMPOSE` variable in the `Makefile` when you need it.

---

## Changing the scheduler (Go)

The scheduler source in `scheduler/` is shared between root and cluster. Both containers need to be rebuilt after a change.

```bash
# edit scheduler/...
make rebuild s=root_scheduler
make rebuild-cluster s=cluster_scheduler
```

Alternatively, add this target to the `Makefile` to do it in one step:

```makefile
rebuild-scheduler:
	$(ROOT_COMPOSE) build root_scheduler && $(ROOT_COMPOSE) up -d root_scheduler
	$(CLUSTER_COMPOSE) build cluster_scheduler && $(CLUSTER_COMPOSE) up -d cluster_scheduler
```

---

## Testing with a real worker node

NodeEngine only runs on Linux, so a lightweight Orbstack VM is used. The VM
reaches the Mac at `host.orb.internal` (Orbstack's built-in hostname), so
no manual IP configuration is needed.

```bash
make worker-create   # one-time: creates VM, installs NodeEngine, configures cluster address
make worker-up       # start NodeEngine
make worker-logs     # tail logs
make worker-down     # stop NodeEngine
make worker-shell    # open a shell in the VM (for manual inspection)
make worker-delete   # remove the VM entirely
```

`worker-create` installs the `alpha` release of NodeEngine, which matches
the `develop` branch running in Docker. Run it again after a version bump
to upgrade.

### Working on go_node_engine/ locally

To test your own NodeEngine changes, use the local-build workflow instead:

```bash
make worker-create-local  # one-time: creates VM, installs containerd, builds + deploys from source
make worker-up            # start NodeEngine
make worker-logs          # tail /var/log/oakestra/nodeengine.log
# edit go_node_engine/...
make worker-rebuild       # recompile, push updated binary, restart service
```

`worker-rebuild` is the fast iteration loop — it only rebuilds and redeploys,
so after the one-time setup it takes ~10–15 seconds per cycle.

The binaries are cross-compiled on the Mac for `linux/arm64` (or `amd64` on
Intel) and pushed into the VM with `orbctl push`. No Go toolchain needed
inside the VM.

To use a different VM name: `make worker-create WORKER_VM=my-vm`.

---

## Developing oakestra-net locally

By default the network services (`root_service_manager`, `cluster_service_manager`) use pre-built images from GHCR. To build from your local `../oakestra-net` checkout instead, uncomment these lines in the `Makefile`:

```makefile
# ROOT_COMPOSE    += -f root_orchestrator/override-local-service-manager.yml
# CLUSTER_COMPOSE += -f cluster_orchestrator/override-local-service-manager.yml
```

---

## How the network works

Both stacks declare `networks: default: name: oakestra`, so all containers — root and cluster — share one Docker network. Service hostnames resolve directly (e.g. `cluster_manager` can reach `system_manager` by name). No IP configuration is needed for local dev.
