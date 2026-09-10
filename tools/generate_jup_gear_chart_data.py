"""Generate the internal JUP GEAR NAV/drawdown/AUM chart data."""
from __future__ import annotations

import json
from bisect import bisect_left, bisect_right
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from io import BytesIO
from pathlib import Path

import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq
from google.cloud import storage

BUCKET = "systematicpositiveskew"
NAV_PREFIX = "fund_data/jupiter/nav/isin=IE00BLP5S809/"
JUP_SHARE_PRICE_OBJECT = "fund_data/jupiter/JUP_LN_share_price_daily/data.parquet"
JUP_DIVIDEND_PREFIX = "fund_data/jupiter/equity_reference/dividends"
OUTPUT = Path(__file__).resolve().parents[1] / "data" / "jup_gear_chart.json"
START_DATE = date(2024, 1, 1)
ROLLING_DAYS = 90

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


def load_jup_dividend_rows() -> list[dict]:
    """Load the canonical ex-date dividend reference through PyArrow dataset."""
    uri = f"gs://{BUCKET}/{JUP_DIVIDEND_PREFIX}"
    try:
        dataset = ds.dataset(uri, format="parquet", partitioning="hive", ignore_prefixes=[".", "_"], exclude_invalid_files=True)
        rows = dataset.to_table().to_pylist()
    except (FileNotFoundError, OSError, ValueError, pa.ArrowInvalid) as exc:
        raise RuntimeError(
            "Canonical JUP dividend reference is unavailable; run "
            "tools/acquire_jup_dividends.py with the validated official export first"
        ) from exc
    if not rows:
        raise RuntimeError("Canonical JUP dividend reference contains no rows")
    for row in rows:
        if not row.get("ex_date") or row.get("cash_amount_gbp_per_share") is None:
            raise RuntimeError("Canonical JUP dividend reference contains an incomplete row")
    return sorted(rows, key=lambda row: (row["ex_date"], row.get("dividend_type", ""), row.get("declaration_date", "")))


def build_jup_dividend_monitor(dividend_rows: list[dict], today: date | None = None) -> dict:
    """Summarize recent/future events and flag a cadence-based review gap."""
    today = today or datetime.now(timezone.utc).date()
    events = []
    for row in sorted(dividend_rows, key=lambda item: (item["ex_date"], item.get("dividend_type", ""))):
        ex_date = row["ex_date"] if isinstance(row["ex_date"], date) else date.fromisoformat(str(row["ex_date"]))
        amount = row["cash_amount_gbp_per_share"]
        events.append({
            "ex_date": ex_date.isoformat(),
            "payment_date": str(row.get("payment_date") or ""),
            "amount_gbp_per_share": round(float(amount), 8),
            "dividend_type": row.get("dividend_type", ""),
            "status": "future" if ex_date > today else "past",
        })
    if not events:
        raise RuntimeError("No JUP dividend events available for monitor")
    ordinary_dates = [date.fromisoformat(event["ex_date"]) for event in events if event["dividend_type"].startswith("ordinary_")]
    expected_next = None
    if len(ordinary_dates) >= 2:
        intervals = [(right - left).days for left, right in zip(ordinary_dates, ordinary_dates[1:])]
        expected_next = ordinary_dates[-1] + timedelta(days=round(sorted(intervals)[len(intervals) // 2]))
    future_events = [event for event in events if event["status"] == "future"]
    missing = bool(expected_next and today > expected_next and not future_events)
    if future_events:
        status = "future_event_stored"
        message = "Issuer-confirmed future dividend event(s) are present in storage."
    elif missing:
        status = "missing_forthcoming_event"
        message = f"No future event is stored; historical cadence suggests a review was due around {expected_next.isoformat()}."
    elif expected_next:
        status = "review_due_later"
        message = f"No future event is stored; review the source around {expected_next.isoformat()} based on historical cadence."
    else:
        status = "insufficient_history"
        message = "No future event is stored and there is insufficient history for a cadence estimate."
    return {
        "as_of_date": today.isoformat(),
        "last_four_events": events[-4:],
        "expected_next_ordinary_ex_date": expected_next.isoformat() if expected_next else None,
        "missing_forthcoming_event": missing,
        "status": status,
        "message": message,
        "note": "Cadence is a review heuristic, not an issuer-confirmed future date; future dates may only become available with a dividend announcement.",
    }


def _asof_value(rows: list[dict], dates: list[date], target: date, field: str) -> tuple[date, float] | None:
    index = bisect_right(dates, target) - 1
    if index < 0:
        return None
    return dates[index], float(rows[index][field])


def build_jup_total_return_rows(price_rows: list[dict], dividend_rows: list[dict]) -> list[dict]:
    """Build a gross, ex-date-attributed TRI in memory.

    Cash distributions are assumed reinvested at the ex-date close.  If an
    event date is not a trading date, it is assigned to the first available
    close on or after that date.  This keeps the as-of window rule explicit:
    events after the prior close and through the current close are included.
    """
    if not price_rows:
        raise ValueError("price_rows cannot be empty")
    rows = sorted(price_rows, key=lambda row: row["date"])
    dates = [row["date"] for row in rows]
    if len(set(dates)) != len(dates):
        raise ValueError("Duplicate JUP price dates")
    distributions = [Decimal("0") for _ in rows]
    for event in dividend_rows:
        ex_date = event["ex_date"]
        if isinstance(ex_date, str):
            ex_date = date.fromisoformat(ex_date)
        index = bisect_left(dates, ex_date)
        if index >= len(rows):
            continue
        amount = Decimal(str(event["cash_amount_gbp_per_share"]))
        if amount < 0:
            raise ValueError("Dividend amounts must be non-negative")
        distributions[index] += amount
    result = []
    tri = 1.0
    for index, row in enumerate(rows):
        close = float(row["close"])
        if close <= 0:
            raise ValueError("JUP close values must be positive")
        if index > 0:
            tri *= (close + float(distributions[index])) / float(rows[index - 1]["close"])
        result.append({**row, "jup_total_return_index": tri, "dividend_gbp_per_share": float(distributions[index])})
    return result


def build_scatter_series(nav_rows: list[dict], price_rows: list[dict], dividend_rows: list[dict] | None = None) -> list[dict]:
    """Pair trailing 90-calendar-day GEAR NAV and gross JUP total returns."""
    total_return_rows = build_jup_total_return_rows(price_rows, dividend_rows or [])
    nav_dates = [row["valuation_date"] for row in nav_rows]
    price_dates = [row["date"] for row in total_return_rows]
    points = []
    for row in nav_rows:
        day = row["valuation_date"]
        prior_day = day - timedelta(days=ROLLING_DAYS)
        current_nav = _asof_value(nav_rows, nav_dates, day, "nav")
        prior_nav = _asof_value(nav_rows, nav_dates, prior_day, "nav")
        current_price = _asof_value(total_return_rows, price_dates, day, "close")
        prior_price = _asof_value(total_return_rows, price_dates, prior_day, "close")
        current_tri = _asof_value(total_return_rows, price_dates, day, "jup_total_return_index")
        prior_tri = _asof_value(total_return_rows, price_dates, prior_day, "jup_total_return_index")
        if None in (current_nav, prior_nav, current_price, prior_price, current_tri, prior_tri):
            continue
        nav_asof, nav_value = current_nav
        nav_prior_asof, nav_prior_value = prior_nav
        price_asof, price_value = current_price
        price_prior_asof, price_prior_value = prior_price
        _, tri_value = current_tri
        _, tri_prior_value = prior_tri
        points.append({
            "date": day.isoformat(),
            "gear_nav_asof_date": nav_asof.isoformat(),
            "gear_nav_prior_date": nav_prior_asof.isoformat(),
            "jup_close_asof_date": price_asof.isoformat(),
            "jup_close_prior_date": price_prior_asof.isoformat(),
            "gear_return_pct": round((nav_value / nav_prior_value - 1.0) * 100.0, 6),
            "jup_price_return_pct": round((price_value / price_prior_value - 1.0) * 100.0, 6),
            "jup_total_return_pct": round((tri_value / tri_prior_value - 1.0) * 100.0, 6),
            "gear_nav": round(nav_value, 8),
            "jup_close_gbp": round(price_value, 6),
        })
    if not points:
        raise RuntimeError("No aligned rolling 90-calendar-day GEAR/JUP observations available")
    return points


def calculate_regression(points: list[dict]) -> dict[str, float | int]:
    x_values = [float(point["gear_return_pct"]) for point in points]
    y_values = [float(point["jup_total_return_pct"]) for point in points]
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
    correlation = sum_xy / (sum_xx * sum_yy) ** 0.5
    beta = sum_xy / sum_xx
    return {"n": count, "beta": round(beta, 8), "intercept_pct": round(y_mean - beta * x_mean, 8), "correlation": round(correlation, 8), "r_squared": round(correlation ** 2, 8)}


def calculate_joint_frequency(points: list[dict], bins: int = 10) -> dict:
    if bins < 2:
        raise ValueError("At least two matrix buckets are required")
    if not points:
        raise ValueError("At least one scatter point is required")

    def bucket_edges(values: list[float]) -> list[float]:
        minimum, maximum = min(values), max(values)
        if minimum == maximum:
            raise RuntimeError("Matrix bucketing requires variation in both return series")
        width = (maximum - minimum) / bins
        return [minimum + index * width for index in range(bins)] + [maximum]

    def bucket_index(value: float, edges: list[float]) -> int:
        index = int((value - edges[0]) / (edges[-1] - edges[0]) * bins)
        return min(max(index, 0), bins - 1)

    def labels(edges: list[float]) -> list[str]:
        return [f"{edges[index]:.1f}% to {edges[index + 1]:.1f}%" for index in range(bins)]

    gear_values = [float(point["gear_return_pct"]) for point in points]
    jup_values = [float(point["jup_total_return_pct"]) for point in points]
    gear_edges, jup_edges = bucket_edges(gear_values), bucket_edges(jup_values)
    counts = [[0 for _ in range(bins)] for _ in range(bins)]
    for gear_value, jup_value in zip(gear_values, jup_values):
        counts[bucket_index(gear_value, gear_edges)][bucket_index(jup_value, jup_edges)] += 1
    total = len(points)
    return {
        "schema_version": "jup_gear_joint_frequency_v2",
        "n": total,
        "bins": bins,
        "row_axis": "GEAR trailing 90-calendar-day NAV growth (%)",
        "column_axis": "JUP trailing 90-calendar-day gross total return (%)",
        "gear_edges_pct": [round(edge, 6) for edge in gear_edges],
        "jup_edges_pct": [round(edge, 6) for edge in jup_edges],
        "gear_labels_pct": labels(gear_edges),
        "jup_labels_pct": labels(jup_edges),
        "counts": counts,
        "percentages": [[round(count / total * 100.0, 2) for count in row] for row in counts],
        "binning": "Equal-width buckets over observed 90-calendar-day return ranges; the final bucket includes the maximum edge.",
    }


def main() -> None:
    nav_rows = load_nav_rows()
    price_rows = load_jup_price_rows()
    dividend_rows = load_jup_dividend_rows()
    dividend_monitor = build_jup_dividend_monitor(dividend_rows)
    base_nav = float(nav_rows[0]["nav"])
    peak_nav = base_nav
    series = []
    for row in nav_rows:
        day, nav = row["valuation_date"], float(row["nav"])
        peak_nav = max(peak_nav, nav)
        series.append({"date": day.isoformat(), "nav": round(nav, 8), "cumulative_return_pct": round((nav / base_nav - 1.0) * 100.0, 6), "drawdown_pct": round((nav / peak_nav - 1.0) * 100.0, 6), "aum_gbp_m": round(interpolate_aum(day), 6)})
    scatter_series = build_scatter_series(nav_rows, price_rows, dividend_rows)
    regression = calculate_regression(scatter_series)
    joint_frequency = calculate_joint_frequency(scatter_series)
    OUTPUT.write_text(json.dumps({
        "schema_version": "jup_gear_chart_v4",
        "fund": "GEAR", "isin": "IE00BLP5S809", "currency": "GBP",
        "start_date": series[0]["date"], "end_date": series[-1]["date"], "base_nav": base_nav,
        "nav_source": "Canonical GEAR GBP NAV series: FE fundinfo-derived pre-overlap history and official Jupiter NAV from 2024-11-28 onward.",
        "aum_source": "Linear interpolation of documented annual/current AUM anchors; visualization only.",
        "jup_share_price_source": f"IBKR historical daily TRADES bars from gs://{BUCKET}/{JUP_SHARE_PRICE_OBJECT}; unadjusted close.",
        "jup_dividend_source": f"Canonical gross dividend reference from gs://{BUCKET}/{JUP_DIVIDEND_PREFIX}; ex-date attribution, cash GBP per share before tax.",
        "jup_dividend_monitor": dividend_monitor,
        "rolling_days": ROLLING_DAYS,
        "rolling_return_definition": "Trailing 90-calendar-day return using the latest available observation on or before each target date; JUP gross total return includes ex-date distributions between the selected prior and current closes.",
        "regression": {**regression, "dependent_variable": "JUP trailing 90-calendar-day gross total return (%)", "independent_variable": "GEAR trailing 90-calendar-day NAV growth (%)", "method": "Ordinary least squares: JUP gross total return = intercept + beta × GEAR return"},
        "joint_frequency": joint_frequency,
        "aum_anchors_gbp_m": [{"date": day.isoformat(), "aum_gbp_m": value} for day, value in AUM_ANCHORS],
        "series": series, "scatter_series": scatter_series,
    }, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUTPUT} ({len(series)} GEAR points, {len(scatter_series)} scatter points, {series[0]['date']} to {series[-1]['date']})")


if __name__ == "__main__":
    main()
