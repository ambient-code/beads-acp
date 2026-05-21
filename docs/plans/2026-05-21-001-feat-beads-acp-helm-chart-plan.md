---
title: "feat: Beads ACP Helm chart for OpenShift shared server deployment"
status: active
origin: docs/superpowers/specs/2026-05-21-beads-acp-design.md
created: 2026-05-21
depth: standard
---

# feat: Beads ACP Helm chart for OpenShift shared server deployment

## Summary

Implement a Helm chart and container images that deploy beads in shared server mode on OpenShift 4.14+ / ROSA. Two workloads: a Dolt SQL server (StatefulSet) for persistent issue storage, and a beads-mcp server (Deployment) bridged to HTTP via mcp-proxy for AI agent access. Team developers connect via `oc port-forward`; agents connect via an OpenShift Route. OpenShift-specific features (oauth-proxy, Routes, service serving certs) are toggled via values for upstream compatibility.

---

## Problem Frame

Beads is a Go-based issue tracker backed by Dolt (a version-controlled MySQL-compatible database). Its "shared server mode" runs a single `dolt sql-server` that multiple clients connect to over TCP. The `beads-mcp` MCP server enables AI agent access but only supports stdio transport. There are no published container images for either component, and the official Dolt image runs as root — incompatible with OpenShift's restricted SCC.

This project provides the deployment packaging to run beads as a shared team service on OpenShift, with the intent of contributing it upstream.

---

## Scope Boundaries

**In scope:**
- Helm chart with all templates (StatefulSet, Deployment, Services, Route, ConfigMap, Secrets, NetworkPolicy)
- Two Containerfiles: one for Dolt (non-root wrapper), one for beads-mcp (with bd binary)
- values.yaml with OpenShift-specific features toggled for upstream portability
- Helm chart test template for connection validation

**Out of scope / deferred:**
- CI/CD pipeline for image builds (can be added later)
- Ingress resource for vanilla Kubernetes (Route covers OpenShift; Ingress is a future PR)
- Backup/restore procedures for the Dolt PVC
- Multi-replica Dolt or read replicas
- Monitoring/alerting (Prometheus ServiceMonitor, Grafana dashboards)

---

## Key Technical Decisions

**Use `dolthub/dolt-sql-server` as base, not `dolthub/dolt`.** The `dolt-sql-server` image includes `docker-entrypoint.sh` with server lifecycle management (port readiness checks, graceful shutdown via tini). The CLI image requires manual server invocation. (see origin: design spec, Components/1: Dolt SQL Server)

**Custom Dolt Containerfile required.** The official `dolthub/dolt-sql-server` image runs as root with no `USER` directive. OpenShift's `restricted-v2` SCC requires `runAsNonRoot`. We wrap the official image to add a non-root user (UID 1001), fix directory permissions on `/var/lib/dolt` and `/etc/dolt`, and set `USER 1001`. This is the minimal change — we don't rebuild Dolt from source. (Research finding)

**TLS via Dolt config file, not CLI flags.** Dolt's `sql-server` does not expose `--tls-cert`/`--tls-key` flags. TLS is configured through a YAML config file under `listener.tls_cert` / `listener.tls_key`. The chart mounts this config via a ConfigMap at `/etc/dolt/servercfg.d/`. (Research finding — corrects design spec Components/1: Dolt SQL Server)

**mcp-proxy spawns beads-mcp as a subprocess.** mcp-proxy's server mode runs: `mcp-proxy --pass-environment --host 0.0.0.0 --port 8080 -- beads-mcp`. The `--pass-environment` flag is required to forward `BEADS_DOLT_*` env vars to the spawned process. The `--host 0.0.0.0` is required for Kubernetes Service/probe reachability (default is 127.0.0.1). The `/status` endpoint provides health checks. No wrapper scripts needed. (Research finding)

---

## Output Structure

```
beads-acp/
├── images/
│   ├── dolt-server/
│   │   └── Containerfile
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
│       ├── serviceaccount.yaml
│       ├── networkpolicy.yaml
│       └── tests/
│           └── test-connection.yaml
└── docs/
    ├── plans/
    └── superpowers/
        └── specs/
```

---

## Implementation Units

### U1. Repository scaffolding and Helm chart skeleton

**Goal:** Create the directory structure, `Chart.yaml`, initial `values.yaml`, and `_helpers.tpl` with standard label/selector helpers.

**Requirements:** Establishes the foundation all other units depend on.

**Dependencies:** None.

**Files:**
- `chart/Chart.yaml` (create)
- `chart/values.yaml` (create)
- `chart/templates/_helpers.tpl` (create)

**Approach:** `Chart.yaml` declares `apiVersion: v2`, chart name `beads-acp`, app version matching the beads release. `_helpers.tpl` defines helpers for `fullname`, `labels`, `selectorLabels`, and `serviceAccountName`. `values.yaml` contains the full structure from the design spec with all defaults, plus the new `dolt.image` section pointing at the custom wrapper image.

**Patterns to follow:** Standard Helm chart conventions. Use `{{ include "beads-acp.fullname" . }}` pattern for all resource names.

**Test expectation:** none — pure scaffolding.

**Verification:** `helm lint chart/` passes with no errors.

---

### U2. Dolt server Containerfile (non-root wrapper)

**Goal:** Create a Containerfile that wraps `dolthub/dolt-sql-server` to run as non-root (UID 1001) for OpenShift restricted SCC compatibility.

**Requirements:** OpenShift `restricted-v2` SCC compliance. Container must start and serve MySQL protocol on port 3308.

**Dependencies:** None (independent of chart work).

**Files:**
- `images/dolt-server/Containerfile` (create)

**Approach:** `FROM dolthub/dolt-sql-server:latest`. Create user `dolt` (UID 1001, group 0 for OpenShift arbitrary UID support). Set ownership/permissions on `/var/lib/dolt` (data), `/etc/dolt/servercfg.d/` (config), and the dolt home directory (for `.dolt/` global config). Set `USER 1001`. Preserve the original `ENTRYPOINT` from the base image (`tini` + `docker-entrypoint.sh`). The group 0 (root group) permission pattern is the OpenShift convention for arbitrary UID assignment.

**Test scenarios:**
- Build image successfully with `docker build`
- Container starts with `--user 1001:0` and serves MySQL on port 3308
- Container starts with OpenShift's arbitrary UID (e.g., `--user 1000620000:0`) and serves MySQL
- `/var/lib/dolt` is writable by the container user
- TLS config file at `/etc/dolt/servercfg.d/` is readable

**Verification:** `docker build` succeeds. `docker run --user 1001:0 -p 3308:3308 <image>` starts and accepts MySQL connections.

---

### U3. beads-mcp Containerfile

**Goal:** Create a Containerfile that bundles the `beads-mcp` Python package and the `bd` Go binary on UBI 9 minimal.

**Requirements:** Non-root, includes both `beads-mcp` and `bd`, entrypoint is `beads-mcp`.

**Dependencies:** None (independent of chart work).

**Files:**
- `images/beads-mcp/Containerfile` (create)

**Approach:** `FROM registry.access.redhat.com/ubi9/ubi-minimal`. Install Python 3.11 and pip via `microdnf`. Install `uv` via pip, then `uv pip install beads-mcp mcp-proxy` into a system-wide location (mcp-proxy is needed to bridge stdio → HTTP in the Deployment). Download a pinned version of the beads CLI binary from GitHub releases — note that release assets are named `beads_X.Y.Z_linux_amd64.tar.gz` (the binary may be named `beads` not `bd` inside the archive; verify and symlink if needed). Verify the download checksum against the release's published checksums. Create non-root user (UID 1001, group 0). Set `USER 1001`, `ENTRYPOINT ["beads-mcp"]`.

**Test scenarios:**
- Build image successfully
- `beads-mcp --help` or `beads-mcp` runs without import errors
- `bd version` outputs a valid version string
- Container runs as non-root (UID 1001)

**Verification:** `docker build` succeeds. `docker run --user 1001:0 <image> bd version` returns a version.

---

### U4. Dolt StatefulSet and ConfigMap

**Goal:** Create the Dolt server StatefulSet template with TLS config, security context, and health checks. Create the ConfigMap for Dolt server configuration.

**Requirements:** Single-replica StatefulSet, PVC via volumeClaimTemplate, TLS via mounted config, compatible with restricted-v2 SCC.

**Dependencies:** U1 (chart skeleton).

**Files:**
- `chart/templates/dolt-statefulset.yaml` (create)
- `chart/templates/dolt-configmap.yaml` (create)

**Approach:**

StatefulSet with `replicas: 1`. No init container — the `dolt-sql-server` entrypoint (`docker-entrypoint.sh`) handles all initialization natively: it discovers config files from `/etc/dolt/servercfg.d/`, creates databases from the `DOLT_DATABASE` env var, processes init scripts from `/docker-entrypoint-initdb.d/`, and manages dolt global config from `/etc/dolt/doltcfg.d/`. The main container uses the default entrypoint with no extra CLI args — the entrypoint auto-discovers the config file and appends `--config` itself.

**Port configuration note:** The `docker-entrypoint.sh` hardcodes `--port=3306` in its `dolt_server_initializer` function and checks readiness on port 3306. The config file's `listener.port` setting may or may not override this. During implementation, verify whether config file port takes precedence over the entrypoint's hardcoded port. If it does not, either use port 3306 throughout the chart or override the entrypoint command.

ConfigMap contains the Dolt server YAML config mounted at `/etc/dolt/servercfg.d/config.yaml`:
```yaml
listener:
  host: 0.0.0.0
  port: 3308
  tls_cert: /etc/dolt/tls/tls.crt
  tls_key: /etc/dolt/tls/tls.key
```
TLS paths reference the Secret mounted from the OpenShift service serving cert. The ConfigMap content is conditionally rendered based on `dolt.tls.enabled`. The entrypoint auto-discovers exactly one `.yaml` file in `/etc/dolt/servercfg.d/`.

`volumeClaimTemplate` uses `storageClassName` and `resources.requests.storage` from values. Volume mounts: PVC at `/var/lib/dolt`, ConfigMap at `/etc/dolt/servercfg.d/`, TLS Secret at `/etc/dolt/tls/`.

Security context at pod and container level: `runAsNonRoot: true`, `allowPrivilegeEscalation: false`, `seccompProfile: { type: RuntimeDefault }`, `capabilities: { drop: [ALL] }`.

Health checks: liveness and readiness use `tcpSocket` on `dolt.port`. Startup probe uses `tcpSocket` with `failureThreshold: 30` and `periodSeconds: 10` (5 minutes for first boot). If the entrypoint's hardcoded port 3306 cannot be overridden, health checks must target 3306 instead.

Optional oauth-proxy sidecar container rendered when `oauthProxy.enabled`. It listens on a separate port (e.g., 4180), proxies to the Dolt port on localhost.

**Test scenarios:**
- Template renders valid YAML with default values
- Template renders valid YAML with `oauthProxy.enabled: false` (no sidecar)
- Template renders valid YAML with `dolt.tls.enabled: false` (no TLS volume mounts, no TLS in ConfigMap)
- volumeClaimTemplate uses the configured storage class and size
- Security context fields are all present and correct
- ConfigMap is the only `.yaml` file in `/etc/dolt/servercfg.d/` (entrypoint expects exactly one)

**Verification:** `helm template chart/` renders valid YAML. `helm lint chart/` passes. Template output matches expected structure when piped through `kubectl apply --dry-run=client`.

---

### U5. Dolt Service and Secrets

**Goal:** Create the ClusterIP Service for Dolt and the Secrets template for credentials and TLS.

**Requirements:** Service on port 3308, service serving cert annotation for OpenShift TLS, credential Secret for Dolt password.

**Dependencies:** U1, U4.

**Files:**
- `chart/templates/dolt-service.yaml` (create)
- `chart/templates/secrets.yaml` (create)

**Approach:**

Service: ClusterIP targeting the Dolt pod on port 3308 (or the oauth-proxy port when enabled). When `dolt.tls.enabled` and on OpenShift, the Service gets the `service.beta.openshift.io/serving-cert-secret-name: {{ .Values.dolt.tls.secretName }}` annotation. This tells OpenShift to auto-provision a TLS cert into the named Secret.

Secrets template: creates a Secret with `BEADS_DOLT_PASSWORD` (auto-generated via `randAlphaNum 24` if not provided in values, using `lookup` to preserve across upgrades). Conditional — only rendered when a password is configured or auto-generated.

**Test scenarios:**
- Service renders with correct port and selector
- Service serving cert annotation is present when `dolt.tls.enabled: true`
- Service serving cert annotation is absent when `dolt.tls.enabled: false`
- Service targets oauth-proxy port when `oauthProxy.enabled: true`
- Secret generates a stable password across `helm upgrade`

**Verification:** `helm template chart/` renders correct Service and Secret manifests.

---

### U6. beads-mcp Deployment

**Goal:** Create the Deployment template for the beads-mcp server (single container running mcp-proxy + beads-mcp) and optional oauth-proxy.

**Requirements:** Single container running `mcp-proxy` that spawns `beads-mcp` as a subprocess. Environment variables configure `bd` to connect to Dolt. Health checks on mcp-proxy `/status` endpoint.

**Dependencies:** U1, U4 (needs to reference the Dolt service name).

**Files:**
- `chart/templates/mcp-deployment.yaml` (create)

**Approach:**

Deployment with `replicas` from values (default 1). Pod has one or two containers:

1. **beads-mcp**: Uses the beads-mcp image (which bundles both `beads-mcp` and `mcp-proxy` Python packages, plus the `bd` binary). Container command overrides entrypoint to: `mcp-proxy --pass-environment --host 0.0.0.0 --port 8080 -- beads-mcp`. The `--pass-environment` flag forwards `BEADS_DOLT_*` env vars to the spawned `beads-mcp` subprocess. Environment variables:
   - `BEADS_DOLT_SERVER_MODE=1`
   - `BEADS_DOLT_SHARED_SERVER=1`
   - `BEADS_DOLT_SERVER_HOST={{ include "beads-acp.fullname" . }}-dolt`
   - `BEADS_DOLT_SERVER_PORT={{ .Values.dolt.port }}`
   - `BEADS_DOLT_SERVER_TLS={{ if .Values.dolt.tls.enabled }}1{{ else }}0{{ end }}`

   Note: `BEADS_DOLT_SERVER_HOST` uses the short Service name (not FQDN) to match the TLS certificate CN/SAN issued by OpenShift service serving certs.

2. **oauth-proxy** (optional): Same pattern as Dolt, fronting port 8080.

Same security context as the Dolt StatefulSet.

Liveness/readiness probes: HTTP GET on `/status` from mcp-proxy (port 8080).

**Test scenarios:**
- Template renders valid YAML with default values
- Template renders without oauth-proxy when `oauthProxy.enabled: false`
- Environment variables correctly reference the Dolt service name and namespace
- `BEADS_DOLT_SERVER_TLS` is `1` when TLS enabled, `0` when disabled
- Replica count matches `mcp.replicas` value
- Health check targets `/status` on port 8080

**Verification:** `helm template chart/` renders correct Deployment manifest. Environment variable values are correct.

---

### U7. MCP Service, Route, ServiceAccounts, and NetworkPolicy

**Goal:** Create remaining networking and access control templates.

**Requirements:** MCP Service on port 8080, OpenShift Route for external MCP access, ServiceAccounts, optional NetworkPolicy.

**Dependencies:** U1, U6.

**Files:**
- `chart/templates/mcp-service.yaml` (create)
- `chart/templates/route.yaml` (create)
- `chart/templates/serviceaccount.yaml` (create)
- `chart/templates/networkpolicy.yaml` (create)

**Approach:**

MCP Service: ClusterIP on port 8080 (or oauth-proxy port when enabled), selecting the MCP deployment pods.

Route: Conditional on `route.enabled`. TLS edge termination. `host` from values (empty = auto-assigned). Targets the MCP service. Uses `if .Capabilities.APIVersions.Has "route.openshift.io/v1"` guard so the template is silently skipped on non-OpenShift clusters.

ServiceAccounts: Two ServiceAccounts (one for dolt, one for mcp), both with `automountServiceAccountToken: false` since neither needs Kubernetes API access. When `oauthProxy.enabled`, the MCP ServiceAccount gets the `serviceaccounts.openshift.io/oauth-redirecturi.primary` annotation for oauth-proxy redirect.

NetworkPolicy: Conditional on a `networkPolicy.enabled` value. Dolt ingress: allow from pods with the MCP app label only. MCP ingress: allow from the OpenShift ingress namespace (for Route traffic).

**Test scenarios:**
- MCP Service renders with correct port and selector
- Route renders only when `route.enabled: true`
- Route is absent from output when `route.enabled: false`
- Route includes TLS configuration
- ServiceAccounts render with correct names and annotations
- NetworkPolicy renders only when `networkPolicy.enabled: true`
- NetworkPolicy allows MCP → Dolt traffic
- NetworkPolicy denies other traffic to Dolt

**Verification:** `helm template chart/` renders all networking resources correctly. `helm lint` passes.

---

### U8. Helm chart test and end-to-end validation

**Goal:** Create the Helm test template and validate the complete chart renders correctly with various value combinations.

**Requirements:** Test pod that validates Dolt connectivity. Chart passes lint and template rendering for all major value combinations.

**Dependencies:** U4, U5, U6, U7.

**Files:**
- `chart/templates/tests/test-connection.yaml` (create)

**Approach:**

Helm test pod: uses the beads-mcp image (which contains `bd`). Runs `bd version` to verify the binary works, then attempts a MySQL TCP connection to the Dolt service using a simple `nc -z` or `timeout 5 bash -c 'echo > /dev/tcp/$HOST/$PORT'` check. Pod has `helm.sh/hook: test` annotation.

Validate the full chart renders correctly by running `helm template` with these value combinations:
- All defaults (OpenShift mode)
- `oauthProxy.enabled: false` (no auth sidecars)
- `route.enabled: false` (no Route)
- `dolt.tls.enabled: false` (no TLS)
- `networkPolicy.enabled: true`
- All optional features disabled (vanilla Kubernetes mode)

**Test scenarios:**
- Test pod renders with correct Service reference and port
- Test pod has `helm.sh/hook: test` annotation
- `helm lint chart/` passes for all value combinations
- `helm template chart/` produces valid YAML for all value combinations
- No unnamed or duplicate resources across all templates

**Verification:** `helm lint chart/` passes. `helm template chart/ | kubectl apply --dry-run=client -f -` succeeds (if a cluster is available). All value combination templates render without error.

---

## System-Wide Impact

**Design spec update needed:** The design spec at `docs/superpowers/specs/2026-05-21-beads-acp-design.md` should be updated to reflect:
1. A second Containerfile for Dolt (non-root wrapper) — `images/dolt-server/Containerfile`
2. TLS configured via Dolt YAML config file, not CLI flags
3. mcp-proxy installed in the beads-mcp image (single container, not sidecar)
4. `networkPolicy.enabled` added to values.yaml structure

**Upstream contribution considerations:** The chart is designed with toggle-based OpenShift features so it can be contributed to `gastownhall/beads` as-is. The custom Dolt Containerfile may motivate an upstream PR to `dolthub/dolt` for native non-root support.

---

## Prodsec Alignment

Reviewed against `prodsec-skills:container-hardening` and `prodsec-skills:network-security`.

### Pinned Component Versions

| Component | Version | Source |
|-----------|---------|--------|
| `dolthub/dolt-sql-server` | `1.83.4` (pin by digest) | [Docker Hub](https://hub.docker.com/r/dolthub/dolt-sql-server) — must be >=1.46.0 for root-localhost-only restriction |
| `ubi9/ubi-minimal` | latest (omit floating tag per Red Hat catalog convention) | [Red Hat Catalog](https://catalog.redhat.com/software/containers/search) |
| `beads` CLI | `v0.60.0` (pin specific version) | [GitHub Releases](https://github.com/gastownhall/beads/releases) |
| `beads-mcp` | latest from PyPI | [PyPI](https://pypi.org/project/beads-mcp/) |
| `mcp-proxy` | `0.11.0` (pin specific version) | [PyPI](https://pypi.org/project/mcp-proxy/) |
| `ose-oauth-proxy` | Match cluster version (e.g., `v4.17`) | [Red Hat Catalog](https://catalog.redhat.com/en/software/containers/openshift4/ose-oauth-proxy/5cdb2133bed8bd5717d5ae64) |

Non-Red Hat base images (`dolthub/dolt-sql-server`) must be pinned by version or digest. Red Hat UBI images omit floating tags to get the latest per Red Hat catalog convention.

### Container Hardening (prodsec-skills:container-hardening)

**Mandatory (all profiles):**
- `runAsNonRoot: true` — set at pod level ✓
- Numeric non-root `USER` in Containerfile (UID 1001, not 0 or 1337) ✓
- `allowPrivilegeEscalation: false` ✓
- `capabilities: { drop: [ALL] }` ✓
- Base image integrity — no runtime modification of `/bin`, `/sbin`, `/lib`, `/usr/bin`, `/usr/lib` etc.
- SELinux enforcing on all cluster nodes (OpenShift default)
- Red Hat UBI base for beads-mcp image ✓; Dolt wraps a non-Red Hat base (acceptable — documented)

**Recommended best practices to add:**
- `readOnlyRootFilesystem: true` on both workloads — mount `emptyDir` for `/tmp` and any runtime-writable paths
- `automountServiceAccountToken: false` ✓ (already in plan)
- One process per container ✓ (Dolt runs one process; beads-mcp container runs mcp-proxy which spawns beads-mcp — acceptable as parent/child)

**Containerfile hardening:**
- Multi-stage build for beads-mcp: builder stage installs `uv` and Python packages; final stage copies only installed packages into clean `ubi-minimal` — removes compilers, pip, uv from final image
- Pin `dolthub/dolt-sql-server` by digest (non-Red Hat registry)
- Verify `bd` binary download checksum against published `checksums.txt`
- Add OCI labels: `org.opencontainers.image.source`, `org.opencontainers.image.revision`, `org.opencontainers.image.version`
- Clean package caches: `microdnf clean all` in same RUN layer as install
- Run `hadolint` on Containerfiles in CI

### Network Security (prodsec-skills:network-security)

**Mandatory (all profiles):**
- `hostIPC: false`, `hostNetwork: false`, `hostPID: false` on all pods
- No `hostPath` volumes, no `hostPort`
- No containers on OpenShift reserved ports (22623, 22624) ✓ (using 3308 and 8080)
- All container ports declared in pod spec

**Recommended best practices:**
- Default deny-all NetworkPolicy for both ingress and egress (make `networkPolicy.enabled: true` the default, not optional)
- Add egress NetworkPolicy: Dolt egress denied (needs no outbound); MCP egress allow only to Dolt ClusterIP and DNS (port 53)
- Services should use `ipFamilyPolicy: PreferDualStack` for IPv6 readiness
- Declare all container ports explicitly with names (e.g., `name: mysql`, `name: http`)

### TLS Hardening

- Add `require_secure_transport: true` to Dolt server config (prevents plaintext downgrade)
- Set `tls_minimum_version: "1.2"` in Dolt listener config
- Use short Service name for `BEADS_DOLT_SERVER_HOST` to match TLS cert CN/SAN ✓ (fixed in U6)

### Auth Enforcement

- Configure a non-root Dolt user with password from the generated Secret (`BEADS_DOLT_PASSWORD`). Wire the password into Dolt init SQL via `/docker-entrypoint-initdb.d/` ConfigMap and into the beads-mcp env vars
- Add values.yaml validation: `route.enabled: true` should require `oauthProxy.enabled: true` (prevent unauthenticated external exposure)
- Restrict `oc port-forward` access via RBAC (`pods/portforward` verb) — document as operational requirement

---

## Deferred Implementation Notes

- Release assets are named `beads_X.Y.Z_linux_amd64.tar.gz` — verify whether the binary inside is named `beads` or `bd` and symlink accordingly. Pin a specific version rather than resolving `latest` at build time
- The `--pass-environment` flag resolves env var forwarding to `beads-mcp` (confirmed from mcp-proxy docs). Runtime verification is still needed to confirm `bd` correctly reads `BEADS_DOLT_*` vars when spawned this way
- oauth-proxy configuration: cookie secret should be auto-generated via `randAlphaNum 32` with `lookup` preservation; session timeout ~8h; redirect URI constrained to Route hostname; upstream TLS validation enabled. Finalize against current `ose-oauth-proxy` docs during implementation
- The `dolt-sql-server` entrypoint's `dolt_server_initializer` hardcodes `--port=3306`. Verify at runtime whether the config file's `listener.port: 3308` overrides this. If not, either use port 3306 or override the entrypoint command
- The `dolt-sql-server` entrypoint's compatibility with non-root UID needs runtime verification — if it fails (e.g., it expects to chown directories), we may need to override the entrypoint
- Dolt reads TLS cert/key from disk at startup and does not reload on Secret rotation. OpenShift service serving certs auto-rotate. Document the operational requirement to restart Dolt pods when certs rotate, or add a sidecar to watch the Secret
- Dolt default root user has no password. Since v1.46.0, root is restricted to localhost. Pin the Dolt image to `>=v1.46.0` and verify the localhost-only restriction applies when accessed from other pods via Service. If not, configure a non-root Dolt user with the generated password
- `oc port-forward` bypasses NetworkPolicy (implemented by the kube API server, not pod networking). Document that OpenShift RBAC on the namespace is the primary access control for CLI users using port-forward
