# beads-acp: Beads Shared Server Mode on OpenShift

## Overview

Helm chart and container images for deploying [beads](https://github.com/gastownhall/beads) in shared server mode on OpenShift. Enables team-wide issue tracking via the `bd` CLI and AI agent access via the beads-mcp server, backed by a shared Dolt SQL database.

## Target Environment

- OpenShift 4.14+ / ROSA
- Default storage class: `gp3-csi`
- Images pushed to the internal OpenShift registry

## Components

### 1. Dolt SQL Server (StatefulSet)

Single-replica StatefulSet using the official `dolthub/dolt` image.

**Storage**: `volumeClaimTemplate`, 20Gi default on `gp3-csi` (configurable). Data directory mounted at `/var/lib/dolt`.

**Init container**: Runs `dolt init` if the data directory is empty (first deploy only). Creates the Dolt repository structure at `/var/lib/dolt`. Individual project databases are created on first connection by `bd init --prefix <name>` — the init container only ensures the Dolt server can start.

**Main container**: Runs `dolt sql-server` with:
- `--host 0.0.0.0`
- `--port 3308`
- `--tls-cert` and `--tls-key` from a mounted TLS Secret
- Additional config from a ConfigMap

**TLS**: Service annotated with `service.beta.openshift.io/serving-cert-secret-name` for automatic OpenShift certificate provisioning. On vanilla Kubernetes, users provide their own cert Secret via `dolt.tls.secretName`.

**OAuth proxy sidecar** (optional, `oauthProxy.enabled`): `ose-oauth-proxy` fronts port 3308 for SSO authentication. When disabled, the Service points directly to Dolt.

**Health checks**:
- Liveness: TCP socket on port 3308
- Readiness: TCP socket on port 3308
- Startup: generous timeout for first-boot init

**Security context**: `runAsNonRoot: true`, `allowPrivilegeEscalation: false`, `seccompProfile: RuntimeDefault`, `drop: [ALL]`. Compatible with OpenShift `restricted-v2` SCC.

### 2. beads-mcp Server (Deployment)

1-2 replica Deployment (configurable). Pod contains:

**beads-mcp container**: Custom image (built from `images/beads-mcp/Containerfile`) containing:
- `beads-mcp` Python package (installed via `uv pip`)
- `bd` Go binary (latest GitHub release)
- Base: `ubi9/ubi-minimal`

Configured via environment variables to connect `bd` to the Dolt StatefulSet:
- `BEADS_DOLT_SERVER_MODE=1`
- `BEADS_DOLT_SHARED_SERVER=1`
- `BEADS_DOLT_SERVER_HOST=<dolt-service>.svc.cluster.local`
- `BEADS_DOLT_SERVER_PORT=3308`
- `BEADS_DOLT_SERVER_TLS=1`

Runs `beads-mcp` on stdio (the only transport it supports natively).

**mcp-proxy sidecar**: Bridges stdio → streamable HTTP. Uses `ghcr.io/sparfenyuk/mcp-proxy`. Spawns `beads-mcp` and exposes it as an HTTP endpoint on port 8080.

**OAuth proxy sidecar** (optional): Same pattern as Dolt — fronts the mcp-proxy HTTP port.

**No persistent storage** — stateless, all state in Dolt.

**Health checks**: HTTP on the mcp-proxy port.

### 3. Container Images

One Containerfile in this repo:

**`images/beads-mcp/Containerfile`**:
- Base: `registry.access.redhat.com/ubi9/ubi-minimal`
- Install Python 3.11+ via microdnf
- Install `beads-mcp` via `uv pip install beads-mcp`
- Download latest `bd` binary from GitHub releases
- Non-root user
- Entrypoint: `beads-mcp`

Dolt uses the official `dolthub/dolt` image directly. mcp-proxy uses its published image.

### 4. Networking

**Services**:
- `beads-dolt` — ClusterIP, port 3308
- `beads-mcp` — ClusterIP, port 8080

**Route**: Single OpenShift Route for the MCP endpoint with TLS edge termination. No Route for Dolt — team CLI users connect via `oc port-forward` (MySQL protocol doesn't work through HTTP Routes).

**NetworkPolicy** (optional, toggled via values):
- Dolt accepts connections only from beads-mcp pods and oauth-proxy
- MCP accepts connections only from the Route ingress controller

**ServiceAccount**: One per component, no extra RBAC needed.

### 5. Security

All pods use:
- `runAsNonRoot: true`
- `allowPrivilegeEscalation: false`
- `seccompProfile: RuntimeDefault`
- `capabilities: { drop: [ALL] }`
- No custom SCCs — compatible with `restricted-v2`

End-to-end TLS:
- Route → oauth-proxy: TLS edge termination at Route
- oauth-proxy → mcp-proxy: localhost within pod
- bd → Dolt: TLS via OpenShift service serving certificates

## Repository Structure

```
beads-acp/
├── images/
│   └── beads-mcp/
│       └── Containerfile
├── chart/
│   ├── Chart.yaml
│   ├── values.yaml
│   └── templates/
│       ├── _helpers.tpl
│       ├── dolt-statefulset.yaml
│       ├── dolt-service.yaml
│       ├── dolt-configmap.yaml
│       ├── mcp-deployment.yaml
│       ├── mcp-service.yaml
│       ├── route.yaml
│       ├── secrets.yaml
│       └── tests/
│           └── test-connection.yaml
└── docs/
```

## values.yaml Structure

```yaml
global:
  namespace: beads-acp

dolt:
  image:
    repository: dolthub/dolt
    tag: latest
  port: 3308
  storage:
    size: 20Gi
    storageClass: gp3-csi
  resources:
    requests: { cpu: 500m, memory: 512Mi }
    limits: { cpu: "2", memory: 2Gi }
  tls:
    enabled: true
    secretName: dolt-serving-cert
  config: {}

mcp:
  replicas: 1
  image:
    repository: image-registry.openshift-image-registry.svc:5000/beads-acp/beads-mcp
    tag: latest
  proxy:
    image:
      repository: ghcr.io/sparfenyuk/mcp-proxy
      tag: latest
    port: 8080
  resources:
    requests: { cpu: 250m, memory: 256Mi }
    limits: { cpu: "1", memory: 1Gi }

oauthProxy:
  enabled: true
  image:
    repository: registry.redhat.io/openshift4/ose-oauth-proxy
    tag: latest
  cookieSecret: ""

route:
  enabled: true
  host: ""
  tls:
    termination: edge
```

## End-to-End Usage

### Developer using bd CLI

```bash
oc port-forward svc/beads-dolt 3308:3308 -n beads-acp

cd ~/my-project
bd init --prefix my-project --shared-server
export BEADS_DOLT_SERVER_HOST=127.0.0.1
export BEADS_DOLT_SERVER_PORT=3308
export BEADS_DOLT_SERVER_MODE=1

bd create --title "Implement auth flow" --priority high
bd list
```

### AI agent via MCP

Agent MCP client config points at the Route:

```json
{
  "mcpServers": {
    "beads": {
      "url": "https://beads-mcp-beads-acp.apps.cluster.example.com",
      "headers": {
        "Authorization": "Bearer <oauth-token>"
      }
    }
  }
}
```

### Multi-agent coordination

Multiple agents connect to the same MCP endpoint. Dolt handles concurrent writes with cell-level merge. Agents create, claim, and complete tasks through the shared beads instance.

## OpenShift-Specific vs Generic Kubernetes

Features toggled via values for upstream compatibility:

| Feature | OpenShift | Vanilla K8s |
|---------|-----------|-------------|
| OAuth proxy | `oauthProxy.enabled: true` | `false`, bring your own auth |
| Route | `route.enabled: true` | `false`, use Ingress instead |
| TLS certs | Service serving cert annotation | User-provided Secret |
| Image registry | Internal OCP registry | Any registry |
| Storage class | `gp3-csi` (ROSA default) | User-specified |

## Decisions

- **Monolithic chart** over subcharts: components are tightly coupled (MCP is useless without Dolt), single values.yaml is simpler to operate at this scale.
- **mcp-proxy sidecar** over patching beads-mcp: avoids upstream dependency, keeps this project self-contained. Can drop the sidecar if upstream adds native HTTP transport.
- **No custom Dolt image**: official image works, configured via Helm templates.
- **Port-forward for CLI access**: MySQL protocol can't traverse HTTP Routes. OAuth proxy on Dolt is for in-cluster service-to-service auth, not external CLI access.
- **Single Containerfile**: beads-mcp image bundles both the Python MCP server and the Go `bd` binary to keep the pod spec simple.
