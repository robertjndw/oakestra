#!/bin/bash
set -e

CLUSTER_ADDRESS="${CLUSTER_ADDRESS:-cluster_manager}"
CLUSTER_PORT="${CLUSTER_PORT:-10100}"
MQTT_URL="${MQTT_URL:-mqtt}"
MQTT_PORT="${MQTT_PORT:-10003}"

# Start inner Docker daemon (DinD)
/usr/local/bin/dockerd-entrypoint.sh dockerd >/var/log/dockerd.log 2>&1 &

echo "[oakestra] Waiting for Docker daemon..."
until docker info >/dev/null 2>&1; do
    sleep 1
done
echo "[oakestra] Docker daemon ready."

# NodeEngine uses a single ClusterAddress for both the HTTP handshake (port
# CLUSTER_PORT) and MQTT (port MQTT_PORT). Since those are separate containers
# in compose, we forward both ports to localhost so NodeEngine can use a single
# address for both.
socat TCP-LISTEN:${CLUSTER_PORT},fork,reuseaddr TCP:${CLUSTER_ADDRESS}:${CLUSTER_PORT} &
socat TCP-LISTEN:${MQTT_PORT},fork,reuseaddr TCP:${MQTT_URL}:${MQTT_PORT} &

# Write NodeEngine config
mkdir -p /etc/oakestra /var/log/oakestra
NodeEngine config default
NodeEngine config cluster localhost --clusterPort "${CLUSTER_PORT}"
# Use manual socket mode so NodeEngine does not try to invoke systemctl
NodeEngine config network manual

# Write NetManager config
mkdir -p /etc/netmanager
cat > /etc/netmanager/netcfg.json <<EOF
{
  "NodePublicAddress": "0.0.0.0",
  "NodePublicPort": "50103",
  "ClusterUrl": "${MQTT_URL}",
  "ClusterMqttPort": "${MQTT_PORT}",
  "DefaultInterface": "",
  "Debug": false,
  "PublicIPNetworking": false,
  "MqttCert": "",
  "MqttKey": ""
}
EOF

# Start NetManager — output goes to stdout so failures are visible in compose logs
NetManager &
NM_PID=$!

echo "[oakestra] Waiting for NetManager socket (pid ${NM_PID})..."
for i in $(seq 30); do
    if ! kill -0 "${NM_PID}" 2>/dev/null; then
        echo "[oakestra] ERROR: NetManager process exited early (exit code: $?)" >&2
        wait "${NM_PID}"; echo "[oakestra] NetManager exit status: $?" >&2
        exit 1
    fi
    [ -S /etc/netmanager/netmanager.sock ] && break
    sleep 1
done
if [ ! -S /etc/netmanager/netmanager.sock ]; then
    echo "[oakestra] ERROR: NetManager socket not ready after 30s" >&2
    exit 1
fi
echo "[oakestra] NetManager ready."

# Generate default containerd config so NodeEngine can probe runtime plugins
mkdir -p /etc/containerd
containerd config default > /etc/containerd/config.toml

exec nodeengined
