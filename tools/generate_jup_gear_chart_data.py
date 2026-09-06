"""Generate the internal JUP GEAR NAV/drawdown/AUM chart data."""
from __future__ import annotations

import json
from bisect import bisect_right
from datetime import date, timedelta
from io import BytesIO
from pathlib import Path

import pyarrow.parquet as pq
from google.cloud import storage

BUCKET = "systematicpositiveskew"
NAV_PREFIX = "fund_data/jupiter/nav/isin=IE00BLP5S809/"
JUP_SHARE_PRICE_OBJECT = "fund_data/jupiter/JUP_LN_share_price_daily/data.parquet"
OUTPUT = Path(__file__).resolve().parents[1] / "data" / "jup_gear_chart.json"
START_DATE = date(2024, 1, 1)
ROLLING_DAYS = 365

# These are the existing documented AUM anchors, in GBP millions. AUM is
# interpolated on read for this visualization and is not a persisted source.
AUM_ANCHORS = [
    (date(2023, 12, 31), 1500.0),
    (date(2024, 12, 31), 2200.0),
    (date(2025, 12, 31), 6200.0),
    (date(2026, 9, 4), 9450.0),
]


def interpolate_aum(day: date) -> float:
    if day <= AUM_ANCHORS[0][0]:
        return AUM_ANCHORS[0][1]
    for (left_day, left_value), (right_day, right_value) in zip(AUM_ANCHORS, AUM_ANCHORS[1:]):
        if left_day <= day <= right_day:
            fraction = (day - left_day).days / (right_day - left_day).days
            return left_value + fraction * (right_value - left_value)
    return AUM_ANCHORS[-1][1]


def load_nav_rows() -> list[dict]:
    client = storage.Client(project=BUCKET)
    rows = []
    for blob in client.list_blobs(BUCKET, prefix=NAV_PREFIX):
        if blob.name.endswith(".parquet"):
            rows.extend(pq.read_table(BytesIO(blob.download_as_bytes())).to_pylist())
    rows = [row for row in rows if row["valuation_date"] >= START_DATE]
    rows.sort(key=lambda row: row["valuation_date"])
    if not rows:
        raise RuntimeError("No GEAR NAV rows available for chart")
    if len({row["valuation_date"] for row in rows}) != len(rows):
        raise RuntimeError("Duplicate GEAR NAV valuation dates")
    return rows


def load_jup_price_rows() -> list[dict]:
    client = storage.Client(project=BUCKET)
    blob = client.bucket(BUCKET).blob(JUP_SHARE_PRICE_OBJECT)
    if not blob.exists():
        raise RuntimeError(f"JUP share-price object is missing: gs://{BUCKET}/{JUP_SHARE_PRICE_OBJECT}")
    rows = pq.read_table(BytesIO(blob.download_as_bytes())).to_pylist()
    rows = [row for row in rows if row.get("date") is not None and row.get("close") is not None]
    rows.sort(key=lambda row: row["date"])
    if not rows:
        raise RuntimeError("No JUP share-price rows available for scatterplot")
    if len({row["date"] for row in rows}) != len(rows):
        raise RuntimeError("Duplicate JUP share-price dates")
    if any(float(row["close"]) <= 0.0 for row in rows):
        raise RuntimeError("JUP share-price close values must be positive")
    return rows


def _asof_value(rows: list[dict], dates: list[date], target: date, field: str) -> tuple[date, float] | None:
    index = bisect_right(dates, target) - 1
    if index < 0:
        return None
    return dates[index], float(rows[index][field])


def build_scatter_series(nav_rows: list[dict], price_rows: list[dict]) -> list[dict]:
    """Pair trailing 365-calendar-day GEAR NAV and JUP price returns.

    Each point is dated by a GEAR valuation date.  The current JUP close and
    the prior JUP close are the latest London closes on or before the GEAR
    date and the date 365 calendar days earlier, respectively.  GEAR uses the
    analogous latest NAV observations on or before those dates.
    """
    nav_dates = [row["valuation_date"] for row in nav_rows]
    price_dates = [row["date"] for row in price_rows]
    points = []
    for row in nav_rows:
        day = row["valuation_date"]
        prior_day = day - timedelta(days=ROLLING_DAYS)
        current_nav = _asof_value(nav_rows, nav_dates, day, "nav")
        prior_nav = _asof_value(nav_rows, nav_dates, prior_day, "nav")
        current_price = _asof_value(price_rows, price_dates, day, "close")
        prior_price = _asof_value(price_rows, price_dates, prior_day, "close")
        if None in (current_nav, prior_nav, current_price, prior_price):
            continue
        nav_asof, nav_value = current_nav
        nav_prior_asof, nav_prior_value = prior_nav
        price_asof, price_value = current_price
        price_prior_asof, price_prior_value = prior_price
        points.append({
            "date": day.isoformat(),
            "gear_nav_asof_date": nav_asof.isoformat(),
            "gear_nav_prior_date": nav_prior_asof.isoformat(),
            "jup_close_asof_date": price_asof.isoformat(),
            "jup_close_prior_date": price_prior_asof.isoformat(),
            "gear_return_pct": round((nav_value / nav_prior_value - 1.0) * 100.0, 6),
            "jup_price_return_pct": round((price_value / price_prior_value - 1.0) * 100.0, 6),
            "gear_nav": round(nav_value, 8),
            "jup_close_gbp": round(price_value, 6),
        })
    if not points:
        raise RuntimeError("No aligned rolling one-year GEAR/JUP observations available")
    return points


def calculate_regression(points: list[dict]) -> dict[str, float | int]:
    """Return OLS JUP-return-on-GEAR-return statistics in percentage units."""
    x_values = [float(point["gear_return_pct"]) for point in points]
    y_values = [float(point["jup_price_return_pct"]) for point in points]
    count = len(points)
    if count < 2:
        raise RuntimeError("At least two scatter points are required for regression")
    x_mean = sum(x_values) / count
    y_mean = sum(y_values) / count
    sum_xx = sum((x - x_mean) ** 2 for x in x_values)
    sum_yy = sum((y - y_mean) ** 2 for y in y_values)
    sum_xy = sum((x - x_mean) * (y - y_mean) for x, y in zip(x_values, y_values))
    if sum_xx == 0.0 or sum_yy == 0.0:
        raise RuntimeError("Regression requires variation in both return series")
    beta = sum_xy / sum_xx
    intercept = y_mean - beta * x_mean
    correlation = sum_xy / (sum_xx * sum_yy) ** 0.5
    return {
        "n": count,
        "beta": round(beta, 8),
        "intercept_pct": round(intercept, 8),
        "correlation": round(correlation, 8),
        "r_squared": round(correlation ** 2, 8),
    }


def main() -> None:
    nav_rows = load_nav_rows()
    price_rows = load_jup_price_rows()
    base_nav = float(nav_rows[0]["nav"])
    peak_nav = base_nav
    series = []
    for row in nav_rows:
        day = row["valuation_date"]
        nav = float(row["nav"])
        peak_nav = max(peak_nav, nav)
        series.append({
            "date": day.isoformat(),
            "nav": round(nav, 8),
            "cumulative_return_pct": round((nav / base_nav - 1.0) * 100.0, 6),
            "drawdown_pct": round((nav / peak_nav - 1.0) * 100.0, 6),
            "aum_gbp_m": round(interpolate_aum(day), 6),
        })
    scatter_series = build_scatter_series(nav_rows, price_rows)
    regression = calculate_regression(scatter_series)
    OUTPUT.write_text(json.dumps({
        "schema_version": "jup_gear_chart_v3",
        "fund": "GEAR",
        "isin": "IE00BLP5S809",
        "currency": "GBP",
        "start_date": series[0]["date"],
        "end_date": series[-1]["date"],
        "base_nav": base_nav,
        "nav_source": "Canonical GEAR GBP NAV series: FE fundinfo-derived pre-overlap history and official Jupiter NAV from 2024-11-28 onward.",
        "aum_source": "Linear interpolation of documented annual/current AUM anchors; visualization only.",
        "jup_share_price_source": f"IBKR historical daily TRADES bars from gs://{BUCKET}/{JUP_SHARE_PRICE_OBJECT}; unadjusted close, not dividend-adjusted total return.",
        "rolling_return_definition": "Trailing 365 calendar-day return using the latest available observation on or before each target date; each point is dated by the GEAR valuation date.",
        "regression": {
            **regression,
            "dependent_variable": "JUP trailing one-year unadjusted share-price return (%)",
            "independent_variable": "GEAR trailing one-year NAV growth (%)",
            "method": "Ordinary least squares: JUP return = intercept + beta × GEAR return",
        },
        "aum_anchors_gbp_m": [{"date": day.isoformat(), "aum_gbp_m": value} for day, value in AUM_ANCHORS],
        "series": series,
        "scatter_series": scatter_series,
    }, indent=2) + "\n", encoding="utf-8")
    print(
        f"wrote {OUTPUT} ({len(series)} GEAR points, "
        f"{len(scatter_series)} scatter points, {series[0]['date']} to {series[-1]['date']})"
    )


if __name__ == "__main__":
    main()
