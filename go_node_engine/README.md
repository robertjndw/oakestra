# NodeEngine 

The node engine is the core of the Oakestra worker node. 

## Build the NodeEngine 

Move to the build directory and run `./build.sh` and then `./install.sh $(dpkg --print-architecture)`.

## Use the NodeEngine 

You can start the node engine using the `sudo NodeEngine` command.

**N.B.** If you wish to start the NodeEngine standalone, without network component ([NetManager](https://github.com/oakestra/oakestra-net/tree/develop/node-net-manager)), first run `sudo NodeEngine config network off` to shut down overlay network mode.

`NodeEngine -h` gives you the list of available startup options.

```
Start a New Oakestra Worker Node

Usage:
  NodeEngine [flags]
  NodeEngine [command]

Available Commands:
  config      configure the node engine
  help        Help about any command
  logs        tail check node engine logs
  status      check status of node engine
  stop        stops the NodeEngine (and NetManager if configured)
  version     Print the version number of NodeEngine

Flags:
  -c, --certFile string         Path to certificate for TLS support
  -a, --clusterAddr string      Address of the cluster orchestrator without port (default "localhost")
  -p, --clusterPort int         Port of the cluster orchestrator (default 10100)
  -d, --detatch                 Run the NodeEngine in the background (daemon mode)
      --flops-learner           Enables the ML-data-server sidecar for data collection for FLOps learners.
  -h, --help                    help for NodeEngine
      --image-builder           Checks if the host has QEMU (apt's qemu-user-static) installed for building multi-platform images.
  -k, --keyFile string          Path to key for TLS support
  -l, --logs string             Directory for application's logs (default "/tmp")
  -o, --overlayNetwork string   Options: default,disabled,custom:<path>. <path> points to the overlay component socket. (default "default")

Use "NodeEngine [command] --help" for more information about a command.
```

## Running a worker locally via Docker (dev setup)

The worker can be run as a container alongside the cluster orchestrator stack. This uses Docker-in-Docker so that workloads deployed by NodeEngine run inside the worker container, with NetManager able to reach their network namespaces.

### Prerequisites

- The cluster orchestrator stack must be running (`docker compose up` in `cluster_orchestrator/`)
- `SYSTEM_MANAGER_URL` must be set to your root orchestrator's IP (same requirement as the normal cluster setup)

### Start the worker

From `cluster_orchestrator/`:

```bash
docker compose up --build worker
```

To simulate multiple workers:

```bash
docker compose up --build --scale worker=3
```

The worker container builds NodeEngine from source and downloads the matching NetManager binary from the `oakestra-net` releases. The NetManager version defaults to the version in `version.txt` and can be overridden:

```bash
OAKESTRA_VERSION=v0.4.411 docker compose up --build worker
```

### Verify registration

Check that the worker registered with the cluster manager:

```bash
curl -s http://localhost:10100/api/nodes | python3 -m json.tool
```

Check that the cluster is visible at the root:

```bash
curl -s http://localhost:10000/api/clusters | python3 -m json.tool
```

### Deploy a test service

```bash
# Obtain a JWT
TOKEN=$(curl -s -X POST http://localhost:10000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"Admin","password":"Admin"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")

# Deploy a minimal container
curl -s -X POST http://localhost:10000/api/application \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "application_name": "test",
    "application_namespace": "default",
    "application_desc": "smoke test",
    "microservices": [{
      "microservice_name": "ping",
      "microservice_namespace": "default",
      "virtualization": "container",
      "cmd": ["sh","-c","while true; do echo ok; sleep 5; done"],
      "image": "alpine:latest",
      "memory": 100, "vcpus": 1, "vgpus": 0, "vtpus": 0,
      "bandwidth_in": 0, "bandwidth_out": 0,
      "port": "", "constraints": []
    }]
  }' | python3 -m json.tool
```

Follow the deployment through the stack:

```bash
docker compose logs -f cluster_manager cluster_scheduler worker
```

Verify the container is running inside the worker's inner Docker daemon:

```bash
docker exec -it cluster_orchestrator-worker-1 docker ps
```

Check the service reached `RUNNING` status:

```bash
curl -s http://localhost:10000/api/applications \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

## How to configure additional OCI runtimes

Containerd (the default NodeEngine container runtime) allows any OCI-compliant runtime to be used as a plugin. This means that you can use any OCI-compliant runtime with Oakestra, including:
- runc
- runsc 
- runu 

and many more. The default container runtime is runc.

To configure a new OCI runtime, you need to edit the containerd configuration file. The default location for this file is `/etc/containerd/config.toml`.

### Example configuration file for runsc secure containerd runtime (aka gVisor)

1. Add the following configuration in your containerd configuration file `/etc/containerd/config.toml`:
```toml
[plugins.cri.containerd.runtimes.runsc]
  runtime_type = "io.containerd.runsc.v1"
[plugins.cri.containerd.runtimes.runsc.options]
  TypeUrl = "io.containerd.runsc.v1.options"
  ConfigPath = "/etc/containerd/runsc.toml"
```

2. Install gVisor on your system as usual following the [gVisor installation instructions](https://gvisor.dev/docs/user_guide/install/).

3. Restart the Oakestra node engine. You will notice how the new runtime is automatically detected and available for your applications.  

