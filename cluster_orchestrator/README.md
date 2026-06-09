# Cluster

By our design, a Cluster Orchestrator contains:

- a message broker (MQTT)
- a scheduler
- a Cluster Manager
- a database (mongoDB)

The edge nodes push cpu+memory data to the mqtt-broker.

## Message Format between cluster components

As a worker node, to register at the cluster manager / to be registerd by a cluster manager, the following json based message format is used.

```json
{
  "id": "int_id",
  "name": "name",
  "ip": "ip-address",
  "port": "port number"
}
```

mqtt data to publish cpu/memory information from worker to cluster manager via topic `nodes/id/information`:

```json
{
  "cpu": "int_id",
  "memory": "name"
}
```

mqtt data to publish control commands from CO to worker via topic `nodes/id/controls`:

```json
{
'command': 'deploy|delete|move|replicate',
'job': {
        'id': 'int',
        'name': 'job_name',
        'image': 'image_address',
        'technology': 'docker|unikernel',
        'etc': 'etc.'
        },
'cluster': 'cluster_id' (optional),
'worker': 'worker_id' (optional)
}
```

json based HTTP message from cluster manager to cluster scheduler:

- job description coming from system-manager

HTTP scheduling answer from scheduler back to cluster manager. A list of workers who are contacted

```json
{
  "workers": ["list", "of", "worker_ids"],
  "job": {
    "image": "image_url"
  }
}
```

## Usage

- First export the required parameters:

  - export SYSTEM_MANAGER_URL=" < ip address of the root orchestrator > "
  - export CLUSTER_NAME=" < name of the cluster > "
  - export CLUSTER_LOCATION=" < location of the cluster > "

>(optional) set the current branch for the system "libraries", otw it will default to develop.
>```
>export LIB_BRANCH=$(git rev-parse --abbrev-ref HEAD)
>```

- Use the docker-compose.yml with `docker-compose -f docker-compose.yml up --build` to start the cluster components.

N.b. if you're using docker compose with **sudo** don't forget to use the -E flag E.g., **sudo -E docker-compose etc..**. This will export the env variables.

## Custom Library Dependency

Per default, pip will build the python dependencies found `libraries/` (resource_abstractor_client and oakestra_utils_library) from the oakestra github
repository, specifically from the develop branch. To override this, set the environment variable `LIB_BRANCH`.

E.g. you have made changes to the resource_abstractor_client library and wish to test this locally. Push your changes to `XXX-example-library-rework` and set
`LIB_BRANCH` to `XXX-example-library-rework`. Pip will then pull the library from your branch. Note that only pushed changes will have an impact on your local setup.


## Customize deployment

It's possible to use the docker override functionality to exclude or customize the cluster orchestrator deployment.

### Exclude network component:

`docker-compose -f docker-compose.yml -f override-no-network.yml up --build`

### Customize network component version

- open and edit `override-custom-serivce-manager.yml` with the correct container image
- run the orchestrator with the override file: `docker-compose -f docker-compose.yml -f override-custom-service-manager.yml up --build`

### Use local development network component

In case you want to use changes made to the cluster network component in your deployment,
you can use the `override-local-service-manager.yml` override file.

- copy the `oakestra-net/cluster-service-manager/service-manager` folder to the `cluster_orchestrator` directory
- run the orchestrator with the override file: `docker-compose -f docker-compose.yml -f override-local-service-manager.yml up --build`

### Enable IPv6 for container deployments

Usage: `docker-compose -f docker-compose.yml -f override-ipv6-enabled.yml`

This override sets up a bridged docker network, assigning each container a static IPv4+IPv6 address.
Note that the IP protocol version used for connection establishment using hostname resolution depends on the implementation.
Example: IPv6 server receiving IPv4 request -> source address is in 4-to-6 mapped format (http://mars.tekkom.dk/w/index.php/IPv4-Mapped_IPv6_Address)

### Disable [observability stack](../root_orchestrator/config/README.md)

Usage: `docker-compose -f docker-compose.yml -f override-no-observe.yml`

### MQTT TLS authentication

The cluster uses NATS in MQTT-compatibility mode (see `nats/nats.conf`). Anonymous
access is enabled by default. TLS/cert-based MQTT authentication via a NATS
`mqtt { tls { ... } }` config block is planned as a follow-up and will replace
the old Mosquitto cert-auth approach.