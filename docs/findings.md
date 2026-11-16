# MERIDIAN historical findings

Portfolio consulting case study; source: Daqing Chen, [UCI Online Retail II](https://archive.ics.uci.edu/dataset/502/online+retail+ii), DOI 10.24432/C5CG6D, CC BY 4.0. Executed snapshot **20261116T192553-472ac294**; source checksum `bcbe73b35f5b7babf197fb0cb983a11f5d9ff929078d4aa53d171b1f2df2e980`. Snapshot manifest: `data/serving/snapshots/20261116T192553-472ac294/manifest.json`. Reproduce with `python -m meridian.analysis --snapshot 20261116T192553-472ac294 --notebook`.

All headline GBP figures are recorded merchandise invoice value, not audited revenue, profit, settled refunds, or cash. The original workbook is preserved as provided; unresolved export overlap limits financial interpretation. Exact result tables and query hashes are in [analysis/report.json](../analysis/report.json), [analysis/results](../analysis/results), and [analysis/queries](../analysis/queries). The [executed notebook](../notebooks/eda.ipynb) includes saved figures and readable methods.

## Source overlap needs reconciliation before accounting adoption

**Question and denominator:** Are all 1,067,371 original workbook coordinates unique business transactions? Exact equality across all eight business fields produces 1,033,036 distinct-looking payloads and 34,335 additional coordinate rows. There are **22,202 payload groups shared by both sheets**, comprising 22,523 matched row pairs; 12,133 extra identical-looking rows occur within sheets. Shared dates and accepted GBP by sheet are recorded in [source_overlap_dates.csv](../analysis/results/source_overlap_dates.csv).

**Interpretation/action:** Overlapping exports are a plausible explanation, but this dataset supplies no immutable upstream line ID proving which repeated-looking rows are accidental. Retain all coordinates and request original system keys/export-boundary documentation before choosing a correction. Do not silently apply `DISTINCT invoice,product` or drop all equal payloads: legitimate repeated invoice lines exist. The coordinate-preserved KPI is trustworthy for its stated as-provided-workbook definition; uniqueness as economic sales remains unresolved. Query: [source_overlap.sql](../analysis/queries/source_overlap.sql), [source_overlap_dates.sql](../analysis/queries/source_overlap_dates.sql). The December caveat is visible in the [monthly adjustment chart](../analysis/charts/adjustment_patterns.svg).

## Reconciled recorded merchandise bridge

**Question and denominator:** What amount is recorded by accepted merchandise logical rows? Of 1,067,371 original rows, 1,061,664 are accepted; named exclusions and quarantine remain in the manifest. Gross positive merchandise value is **£20,112,074.02**, signed negative adjustments are **−£732,828.20**, and net recorded merchandise value is **£19,379,245.82**. These figures add exactly in decimal arithmetic.

**Action and limits:** Use the bridge and exception ledger when discussing dashboard discrepancies; request ledger/settlement data before accounting reconciliation. Missing customer IDs remain in valid sales. Stock-code exclusions and line-rounding rules are modeling assumptions in [metric definitions](metrics.md). Evidence: snapshot manifest and [revenue bridge chart](../analysis/charts/revenue_bridge.svg); this diagnostic independently recomputes the fact totals against that manifest.

## Comparable months show a change, not its cause

**Question and denominator:** How do January–November totals compare, excluding the partial last December and overlap-affected December 2010? Net recorded GBP is £8,404,842.90 in 2010 and £8,588,634.91 in 2011: **£183,792.01, 2.2%**. Each interval spans 334 calendar dates; source dates without records remain unavailable, not proven zero-sales days. The monthly chart supports discussion of seasonal shape over two observed years, not a causal holiday or marketing effect.

The largest country arithmetic contribution is Australia: £106,675.50. Country and product contributions each independently sum to the total change; they must not be added together. Query/results: [monthly_sales.sql](../analysis/queries/monthly_sales.sql), [monthly_sales.csv](../analysis/results/monthly_sales.csv), [growth_contributions.sql](../analysis/queries/growth_contributions.sql). Charts: [monthly comparison](../analysis/charts/monthly_sales.svg), [country contributions](../analysis/charts/country_change.svg).

**Uncertainty/action:** A seeded moving-block sensitivity analysis of observed-day means gives a change of £772.65 per observed day and 95% percentile bounds [−£3,696.04, £5,365.22]. It resamples blocks of 7 observed trading dates independently in each year (1,000 repetitions, seed 20261116), preserving short local dependence. It does not quantify export errors, missing dates, longer dependence, or changing trends, and is not a significance or causal claim. [Sensitivity figure](../analysis/charts/bootstrap_sensitivity.svg), [daily query](../analysis/queries/daily_comparison.sql), [method and draws](../analysis/bootstrap.json). Investigate product availability, customer mix, pricing, export coverage, and promotions as competing explanations; the dataset cannot distinguish them.

## Concentration focuses commercial review

**Question and denominator:** Where is positive merchandise GBP concentrated across all accepted source coordinates? United Kingdom contributes £17,235,794.31, **85.7%** of gross; the top 20 product codes contribute **11.5%**. Negative amounts are excluded from this share denominator to avoid an unstable net-sales rank.

**Action and competing explanations:** Prioritize reliable reporting and service review for these segments. A UK-based business and wholesale ordering can explain concentration; this does not establish segment profitability or attractive acquisition spend. Product prices, units, catalogue breadth, and observation-window exposure all influence GBP ranks. Queries: [country](../analysis/queries/country_concentration.sql), [product](../analysis/queries/product_concentration.sql); [chart](../analysis/charts/concentration.svg), exact sorted CSVs in results.

## Customer identifiers constrain repeat-purchase and cohort analysis

**Question and denominator:** Among 5,852 identified customers with positive paid merchandise, **4,234 (72.4%)** have at least two distinct observed invoice IDs. Their repeat behavior uses known identities only; unidentified buyers do not become new customers. Accepted-line ID coverage is **77.3%** (820,647/1,061,664); raw missing IDs are reported separately by sheet in [missingness.csv](../analysis/results/missingness.csv).

For first-observed purchase cohorts with an eligible next month through November 2011, the pooled month 1 recorded return rate is **23.3%**. The cohort denominator counts each cohort's known first-observed buyers; repeated monthly activity is binary per customer. Late cohorts' immature cells remain blank. Full-month bounds do not prove source-day completeness, and no recorded return is not proof that a customer bought nothing. First observation is left-truncated, so do not call it acquisition or infer lifetime value.

**Action and competing explanations:** Improve ID capture and confirm identity stability before retention targeting. The known-ID subset may differ systematically from unidentified transactions, and cohorts have unequal follow-up. Queries: [repeat purchasing](../analysis/queries/repeat_purchases.sql), [cohorts](../analysis/queries/cohort_retention.sql), [missingness](../analysis/queries/missingness.sql). Figures: [purchase bands](../analysis/charts/repeat_purchases.svg), [cohort heatmap](../analysis/charts/cohort_retention.svg).

## Negative adjustments and extreme lines merit investigation

**Question and denominator:** Absolute negative merchandise GBP is **3.6%** of positive merchandise GBP over the whole workbook. This is an adjustment-to-gross ratio, not a settled-refund rate or percent of orders canceled. Monthly ratios include an explicitly marked partial final month and overlap month. [Query](../analysis/queries/adjustments.sql), [monthly chart](../analysis/charts/adjustment_patterns.svg).

The largest absolute accepted line is £168,469.60, quantity 80,995, invoice `581483`, source `Year 2010-2011` row540,423. [Top 20 exact source coordinates](../analysis/results/extreme_lines.csv) come from [extreme_lines.sql](../analysis/queries/extreme_lines.sql). Preserve and inspect these records rather than deleting valid extremes by percentile. Wholesale bulk orders, cancellation timing, or data-entry errors are competing explanations; inventory, original order, and settlement evidence is needed to resolve them.

## Adoption steps and remaining evidence

1. Reconcile overlapping exports with immutable client line keys and confirm merchandise-code/rounding policies.
2. Validate source coverage and customer-ID capture before treating trend or retention changes as commercial behavior.
3. Use segment concentration and exception queues to prioritize investigation; compare forecasting policies only after chronological evaluation is executed.
4. Obtain inventory availability, cost, promotions, payment settlement, and operational ownership before inventory or profit decisions. No realized client savings are claimed.

Analysis reproducibility is recorded in [report.json](../analysis/report.json): snapshot/manifest hash, source/converted checksums, query hashes, seed, software versions, command, elapsed timings, and verification assertions. Source accuracy and economic uniqueness remain open even when software checks pass.
