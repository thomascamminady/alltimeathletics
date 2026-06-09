"""Server-rendered inline-SVG charts for analytics pages."""

from __future__ import annotations

from typing import Any


def _scale(v: float, vmin: float, vmax: float, lo: float, hi: float) -> float:
    """Linear scale ``v`` from ``[vmin, vmax]`` into ``[lo, hi]``."""
    if vmax == vmin:
        return (lo + hi) / 2
    return lo + (hi - lo) * (v - vmin) / (vmax - vmin)


def _decade_ticks(year_min: int, year_max: int) -> list[int]:
    """Decade-aligned tick positions inside ``[year_min, year_max]``."""
    lo = ((year_min + 9) // 10) * 10
    hi = (year_max // 10) * 10
    return list(range(lo, hi + 1, 10))


def _render_year_bars_svg(points: list[dict[str, Any]]) -> str:
    """Entries-per-year vertical bars."""
    if len(points) < 2:
        return ""
    W, H = 560, 160
    M_L, M_R, M_T, M_B = 40, 12, 12, 28
    plot_left, plot_right = M_L, W - M_R
    plot_top, plot_bot = M_T, H - M_B

    years = [p["year"] for p in points]
    counts = [p["count"] for p in points]
    x_min, x_max = min(years), max(years)
    c_max = max(counts)
    if x_min == x_max or c_max == 0:
        return ""

    def sx(yr: float) -> float:
        return _scale(yr, x_min, x_max, plot_left, plot_right)

    def sy(c: float) -> float:
        return _scale(c, 0, c_max, plot_bot, plot_top)

    span = x_max - x_min + 1
    # Bars sit centered on each year; width is slightly less than the slot.
    slot_w = (plot_right - plot_left) / max(span, 1)
    bar_w = max(slot_w * 0.85, 1.0)

    bars = "".join(
        '<rect class="bar" x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" '
        'height="{h:.2f}"><title>{tip}</title></rect>'.format(
            x=sx(p["year"]) - bar_w / 2,
            y=sy(p["count"]),
            w=bar_w,
            h=plot_bot - sy(p["count"]),
            tip=f"{p['year']}: {p['count']} performance{'s' if p['count'] != 1 else ''}",
        )
        for p in points
    )

    # Decade x-axis labels
    decade_years = _decade_ticks(int(x_min), int(x_max))
    label_x = "".join(
        f'<text x="{sx(yr):.1f}" y="{H - 8}" text-anchor="middle" class="ax-grid">{yr}</text>'
        for yr in decade_years
    )
    x_first = (
        f'<text x="{sx(x_min):.1f}" y="{H - 8}" text-anchor="middle" class="ax">{x_min}</text>'
    )
    x_last = f'<text x="{sx(x_max):.1f}" y="{H - 8}" text-anchor="middle" class="ax">{x_max}</text>'
    # Y-axis: just min (0) and max
    label_y = (
        f'<text x="{plot_left - 4}" y="{sy(0):.1f}" dy="4" '
        f'text-anchor="end" class="ax">0</text>'
        f'<text x="{plot_left - 4}" y="{sy(c_max):.1f}" dy="4" '
        f'text-anchor="end" class="ax">{c_max}</text>'
    )
    box = (
        f'<line class="grid grid-axis" x1="{plot_left}" y1="{plot_bot}" '
        f'x2="{plot_right}" y2="{plot_bot}" />'
    )
    return (
        f'<svg viewBox="0 0 {W} {H}" class="wr-chart" preserveAspectRatio="xMidYMid meet" '
        f'role="img" aria-label="Entries per year, {x_min}-{x_max}">'
        f"{box}{bars}{label_x}{label_y}{x_first}{x_last}"
        "</svg>"
    )
