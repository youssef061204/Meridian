# Inventory scenario simulator (Gate 6)

> **SIMULATED – assumptions, not client outcomes.** All stock levels, lead times and
> costs below are ASSUMED. Costs are abstract **cost units**, not GBP. Observed sales
> are only a censored demand proxy, so the simulated lost sales are not measured lost
> sales, and no result here is a saving achieved by any business.

```
PYTHONPATH=src .venv/Scripts/python.exe -m meridian.inventory --pytest
.venv/Scripts/python.exe -m pytest -q tests/test_inventory.py
```

Code: [`src/meridian/inventory.py`](../src/meridian/inventory.py). Outputs:
- [`analysis/forecast/inventory.json`](../analysis/forecast/inventory.json)
- [`inventory_runs.csv`](../analysis/forecast/inventory_runs.csv): every scenario × episode × policy
- [`inventory_assumptions.json`](../analysis/forecast/inventory_assumptions.json)
- [`inventory_sensitivity.png`](../analysis/forecast/inventory_sensitivity.png) (+ `.svg`)

Evidence: [`evidence/inventory-run.json`](../evidence/inventory-run.json).
Tests: 6 passed.

## Design

- **Episodes (held-out only).** There is one 28-day episode (2011-11-11 … 2011-12-08)
  for each of the 5 products in the frozen test panel: 22197, 84077, 84879, 85099B and
  85123A. Demand on each day is the observed sales. Days with no source records (the
  Saturdays) have demand 0, which is ASSUMED.
- **Inputs.** Forecasts are read from the preserved test-period predictions in
  `predictions.csv`. These were issued at the review days 2011-11-10, 11-17, 11-24 and
  12-01 using only data available on or before each of those days.
- **Policy.** Periodic review every 7 days at the end of each review day, with an
  order-up-to level S.
  - order = max(0, S − (on hand + on order));
  - the order arrives at the start of day review + L;
  - unmet demand is lost.
- **Policies compared.** All use identical demand, starting stock, lead time, safety
  stock and costs:
  - *Fixed*: S = μ0 · (L + R) + SS, frozen at the start of the episode.
  - *Seasonal naive*, *trailing mean* and *gradient boosting* (the selected, promoted
    model): S = the sum of that model's point forecasts for days +1 … +(L + R) + SS.
- **Shared inputs.** These are known at the start of the episode, from snapshot
  history on or before 2011-11-10:
  - μ0 is the calendar-day mean of observed sales over the previous 28 days;
  - σ0 is the standard deviation of daily sales on the available days in those 28 days;
  - SS = z · σ0 · √(L + R).

| ASSUMED parameter | Base value | Sensitivity grid |
|---|---|---|
| Review period R | 7 days | – |
| Lead time L | 3 days | 1, 3, 7, 14 |
| Holding cost | 1 cost unit per unit-day | – |
| Shortage (lost-sale) cost | 100 cost units per unit | shortage/holding ratio 5, 10, 25, 100, 500 |
| Safety factor z | 0.84 | 0, 0.84, 1.28 (at L = 3, ratio 100) |
| Starting on-hand | 14 × μ0 | – |

Orders still in transit when the episode ends are ignored for every policy.

## Results (executed run; totals over the 5 product episodes)

Base scenario (L = 3, ratio 100, z = 0.84):

| Policy | Total cost | Holding | Shortage | Lost units | Fill rate |
|---|---|---|---|---|---|
| Fixed | 421,462 | 319,026 | 102,436 | 1,024 | 0.966 |
| Seasonal naive | 449,401 | 284,244 | 165,157 | 1,652 | 0.945 |
| Trailing mean | **334,570** | 321,013 | 13,557 | 136 | 0.996 |
| Gradient boosting (selected) | 516,177 | 245,420 | 270,757 | 2,708 | 0.910 |

In the base scenario the selected model's policy cost **22.5 % more** than the fixed
policy and **14.9 % more** than seasonal naive. All of the lost units in the base
scenario come from one product, 22197 (SMALL POPCORN HOLDER), which had bulk-order
days of up to ~3,100 units. The gradient-boosting model was trained with
absolute-error loss, so it forecasts close to the median and under-covers those
spikes. On the other four products it had the lowest or near-lowest cost (see
`base_scenario_per_episode`).

Sensitivity across all 22 scenarios. Change is gradient boosting vs fixed and vs
seasonal naive (negative = cheaper):

| L | Ratio | z | Fixed | Seas. naive | Trailing mean | Grad. boosting | Lowest | Δ vs fixed | Δ vs SN |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 5 | 0.84 | 305,794 | 300,344 | 320,763 | 272,627 | grad. boosting | −10.8 % | −9.2 % |
| 1 | 10 | 0.84 | 314,898 | 309,621 | 324,780 | 286,713 | grad. boosting | −8.9 % | −7.4 % |
| 1 | 25 | 0.84 | 342,210 | 337,452 | 336,829 | 328,970 | grad. boosting | −3.9 % | −2.5 % |
| 1 | 100 | 0.84 | 478,772 | 476,606 | 397,074 | 540,254 | trailing mean | +12.8 % | +13.4 % |
| 1 | 500 | 0.84 | 1,207,100 | 1,218,763 | 718,381 | 1,667,102 | trailing mean | +38.1 % | +36.8 % |
| 3 | 5 | 0.84 | 324,148 | 292,502 | 321,691 | 258,958 | grad. boosting | −20.1 % | −11.5 % |
| 3 | 10 | 0.84 | 329,270 | 300,760 | 322,369 | 272,496 | grad. boosting | −17.2 % | −9.4 % |
| 3 | 25 | 0.84 | 344,635 | 325,533 | 324,403 | 313,109 | grad. boosting | −9.2 % | −3.8 % |
| 3 | 100 | 0 | 490,711 | 603,556 | 383,008 | 738,652 | trailing mean | +50.5 % | +22.4 % |
| 3 | 100 | 0.84 | 421,462 | 449,401 | 334,570 | 516,177 | trailing mean | +22.5 % | +14.9 % |
| 3 | 100 | 1.28 | 425,132 | 452,462 | 402,055 | 424,591 | trailing mean | −0.1 % | −6.2 % |
| 3 | 500 | 0.84 | 831,205 | 1,110,030 | 388,799 | 1,599,203 | trailing mean | +92.4 % | +44.1 % |
| 7 | 5 | 0.84 | 346,635 | 395,387 | 379,502 | 276,974 | grad. boosting | −20.1 % | −29.9 % |
| 7 | 10 | 0.84 | 346,635 | 396,206 | 379,502 | 287,436 | grad. boosting | −17.1 % | −27.5 % |
| 7 | 25 | 0.84 | 346,635 | 398,663 | 379,502 | 318,824 | grad. boosting | −8.0 % | −20.0 % |
| 7 | 100 | 0.84 | 346,635 | 410,947 | 379,502 | 475,764 | fixed | +37.2 % | +15.8 % |
| 7 | 500 | 0.84 | 346,635 | 476,462 | 379,502 | 1,312,775 | fixed | +278.7 % | +175.5 % |
| 14 | 5 | 0.84 | 351,848 | 441,501 | 374,939 | 272,247 | grad. boosting | −22.6 % | −38.3 % |
| 14 | 10 | 0.84 | 359,493 | 449,146 | 382,584 | 289,185 | grad. boosting | −19.6 % | −35.6 % |
| 14 | 25 | 0.84 | 382,428 | 472,081 | 405,519 | 340,000 | grad. boosting | −11.1 % | −28.0 % |
| 14 | 100 | 0.84 | 497,103 | 586,756 | 520,194 | 594,071 | fixed | +19.5 % | +1.2 % |
| 14 | 500 | 0.84 | 1,108,703 | 1,198,356 | 1,131,794 | 1,949,119 | fixed | +75.8 % | +62.6 % |

Lowest-cost policy counts: gradient boosting 12, trailing mean 6, fixed 4, seasonal
naive 0.

**Reading.**
- When holding stock is relatively expensive (shortage/holding ratio ≤ 25), the
  selected model's lower and more accurate forecasts give the lowest simulated cost
  in every lead time tested.
- When lost sales are expensive (ratio ≥ 100), the higher trailing-mean or fixed
  levels win. Those levels are inflated by recent bulk orders and so act as a buffer.
  A median-accurate forecast plus a normal-theory safety stock under-protects
  intermittent, spiky products.
- Better MAE/WAPE therefore does not automatically mean lower inventory cost.
  Quantile or service-level forecasts, or demand-distribution safety stock, would be
  the next step.

**Disclosure.** The first executed run used a base shortage cost of 10 per unit (ratio
10) and a grid of 2, 5, 10, 25. In that run the selected model was cheapest in 17 of
18 scenarios. After seeing that result, the base ratio was changed to 100 and the grid
was widened to 5–500, because realistic lost-margin/holding ratios per unit-day are
large. That flipped the base-scenario winner. Both regimes are shown in the table
above. The assumptions were not tuned to favour any policy.

## Limitations

- These are SIMULATED results with ASSUMED parameters: 5 episodes, a single 28-day
  window, and an end-of-episode horizon effect from orders still in transit.
- Observed sales are a censored demand proxy, and the source has no stock data.
- The cost units are abstract. They are not GBP and are not client savings.
