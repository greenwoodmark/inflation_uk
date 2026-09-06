"""Write standalone JUP/GEAR regression scatterplot artifacts."""
from __future__ import annotations

import argparse
from pathlib import Path

import plotly.graph_objects as go

import generate_jup_gear_chart_data as chart_data

DEFAULT_DAYS = 180
DEFAULT_OUTPUT = Path("/home/mark/trading_env/artifacts/jup_gear_180d_scatter.html")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS, help="Trailing calendar-day horizon.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Standalone HTML output path.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.days <= 0:
        raise SystemExit("--days must be positive")
    chart_data.ROLLING_DAYS = args.days
    nav_rows = chart_data.load_nav_rows()
    price_rows = chart_data.load_jup_price_rows()
    points = chart_data.build_scatter_series(nav_rows, price_rows)
    regression = chart_data.calculate_regression(points)

    x_values = [row["gear_return_pct"] for row in points]
    y_values = [row["jup_price_return_pct"] for row in points]
    x_min, x_max = min(x_values), max(x_values)
    regression_x = [x_min, x_max]
    regression_y = [regression["intercept_pct"] + regression["beta"] * value for value in regression_x]
    statistics = (
        f"beta = {regression['beta']:.2f} | correlation = {regression['correlation']:.2f} | "
        f"R² = {regression['r_squared']:.2f} | n = {regression['n']}"
    )

    figure = go.Figure(data=[
        go.Scatter(
            x=x_values, y=y_values, mode="markers",
            name=f"Rolling {args.days}-day observations",
            text=[
                f"{row['date']}<br>GEAR NAV: {row['gear_return_pct']:.2f}%<br>"
                f"JUP price: {row['jup_price_return_pct']:.2f}%"
                for row in points
            ],
            hovertemplate="%{text}<extra></extra>",
            marker={"color": "#7c3aed", "size": 7, "opacity": 0.72,
                    "line": {"color": "#5b21b6", "width": 0.5}},
        ),
        go.Scatter(
            x=regression_x, y=regression_y, mode="lines",
            name="OLS regression line",
            hovertemplate="Fitted JUP return: %{y:.2f}%<br>GEAR return: %{x:.2f}%<extra></extra>",
            line={"color": "#111827", "width": 2.5},
        ),
    ])
    figure.update_layout(
        title={"text": (
            "JUP share-price return versus GEAR NAV growth"
            f"<br><sup>Trailing {args.days}-calendar-day returns | {statistics}</sup>"
        ), "x": 0, "xanchor": "left"},
        height=620, width=1100, paper_bgcolor="#f8fafc", plot_bgcolor="#f8fafc",
        hovermode="closest",
        xaxis={"title": f"GEAR trailing {args.days}-day NAV growth (%)", "ticksuffix": "%",
               "zeroline": True, "zerolinecolor": "#64748b", "gridcolor": "#e2e8f0"},
        yaxis={"title": f"JUP trailing {args.days}-day share-price return (%)", "ticksuffix": "%",
               "zeroline": True, "zerolinecolor": "#64748b", "gridcolor": "#e2e8f0"},
        legend={"orientation": "h", "y": -0.14, "x": 0},
        margin={"l": 78, "r": 34, "t": 95, "b": 85},
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    figure.write_html(str(args.output), include_plotlyjs="inline", full_html=True,
                      config={"responsive": True, "displaylogo": False})
    print(f"wrote {args.output} ({len(points)} points, {points[0]['date']} to {points[-1]['date']}; {statistics})")


if __name__ == "__main__":
    main()
