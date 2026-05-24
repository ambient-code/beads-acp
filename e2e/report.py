"""Interactive HTML report generation for beads-acp simulation metrics."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from metrics import MetricCollector

CRUD_COLORS = {"C": "#2ecc71", "R": "#3498db", "U": "#f39c12", "D": "#e74c3c"}


def generate_report(collector: MetricCollector, output_dir: str = "e2e/reports") -> str:
    """Generate an interactive HTML report. Returns the output file path."""
    df = collector.to_dataframe()
    if df.empty:
        raise ValueError("No metrics collected — cannot generate report")

    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    filename = out_path / f"report-{datetime.now().strftime('%Y%m%d-%H%M%S')}.html"

    fig = make_subplots(
        rows=3, cols=2,
        subplot_titles=(
            "CRUD Distribution",
            "Per-User Breakdown",
            "Operations Timeline",
            "Latency by Tool",
            "Cumulative Operations",
            "Summary",
        ),
        specs=[
            [{"type": "bar"}, {"type": "bar"}],
            [{"type": "scatter"}, {"type": "box"}],
            [{"type": "scatter"}, {"type": "table"}],
        ],
        vertical_spacing=0.12,
        horizontal_spacing=0.1,
    )

    # 1. CRUD Distribution (bar)
    crud_counts = df.groupby("crud").size().reset_index(name="count")
    for _, row in crud_counts.iterrows():
        fig.add_trace(
            go.Bar(
                x=[row["crud"]], y=[row["count"]],
                name=row["crud"],
                marker_color=CRUD_COLORS.get(row["crud"], "#999"),
                showlegend=False,
            ),
            row=1, col=1,
        )

    # 2. Per-User Breakdown (grouped bar)
    user_crud = df.groupby(["user_id", "crud"]).size().reset_index(name="count")
    for crud_cat in ["C", "R", "U"]:
        subset = user_crud[user_crud["crud"] == crud_cat]
        if not subset.empty:
            fig.add_trace(
                go.Bar(
                    x=subset["user_id"], y=subset["count"],
                    name=crud_cat,
                    marker_color=CRUD_COLORS.get(crud_cat, "#999"),
                ),
                row=1, col=2,
            )

    # 3. Operations Timeline (scatter: x=time, y=latency, color=crud)
    for crud_cat in df["crud"].unique():
        subset = df[df["crud"] == crud_cat]
        fig.add_trace(
            go.Scatter(
                x=subset["timestamp"], y=subset["latency_ms"],
                mode="markers",
                name=f"{crud_cat} ops",
                marker=dict(
                    color=CRUD_COLORS.get(crud_cat, "#999"),
                    size=8,
                    symbol="circle" if subset["success"].all() else "x",
                ),
                text=subset.apply(
                    lambda r: f"{r['user_id']}: {r['tool_name']} ({r['latency_ms']:.0f}ms)",
                    axis=1,
                ),
                hoverinfo="text",
                showlegend=False,
            ),
            row=2, col=1,
        )

    # 4. Latency by Tool (box plot)
    for tool in sorted(df["tool_name"].unique()):
        subset = df[df["tool_name"] == tool]
        fig.add_trace(
            go.Box(y=subset["latency_ms"], name=tool, showlegend=False),
            row=2, col=2,
        )

    # 5. Cumulative Operations (line per CRUD)
    df_sorted = df.sort_values("timestamp")
    for crud_cat in ["C", "R", "U"]:
        subset = df_sorted[df_sorted["crud"] == crud_cat].copy()
        if not subset.empty:
            subset["cumcount"] = range(1, len(subset) + 1)
            fig.add_trace(
                go.Scatter(
                    x=subset["timestamp"], y=subset["cumcount"],
                    mode="lines+markers",
                    name=f"Cumulative {crud_cat}",
                    line=dict(color=CRUD_COLORS.get(crud_cat, "#999")),
                    showlegend=False,
                ),
                row=3, col=1,
            )

    # 6. Summary table
    tool_stats = df.groupby("tool_name").agg(
        count=("tool_name", "size"),
        mean_latency=("latency_ms", "mean"),
        p95_latency=("latency_ms", lambda x: x.quantile(0.95)),
        success_rate=("success", "mean"),
    ).reset_index()
    fig.add_trace(
        go.Table(
            header=dict(values=["Tool", "Count", "Mean (ms)", "p95 (ms)", "Success %"]),
            cells=dict(values=[
                tool_stats["tool_name"],
                tool_stats["count"],
                tool_stats["mean_latency"].round(1),
                tool_stats["p95_latency"].round(1),
                (tool_stats["success_rate"] * 100).round(1),
            ]),
        ),
        row=3, col=2,
    )

    # Layout
    fig.update_layout(
        title="beads-acp E2E Simulation Report",
        height=1200,
        template="plotly_dark",
        barmode="group",
    )
    fig.update_yaxes(title_text="Count", row=1, col=1)
    fig.update_yaxes(title_text="Count", row=1, col=2)
    fig.update_yaxes(title_text="Latency (ms)", row=2, col=1)
    fig.update_yaxes(title_text="Latency (ms)", row=2, col=2)
    fig.update_yaxes(title_text="Cumulative Count", row=3, col=1)

    fig.write_html(str(filename), include_plotlyjs=True)
    return str(filename)
