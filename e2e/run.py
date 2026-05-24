"""CLI entrypoint for beads-acp E2E simulation."""

from __future__ import annotations

import asyncio
import logging
import sys

import click

from dashboard import run_dashboard
from deploy import DeployManager
from mcp_client import MCPClient
from metrics import MetricCollector
from report import generate_report
from scenarios import Scenario

logger = logging.getLogger("e2e")


async def _run_simulation(
    endpoint: str,
    dashboard: bool,
    report_dir: str,
) -> None:
    collector = MetricCollector()

    client_a = MCPClient(endpoint=endpoint, user_id="user-a")
    client_b = MCPClient(endpoint=endpoint, user_id="user-b")

    try:
        await client_a.connect()
        await client_b.connect()

        scenario = Scenario(client_a, client_b, collector)

        if dashboard:
            stop = asyncio.Event()
            dashboard_task = asyncio.create_task(run_dashboard(collector, stop))
            try:
                await scenario.run()
            finally:
                stop.set()
                await dashboard_task
        else:
            await scenario.run()

    finally:
        await client_a.close()
        await client_b.close()

    # Generate HTML report
    path = generate_report(collector, output_dir=report_dir)
    click.echo(f"\nReport written to {path}")

    # Print summary
    counts = collector.count_by_crud()
    pcts = collector.latency_percentiles()
    click.echo(
        f"\nResults: C={counts['C']} R={counts['R']} U={counts['U']} D={counts['D']}  "
        f"| p50={pcts['p50']:.0f}ms p95={pcts['p95']:.0f}ms  "
        f"| success={collector.success_rate():.0%}  "
        f"| total={len(collector.events)} ops in {collector.elapsed_seconds:.1f}s"
    )


@click.command()
@click.option("--endpoint", default=None, help="MCP endpoint URL (auto-detected from route if omitted)")
@click.option("--namespace", default="beads-acp-e2e", help="Kubernetes namespace")
@click.option("--deploy/--no-deploy", default=True, help="Deploy chart before simulation")
@click.option("--teardown/--no-teardown", default=False, help="Cleanup after simulation")
@click.option("--dashboard/--no-dashboard", default=True, help="Show live terminal dashboard")
@click.option("--report-dir", default="e2e/reports", help="HTML report output directory")
@click.option("-v", "--verbose", is_flag=True, help="Verbose logging")
def main(
    endpoint: str | None,
    namespace: str,
    deploy: bool,
    teardown: bool,
    dashboard: bool,
    report_dir: str,
    verbose: bool,
) -> None:
    """Run beads-acp E2E simulation."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )

    dm = DeployManager(namespace=namespace)

    if deploy:
        click.echo("Deploying beads-acp...")
        endpoint = dm.deploy()
        click.echo(f"MCP endpoint ready at {endpoint}")
    elif endpoint is None:
        endpoint = dm.get_route_url()
        dm.wait_ready()

    try:
        asyncio.run(_run_simulation(endpoint, dashboard, report_dir))
    finally:
        if teardown:
            click.echo("Tearing down...")
            dm.teardown()


if __name__ == "__main__":
    main()
