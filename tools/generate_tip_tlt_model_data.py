"""Generate the latest TIP/TLT GH5 smile payload for the internal website."""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.dataset as ds
from scipy.optimize import brentq
from scipy.stats import norm

TRADING_ROOT = Path("/home/mark/trading_env")
GS_MARQUEE_ROOT = TRADING_ROOT / "gs_marquee"
if str(GS_MARQUEE_ROOT) not in sys.path:
    sys.path.insert(0, str(GS_MARQUEE_ROOT))

from infrastructure.equity_vol_gh3 import fit_equity_vol_day  # noqa: E402
from infrastructure.equity_vol_model import (  # noqa: E402
    GH3_VERSION,
    GH5_VERSION,
    FitSettings,
    _realisation,
    _zsample,
    gh5_realisation,
)

GCS_BASE = "gs://systematicpositiveskew"
RAW_BASE = f"{GCS_BASE}/gs_marquee_data/equity_vol"
FIT_BASE = f"{GCS_BASE}/options_data"
ACTIVE_MODEL_VERSION = "gh5_v1"
HOLDINGS_BASE = f"{GCS_BASE}/etf_reference/holdings"
SYMBOLS = ("TIP", "TLT")
TENOR_YEARS = 1.0 / 12.0
DATE_RE = re.compile(r"year=(\d{4})/(\d{4}-\d{2}-\d{2})\.parquet$")


def _blob_names(prefix: str) -> list[str]:
    from google.cloud import storage

    client = storage.Client(project="systematicpositiveskew")
    return [blob.name for blob in client.list_blobs("systematicpositiveskew", prefix=prefix)]


def _raw_dates(symbol: str) -> set[str]:
    prefix = f"gs_marquee_data/equity_vol/symbol={symbol}/"
    dates = set()
    for name in _blob_names(prefix):
        match = DATE_RE.search(name)
        if match:
            dates.add(match.group(2))
    if not dates:
        raise RuntimeError(f"No canonical equity-volatility dates found for {symbol}")
    return dates


def _latest_raw_date(symbol: str) -> str:
    return max(_raw_dates(symbol))


def _latest_common_raw_date() -> str:
    common_dates = _raw_dates(SYMBOLS[0]).intersection(_raw_dates(SYMBOLS[1]))
    if not common_dates:
        raise RuntimeError("TIP and TLT have no common canonical equity-volatility date")
    return max(common_dates)


def _read_raw(symbol: str, date_str: str) -> pd.DataFrame:
    uri = f"{RAW_BASE}/symbol={symbol}/year={date_str[:4]}/{date_str}.parquet"
    return ds.dataset(uri, format="parquet").to_table().to_pandas()


def _read_latest_fit(
    symbol: str,
    latest_date: str,
    model_version: str = ACTIVE_MODEL_VERSION,
    allow_gh3_fallback: bool = False,
) -> tuple[pd.Series, str]:
    """Read the latest explicitly versioned fit on or before the raw date."""
    if model_version == GH5_VERSION:
        prefix = f"options_data/symbol={symbol}_UP/opt_expiry=1m/"
    elif model_version == GH3_VERSION:
        prefix = f"options_data/symbol={symbol}_UP/opt_expiry=1m/"
    else:
        raise ValueError(f"Unsupported model version: {model_version}")
    candidates = [
        f"{GCS_BASE}/{name}"
        for name in _blob_names(prefix)
        if name.endswith("/data.parquet")
    ]
    if not candidates:
        if model_version == GH5_VERSION and allow_gh3_fallback:
            return _read_latest_fit(symbol, latest_date, GH3_VERSION, False)
        raise RuntimeError(f"No {model_version} fit files found for {symbol}")
    frames = [ds.dataset(uri, format="parquet", partitioning="hive").to_table().to_pandas() for uri in candidates]
    fits = pd.concat(frames, ignore_index=True)
    if "model_version" not in fits.columns:
        fits["model_version"] = GH3_VERSION
    fits["date"] = fits["date"].astype(str)
    eligible = fits.loc[
        fits["model_version"].astype(str).eq(model_version) & (fits["date"] <= latest_date)
    ].sort_values("date")
    if eligible.empty:
        if model_version == GH5_VERSION and allow_gh3_fallback:
            return _read_latest_fit(symbol, latest_date, GH3_VERSION, False)
        raise RuntimeError(f"No {model_version} fit on or before {latest_date} for {symbol}")
    row = eligible.iloc[-1]
    return row, str(row["date"])


def _read_holdings(symbol: str) -> pd.DataFrame:
    dataset = ds.dataset(HOLDINGS_BASE, format="parquet", partitioning="hive", ignore_prefixes=[".", "_"])
    table = dataset.to_table(filter=ds.field("symbol") == symbol)
    frame = table.to_pandas()
    if frame.empty:
        raise RuntimeError(f"No holdings snapshot found for {symbol}")
    return frame


def _effective_duration(frame: pd.DataFrame) -> tuple[float, str, str, int]:
    frame = frame.copy()
    frame["market_value"] = pd.to_numeric(frame["market_value"], errors="coerce")
    frame["duration"] = pd.to_numeric(frame["duration"], errors="coerce")
    usable = frame.loc[frame["market_value"].gt(0) & frame["duration"].notna()].copy()
    if usable.empty:
        raise RuntimeError("Holdings snapshot has no usable market-value/duration rows")
    value = float(np.average(usable["duration"], weights=usable["market_value"]))
    return value, "iShares Duration field, market-value weighted", str(frame["as_of_date"].iloc[0]), len(frame)


def _black76_normalized_price(forward: float, strike: float, sigma: float, right: str) -> float:
    if forward <= 0 or strike <= 0 or sigma <= 0:
        return float("nan")
    root_t = TENOR_YEARS**0.5
    d1 = (np.log(forward / strike) + 0.5 * sigma**2 * TENOR_YEARS) / (sigma * root_t)
    d2 = d1 - sigma * root_t
    if right == "C":
        price = forward * norm.cdf(d1) - strike * norm.cdf(d2)
    else:
        price = strike * norm.cdf(-d2) - forward * norm.cdf(-d1)
    return float(price / forward)


def _implied_vol(forward: float, strike: float, normalized_price: float, right: str) -> float | None:
    if not np.isfinite(normalized_price) or normalized_price <= 0:
        return None
    intrinsic = max((forward - strike) if right == "C" else (strike - forward), 0.0) / forward
    if normalized_price <= intrinsic + 1e-10:
        return None
    try:
        return float(brentq(
            lambda sigma: _black76_normalized_price(forward, strike, sigma, right) - normalized_price,
            1e-6,
            10.0,
        ))
    except ValueError:
        return None


def _model_normalized_price(forward: float, strike: float, right: str, fit: pd.Series) -> float:
    z, probabilities = _zsample(float(fit.get("truncpoint", 4.5)), int(fit.get("zsteps", 400)))
    model_version = str(fit.get("model_version", GH3_VERSION))
    if model_version == GH5_VERSION:
        returns = gh5_realisation(
            z,
            float(fit["b"]),
            float(fit["g"]),
            float(fit["h"]),
            float(fit["c"]),
            float(fit["q"]),
        )
    elif model_version == GH3_VERSION:
        returns = _realisation(z, float(fit["b"]), float(fit["g"]), float(fit["h"]))
    else:
        raise ValueError(f"Unsupported persisted model version: {model_version}")
    centered = returns - np.dot(returns, probabilities)
    underlying = 1.0 + centered
    payoff = underlying - strike / forward if right == "C" else strike / forward - underlying
    return float(np.dot(probabilities, np.maximum(payoff, 0.0)))


def _smile_payload(raw: pd.DataFrame, fit: pd.Series) -> list[dict]:
    delta = raw.loc[raw["strikeReference"].astype(str).str.lower().eq("delta")].copy()
    for column in ("absoluteStrike", "impliedVolatility", "relativeStrike"):
        delta[column] = pd.to_numeric(delta[column], errors="coerce")
    delta = delta.dropna(subset=["absoluteStrike", "impliedVolatility", "relativeStrike"])
    forward = float(fit["fwd"])
    delta["right"] = np.where(delta["absoluteStrike"] < forward, "P", "C")
    delta = delta.loc[delta["absoluteStrike"] != forward].sort_values("absoluteStrike")
    points = []
    for row in delta.itertuples(index=False):
        strike = float(row.absoluteStrike)
        right = str(row.right)
        model_price = _model_normalized_price(forward, strike, right, fit)
        points.append({
            "moneyness": strike / forward,
            "strike": strike,
            "right": right,
            "delta_reference": float(row.relativeStrike),
            "raw_iv": float(row.impliedVolatility),
            "fitted_iv": _implied_vol(forward, strike, model_price, right),
        })
    return points


def _fit_metadata(fit: pd.Series, fit_date: str, *, source: str) -> dict:
    metadata = {
        "fit_date": fit_date,
        "model_version": str(fit.get("model_version", GH3_VERSION)),
        "source": source,
        "forward": float(fit["fwd"]),
        "medcouple": float(fit["medcouple"]),
        "b": float(fit["b"]),
        "g": float(fit["g"]),
        "h": float(fit["h"]),
        "mad_err": float(fit["mad_err"]),
    }
    if "cab" in fit and pd.notna(fit.get("cab")):
        metadata["cab"] = float(fit["cab"])
    if "settings_hash" in fit and pd.notna(fit.get("settings_hash")):
        metadata["settings_hash"] = str(fit["settings_hash"])
    if metadata["model_version"] == GH5_VERSION:
        metadata["c"] = float(fit["c"])
        metadata["q"] = float(fit["q"])
    return metadata


def _comparison_points(raw: pd.DataFrame, gh3_fit: pd.Series, gh5_fit: pd.Series) -> list[dict]:
    delta = raw.loc[raw["strikeReference"].astype(str).str.lower().eq("delta")].copy()
    for column in ("absoluteStrike", "impliedVolatility", "relativeStrike"):
        delta[column] = pd.to_numeric(delta[column], errors="coerce")
    delta = delta.dropna(subset=["absoluteStrike", "impliedVolatility", "relativeStrike"])
    comparison_forward = float(gh5_fit["fwd"])
    delta["right"] = np.where(delta["absoluteStrike"] < comparison_forward, "P", "C")
    delta = delta.loc[delta["absoluteStrike"] != comparison_forward].sort_values("absoluteStrike")
    points = []
    for row in delta.itertuples(index=False):
        strike = float(row.absoluteStrike)
        right = str(row.right)
        point = {
            "moneyness": strike / comparison_forward,
            "strike": strike,
            "right": right,
            "delta_reference": float(row.relativeStrike),
            "raw_iv": float(row.impliedVolatility),
        }
        for version, fit in ((GH3_VERSION, gh3_fit), (GH5_VERSION, gh5_fit)):
            forward = float(fit["fwd"])
            model_price = _model_normalized_price(forward, strike, right, fit)
            point[f"{version}_fitted_iv"] = _implied_vol(forward, strike, model_price, right)
        points.append(point)
    return points


def _build_latest_comparison(common_date: str) -> dict:
    comparison = {"raw_date": common_date, "cab": 0.0, "symbols": {}}
    gh3_settings = FitSettings(model_version=GH3_VERSION, cab=0.0)
    gh5_settings = FitSettings(model_version=GH5_VERSION, cab=0.0)
    for symbol in SYMBOLS:
        raw = _read_raw(symbol, common_date)
        gh3_fit = pd.Series(fit_equity_vol_day(symbol, common_date, gh3_settings))
        gh5_fit = pd.Series(fit_equity_vol_day(symbol, common_date, gh5_settings))
        comparison["symbols"][symbol] = {
            "raw_date": common_date,
            "fits": {
                GH3_VERSION: _fit_metadata(
                    gh3_fit, common_date, source="recomputed_from_latest_raw_smile_cab_0"
                ),
                GH5_VERSION: _fit_metadata(
                    gh5_fit, common_date, source="recomputed_from_latest_raw_smile_cab_0"
                ),
            },
            "points": _comparison_points(raw, gh3_fit, gh5_fit),
        }
    return comparison


def build_payload(
    model_version: str = ACTIVE_MODEL_VERSION,
    allow_gh3_fallback: bool = False,
) -> dict:
    result = {
        "schema_version": "tip_tlt_gh5_model_v1" if model_version == GH5_VERSION else "tip_tlt_gh3_model_v1",
        "model_version": model_version,
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "tenor": "1m",
        "symbols": {},
    }
    latest_dates = []
    for symbol in SYMBOLS:
        latest_date = _latest_raw_date(symbol)
        raw = _read_raw(symbol, latest_date)
        fit, fit_date = _read_latest_fit(symbol, latest_date, model_version, allow_gh3_fallback)
        holdings = _read_holdings(symbol)
        duration, duration_source, holdings_date, holdings_rows = _effective_duration(holdings)
        points = _smile_payload(raw, fit)
        symbol_model_version = str(fit.get("model_version", GH3_VERSION))
        entry = {
            "raw_date": latest_date,
            "fit_date": fit_date,
            "model_version": symbol_model_version,
            "forward": float(fit["fwd"]),
            "medcouple": float(fit["medcouple"]),
            "b": float(fit["b"]),
            "g": float(fit["g"]),
            "h": float(fit["h"]),
            "mad_err": float(fit["mad_err"]),
            "effective_duration": duration,
            "effective_duration_source": duration_source,
            "holdings_as_of_date": holdings_date,
            "holdings_rows": holdings_rows,
            "points": points,
        }
        if symbol_model_version == GH5_VERSION:
            entry["c"] = float(fit["c"])
            entry["q"] = float(fit["q"])
        result["symbols"][symbol] = entry
        latest_dates.extend([latest_date, fit_date])
    result["latest_date"] = max(latest_dates)
    if model_version == GH5_VERSION:
        comparison_date = _latest_common_raw_date()
        result["comparison_schema_version"] = "tip_tlt_gh3_gh5_comparison_v1"
        result["comparison"] = _build_latest_comparison(comparison_date)
    return result


def main() -> int:
    output = Path(__file__).resolve().parents[1] / "data/tip_tlt_model.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = build_payload()
    temporary = output.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(output)
    print(json.dumps({"output": str(output), "latest_date": payload["latest_date"], "symbols": list(payload["symbols"])}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
