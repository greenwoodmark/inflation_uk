from datetime import date

from tools.generate_jup_gear_chart_data import build_jup_total_return_rows, build_scatter_series


def test_gross_total_return_reinvests_ex_date_cash():
    prices = [
        {"date": date(2024, 1, 2), "close": 100.0},
        {"date": date(2024, 1, 3), "close": 90.0},
        {"date": date(2024, 1, 4), "close": 95.0},
    ]
    dividends = [{"ex_date": "2024-01-03", "cash_amount_gbp_per_share": "5.00000000"}]
    rows = build_jup_total_return_rows(prices, dividends)
    assert rows[1]["dividend_gbp_per_share"] == 5.0
    assert rows[1]["jup_total_return_index"] == 0.95
    assert rows[2]["jup_total_return_index"] == 0.95 * (95.0 / 90.0)


def test_scatter_keeps_price_return_and_uses_gross_total_return():
    prices = [
        {"date": date(2023, 1, 2), "close": 100.0},
        {"date": date(2023, 1, 3), "close": 90.0},
        {"date": date(2024, 1, 2), "close": 95.0},
        {"date": date(2024, 1, 3), "close": 95.0},
    ]
    nav = [
        {"valuation_date": date(2023, 1, 3), "nav": 1.0},
        {"valuation_date": date(2024, 1, 3), "nav": 1.1},
    ]
    points = build_scatter_series(nav, prices, [{"ex_date": "2023-01-04", "cash_amount_gbp_per_share": "5"}])
    point = points[-1]
    assert point["jup_price_return_pct"] == round((95.0 / 90.0 - 1) * 100, 6)
    assert point["jup_total_return_pct"] > point["jup_price_return_pct"]


def test_dividend_monitor_flags_missing_event_after_cadence():
    from tools.generate_jup_gear_chart_data import build_jup_dividend_monitor

    rows = [
        {"ex_date": "2025-04-17", "payment_date": "2025-05-20", "cash_amount_gbp_per_share": "0.022", "dividend_type": "ordinary_final"},
        {"ex_date": "2025-08-07", "payment_date": "2025-09-05", "cash_amount_gbp_per_share": "0.021", "dividend_type": "ordinary_interim"},
        {"ex_date": "2026-04-16", "payment_date": "2026-05-19", "cash_amount_gbp_per_share": "0.023", "dividend_type": "ordinary_final"},
    ]
    result = build_jup_dividend_monitor(rows, today=date(2027, 1, 1))
    assert len(result["last_four_events"]) == 3
    assert result["missing_forthcoming_event"] is True
    assert result["status"] == "missing_forthcoming_event"
