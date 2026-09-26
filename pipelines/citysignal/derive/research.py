"""Prespecified exploratory tests and derived alternative-data features.

Tests use revised history and approximate observation alignment, not historical
release calendars. They are diagnostics, never evidence of executable alpha.
"""
from math import sqrt
from statistics import mean
import numpy as np

from ..framework.record import period_end, period_shift, utc_today
from .transforms import yoy

PAIRS = [
    ('electricity_large_users', 'grid-es-mainland', 'macro_industry', 'Business power → industrial output'),
    ('hiring_total', 'es', 'macro_retail', 'Hiring intentions → consumption'),
    ('hiring_new', 'es', 'macro_industry', 'New hiring → industrial output'),
]


def monthly(values):
    """Daily inputs require every calendar day before entering monthly research."""
    groups = {}
    for period, value in sorted(values.items()):
        groups.setdefault(period[:7], {})[period] = value
    return {p: mean(v.values()) for p, v in groups.items()
            if period_end(p) < utc_today() and len(v) == period_end(p).day}


def walk_forward(signal, target, min_train=36):
    # Fixed two-month spacing: x[t] and y[t] predict y[t+2]. This conservative
    # spacing reduces publication overlap but does not reconstruct release dates.
    samples = [(p, x, target[p], target[q]) for p, x in sorted(signal.items())
               if p in target and (q := period_shift(p, 2)) in target]
    errors, baselines, persistence, periods = [], [], [], []
    for i in range(min_train, len(samples)):
        p, x, baseline, truth = samples[i]
        # Only labels whose outcome month ended before the current input month.
        train = [s for s in samples[:i] if period_shift(s[0], 2) < p]
        if len(train) < min_train:
            continue
        matrix = np.array([[1, s[1], s[2]] for s in train], dtype=float)
        coefficients = np.linalg.lstsq(matrix, np.array([s[3] for s in train]), rcond=None)[0]
        prediction = float(np.array([1, x, baseline]) @ coefficients)
        baseline_matrix = np.array([[1, s[2]] for s in train], dtype=float)
        baseline_coefficients = np.linalg.lstsq(baseline_matrix, np.array([s[3] for s in train]), rcond=None)[0]
        baseline_prediction = float(np.array([1, baseline]) @ baseline_coefficients)
        errors.append((prediction - truth) ** 2)
        baselines.append((baseline_prediction - truth) ** 2)
        persistence.append((baseline - truth) ** 2)
        periods.append(period_shift(p, 2))
    n = len(errors)
    if n < 12:
        return {'status': 'Insufficient history', 'test_observations': n, 'skill': None}
    rmse, baseline_rmse = sqrt(mean(errors)), sqrt(mean(baselines))
    return {'status': 'Exploratory only', 'test_observations': n,
        'rmse': round(rmse, 3), 'baseline_rmse': round(baseline_rmse, 3),
        'persistence_rmse': round(sqrt(mean(persistence)), 3),
        'skill': round(100 * (1 - rmse / baseline_rmse), 1) if baseline_rmse > 1e-10 else None,
        'test_start': periods[0], 'test_end': periods[-1]}


def build_research(store):
    tests = []
    for metric, geo, target_id, label in PAIRS:
        s, t = store.get(metric, geo), store.get(target_id, 'es')
        result = {'status': 'Not collected', 'test_observations': 0, 'skill': None}
        if s and t:
            values = monthly(s.values) if metric.startswith('hiring') else s.values
            result = walk_forward(yoy(values), yoy(t.values))
        tests.append({'signal': metric, 'target': target_id, 'label': label, **result})
    recipes = []
    from ..countries import COUNTRIES
    for code in COUNTRIES:
        total, new = store.get('hiring_total', code), store.get('hiring_new', code)
        if not total or not new:
            continue
        points = []
        for p in sorted(total.values.keys() & new.values.keys()):
            before = period_shift(p, -28)
            if total.values.get(before, 0) > 0 and new.values.get(before, 0) > 0:
                value = 100 * (new.values[p] / new.values[before] - total.values[p] / total.values[before])
                points.append({'period': p, 'value': round(value, 3)})
        if points:
            recipes.append({'id': 'hiring_flow_gap', 'country': code, 'label': 'Hiring flow gap',
                'value': points[-1]['value'], 'period': points[-1]['period'], 'unit': 'pp',
                'series': points[-800:], 'definition': '28-day percentage growth in new postings minus 28-day percentage growth in all postings. Positive means fresh hiring intent is growing faster than the outstanding pool. It does not measure vacancies filled.',
                'source': 'Indeed Hiring Lab', 'status': 'Research candidate'})
    return {'version': 'research-v1', 'tests': tests, 'recipes': recipes,
        'method': 'Expanding-window OLS: target annual growth two months ahead, using signal annual growth and current target growth. Minimum 36 training observations; labels must end before test inputs. Baseline is an autoregression fitted on the same training rows, using only current target growth. At least 12 test months required. Positive skill means lower RMSE than that matched autoregressive baseline. Persistence RMSE is also reported. Fixed pairs; unsuccessful results retained.',
        'limitation': 'Revised history, not point-in-time releases. Publication lags, parameter searches, transaction costs and market returns are not modelled. These diagnostics do not establish tradable alpha.'}
