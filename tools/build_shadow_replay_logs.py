"""Build the internal shadow-replay node log payload."""
from __future__ import annotations
import csv, json
from datetime import datetime, timezone
from pathlib import Path
import sys

DEFAULT_REPLAY = Path('/home/mark/trading_env/artifacts/uscpi_long_shadow_history_v4_median_maturity_2026')
DEFAULT_SHORT_REPLAY = Path('/home/mark/trading_env/artifacts/uscpi_six_driver/shadow/replay_june12_aug28_median_maturity_2026')
LIVE_SHORT_SNAPSHOT_DIR = Path('/home/mark/inflation_uk/data/cpurnsa_snapshots')
DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / 'site/internal/data/uscpi_shadow_replay_history.json'
POST_BROKER_START = '2026-06-12'

def number(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if value == value and abs(value) != float('inf') else None

def build_short_records(replay_dir):
    """Build 1M–12M records from the short six-driver DTCC adapter."""
    sys.path.insert(0, '/home/mark/trading_env')
    import pandas as pd
    from models.USCPI_fixings.run_eod import (
        _base_vintage, read_sdr_object, sdr_object_uri, target_months_for_date,
    )
    from models.USCPI_fixings.cpi_history import attach_initial_reference_cpi, load_headline_cpi
    from models.USCPI_fixings.driver_space_adapter import adapt_dtcc_rows_to_driver_observations
    from models.USCPI_fixings.dtcc_adapter import AdapterConfig
    from models.USCPI_fixings.conventions import reference_cpi_weights

    cpi = load_headline_cpi()
    short_snapshot_dir = DEFAULT_SHORT_REPLAY / 'sanitized_curves'
    short_snapshots = {}
    for path in sorted(short_snapshot_dir.glob('*.csv')):
        with path.open(newline='') as handle:
            rows = list(csv.DictReader(handle))
        if rows:
            short_snapshots[path.stem] = rows
    dates = sorted(date for date in short_snapshots if date >= POST_BROKER_START and (replay_dir / 'snapshots' / f'{date}.json').is_file())
    records = []
    config = AdapterConfig(economic_conventions_approved=True, quote_semantics='annualized_zero_coupon', fixed_rate_semantics='annualized_zero_coupon')
    cache = {}
    for date in dates:
        target_months = target_months_for_date(date, cpi)
        base_month, base_cpi, _, _ = _base_vintage(date, cpi, target_months)
        observation_path = DEFAULT_SHORT_REPLAY / 'observations' / f'{date}.csv'
        if not observation_path.is_file():
            raise FileNotFoundError(f'median replay observations missing: {observation_path}')
        grouped = {month.strftime('%Y-%m'): [] for month in target_months}
        target_set = set(grouped)
        with observation_path.open(newline='') as handle:
            replay_observations = csv.DictReader(handle)
            for row in replay_observations:
                maturity = row.get('maturity_date')
                rate = number(row.get('fixed_rate_decimal'))
                if not maturity or rate is None:
                    continue
                earlier, later, weight = reference_cpi_weights(pd.Timestamp(maturity))
                endpoints = {earlier, later} if weight > 0.0 and later != earlier else {earlier}
                for endpoint in endpoints:
                    key = endpoint.strftime('%Y-%m')
                    if key in target_set:
                        grouped[key].append(rate * 100.0)
        cache[date] = (target_months, grouped)
    for index, date in enumerate(dates[:-1]):
        next_date = dates[index + 1]
        snapshot = short_snapshots[date]
        next_snapshot = short_snapshots[next_date]
        rates = {index + 1: (number(row.get('implied_zc_rate')) * 100.0 if number(row.get('implied_zc_rate')) is not None else None) for index, row in enumerate(snapshot)}
        next_rates = {index + 1: (number(row.get('implied_zc_rate')) * 100.0 if number(row.get('implied_zc_rate')) is not None else None) for index, row in enumerate(next_snapshot)}
        targets, grouped = cache[date]
        _, next_grouped = cache[next_date]
        rows = []
        for month_index, target in enumerate(targets, start=1):
            t_rates = grouped[target.strftime('%Y-%m')]
            t1_rates = next_grouped.get(target.strftime('%Y-%m'), [])
            # For t+1, the target month frame can move after a CPI publication;
            # use the corresponding node index in the next frame.
            next_target = cache[next_date][0][month_index - 1]
            t1_rates = cache[next_date][1][next_target.strftime('%Y-%m')]
            rows.append({'node': f'{month_index}M', 'node_index': month_index, 'shadow_t_percent': rates.get(month_index), 'shadow_t_plus_1_percent': next_rates.get(month_index), 'trade_count': len(t_rates), 'mean_dtcc_zc_rate_t_percent': sum(t_rates) / len(t_rates) if t_rates else None, 'mean_dtcc_zc_rate_t_plus_1_percent': sum(t1_rates) / len(t1_rates) if t1_rates else None, 'zc_t_percent': rates.get(month_index), 'zc_t_plus_1_percent': next_rates.get(month_index)})
        records.append({'date': date, 'next_date': next_date, 'status_t': snapshot[0].get('fit_status'), 'status_t_plus_1': next_snapshot[0].get('fit_status'), 'nodes': rows})
    return records


def build_live_short_pair(snapshot_dir=LIVE_SHORT_SNAPSHOT_DIR):
    """Add the latest live shadow pair after the bounded historical replay."""
    paths = sorted(snapshot_dir.glob('*.csv'))
    if len(paths) < 2:
        return None
    selected = paths[-2:]
    frames = []
    for path in selected:
        with path.open(newline='') as handle:
            rows = list(csv.DictReader(handle))
        if len(rows) != 12:
            return None
        frames.append((path.stem, rows))
    (date, snapshot), (next_date, next_snapshot) = frames
    rows = []
    for index, (current, following) in enumerate(zip(snapshot, next_snapshot), start=1):
        current_rate = number(current.get('implied_zc_rate'))
        following_rate = number(following.get('implied_zc_rate'))
        current_count = int(float(current.get('node_trade_count') or 0))
        following_count = int(float(following.get('node_trade_count') or 0))
        rows.append({
            'node': f'{index}M',
            'node_index': index,
            'shadow_t_percent': current_rate * 100.0 if current_rate is not None else None,
            'shadow_t_plus_1_percent': following_rate * 100.0 if following_rate is not None else None,
            'trade_count': current_count,
            'mean_dtcc_zc_rate_t_percent': None,
            'mean_dtcc_zc_rate_t_plus_1_percent': None,
            'zc_t_percent': current_rate * 100.0 if current_rate is not None else None,
            'zc_t_plus_1_percent': following_rate * 100.0 if following_rate is not None else None,
        })
    return {
        'date': date,
        'next_date': next_date,
        'status_t': snapshot[0].get('fit_status'),
        'status_t_plus_1': next_snapshot[0].get('fit_status'),
        'nodes': rows,
    }


def build(replay_dir=DEFAULT_REPLAY, output=DEFAULT_OUTPUT):
    snapshots = {}
    for path in sorted((replay_dir / 'snapshots').glob('*.json')):
        snapshot = json.loads(path.read_text())
        terms = snapshot.get('term_months', [])
        rates = snapshot.get('inflation_rate_percent', [])
        by_year = {}
        for months, rate in zip(terms, rates):
            if int(months) % 12 == 0 and 12 <= int(months) <= 360:
                by_year[str(int(months) // 12)] = number(rate)
        snapshots[str(snapshot.get('model_date', path.stem))] = {
            'date': str(snapshot.get('model_date', path.stem)),
            'status': snapshot.get('status'),
            'rates_by_year': by_year,
        }
    trades = {}
    for path in sorted((replay_dir / 'posterior_updates').glob('*.csv')):
        date = path.stem
        by_year = {}
        with path.open(newline='') as handle:
            for row in csv.DictReader(handle):
                if row.get('status') != 'included' or row.get('accepted', '').lower() not in {'true', '1', 'yes'}:
                    continue
                year = row.get('term_years')
                rate_bp = number(row.get('market_rate_bp'))
                if year is None or rate_bp is None:
                    continue
                bucket = by_year.setdefault(str(int(float(year))), [])
                bucket.append(rate_bp / 100.0)
        trades[date] = {year: {'trade_count': len(rates), 'average_zc_rate_percent': sum(rates) / len(rates)} for year, rates in by_year.items()}
    dates = sorted(date for date in snapshots if date >= POST_BROKER_START)
    records = []
    for index, date in enumerate(dates[:-1]):
        next_date = dates[index + 1]
        rows = []
        for year in range(2, 31):
            key = str(year)
            trade_t = trades.get(date, {}).get(key, {})
            trade_t_plus_1 = trades.get(next_date, {}).get(key, {})
            # Snapshot rates are already effective annualized ZC rates from the
            # replay's three-month-lagged/interpolated CPI convention. Keep
            # explicit zc fields so the UI compares like-for-like with DTCC.
            zc_t = snapshots[date]['rates_by_year'].get(key)
            zc_t_plus_1 = snapshots[next_date]['rates_by_year'].get(key)
            rows.append({
                'node': f'{year}Y',
                'node_index': year,
                'shadow_rate_t_percent': zc_t,
                'shadow_rate_t_plus_1_percent': zc_t_plus_1,
                'trade_count': trade_t.get('trade_count', 0),
                'mean_dtcc_zc_rate_t_percent': trade_t.get('average_zc_rate_percent'),
                'mean_dtcc_zc_rate_t_plus_1_percent': trade_t_plus_1.get('average_zc_rate_percent'),
                'zc_t_percent': zc_t,
                'zc_t_plus_1_percent': zc_t_plus_1,
            })
        records.append({'date': date, 'next_date': next_date, 'status_t': snapshots[date]['status'], 'status_t_plus_1': snapshots[next_date]['status'], 'nodes': rows})
    long_records = records
    short_records = build_short_records(replay_dir)
    live_short_pair = build_live_short_pair()
    if live_short_pair is not None and live_short_pair['date'] not in {record['date'] for record in short_records}:
        short_records.append(live_short_pair)
    payload = {
        'schema_version': 'uscpi_shadow_replay_logs_v2',
        'generated_at_utc': datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        'source_directory': str(replay_dir),
        'post_broker_start_date': POST_BROKER_START,
        'date_count': len(long_records),
        'first_date': long_records[0]['date'] if long_records else None,
        'last_pair_date': long_records[-1]['date'] if long_records else None,
        'models': {
            'short': {'label': 'fixings market to 12m', 'date_count': len(short_records), 'first_date': short_records[0]['date'] if short_records else None, 'last_pair_date': short_records[-1]['date'] if short_records else None, 'records': short_records},
            'long': {'label': 'long end model >1y', 'date_count': len(long_records), 'first_date': long_records[0]['date'] if long_records else None, 'last_pair_date': long_records[-1]['date'] if long_records else None, 'records': long_records},
        },
        'records': long_records,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2) + '\n')
    return payload

if __name__ == '__main__':
    result = build()
    print(f"Wrote {result['date_count']} date pairs to {DEFAULT_OUTPUT}")
