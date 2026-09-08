from pathlib import Path
from pathlib import Path
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path("/home/mark/inflation_uk/tools")))
from generate_tip_tlt_model_data import _comparison_points, _model_normalized_price  # noqa: E402


def test_website_reconstruction_uses_gh5_c_and_q():
    gh5 = pd.Series({
        "model_version": "gh5_v1",
        "b": 0.12,
        "g": -0.08,
        "h": 0.04,
        "c": 0.006,
        "q": -0.004,
        "truncpoint": 4.5,
        "zsteps": 100,
    })
    nested = gh5.copy()
    nested["c"] = 0.0
    nested["q"] = 0.0
    price = _model_normalized_price(100.0, 96.0, "P", gh5)
    nested_price = _model_normalized_price(100.0, 96.0, "P", nested)
    assert np.isfinite(price)
    assert np.isfinite(nested_price)
    assert price != nested_price


def test_website_reconstruction_rejects_unknown_model_version():
    fit = pd.Series({"model_version": "unknown", "b": 0.1, "g": 0.0, "h": 0.04})
    try:
        _model_normalized_price(100.0, 96.0, "P", fit)
    except ValueError as exc:
        assert "Unsupported persisted model version" in str(exc)
    else:
        raise AssertionError("unknown model version was accepted")


def test_comparison_points_reconstruct_both_models_at_same_strikes():
    raw = pd.DataFrame({
        "strikeReference": ["delta", "delta"],
        "absoluteStrike": [95.0, 105.0],
        "impliedVolatility": [0.20, 0.21],
        "relativeStrike": [0.10, 0.10],
    })
    gh3 = pd.Series({
        "model_version": "gh3_v1",
        "fwd": 100.0,
        "b": 0.12,
        "g": -0.08,
        "h": 0.04,
        "medcouple": 0.0,
        "mad_err": 0.01,
        "truncpoint": 4.5,
        "zsteps": 100,
    })
    gh5 = gh3.copy()
    gh5["model_version"] = "gh5_v1"
    gh5["c"] = 0.006
    gh5["q"] = -0.004
    points = _comparison_points(raw, gh3, gh5)
    assert len(points) == 2
    assert all("gh3_v1_fitted_iv" in point and "gh5_v1_fitted_iv" in point for point in points)
    assert [point["strike"] for point in points] == [95.0, 105.0]
    assert any(point["gh3_v1_fitted_iv"] != point["gh5_v1_fitted_iv"] for point in points)
