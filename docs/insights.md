# Customer, basket, predictive and statistical insights

`src/meridian/insights.py` (`python -m meridian.cli insights`) adds data-mining, predictive and inferential analysis on top of the validated accepted lines. Everything runs on one machine with DuckDB and scikit-learn in about 17 seconds. Results are written to `analysis/insights/insights.json`, served by `GET /api/insights`, and shown in the **Customers**, **Baskets** and **Signals** views.

## Which input produced these numbers

The UCI host was unreachable from the machine that ran this analysis, so the input was rebuilt from two public copies of the workbook's sheets (`scripts/reconstruct_from_mirrors.py`):

| Sheet | Source used |
|---|---|
| Year 2009-2010 | CSV export of the sheet (Packt *Data Analysis Workshop*, chapter 8) |
| Year 2010-2011 | the older UCI *Online Retail* dataset (id 352), which is the same rows |

Both sheets went through the project's own `sql/silver.sql`. Every **accepted** row and every GBP total matched the pinned Spark run exactly ([evidence/mirror-reconstruction.json](../evidence/mirror-reconstruction.json)):

- 1,061,664 accepted lines
- gross £20,112,074.02, adjustments −£732,828.20, net £19,379,245.82
- 6 quarantined rows

The only difference is one fewer **excluded** non-merchandise row: 1,067,370 source rows against 1,067,371. Excluded rows never enter a metric. Line identities in this input use a distinct marker and are not the pinned workbook's `line_id`s, so these outputs are not joined back to snapshot lineage.

On a machine with the pinned workbook, run `python -m meridian.cli insights` and it reads the published snapshot's `fact_invoice_line` directly.

## Segmentation (k-means on RFM)

- **Features:** standardised `log1p(recency_days)`, `log1p(frequency)` and `log1p(monetary)` for 5,852 identified buyers, as of 2011-12-08 (the last complete day).
- **Choosing k:** silhouette was computed on a fixed 3,000-buyer sample for k = 3…7. The values were 0.343, **0.363**, 0.336, 0.330 and 0.303, and k = 4 was chosen.
- **Rule for k:** take the largest k within 0.02 of the best silhouette, so the segments stay actionable.
- **Naming:** clusters are named from the rank of their medians, not by hand.

| Segment | Buyers | Share of buyers | Share of value | Median recency | Median orders | Median value | Mean P(return in 90 d) |
|---|---:|---:|---:|---:|---:|---:|---:|
| Champions | 1,179 | 20% | **73%** | 16 d | 13 | £5,081 | 84% |
| At risk | 1,463 | 25% | 17% | 180 d | 5 | £1,506 | 46% |
| New & promising | 1,241 | 21% | 6% | 23 d | 3 | £733 | 59% |
| Hibernating | 1,969 | 34% | 4% | 401 d | 1 | £283 | 18% |

Reading: one buyer in five generates nearly three quarters of identified value. The *At risk* group, which used to buy regularly but has gone quiet for about six months, holds 17% of value and is the obvious win-back target.

## Repeat-purchase model (predictive)

**Question:** will an identified buyer place another positive-merchandise order within 90 days?

**Time-split design (no leakage):**

| | Features up to | Label window | Base rate |
|---|---|---|---|
| Training | 2011-06-09 | 2011-06-10 → 2011-09-07 | 32% |
| Untouched test | 2011-09-09 | 2011-09-10 → 2011-12-08 | 44% |

Features only use rows on or before each cutoff. A unit test checks this (`tests/test_insights.py`).

| Model | Test AUC | Brier |
|---|---:|---:|
| Logistic regression (selected) | **0.793** | 0.197 |
| Gradient boosting | 0.793 | 0.199 |
| Recency-only rule (baseline) | 0.762 | n/a |

- **Model choice:** boosting did not beat logistic regression by the required 0.005 AUC, so the interpretable model is kept.
- **Top decile:** 92% of those buyers returned, and that decile captures 21% of all returners.
- **Importance:** permutation importance is led by active months, lifetime value, orders in the last 90 days and tenure. These features are correlated (recency, frequency and active months), so they share credit, and individual importances should not be read causally.
- **Action list:** 2,208 buyers fall below a 33% return probability. They hold £1.17m of historical value: 348 *At risk*, 1,818 *Hibernating* and 42 *New & promising*.

**Limitations.**
- The base rate shifts with the season (32% vs 44%; the test window holds the Christmas peak), so probabilities are under-calibrated in the middle deciles. Ranking is the reliable output.
- Scores at the reference day come from a refit on the latest labelled window and have no held-out evaluation of their own.
- The model predicts observed behaviour, not the effect of any campaign.

## Market basket analysis

- **Method:** pairwise association rules over 41,383 positive-merchandise invoices.
- **Threshold:** items and pairs must appear in at least 1% of invoices (413), which leaves 671 frequent items and 940 rules.
- **Measures:** lift = confidence ÷ consequent support.
- **Strongest rules:** the Poppy's Playhouse set (bedroom → living room, lift **48×**, confidence 66%) and the Scandinavian wooden Christmas decorations.
- **Business use:** high-lift sets are bundle candidates; high-support pairs are where a "frequently bought together" widget earns impressions.
- **Caveat:** association is not causation. Co-purchase also reflects catalogue layout and wholesale buyers restocking a range.

## Anomaly detection

- **Expected value:** the median net of the same weekday over the previous 8 observed weeks (at least 4 needed).
- **Score:** robust z = 0.6745 · (residual − median) / MAD, with days flagged at |z| > 3.5.
- **Result:** 17 days were flagged, 13 spikes and 4 drops. Each one comes with its three largest lines and the largest buyer's share of the day. Examples:
  - **2010-09-27** (z +6.4): one buyer (17450) placed 40% of the day's value in a single wholesale order. It is not a demand shift.
  - **2011-01-18** (drop): the 74,215-unit storage-jar order and its same-day cancellation.
  - **Late December 2010:** holiday closures.

## Hypothesis tests

| Question | Test | Result |
|---|---|---|
| Does weekday matter? | Kruskal–Wallis on observed-day net | H = 119.4, p ≈ 2×10⁻²³, ε² = 0.19. Thursday is highest and Sunday lowest; Saturday has one observed trading day. |
| Did orders get bigger year on year? (Dec–Nov years) | Mann–Whitney U; bootstrap of the mean (2,000 resamples) | Median £284 → £301, p ≈ 4×10⁻¹². Mean +£58, 95% CI [£35, £83]. |
| How concentrated is value? | Lorenz / Gini | Products: Gini 0.75, top 20% earn 78%. Customers: Gini 0.74, top 20% spend 77%. |

Invoices are not independent draws, because customers repeat. P-values are therefore descriptive, and the bootstrap interval is the more honest summary.

## Reproducibility

- **Seed:** 20261117.
- **Deterministic ordering:** inputs to clustering and modelling are explicitly ordered. Multithreaded `GROUP BY` order once changed the k-means seeding between runs; that was found and fixed.
- **Re-runs:** two consecutive runs produce identical segments, model and rules.

## Browser marts

`frontend/showcase/data/*.csv.gz` (5.9 MB) is what the SQL Lab and the public filters query with DuckDB-WASM.

- **Why gzip CSV:** CSV and gzip are part of DuckDB's core. Parquet support would be downloaded from `extensions.duckdb.org` at runtime.
- **Same SQL in both places:** the browser rebuilds the derived columns with `frontend/shared/browser-marts.sql`, using the same decimal expression as `sql/silver.sql`.
- **Export check:** the export runs that same SQL and asserts it reproduces the accepted lines: 1,061,664 rows, £19,379,245.82 and 21,845 negative-adjustment rows.
- **Bulk copies:** Parquet copies for Azure and Power BI are written to `data/marts/` and are not committed.
