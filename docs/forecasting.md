# Forecasting with honest evaluation (Gate 4)

Forecasts of **observed sales**: positive merchandise units per calendar day (sum of
`quantity > 0` on accepted `positive_merchandise` lines). These are not unconstrained
demand. The input is the immutable validated snapshot `20261116T192553-472ac294`
(`forecast_inputs` for the aggregate calendar, `fact_invoice_line` for products).

```
PYTHONPATH=src .venv/Scripts/python.exe -m meridian.forecast --pytest
.venv/Scripts/python.exe -m pytest -q tests/test_forecast.py
```

Code: [`src/meridian/forecast.py`](../src/meridian/forecast.py). Tests:
[`tests/test_forecast.py`](../tests/test_forecast.py). Evidence:
[`evidence/forecast-run.json`](../evidence/forecast-run.json).

## Protocol (fixed before fitting)

[`analysis/forecast/config.json`](../analysis/forecast/config.json) is written and
hashed (SHA-256 `b2db0997…a71d88e`) before any model is fitted. It holds the origins,
horizons, models, tuning grid, product-selection rule, interval method, and promotion
rule. All origins are Thursdays spaced 28 days apart. The last complete source day is
2011-12-08. The partial day 2011-12-09 is never used as an actual.

| Role | Origins | Use |
|---|---|---|
| warm-up | 2011-02-03, 03-03, 03-31, 04-28 | Only to seed the interval residuals. Not scored. |
| validation | 2011-05-26, 06-23, 07-21, 08-18, 09-15, 10-13 | Tuning, promotion, and interval residuals |
| test (untouched) | 2011-11-10 (28-day window ends 12-08), plus 11-17, 11-24, 12-01 for the 7-day horizon | Evaluated once, with the frozen choice |
| latest | 2011-12-08 | Operational forecast for 2011-12-09 … 2012-01-05 |

**Leakage protection.** At each origin, `run_origin` first cuts the calendar and
product lines down to dates on or before the origin. Product selection, scaling,
features, training rows, and fits all use only that cut history. Training targets
must also fall on or before the origin, and the code raises an error if one does not.

- **Horizons.** Each origin forecasts h = 1…28 calendar days. The *7-day horizon*
  scores h ≤ 7 and the *28-day horizon* scores h ≤ 28. Only origins whose full window
  ends by 2011-12-08 count.
- **Models.**
  - *Seasonal naive*: the most recent observation-available value on the same weekday,
    on or before the origin. For h > 7 this reuses the last observed week; it never
    uses future actuals.
  - *Trailing mean*: the mean of the observation-available days in the 28 days that
    end at the origin.
  - *Gradient boosting*: `HistGradientBoostingRegressor(loss="absolute_error")`, a
    direct multi-horizon model with horizon as a feature. It is pooled over the
    aggregate and the selected products. Features are lags 0–13, trailing 7/28-day
    mean, std, zero share and availability share, the latest and 8-week mean
    same-weekday value, and calendar fields for the target date (weekday, month, day)
    and origin weekday. Target and level features are divided by an origin-time scale:
    the trailing 56-day available mean, with a floor of 1. That is the normalisation.
    Missing lags stay NaN and the model handles them natively; nothing is imputed with
    future data. LightGBM is not installed, so scikit-learn is used.
- **Products.** At each origin, products qualify if they sold on ≥ 50 % of the
  observation-available days in the trailing 182 days. The top 5 by units in that
  window are selected. The rule excludes one-off bulk items, such as 23166. The test
  panel is selected once at 2011-11-10 and kept for the later weekly test origins.
- **Cold start and sparse histories.** A product contributes training rows only from
  28 days after its first observed sale. Trailing statistics use `min_periods` and
  fall back to the expanding mean. Seasonal naive falls back to the trailing mean.
- **Unavailable vs zero.** A day with no source records, a quarantined row, or the
  partial final day is *unavailable* (NaN). It is forecast but never scored. A product
  with no sales on an available day is an *observed zero*. If a weekday was available
  on < 10 % of days in the history (in practice Saturday, 1 of 105), all models
  forecast 0 for it and flag `expected_non_trading`. That day is still unscored.
- **Metrics.** MAE, and WAPE = Σ|error| / Σactual. WAPE is `null` with
  `wape_null_reason` (`zero_actual_sum` or `no_observation_available_days`) when it is
  undefined. Results are reported per horizon, per series, and for the *aggregate*
  and *products* (pooled selected products) scopes.
- **80 % intervals.** The interval is prediction + scale × the empirical 10 %/90 %
  quantiles of the same model's normalised residuals. Residuals come from earlier
  non-test origins whose target dates fall on or before the current origin. They are
  bucketed by scope and horizon (1–7 or 8–28) and fall back to wider pools when fewer
  than 25 residuals exist. Test intervals never use test residuals. Only the latest
  operational forecast may use them.
- **Promotion.** The model with the lowest mean validation WAPE over {aggregate,
  products} × {7, 28} wins. Ties go to the simpler model. The gradient-boosting
  configuration (`g1`/`g2`/`g3`) is chosen the same way, on validation only.

## Results (from the executed run)

Validation promotion scores: seasonal naive 0.6760, trailing mean 0.6456, gradient
boosting g1 0.5354, g2 0.5383, **g3 0.5248**. **Promoted: gradient boosting (g3).**
The choice came from validation only. The test results below confirm the direction of
the result but did not influence it.

### Validation (6 origins)

| Model | Horizon | Scope | n days | MAE (units) | WAPE | 80 % coverage | Mean 80 % width |
|---|---|---|---|---|---|---|---|
| Seasonal naive | 7 | aggregate | 35 | 6,434.7 | 0.355 | 0.657 | 16,194 |
| Trailing mean | 7 | aggregate | 35 | 7,160.0 | 0.395 | 0.686 | 16,502 |
| Gradient boosting | 7 | aggregate | 35 | 5,397.8 | 0.298 | 0.714 | 11,910 |
| Seasonal naive | 28 | aggregate | 141 | 7,376.1 | 0.383 | 0.688 | 18,575 |
| Trailing mean | 28 | aggregate | 141 | 6,505.5 | 0.337 | 0.759 | 17,252 |
| Gradient boosting | 28 | aggregate | 141 | 5,482.3 | 0.284 | 0.695 | 13,727 |
| Seasonal naive | 7 | products | 175 | 140.0 | 0.970 | 0.857 | 291 |
| Trailing mean | 7 | products | 175 | 136.1 | 0.943 | 0.869 | 299 |
| Gradient boosting | 7 | products | 175 | 109.3 | 0.757 | 0.777 | 289 |
| Seasonal naive | 28 | products | 705 | 153.4 | 0.996 | 0.823 | 292 |
| Trailing mean | 28 | products | 705 | 139.7 | 0.907 | 0.848 | 293 |
| Gradient boosting | 28 | products | 705 | 116.9 | 0.759 | 0.732 | 282 |

### Untouched test (7-day: 4 weekly origins; 28-day: origin 2011-11-10)

| Model | Horizon | Scope | n days | MAE (units) | WAPE | 80 % coverage | Mean 80 % width |
|---|---|---|---|---|---|---|---|
| Seasonal naive | 7 | aggregate | 24 | 6,582.3 | 0.221 | 0.833 | 28,296 |
| Trailing mean | 7 | aggregate | 24 | 6,567.4 | 0.220 | 0.833 | 29,837 |
| Gradient boosting | 7 | aggregate | 24 | 6,378.8 | 0.214 | 0.833 | 22,856 |
| Seasonal naive | 28 | aggregate | 24 | 6,897.8 | 0.232 | 0.958 | 32,498 |
| Trailing mean | 28 | aggregate | 24 | 7,284.3 | 0.244 | 0.833 | 28,434 |
| Gradient boosting | 28 | aggregate | 24 | 5,879.8 | 0.197 | 0.792 | 23,272 |
| Seasonal naive | 7 | products | 120 | 196.7 | 0.788 | 0.817 | 468 |
| Trailing mean | 7 | products | 120 | 172.8 | 0.692 | 0.850 | 472 |
| Gradient boosting | 7 | products | 120 | 161.6 | 0.647 | 0.700 | 419 |
| Seasonal naive | 28 | products | 120 | 299.5 | 1.199 | 0.700 | 443 |
| Trailing mean | 28 | products | 120 | 171.6 | 0.687 | 0.800 | 427 |
| Gradient boosting | 28 | products | 120 | 160.5 | 0.643 | 0.717 | 390 |

The 28-day test window has 28 calendar days but only 24 observation-available days;
the 4 Saturdays have no records.

**WAPE reduction of gradient boosting vs seasonal naive** (computed from these runs):

| | 7-day aggregate | 28-day aggregate | 7-day products | 28-day products |
|---|---|---|---|---|
| Validation | 16.1 % | 25.7 % | 21.9 % | 23.8 % |
| Test | 3.1 % | 14.8 % | 17.9 % | 46.4 % |

The large 28-day product gain on test mostly reflects seasonal naive repeating one
week's bulk orders four times; see 84879 and 85099B in the product chart.

**Test, per product (gradient boosting, 28-day WAPE):** 22197 0.715, 84077 0.456,
84879 0.619, 85099B 0.681, 85123A 0.570. Every series and scope in both roles is in
[`metrics.json`](../analysis/forecast/metrics.json). No series had a zero-actual WAPE
window in this run, but the null rule is implemented and tested.

**Interval calibration.** Nominal coverage is 80 %. Observed validation coverage was
lower for gradient boosting: 0.695–0.714 for the aggregate and 0.73–0.78 for
products. Test coverage was 0.70–0.83. The intervals use few past residuals (4–9
earlier origins), the series are non-stationary (growth into the November peak), and
product series have bulk-order spikes. They should be read as approximate.

Charts:
- [test_aggregate.png](../analysis/forecast/test_aggregate.png) (+ `.svg`): actual vs.
  forecast with the 80 % interval, aggregate, test origin 2011-11-10.
- [test_products.png](../analysis/forecast/test_products.png) (+ `.svg`): the same for
  the 5 test-panel products.
- [error_by_model.png](../analysis/forecast/error_by_model.png) (+ `.svg`): WAPE by
  model, horizon, and role.

## Leakage tests (`tests/test_forecast.py`, 7 passed)

- `test_future_perturbation_does_not_change_origin_forecasts_or_features` runs an
  origin on synthetic data, then reruns it after changing everything dated after the
  origin:
  - aggregate units ×50 + 999, with availability flipped;
  - product units ×100;
  - a new 1e6-unit product.

  The product selection, every origin feature vector, and every prediction from all
  three models (including a fitted gradient-boosting model) must be bit-identical. The
  test also checks that the latest training target is on or before the origin.
- `test_past_perturbation_does_change_forecasts` is the control: tripling the last 14
  days *before* the origin must change the forecasts. This shows the test above is
  sensitive.
- `test_intervals_use_only_earlier_origins_with_past_targets`: the first origin has
  no interval, and multiplying test actuals ×7 leaves every interval unchanged.
- The remaining tests cover:
  - seasonal-naive correctness: the last *available* same weekday, gap skipping,
    equal values for h = 7/14/21/28, and Saturday forecast as 0 when non-trading;
  - trailing mean ignoring unavailable days;
  - the WAPE zero-denominator rule, the no-data rule, and exclusion of unavailable
    days from scoring;
  - chronological origins, with validation windows ending before the test.

## Outputs

- [`analysis/forecast/predictions.csv`](../analysis/forecast/predictions.csv) holds
  every forecast, including all three tuning configs on warm-up and validation.
  Columns: role, origin, h, target_date, series_id, scope, model, config, prediction,
  lower, upper, actual, available, availability_status, expected_non_trading, scale,
  interval_method, interval_n.
- [`analysis/forecast/metrics.json`](../analysis/forecast/metrics.json): all metric
  rows, tuning scores, improvements, per-origin product selections, and training sizes.
- [`analysis/forecast/serving.json`](../analysis/forecast/serving.json) (~120 KB) for
  the UI. Top-level keys: `state, snapshot_id, generated_at, config, promoted_model,
  promotion_reason, models, metrics{validation,test}, improvement, series,
  test_forecast{series_id:[…]}, latest_forecast{series_id:[…]}, charts, limitations`.

## Limitations

- Observed sales are not unconstrained demand. The source has no stock-out or
  inventory data.
- No prices, promotions, customer aggregates, or other exogenous drivers are used.
- There are only about two years of history, so the November peak is seen about once
  before the test. Validation covers May–November 2011 and the test covers
  mid-November to early December.
- Holiday closures are not modelled. The latest forecast (from 2011-12-08) will
  over-forecast any late-December closure days. Saturdays are forecast as 0 because
  they are historically non-trading.
- The gradient-boosting config was picked among 3 candidates on the same validation
  folds, which slightly flatters its validation score. The test is the unbiased check.
- The test has a single 28-day window, with 24 scored aggregate days, so its numbers
  are noisy.
- Product series are intermittent, with bulk orders. Product WAPE is high and the
  model, trained with absolute-error loss, forecasts medians. That under-forecasts
  spikes, which matters for inventory; see [inventory scenarios](inventory-scenarios.md).
