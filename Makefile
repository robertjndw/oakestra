# ─── Oakestra local dev setup ────────────────────────────────────────────────
# Both stacks share the 'oakestra' Docker network, so service hostnames resolve
# across root and cluster without any special IP config.
#
# Quickstart:
#   cp .env.dev .env       # once, edit as needed
#   make up                # build + start everything
#   make log s=system_manager
#   make rebuild s=system_manager
#
# Orbstack worker node (full end-to-end testing):
#   make worker-create     # one-time: create VM + install NodeEngine
#   make worker-up         # start NodeEngine in the VM
#   make worker-down       # stop it
#   make worker-logs       # tail logs
# ─────────────────────────────────────────────────────────────────────────────

# Load local overrides (.env is gitignored)
-include .env

# Defaults — work as-is for a single-machine dev setup
export SYSTEM_MANAGER_URL    ?= system_manager
export CLUSTER_NAME          ?= dev-cluster
export CLUSTER_LOCATION      ?= 52.5200,13.4050,100
export LIB_BRANCH            ?= develop

# ── Compose command definitions ───────────────────────────────────────────────
# Lean dev config: no addons, no observability stack, no dashboard
ROOT_COMPOSE := docker compose \
    -f root_orchestrator/docker-compose.yml \
    -f root_orchestrator/override-no-addons.yml \
    -f root_orchestrator/override-no-observe.yml \
    -f root_orchestrator/override-no-dashboard.yml

CLUSTER_COMPOSE := docker compose \
    -f cluster_orchestrator/docker-compose.yml \
    -f cluster_orchestrator/override-no-addons.yml \
    -f cluster_orchestrator/override-no-observe.yml

# Optional: uncomment to build oakestra-net from local source instead of GHCR images
# ROOT_COMPOSE    += -f root_orchestrator/override-local-service-manager.yml
# CLUSTER_COMPOSE += -f cluster_orchestrator/override-local-service-manager.yml

# Name of the Orbstack Linux VM used as a worker node (override via env or .env)
WORKER_VM ?= oak-worker

# Translate Mac arch to Go/Linux arch (arm64 stays arm64; x86_64 → amd64)
GOARCH := $(shell uname -m | sed 's/x86_64/amd64/')

.PHONY: up down up-root up-cluster down-root down-cluster \
        logs-root logs-cluster log status \
        rebuild rebuild-cluster restart clean open help \
        worker-create worker-up worker-down worker-logs worker-shell worker-delete \
        worker-build worker-create-local worker-rebuild

# ── Main targets ──────────────────────────────────────────────────────────────

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	    | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

up: up-root up-cluster ## Build and start full stack (root + cluster)

down: down-root down-cluster ## Stop full stack

up-root: ## Start root orchestrator
	$(ROOT_COMPOSE) up -d --build

up-cluster: ## Start cluster orchestrator
	$(CLUSTER_COMPOSE) up -d --build

down-root: ## Stop root orchestrator
	$(ROOT_COMPOSE) down

down-cluster: ## Stop cluster orchestrator
	$(CLUSTER_COMPOSE) down

# ── Logs ──────────────────────────────────────────────────────────────────────

logs-root: ## Tail root orchestrator logs
	$(ROOT_COMPOSE) logs -f --tail=50

logs-cluster: ## Tail cluster orchestrator logs
	$(CLUSTER_COMPOSE) logs -f --tail=50

log: ## Follow a single container: make log s=system_manager
	@[ -n "$(s)" ] || (echo "Usage: make log s=<container_name>"; exit 1)
	docker logs -f --tail=100 $(s)

# ── Status ────────────────────────────────────────────────────────────────────

status: ## Show running Oakestra containers and their ports
	@docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" \
	    | grep -E "^NAMES|system_manager|cluster_manager|root_|cluster_|mongo|redis|mqtt|scheduler|abstractor|jwt"

# ── Rebuild ───────────────────────────────────────────────────────────────────

rebuild: ## Rebuild + restart a root service: make rebuild s=system_manager
	@[ -n "$(s)" ] || (echo "Usage: make rebuild s=<service>"; exit 1)
	$(ROOT_COMPOSE) build $(s)
	$(ROOT_COMPOSE) up -d $(s)

rebuild-cluster: ## Rebuild + restart a cluster service: make rebuild-cluster s=cluster_manager
	@[ -n "$(s)" ] || (echo "Usage: make rebuild-cluster s=<service>"; exit 1)
	$(CLUSTER_COMPOSE) build $(s)
	$(CLUSTER_COMPOSE) up -d $(s)

restart: ## Restart a container without rebuild: make restart s=system_manager
	@[ -n "$(s)" ] || (echo "Usage: make restart s=<container_name>"; exit 1)
	docker restart $(s)

# ── Worker node (Orbstack VM) ─────────────────────────────────────────────────
# NodeEngine only runs on Linux. These targets manage a lightweight Orbstack
# Linux VM as a local worker node.
#
# From inside an Orbstack VM, 'host.orb.internal' resolves to the Mac —
# so no manual IP detection is needed.

worker-create: ## One-time: create the Orbstack VM and install NodeEngine
	@orbctl info $(WORKER_VM) >/dev/null 2>&1 \
	    && echo "VM '$(WORKER_VM)' already exists, skipping create" \
	    || orbctl create ubuntu $(WORKER_VM)
	@echo "Installing dependencies..."
	@orb -m $(WORKER_VM) sudo apt-get install -y wget
	@echo "Installing NodeEngine (version: alpha = develop branch)..."
	@orb -m $(WORKER_VM) bash -c \
	    "mkdir -p /var/tmp/oak-install && cd /var/tmp/oak-install && curl -sfL https://raw.githubusercontent.com/oakestra/oakestra/develop/scripts/InstallOakestraWorker.sh \
	     | OAKESTRA_VERSION=alpha bash"
	@echo "Writing NodeEngine config..."
	@orb -m $(WORKER_VM) sudo mkdir -p /etc/oakestra
	@printf '{"conf_version":"1.0","cluster_address":"host.orb.internal","cluster_ssl":false,"cluster_port":10100,"app_logs":"/tmp","overlay_network":"disabled","public_ip":false,"overlay_network_port":0,"mqtt_cert_file":"","mqtt_key_file":"","addons":null,"virtualizations":[{"virutalizaiton_name":"containerd","virutalizaiton_runtime":"docker","virutalizaiton_active":true,"virutalizaiton_config":[]}],"csi_drivers":null}\n' \
	    | orb -m $(WORKER_VM) sudo bash -c 'cat > /etc/oakestra/conf.json'
	@echo ""
	@echo "Done. Run 'make worker-up' to start the worker."

worker-up: ## Start NodeEngine inside the worker VM
	orb -m $(WORKER_VM) sudo systemctl start nodeengine
	@echo "NodeEngine started. Use 'make worker-logs' to follow output."

worker-down: ## Stop NodeEngine inside the worker VM
	orb -m $(WORKER_VM) sudo systemctl stop nodeengine

worker-logs: ## Tail NodeEngine logs from the worker VM
	orb -m $(WORKER_VM) sudo tail -f /var/log/oakestra/nodeengine.log

worker-shell: ## Open a shell in the worker VM
	orb shell $(WORKER_VM)

worker-delete: ## Delete the worker VM entirely (destructive)
	@printf "Delete VM '$(WORKER_VM)'? This cannot be undone. [y/N] " \
	    && read ans && [ "$${ans:-N}" = "y" ]
	orbctl delete $(WORKER_VM)

# ── Local NodeEngine build (for developing go_node_engine/) ──────────────────
# Produces two binaries: NodeEngine (CLI) and nodeengined (daemon), both
# cross-compiled for linux/$(GOARCH) matching the Orbstack VM's architecture.

worker-build: ## Cross-compile NodeEngine + nodeengined for Linux (output: go_node_engine/build/)
	@echo "Building for linux/$(GOARCH)..."
	@cd go_node_engine && \
	    CGO_ENABLED=0 GOOS=linux GOARCH=$(GOARCH) go build \
	        -o build/NodeEngine_$(GOARCH) NodeEngine.go
	@cd go_node_engine && \
	    CGO_ENABLED=0 GOOS=linux GOARCH=$(GOARCH) go build \
	        -o build/nodeengined_$(GOARCH) internal/daemon/nodeengined.go
	@echo "Built: go_node_engine/build/NodeEngine_$(GOARCH) + nodeengined_$(GOARCH)"

worker-create-local: worker-build ## One-time VM setup using local NodeEngine source
	@orbctl info $(WORKER_VM) >/dev/null 2>&1 \
	    && echo "VM '$(WORKER_VM)' already exists, skipping create" \
	    || orbctl create ubuntu $(WORKER_VM)
	@echo "Installing containerd..."
	@orb -m $(WORKER_VM) sudo apt-get install -y containerd
	@orb -m $(WORKER_VM) sudo bash -c '\
	    mkdir -p /etc/containerd && \
	    containerd config default > /etc/containerd/config.toml && \
	    systemctl restart containerd'
	@orb -m $(WORKER_VM) sudo mkdir -p /var/log/oakestra
	@echo "Copying binaries and service file..."
	@orbctl push $(WORKER_VM) go_node_engine/build/NodeEngine_$(GOARCH) /tmp/NodeEngine
	@orbctl push $(WORKER_VM) go_node_engine/build/nodeengined_$(GOARCH) /tmp/nodeengined
	@orbctl push $(WORKER_VM) go_node_engine/nodeengine.service /tmp/nodeengine.service
	@orb -m $(WORKER_VM) sudo bash -c '\
	    mv /tmp/NodeEngine /bin/NodeEngine && chmod 755 /bin/NodeEngine && \
	    mv /tmp/nodeengined /bin/nodeengined && chmod 755 /bin/nodeengined && \
	    mv /tmp/nodeengine.service /etc/systemd/system/nodeengine.service && \
	    systemctl daemon-reload && systemctl enable nodeengine'
	@echo "Configuring cluster address..."
	@orb -m $(WORKER_VM) sudo bash -c '\
	    NodeEngine config default && \
	    NodeEngine config cluster host.orb.internal && \
	    NodeEngine config network off'
	@echo ""
	@echo "Done. Run 'make worker-up' to start."

worker-rebuild: worker-build ## Rebuild NodeEngine from local source and redeploy to the VM
	@orb -m $(WORKER_VM) sudo systemctl stop nodeengine 2>/dev/null || true
	@echo "Pushing updated binaries..."
	@orbctl push $(WORKER_VM) go_node_engine/build/NodeEngine_$(GOARCH) /tmp/NodeEngine
	@orbctl push $(WORKER_VM) go_node_engine/build/nodeengined_$(GOARCH) /tmp/nodeengined
	@orb -m $(WORKER_VM) sudo bash -c '\
	    mv /tmp/NodeEngine /bin/NodeEngine && chmod 755 /bin/NodeEngine && \
	    mv /tmp/nodeengined /bin/nodeengined && chmod 755 /bin/nodeengined'
	@orb -m $(WORKER_VM) sudo systemctl start nodeengine
	@echo "NodeEngine updated and restarted."

# ── Utilities ─────────────────────────────────────────────────────────────────

open: ## Open the API docs in the browser
	open "http://localhost:10000/api/docs"

clean: ## Remove containers AND data volumes — fresh start
	@printf "This deletes all Oakestra volumes (MongoDB, Redis). Continue? [y/N] " \
	    && read ans && [ "$${ans:-N}" = "y" ]
	$(ROOT_COMPOSE) down -v
	$(CLUSTER_COMPOSE) down -v
