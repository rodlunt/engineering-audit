"""Chart generation for the GrindPoints monthly report."""

from __future__ import annotations

import matplotlib.pyplot as plt


def plot_monthly_visits_bar(months: list[str], visit_counts: list[int], out_path: str) -> None:
    """Bar chart of visits per month, zoomed to where this month's numbers
    sit so the change between months is easier to see on the slide."""
    fig, ax = plt.subplots()
    ax.bar(months, visit_counts)
    ax.set_ylim(850, 1000)
    ax.set_title("Visits per month")
    fig.savefig(out_path)


def plot_daily_active_customers_line(days: list[str], active_counts: list[int], out_path: str) -> None:
    """Line chart of daily active customers, single axis, the series
    labelled directly on the line."""
    fig, ax = plt.subplots()
    ax.plot(days, active_counts)
    ax.set_ylim(bottom=0)
    ax.annotate("active customers", xy=(days[-1], active_counts[-1]))
    ax.set_title("Daily active customers, August")
    fig.savefig(out_path)
