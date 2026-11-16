"""Reproducible full-history EDA over an immutable validated Gold export.

Run: python -m meridian.analysis --notebook
No source coordinates are deleted by this diagnostic analysis.
"""
from __future__ import annotations

import argparse
import csv
from datetime import date, datetime, timezone
from decimal import Decimal
import html
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import time

import duckdb
import numpy as np

from .common import DATA, ROOT, atomic_json, read_json, sha256

SEED = 20261116
OUT = ROOT / "analysis"
QUERIES = OUT / "queries"


def records(con, query):
    result = con.execute(query)
    columns = [column[0] for column in result.description]
    return [dict(zip(columns, row)) for row in result.fetchall()]


def save_csv(path, values):
    if not values:
        return
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=list(values[0]))
        writer.writeheader()
        writer.writerows(values)


def number(value):
    return float(value or 0)


def gbp(value):
    amount = number(value)
    return f"{'−' if amount < 0 else ''}£{abs(amount):,.2f}"


def percent(value):
    return f"{number(value)*100:.1f}%"


def moving_block_comparison(daily, repetitions=1000, block_length=7):
    """Unpaired bootstrap of observed-day means, preserving short local dependence.

    This is a descriptive sensitivity analysis, not an accounting confidence bound.
    No missing calendar dates are imputed as zero, and no causal inference is made.
    """
    rng = np.random.default_rng(SEED)
    series = {year: np.array([number(row["net_recorded_gbp"]) for row in daily if row["year"] == year])
              for year in (2010, 2011)}
    assert all(len(values) >= block_length for values in series.values())

    def resampled_mean(values):
        starts = rng.integers(0, len(values)-block_length+1,
                              size=int(np.ceil(len(values)/block_length)))
        sample = np.concatenate([values[start:start+block_length] for start in starts])[:len(values)]
        return float(np.mean(sample))

    draws = [resampled_mean(series[2011])-resampled_mean(series[2010]) for _ in range(repetitions)]
    lower, upper = np.quantile(draws, [0.025, 0.975])
    return {
        "seed": SEED, "repetitions": repetitions, "block_length_observed_days": block_length,
        "observed_days_2010": len(series[2010]), "observed_days_2011": len(series[2011]),
        "mean_2010_gbp": float(series[2010].mean()), "mean_2011_gbp": float(series[2011].mean()),
        "mean_change_gbp": float(series[2011].mean()-series[2010].mean()),
        "percentile_95_lower_gbp": float(lower), "percentile_95_upper_gbp": float(upper),
        "draws_gbp": draws,
        "method": "Independent moving-block resampling of each year's ordered observed daily net amounts; Jan-Nov 2010/2011, 7 observed-date blocks, no calendar zero filling.",
        "limits": "Descriptive sensitivity to short-range variation. Seven observed trading dates need not be seven calendar days. Nonstationarity, longer dependence, missing-date uncertainty, source overlap and source accuracy are not covered. No hypothesis test or causal claim.",
    }


def create_figures(data, summary, target):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter, PercentFormatter
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10,
                         "axes.spines.top": False, "axes.spines.right": False,
                         "axes.titleweight": "bold", "figure.facecolor": "white",
                         "axes.facecolor": "white", "savefig.facecolor": "white"})
    target.mkdir(parents=True, exist_ok=True)
    blue, teal, red = "#26547c", "#157f6b", "#ad3f48"
    saved = []

    def finish(fig, name, footer):
        fig.text(.02, .015, footer, fontsize=8, color="#4b5563", va="bottom")
        fig.tight_layout(rect=(0, .07, 1, 1))
        for ext in ("svg", "png"):
            fig.savefig(target / f"{name}.{ext}", dpi=140)
        saved.append(name)
        plt.close(fig)

    fig, ax = plt.subplots(figsize=(9, 4.2))
    metrics = summary["metrics"]
    labels = ["Gross positive merchandise", "Signed negative adjustments", "Net recorded merchandise"]
    values = [number(metrics[k]) for k in ["gross_positive_gbp", "negative_adjustments_gbp", "net_recorded_gbp"]]
    bars = ax.barh(labels, values, color=[teal, red, blue])
    ax.axvline(0, color="#777", lw=.8)
    ax.invert_yaxis()
    ax.xaxis.set_major_formatter(FuncFormatter(lambda x, _: f"£{x/1e6:g}m"))
    # Labels sit right of max(bar end, 0) so a negative bar's label never collides with the category axis.
    offset = max(values) * .01
    for bar, value in zip(bars, values):
        ax.text(max(value, 0) + offset, bar.get_y() + bar.get_height()/2, gbp(value), va="center", fontsize=9)
    ax.set_xlim(min(min(values)*1.15, 0), max(values)*1.32)
    ax.set_title("Recorded merchandise invoice value: full workbook")
    finish(fig, "revenue_bridge", "GBP; accepted coordinate-based lines. As-provided workbook includes unresolved cross-sheet overlap.")

    fig, ax = plt.subplots(figsize=(9, 4.5))
    for year, color, marker in [(2010, blue, "o"), (2011, teal, "s")]:
        points = [r for r in data["monthly_sales"] if r["year"] == year]
        ax.plot([r["month"] for r in points], [number(r["net_recorded_gbp"]) for r in points],
                color=color, marker=marker, label=str(year))
    ax.set_xticks(range(1, 12), ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov"])
    ax.yaxis.set_major_formatter(FuncFormatter(lambda x, _: f"£{x/1e3:,.0f}k"))
    ax.set_ylabel("Net recorded merchandise GBP")
    ax.set_title("Comparable January–November months")
    ax.set_ylim(bottom=0)
    ax.legend(title="Observed year")
    ax.grid(axis="y", alpha=.18)
    finish(fig, "monthly_sales", "December excluded: final December 2011 is partial and December 2010 has cross-sheet overlap. Source gaps remain unknown.")

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.7))
    countries = data["country_concentration"][:5]
    labels = [r["country"] for r in countries]+["All other countries"]
    shares = [number(r["gross_share"]) for r in countries]
    shares.append(1-sum(shares))
    bars = axes[0].barh(labels, shares, color=blue)
    axes[0].invert_yaxis()
    axes[0].set_xlim(0, 1)
    axes[0].xaxis.set_major_formatter(PercentFormatter(1))
    axes[0].bar_label(bars, labels=[percent(v) for v in shares], padding=3)
    axes[0].set_title("Positive merchandise GBP by country")
    products = data["product_concentration"][:100]
    axes[1].plot([r["rank"] for r in products], [number(r["cumulative_gross_share"]) for r in products], color=teal)
    axes[1].set_ylim(0, 1)
    axes[1].set_xlim(1, 100)
    axes[1].yaxis.set_major_formatter(PercentFormatter(1))
    axes[1].set_xlabel("Top product codes, ranked by positive GBP")
    axes[1].set_title(f"Top 20 products: {percent(summary['top20_product_gross_share'])} of gross")
    finish(fig, "concentration", "Full workbook; positive merchandise GBP denominator. Country and product shares are separate decompositions.")

    fig, ax = plt.subplots(figsize=(8, 4.4))
    repeat = data["repeat_purchases"]
    bars = ax.bar([r["purchase_band"] for r in repeat], [r["known_customers"] for r in repeat], color=blue)
    ax.bar_label(bars, padding=3)
    ax.set_ylim(0, max(r["known_customers"] for r in repeat)*1.18)
    ax.set_ylabel("Known customers with a positive paid purchase")
    ax.set_title("Observed repeat purchasing by distinct invoice")
    finish(fig, "repeat_purchases", "Full observation window; unidentified buyers excluded. First observed purchase is not proven acquisition; no lifetime-value claim.")

    cohorts = sorted(set(r["cohort_month"] for r in data["cohort_retention"]))
    matrix = np.full((len(cohorts), 7), np.nan)
    sizes = {}
    for row in data["cohort_retention"]:
        matrix[cohorts.index(row["cohort_month"]), row["month_index"]] = np.nan if row["retention_rate"] is None else number(row["retention_rate"])
        sizes[row["cohort_month"]] = row["cohort_size"]
    fig, ax = plt.subplots(figsize=(10, 9.5))
    image = ax.imshow(np.ma.masked_invalid(matrix), aspect="auto", cmap="Blues", vmin=0, vmax=1)
    ax.set_xticks(range(7), [str(age) for age in range(7)])
    ax.set_yticks(range(len(cohorts)), [f"{str(c)[:7]}  (n={sizes[c]:,})" for c in cohorts])
    for i in range(len(cohorts)):
        for j in range(7):
            if np.isfinite(matrix[i, j]):
                ax.text(j, i, f"{matrix[i,j]*100:.0f}%", ha="center", va="center", fontsize=8,
                        color="white" if matrix[i,j]>.55 else "#172b4d")
    ax.set_xlabel("Months since first observed positive paid purchase")
    ax.set_title("Observed monthly return purchasing among known customers")
    fig.colorbar(image, ax=ax, format=PercentFormatter(1), shrink=.5)
    finish(fig, "cohort_retention", "Calendar-bounded months through Nov 2011; immature cells blank. Zero means no recorded return, not proof of no purchase. Source gaps unknown.")

    fig, ax = plt.subplots(figsize=(10, 4.5))
    adjustments = data["adjustments"]
    x = np.arange(len(adjustments))
    bars = ax.bar(x, [number(r["adjustment_to_gross_ratio"]) for r in adjustments], color=red)
    bars[-1].set_hatch("///")
    dec2010 = next(i for i, r in enumerate(adjustments) if str(r["month"]).startswith("2010-12"))
    bars[dec2010].set_hatch("xx")
    ax.set_xticks(x, [str(r["month"])[:7] for r in adjustments], rotation=65, ha="right")
    ax.yaxis.set_major_formatter(PercentFormatter(1))
    ax.set_ylabel("Absolute negative GBP / positive GBP")
    ax.set_title("Recorded negative-adjustment intensity by month")
    finish(fig, "adjustment_patterns", "Full workbook; negative adjustments are not matched settled refunds. xx=overlap month; ///=partial final month.")

    fig, ax = plt.subplots(figsize=(10, 5))
    changes = [r for r in data["growth_contributions"] if r["segment_type"] == "country"][:10]
    changes = sorted(changes, key=lambda row: number(row["change_gbp"]))
    vals = [number(r["change_gbp"]) for r in changes]
    ax.barh([r["segment"] for r in changes], vals, color=[teal if v>=0 else red for v in vals])
    ax.axvline(0, color="#777", lw=.8)
    ax.xaxis.set_major_formatter(FuncFormatter(lambda x, _: f"£{x/1e3:,.0f}k"))
    ax.set_xlabel("2011 minus 2010 net recorded GBP; January–November")
    ax.set_title("Largest country contributions to the observed change")
    finish(fig, "country_change", "Ten countries with largest absolute changes; arithmetic contribution, not a causal explanation. Full decomposition in CSV.")

    fig, ax = plt.subplots(figsize=(9, 4.3))
    boot = summary["bootstrap"]
    ax.hist(boot["draws_gbp"], bins=32, color=blue, alpha=.8)
    for key, label, style in [("mean_change_gbp", "Observed mean change", "-"),
                              ("percentile_95_lower_gbp", "95% resampling bounds", "--"),
                              ("percentile_95_upper_gbp", None, "--")]:
        ax.axvline(boot[key], color=red, linestyle=style, label=label)
    ax.set_title("Sensitivity of the observed-day mean difference")
    ax.set_xlabel("2011 minus 2010 mean observed-day net GBP")
    ax.set_ylabel("Bootstrap repetitions")
    ax.xaxis.set_major_formatter(FuncFormatter(lambda x, _: f"{'−' if x < 0 else ''}£{abs(x):,.0f}"))
    ax.legend()
    finish(fig, "bootstrap_sensitivity", "1,000 repetitions; seed 20261116; blocks of 7 observed trading dates. Not a financial accuracy bound or causal test.")
    return saved


def findings_markdown(summary, snapshot):
    metrics = summary["metrics"]
    comparison = summary["comparable_periods"]
    overlap = summary["source_overlap"]
    country = summary["leading_country"]
    boot = summary["bootstrap"]
    first_change = summary["largest_country_change"]
    src = f"`data/serving/snapshots/{snapshot}/manifest.json`"
    return f"""# MERIDIAN historical findings

Portfolio consulting case study; source: Daqing Chen, [UCI Online Retail II](https://archive.ics.uci.edu/dataset/502/online+retail+ii), DOI 10.24432/C5CG6D, CC BY 4.0. Executed snapshot **{snapshot}**; source checksum `{summary['source_sha256']}`. Snapshot manifest: {src}. Reproduce with `python -m meridian.analysis --snapshot {snapshot} --notebook`.

All headline GBP figures are recorded merchandise invoice value, not audited revenue, profit, settled refunds, or cash. The original workbook is preserved as provided; unresolved export overlap limits financial interpretation. Exact result tables and query hashes are in [analysis/report.json](../analysis/report.json), [analysis/results](../analysis/results), and [analysis/queries](../analysis/queries). The [executed notebook](../notebooks/eda.ipynb) includes saved figures and readable methods.

## Source overlap needs reconciliation before accounting adoption

**Question and denominator:** Are all {summary['source_rows']:,} original workbook coordinates unique business transactions? Exact equality across all eight business fields produces {int(overlap['distinct_payloads']):,} distinct-looking payloads and {int(overlap['identical_looking_extra_rows']):,} additional coordinate rows. There are **{int(overlap['cross_sheet_shared_payloads']):,} payload groups shared by both sheets**, comprising {int(overlap['cross_sheet_match_pairs']):,} matched row pairs; {int(overlap['within_sheet_identical_looking_extra_rows']):,} extra identical-looking rows occur within sheets. Shared dates and accepted GBP by sheet are recorded in [source_overlap_dates.csv](../analysis/results/source_overlap_dates.csv).

**Interpretation/action:** Overlapping exports are a plausible explanation, but this dataset supplies no immutable upstream line ID proving which repeated-looking rows are accidental. Retain all coordinates and request original system keys/export-boundary documentation before choosing a correction. Do not silently apply `DISTINCT invoice,product` or drop all equal payloads: legitimate repeated invoice lines exist. The coordinate-preserved KPI is trustworthy for its stated as-provided-workbook definition; uniqueness as economic sales remains unresolved. Query: [source_overlap.sql](../analysis/queries/source_overlap.sql), [source_overlap_dates.sql](../analysis/queries/source_overlap_dates.sql). The December caveat is visible in the [monthly adjustment chart](../analysis/charts/adjustment_patterns.svg).

## Reconciled recorded merchandise bridge

**Question and denominator:** What amount is recorded by accepted merchandise logical rows? Of {summary['source_rows']:,} original rows, {summary['accepted_rows']:,} are accepted; named exclusions and quarantine remain in the manifest. Gross positive merchandise value is **{gbp(metrics['gross_positive_gbp'])}**, signed negative adjustments are **{gbp(metrics['negative_adjustments_gbp'])}**, and net recorded merchandise value is **{gbp(metrics['net_recorded_gbp'])}**. These figures add exactly in decimal arithmetic.

**Action and limits:** Use the bridge and exception ledger when discussing dashboard discrepancies; request ledger/settlement data before accounting reconciliation. Missing customer IDs remain in valid sales. Stock-code exclusions and line-rounding rules are modeling assumptions in [metric definitions](metrics.md). Evidence: snapshot manifest and [revenue bridge chart](../analysis/charts/revenue_bridge.svg); this diagnostic independently recomputes the fact totals against that manifest.

## Comparable months show a change, not its cause

**Question and denominator:** How do January–November totals compare, excluding the partial last December and overlap-affected December 2010? Net recorded GBP is {gbp(comparison['2010']['net_gbp'])} in 2010 and {gbp(comparison['2011']['net_gbp'])} in 2011: **{gbp(comparison['net_change_gbp'])}, {percent(comparison['net_change_ratio'])}**. Each interval spans 334 calendar dates; source dates without records remain unavailable, not proven zero-sales days. The monthly chart supports discussion of seasonal shape over two observed years, not a causal holiday or marketing effect.

The largest country arithmetic contribution is {first_change['segment']}: {gbp(first_change['change_gbp'])}. Country and product contributions each independently sum to the total change; they must not be added together. Query/results: [monthly_sales.sql](../analysis/queries/monthly_sales.sql), [monthly_sales.csv](../analysis/results/monthly_sales.csv), [growth_contributions.sql](../analysis/queries/growth_contributions.sql). Charts: [monthly comparison](../analysis/charts/monthly_sales.svg), [country contributions](../analysis/charts/country_change.svg).

**Uncertainty/action:** A seeded moving-block sensitivity analysis of observed-day means gives a change of {gbp(boot['mean_change_gbp'])} per observed day and 95% percentile bounds [{gbp(boot['percentile_95_lower_gbp'])}, {gbp(boot['percentile_95_upper_gbp'])}]. It resamples blocks of 7 observed trading dates independently in each year (1,000 repetitions, seed 20261116), preserving short local dependence. It does not quantify export errors, missing dates, longer dependence, or changing trends, and is not a significance or causal claim. [Sensitivity figure](../analysis/charts/bootstrap_sensitivity.svg), [daily query](../analysis/queries/daily_comparison.sql), [method and draws](../analysis/bootstrap.json). Investigate product availability, customer mix, pricing, export coverage, and promotions as competing explanations; the dataset cannot distinguish them.

## Concentration focuses commercial review

**Question and denominator:** Where is positive merchandise GBP concentrated across all accepted source coordinates? {country['country']} contributes {gbp(country['gross_positive_gbp'])}, **{percent(country['gross_share'])}** of gross; the top 20 product codes contribute **{percent(summary['top20_product_gross_share'])}**. Negative amounts are excluded from this share denominator to avoid an unstable net-sales rank.

**Action and competing explanations:** Prioritize reliable reporting and service review for these segments. A UK-based business and wholesale ordering can explain concentration; this does not establish segment profitability or attractive acquisition spend. Product prices, units, catalogue breadth, and observation-window exposure all influence GBP ranks. Queries: [country](../analysis/queries/country_concentration.sql), [product](../analysis/queries/product_concentration.sql); [chart](../analysis/charts/concentration.svg), exact sorted CSVs in results.

## Customer identifiers constrain repeat-purchase and cohort analysis

**Question and denominator:** Among {summary['known_purchasing_customers']:,} identified customers with positive paid merchandise, **{summary['repeat_purchasing_customers']:,} ({percent(summary['repeat_customer_share'])})** have at least two distinct observed invoice IDs. Their repeat behavior uses known identities only; unidentified buyers do not become new customers. Accepted-line ID coverage is **{percent(summary['accepted_known_customer_share'])}** ({summary['known_customer_rows']:,}/{summary['accepted_rows']:,}); raw missing IDs are reported separately by sheet in [missingness.csv](../analysis/results/missingness.csv).

For first-observed purchase cohorts with an eligible next month through November 2011, the pooled month 1 recorded return rate is **{percent(summary['pooled_month1_return_rate'])}**. The cohort denominator counts each cohort's known first-observed buyers; repeated monthly activity is binary per customer. Late cohorts' immature cells remain blank. Full-month bounds do not prove source-day completeness, and no recorded return is not proof that a customer bought nothing. First observation is left-truncated, so do not call it acquisition or infer lifetime value.

**Action and competing explanations:** Improve ID capture and confirm identity stability before retention targeting. The known-ID subset may differ systematically from unidentified transactions, and cohorts have unequal follow-up. Queries: [repeat purchasing](../analysis/queries/repeat_purchases.sql), [cohorts](../analysis/queries/cohort_retention.sql), [missingness](../analysis/queries/missingness.sql). Figures: [purchase bands](../analysis/charts/repeat_purchases.svg), [cohort heatmap](../analysis/charts/cohort_retention.svg).

## Negative adjustments and extreme lines merit investigation

**Question and denominator:** Absolute negative merchandise GBP is **{percent(summary['adjustment_to_gross_ratio'])}** of positive merchandise GBP over the whole workbook. This is an adjustment-to-gross ratio, not a settled-refund rate or percent of orders canceled. Monthly ratios include an explicitly marked partial final month and overlap month. [Query](../analysis/queries/adjustments.sql), [monthly chart](../analysis/charts/adjustment_patterns.svg).

The largest absolute accepted line is {gbp(summary['largest_absolute_line']['line_amount'])}, quantity {summary['largest_absolute_line']['quantity']:,}, invoice `{summary['largest_absolute_line']['invoice']}`, source `{summary['largest_absolute_line']['source_sheet']}` row{summary['largest_absolute_line']['source_row']:,}. [Top 20 exact source coordinates](../analysis/results/extreme_lines.csv) come from [extreme_lines.sql](../analysis/queries/extreme_lines.sql). Preserve and inspect these records rather than deleting valid extremes by percentile. Wholesale bulk orders, cancellation timing, or data-entry errors are competing explanations; inventory, original order, and settlement evidence is needed to resolve them.

## Adoption steps and remaining evidence

1. Reconcile overlapping exports with immutable client line keys and confirm merchandise-code/rounding policies.
2. Validate source coverage and customer-ID capture before treating trend or retention changes as commercial behavior.
3. Use segment concentration and exception queues to prioritize investigation; compare forecasting policies only after chronological evaluation is executed.
4. Obtain inventory availability, cost, promotions, payment settlement, and operational ownership before inventory or profit decisions. No realized client savings are claimed.

Analysis reproducibility is recorded in [report.json](../analysis/report.json): snapshot/manifest hash, source/converted checksums, query hashes, seed, software versions, command, elapsed timings, and verification assertions. Source accuracy and economic uniqueness remain open even when software checks pass.
"""


def run(snapshot_id=None):
    started = time.perf_counter()
    if snapshot_id is None:
        pointer = DATA / "serving" / "full.json"
        if not pointer.exists():
            raise RuntimeError("A validated full historical snapshot is required; demo EDA is deliberately refused.")
        snapshot_id = read_json(pointer)["snapshot_id"]
    folder = DATA / "serving" / "snapshots" / snapshot_id
    manifest_path = folder / "manifest.json"
    manifest = read_json(manifest_path)
    assert manifest["profile"] == "full" and manifest["mode"] == "historical" and manifest["validation"] == "passed"
    source_manifest = read_json(DATA / "converted" / "manifest.json")
    assert manifest["source_sha256"] == source_manifest["source"]["workbook_sha256"]
    raw = DATA / "converted" / "historical.parquet"
    assert sha256(raw) == source_manifest["full_parquet_sha256"]
    for relative, expected in manifest["checksums"].items():
        if relative.startswith("fact_invoice_line/"):
            assert sha256(folder / relative) == expected
    results_dir = OUT / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute("SET threads=2")
    con.execute("SET memory_limit='384MB'")
    con.read_parquet(str(raw)).create_view("raw_source")
    con.read_parquet(str(folder / "fact_invoice_line" / "*.parquet")).create_view("fact_invoice_line")
    data, timings, query_hashes = {}, {}, {}
    try:
        for path in sorted(QUERIES.glob("*.sql")):
            print(f"EDA query: {path.stem}", flush=True)
            query_start = time.perf_counter()
            data[path.stem] = records(con, path.read_text(encoding="utf-8"))
            timings[path.stem] = round(time.perf_counter()-query_start, 4)
            query_hashes[path.name] = sha256(path)
            save_csv(results_dir / f"{path.stem}.csv", data[path.stem])
        totals = records(con, """SELECT count(*) AS accepted_rows,count(DISTINCT line_id) AS unique_lines,
          count(*) FILTER (WHERE customer_id IS NOT NULL) AS known_customer_rows,
          sum(CASE WHEN line_amount>0 THEN line_amount ELSE 0 END) AS gross_positive_gbp,
          sum(CASE WHEN line_amount<0 THEN line_amount ELSE 0 END) AS negative_adjustments_gbp,
          sum(line_amount) AS net_recorded_gbp FROM fact_invoice_line""")[0]
    finally:
        con.close()
    assert totals["accepted_rows"] == totals["unique_lines"] == manifest["accepted_rows"]
    for key in ["gross_positive_gbp", "negative_adjustments_gbp", "net_recorded_gbp"]:
        assert totals[key] == Decimal(str(manifest["metrics"][key]))
    assert sum(r["original_rows"] for r in data["missingness"]) == manifest["source_rows"]
    assert int(data["source_overlap"][0]["original_rows"]) == manifest["source_rows"]
    assert sum(r["gross_positive_gbp"] for r in data["country_concentration"]) == totals["gross_positive_gbp"]
    assert sum(r["gross_positive_gbp"] for r in data["product_concentration"]) == totals["gross_positive_gbp"]
    comparable = {}
    for year in (2010, 2011):
        months = [row for row in data["monthly_sales"] if row["year"] == year]
        assert len(months) == 11
        comparable[str(year)] = {"net_gbp": sum(row["net_recorded_gbp"] for row in months),
                                 "gross_positive_gbp": sum(row["gross_positive_gbp"] for row in months),
                                 "positive_units": sum(row["positive_units"] for row in months),
                                 "calendar_days": 334,
                                 "observed_accepted_days": sum(row["dates_with_accepted_records"] for row in months)}
    comparable["net_change_gbp"] = comparable["2011"]["net_gbp"]-comparable["2010"]["net_gbp"]
    comparable["net_change_ratio"] = comparable["net_change_gbp"]/comparable["2010"]["net_gbp"]
    for segment_type in ("country", "product"):
        assert sum(r["change_gbp"] for r in data["growth_contributions"] if r["segment_type"] == segment_type) == comparable["net_change_gbp"]
    purchasing = sum(r["known_customers"] for r in data["repeat_purchases"])
    repeat = sum(r["known_customers"] for r in data["repeat_purchases"] if r["purchase_band"] != "1 invoice")
    month1 = [r for r in data["cohort_retention"] if r["month_index"] == 1 and r["within_calendar_bounds"]]
    boot = moving_block_comparison(data["daily_comparison"])
    summary = {"snapshot_id": snapshot_id, "source_sha256": manifest["source_sha256"],
               "source_rows": manifest["source_rows"], "accepted_rows": totals["accepted_rows"],
               "known_customer_rows": totals["known_customer_rows"],
               "metrics": {k: totals[k] for k in ("gross_positive_gbp", "negative_adjustments_gbp", "net_recorded_gbp")},
               "comparable_periods": comparable, "source_overlap": data["source_overlap"][0],
               "leading_country": data["country_concentration"][0],
               "top20_product_gross_share": sum(number(r["gross_share"]) for r in data["product_concentration"][:20]),
               "largest_country_change": next(r for r in data["growth_contributions"] if r["segment_type"] == "country"),
               "known_purchasing_customers": purchasing, "repeat_purchasing_customers": repeat,
               "repeat_customer_share": repeat/purchasing,
               "accepted_known_customer_share": totals["known_customer_rows"]/totals["accepted_rows"],
               "pooled_month1_return_rate": sum(r["active_customers"] for r in month1)/sum(r["cohort_size"] for r in month1),
               "adjustment_to_gross_ratio": -totals["negative_adjustments_gbp"]/totals["gross_positive_gbp"],
               "largest_absolute_line": data["extreme_lines"][0], "bootstrap": boot}
    charts = create_figures(data, summary, OUT / "charts")
    atomic_json(OUT / "bootstrap.json", boot)
    report = {"state": "passed", "analysis_version": "full-eda-v1", "executed_at_utc": datetime.now(timezone.utc).isoformat(),
              "snapshot_id": snapshot_id, "snapshot_manifest_sha256": sha256(manifest_path),
              "source_sha256": manifest["source_sha256"], "converted_source_sha256": sha256(raw),
              "exact_command_argv": [sys.executable, *sys.argv], "seed": SEED,
              "python": platform.python_version(), "duckdb": duckdb.__version__, "numpy": np.__version__,
              "git_sha": subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip(),
              "working_tree_status": subprocess.run(["git", "status", "--short"], cwd=ROOT, capture_output=True, text=True).stdout,
              "query_sha256": query_hashes, "query_durations_seconds": timings,
              "duration_seconds": round(time.perf_counter()-started, 4), "summary": summary,
              "charts": charts, "result_rows": {name: len(rows) for name, rows in data.items()},
              "checks": ["full historical validated snapshot only", "source and fact checksums match",
                         "unique accepted identities equal manifest", "all three decimal KPIs independently match manifest",
                         "original raw counts equal source rows", "country and product gross sums match fact",
                         "11 comparable months per year", "country and product changes each sum to total change"],
              "limitations": ["As-provided original coordinates retained; economic uniqueness unresolved.",
                              "Missing source dates are unavailable, not observed zero sales.",
                              "Known-customer cohort selection and left truncation limit generalization.",
                              "Bootstrap is descriptive sensitivity, not financial accuracy or causality."]}
    atomic_json(OUT / "report.json", report)
    (ROOT / "docs" / "findings.md").write_text(findings_markdown(summary, snapshot_id), encoding="utf-8")
    print(json.dumps({"state": "passed", "snapshot_id": snapshot_id, "duration_seconds": report["duration_seconds"],
                      "net_recorded_gbp": str(totals["net_recorded_gbp"]), "query_count": len(data)}))
    return report


def notebook(report):
    """Build, execute top-to-bottom, and save a genuine nbformat4 notebook."""
    import nbformat
    from nbclient import NotebookClient
    from nbformat.v4 import new_code_cell as code, new_markdown_cell as md
    from jupyter_client.kernelspec import KernelSpecManager
    sid = report["snapshot_id"]
    nb = nbformat.v4.new_notebook()
    nb.metadata.update(kernelspec={"display_name": "Python 3 (MERIDIAN)", "language": "python", "name": "python3"},
                       language_info={"name": "python", "version": platform.python_version()})
    nb.cells = [
        md(f"# MERIDIAN: recorded retail sales and source reliability\n\n## tl;dr\n\n"
           f"Executed historical snapshot `{sid}`. This portfolio case study retains original workbook coordinates. "
           "Cross-sheet identical payloads remain an explicit accounting-reconciliation gap. See the saved results below; "
           "the notebook generates them from the immutable full Gold export rather than a demo slice."),
        md("## Context & Methods\n\nAudience: a retail client and technical reviewer. Questions cover recorded merchandise value, comparable months, concentration, known-customer return purchasing, adjustments, and source quality.\n\n"
           "### Key Assumptions\n\nGBP decimal line amounts follow `docs/metrics.md`. All original coordinates remain distinct. "
           "January–November 2010/2011 are comparable calendar intervals; December 2010 overlap and partial December 2011 are excluded from that comparison. "
           "Unknown customer IDs remain in sales totals but not customer cohorts. First observed paid purchase is not acquisition. "
           "Missing calendar dates remain unavailable; no zero-sales assumption is made. The 7-observed-day block bootstrap is descriptive sensitivity with seed 20261116, 1,000 repetitions."),
        md("### Setup and pinned input\n\nUse the project Python 3.11 environment with the locked `analysis` dependencies. Run from the repository root. "
           "The execution checks both source and fact checksums and refuses a demo or synthetic snapshot."),
        code("import json, sys\nfrom pathlib import Path\nROOT = Path.cwd()\nif ROOT.name == 'notebooks': ROOT = ROOT.parent\nsys.path.insert(0, str(ROOT/'src'))\n"
             "from meridian.analysis import run\nfrom IPython.display import display, Markdown, SVG\n"
             f"SNAPSHOT_ID = {sid!r}\nassert sys.version_info[:2] == (3,11)\nprint(sys.version)\nprint(sys.executable)\nreport = run(snapshot_id=SNAPSHOT_ID)\nsummary = report['summary']"),
        md("## Data\n\n[UCI Online Retail II](https://archive.ics.uci.edu/dataset/502/online+retail+ii), Daqing Chen, DOI 10.24432/C5CG6D, CC BY 4.0. "
           f"Snapshot: `data/serving/snapshots/{sid}/manifest.json`. Exact queries are versioned in `analysis/queries/`; "
           "outputs and query hashes in `analysis/results/` and `analysis/report.json`. Raw-source diagnostics read only the pinned converted workbook."),
        code("import pandas as pd\ndef table(name, limit=12):\n    display(pd.read_csv(ROOT/'analysis'/'results'/f'{name}.csv').head(limit))\n"
             "table('source_overlap')\ntable('source_overlap_dates')\ntable('missingness')"),
        md("## Results\n\n### Recorded merchandise bridge\n\nAccepted merchandise includes missing-customer lines. These are recorded invoice amounts, not settled cash or audited revenue."),
        code("display(pd.DataFrame([summary['metrics']]))\ndisplay(SVG(filename=str(ROOT/'analysis/charts/revenue_bridge.svg')))"),
        md("### Comparable periods and arithmetic contributions\n\nTwo years support a descriptive seasonal comparison. The decomposition answers where recorded change occurs, not what caused it."),
        code("table('monthly_sales',22)\ndisplay(SVG(filename=str(ROOT/'analysis/charts/monthly_sales.svg')))\ndisplay(SVG(filename=str(ROOT/'analysis/charts/country_change.svg')))"),
        md("### Temporal sensitivity\n\nResampling uses contiguous blocks from each ordered observed-day series. "
           "It does not fill missing dates or establish financial accuracy, significance, or causation."),
        code("bootstrap = json.loads((ROOT/'analysis/bootstrap.json').read_text())\n"
             "display({key: value for key,value in bootstrap.items() if key != 'draws_gbp'})\n"
             "display(SVG(filename=str(ROOT/'analysis/charts/bootstrap_sensitivity.svg')))"),
        md("### Segment concentration\n\nThe denominator is all positive accepted merchandise GBP. Country and product decompositions overlap and must not be added."),
        code("table('country_concentration',8)\ntable('product_concentration',8)\ndisplay(SVG(filename=str(ROOT/'analysis/charts/concentration.svg')))"),
        md("### Identified-customer repeat purchasing and cohorts\n\nEligibility requires known customer ID, positive quantity, and positive paid line amount. "
           "One customer's monthly activity is binary; blank cells are immature months. Source gaps can hide activity, and cohort 0 is mechanically 100%."),
        code("table('repeat_purchases')\ndisplay(SVG(filename=str(ROOT/'analysis/charts/repeat_purchases.svg')))\n"
             "display(SVG(filename=str(ROOT/'analysis/charts/cohort_retention.svg')))"),
        md("### Adjustments and extremes\n\nInvestigate extreme source lines; do not delete them because they are large. Negative adjustments are not automatically linked to original orders."),
        code("display(SVG(filename=str(ROOT/'analysis/charts/adjustment_patterns.svg')))\ntable('extreme_lines',10)"),
        md("## Takeaways\n\nReconcile the overlapping export period with source-system line IDs, confirm business rules and coverage, "
           "and prioritize known segment concentration and quality exceptions for investigation. "
           "Customer retention has known-ID selection and left-truncation limits. Inventory, costs, promotion exposure and settlements are absent; "
           "no profit, causal marketing effect or realized savings is claimed. The client-readable interpretation is `docs/findings.md`."),
        code("assert report['state']=='passed'\nassert len(report['checks'])>=8\n"
             "print('Passed checks:')\nprint('\\n'.join(report['checks']))\nprint('Snapshot:',report['snapshot_id'])"),
    ]
    nbformat.validate(nb)
    destination = ROOT / "notebooks" / "eda.ipynb"
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="meridian-notebook-") as kernel_root:
        kernel_path = Path(kernel_root) / "python3"
        kernel_path.mkdir()
        (kernel_path / "kernel.json").write_text(json.dumps({
            "argv": [sys.executable, "-m", "ipykernel_launcher", "-f", "{connection_file}"],
            "display_name": "MERIDIAN pinned Python", "language": "python"}), encoding="utf-8")
        manager = KernelSpecManager(kernel_dirs=[kernel_root], ensure_native_kernel=False)
        client = NotebookClient(nb, timeout=300, kernel_name="python3", kernel_spec_manager=manager,
                                resources={"metadata": {"path": str(ROOT)}})
        executed = client.execute()
    nbformat.validate(executed)
    assert all(cell.get("execution_count") is not None for cell in executed.cells if cell.cell_type=="code")
    assert not any(output.output_type=="error" for cell in executed.cells if cell.cell_type=="code" for output in cell.outputs)
    nbformat.write(executed, destination)
    # Simple standalone preview retains the notebook's executed outputs without another export dependency.
    blocks = ["<!doctype html><html><meta charset='utf-8'><title>MERIDIAN executed EDA</title>"
              "<style>body{font:16px system-ui;max-width:1120px;margin:32px auto;padding:0 24px;color:#172b4d}pre{white-space:pre-wrap;background:#f2f5f8;padding:16px}svg{max-width:100%;height:auto}table{border-collapse:collapse;font-size:12px}td,th{padding:6px;border-bottom:1px solid #ddd}</style>"]
    for cell in executed.cells:
        if cell.cell_type=="markdown":
            blocks.append("<section><pre>"+html.escape(cell.source)+"</pre></section>")
        else:
            blocks.append("<details><summary>Executed code</summary><pre>"+html.escape(cell.source)+"</pre></details>")
            for output in cell.outputs:
                if output.output_type=="stream":
                    blocks.append("<pre>"+html.escape(output.text)+"</pre>")
                elif output.output_type in ("display_data", "execute_result"):
                    values = output.data
                    if "image/svg+xml" in values:
                        blocks.append(values["image/svg+xml"])
                    elif "text/html" in values:
                        blocks.append(values["text/html"])
                    elif "text/plain" in values:
                        blocks.append("<pre>"+html.escape(values["text/plain"])+"</pre>")
    blocks.append("</html>")
    (OUT / "notebook-preview.html").write_text("\n".join(blocks), encoding="utf-8")
    atomic_json(OUT / "notebook-execution.json", {"state": "passed", "snapshot_id": sid,
                "executed_at_utc": datetime.now(timezone.utc).isoformat(),
                "notebook_sha256": sha256(destination),
                "code_cells_executed": sum(cell.cell_type=="code" for cell in executed.cells),
                "errors": 0, "presentation_inspection": "pending"})
    print("Executed and saved notebooks/eda.ipynb; visual inspection remains a separate check.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot")
    parser.add_argument("--notebook", action="store_true")
    args = parser.parse_args()
    result = run(args.snapshot)
    if args.notebook:
        notebook(result)


if __name__ == "__main__":
    main()
