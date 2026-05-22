"""Deployment orchestrator for beads-acp on OpenShift."""

from __future__ import annotations

import logging
import subprocess
import time

logger = logging.getLogger(__name__)

CHART_PATH = "chart"
VALUES_E2E = "e2e/values-e2e.yaml"
RELEASE_NAME = "beads-e2e"
DOLT_IMAGE = "dolt-server"
MCP_IMAGE = "beads-mcp"
CONTAINERFILES = {
    DOLT_IMAGE: "images/dolt-server/Containerfile",
    MCP_IMAGE: "images/beads-mcp/Containerfile",
}


def _run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    logger.info("Running: %s", " ".join(cmd))
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


class DeployManager:
    """Manages the lifecycle of a beads-acp deployment on OpenShift."""

    def __init__(self, namespace: str = "beads-acp-e2e"):
        self.namespace = namespace
        self._registry_route: str | None = None
        self._route_host: str | None = None

    def get_registry_route(self) -> str:
        if self._registry_route:
            return self._registry_route
        result = _run([
            "oc", "get", "route", "default-route",
            "-n", "openshift-image-registry",
            "-o", "jsonpath={.spec.host}",
        ])
        self._registry_route = result.stdout.strip()
        if not self._registry_route:
            raise RuntimeError("Could not get image registry route")
        return self._registry_route

    def ensure_namespace(self) -> None:
        result = _run(["oc", "get", "namespace", self.namespace], check=False)
        if result.returncode != 0:
            _run(["oc", "create", "namespace", self.namespace])
            logger.info("Created namespace %s", self.namespace)
        else:
            logger.info("Namespace %s already exists", self.namespace)

    def login_registry(self) -> None:
        registry = self.get_registry_route()
        token = _run(["oc", "whoami", "-t"]).stdout.strip()
        user = _run(["oc", "whoami"]).stdout.strip()
        _run(["docker", "login", registry, "--username", user, "--password", token])
        logger.info("Logged into registry %s", registry)

    def build_images(self) -> None:
        registry = self.get_registry_route()
        for name, containerfile in CONTAINERFILES.items():
            tag = f"{registry}/{self.namespace}/{name}:latest"
            _run(["docker", "build", "-t", tag, "-f", containerfile, "."])
            logger.info("Built %s", tag)

    def push_images(self) -> None:
        registry = self.get_registry_route()
        for name in CONTAINERFILES:
            tag = f"{registry}/{self.namespace}/{name}:latest"
            _run(["docker", "push", tag])
            logger.info("Pushed %s", tag)

    def helm_install(self) -> None:
        _run([
            "helm", "upgrade", "--install", RELEASE_NAME, CHART_PATH,
            "-n", self.namespace,
            "-f", VALUES_E2E,
            "--wait", "--timeout", "5m",
        ])
        logger.info("Helm release %s installed", RELEASE_NAME)

    def get_route_url(self) -> str:
        if self._route_host:
            return f"https://{self._route_host}"
        result = _run([
            "oc", "get", "route",
            f"{RELEASE_NAME}-beads-acp-mcp",
            "-n", self.namespace,
            "-o", "jsonpath={.spec.host}",
        ])
        self._route_host = result.stdout.strip()
        if not self._route_host:
            raise RuntimeError("Could not get MCP route host")
        return f"https://{self._route_host}"

    def wait_ready(self, timeout: int = 120) -> str:
        """Wait for the MCP endpoint to be ready. Returns the endpoint URL."""
        import httpx

        url = self.get_route_url()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                resp = httpx.get(f"{url}/status", verify=False, timeout=5)
                if resp.status_code == 200:
                    logger.info("MCP endpoint ready at %s", url)
                    return url
            except (httpx.ConnectError, httpx.ReadTimeout):
                pass
            time.sleep(5)
        raise TimeoutError(f"MCP endpoint not ready after {timeout}s")

    def deploy(self) -> str:
        """Full deployment pipeline. Returns the MCP endpoint URL."""
        self.ensure_namespace()
        self.login_registry()
        self.build_images()
        self.push_images()
        self.helm_install()
        return self.wait_ready()

    def teardown(self) -> None:
        _run(["helm", "uninstall", RELEASE_NAME, "-n", self.namespace], check=False)
        _run(["oc", "delete", "namespace", self.namespace, "--wait=false"], check=False)
        logger.info("Teardown complete")
