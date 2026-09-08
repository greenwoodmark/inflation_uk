from pathlib import Path
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path("/home/mark/inflation_uk/tools")))
from generate_tip_tlt_model_data import _model_normalized_price  # noqa: E402


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
