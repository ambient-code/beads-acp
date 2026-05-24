"""Live Rich terminal dashboard for beads-acp simulation metrics."""

from __future__ import annotations

import asyncio
from datetime import datetime

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from metrics import MetricCollector

CRUD_COLORS = {"C": "green", "R": "cyan", "U": "yellow", "D": "red"}


def _build_header(collector: MetricCollector) -> Panel:
    elapsed = collector.elapsed_seconds
    mins, secs = divmod(int(elapsed), 60)
    text = Text()
    text.append("beads-acp E2E Simulation", style="bold white")
    text.append(f"  |  Elapsed: {mins:02d}:{secs:02d}", style="dim")
    text.append(f"  |  Operations: {collector.event_count}", style="dim")
    return Panel(text, style="blue")


def _build_crud_table(collector: MetricCollector) -> Panel:
    counts = collector.count_by_crud()
    table = Table(title="CRUD Totals", expand=True)
    table.add_column("Category", style="bold")
    table.add_column("Count", justify="right")
    for cat in ["C", "R", "U", "D"]:
        table.add_row(
            Text(cat, style=CRUD_COLORS[cat]),
            str(counts.get(cat, 0)),
        )
    table.add_row(Text("Total", style="bold"), str(sum(counts.values())))
    return Panel(table)


def _build_user_table(collector: MetricCollector) -> Panel:
    by_user = collector.count_by_user()
    table = Table(title="Per-User Breakdown", expand=True)
    table.add_column("User", style="bold")
    for cat in ["C", "R", "U", "D"]:
        table.add_column(cat, justify="right", style=CRUD_COLORS[cat])
    for user_id in sorted(by_user.keys()):
        counts = by_user[user_id]
        table.add_row(user_id, *[str(counts.get(c, 0)) for c in ["C", "R", "U", "D"]])
    return Panel(table)


def _build_recent_table(collector: MetricCollector) -> Panel:
    recent = collector.recent(10)
    table = Table(title="Recent Operations", expand=True)
    table.add_column("Time", style="dim", width=8)
    table.add_column("User", width=8)
    table.add_column("Tool", width=10)
    table.add_column("CRUD", width=4)
    table.add_column("Latency", justify="right", width=8)
    table.add_column("Status", width=6)
    for event in reversed(recent):
        status = Text("OK", style="green") if event.success else Text("FAIL", style="red bold")
        crud = Text(event.crud_category, style=CRUD_COLORS.get(event.crud_category, "white"))
        table.add_row(
            event.timestamp.strftime("%H:%M:%S"),
            event.user_id,
            event.tool_name,
            crud,
            f"{event.latency_ms:.0f}ms",
            status,
        )
    return Panel(table)


def _build_stats_panel(collector: MetricCollector) -> Panel:
    pcts = collector.latency_percentiles()
    rate = collector.success_rate()
    errors = collector.error_count()
    text = Text()
    text.append(f"p50: {pcts['p50']:.0f}ms  ", style="cyan")
    text.append(f"p95: {pcts['p95']:.0f}ms  ", style="yellow")
    text.append(f"p99: {pcts['p99']:.0f}ms\n", style="red")
    text.append(f"Success: {rate:.0%}  ", style="green" if rate == 1.0 else "yellow")
    text.append(f"Errors: {errors}", style="red" if errors > 0 else "green")
    return Panel(text, title="Latency & Health")


def build_layout(collector: MetricCollector) -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="body"),
        Layout(name="footer", size=6),
    )
    layout["body"].split_row(
        Layout(name="left"),
        Layout(name="right"),
    )
    layout["left"].split_column(
        Layout(name="crud"),
        Layout(name="users"),
    )
    layout["header"].update(_build_header(collector))
    layout["crud"].update(_build_crud_table(collector))
    layout["users"].update(_build_user_table(collector))
    layout["right"].update(_build_recent_table(collector))
    layout["footer"].update(_build_stats_panel(collector))
    return layout


async def run_dashboard(collector: MetricCollector, stop_event: asyncio.Event) -> None:
    """Run the live dashboard until stop_event is set."""
    console = Console()
    with Live(build_layout(collector), console=console, refresh_per_second=2, screen=True) as live:
        while not stop_event.is_set():
            live.update(build_layout(collector))
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=0.5)
            except asyncio.TimeoutError:
                pass
