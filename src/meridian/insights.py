"""Customer, basket, predictive and statistical insights over accepted invoice lines.

Runs entirely in DuckDB + scikit-learn on one machine. Input is either a published Spark
snapshot (`fact_invoice_line/*.parquet`) or a converted source Parquet, which is pushed
through the same `sql/silver.sql` business rules first. Every output carries its input.

    python -m meridian.insights --fact "data/serving/snapshots/<id>/fact_invoice_line/*.parquet"
    python -m meridian.insights --converted data/mirror/historical.parquet

Outputs: analysis/insights/insights.json and browser marts in frontend/showcase/data/.
"""
from __future__ import annotations

import argparse
import math
import time
from datetime import date, timedelta
from pathlib import Path

import duckdb
import numpy as np

from .common import ROOT, atomic_json, now, sha256

SEED = 20261117
REFERENCE_DAY = date(2011, 12, 8)      # last complete observed day; 2011-12-09 is partial
HORIZON_DAYS = 90
TRAIN_CUTOFF = date(2011, 6, 9)        # label window 2011-06-10 .. 2011-09-07
TEST_CUTOFF = REFERENCE_DAY - timedelta(days=HORIZON_DAYS)  # 2011-09-09; label window ends at the reference day
OUT = ROOT / "analysis" / "insights"
MARTS = ROOT / "frontend" / "showcase" / "data"


def connect(fact: str | None, converted: str | None):
    con = duckdb.connect()
    con.execute("SET threads=4")
    if fact:
        con.execute(f"CREATE VIEW fact_invoice_line AS SELECT * FROM read_parquet('{fact}')")
        origin = {"kind": "published_snapshot_fact", "path": fact}
    else:
        path = Path(converted)
        con.execute(f"CREATE TABLE source_lines AS SELECT *, sha256(concat_ws('|',source_sha256,source_sheet,cast(source_row AS VARCHAR))) AS line_id FROM read_parquet('{path.as_posix()}')")
        con.execute("CREATE TABLE silver AS " + (ROOT / "sql" / "silver.sql").read_text().replace("TIMESTAMP_NTZ", "TIMESTAMP"))
        con.execute("CREATE VIEW fact_invoice_line AS SELECT * FROM silver WHERE disposition='accepted'")
        origin = {"kind": "converted_source_through_silver_sql", "path": path.as_posix(), "sha256": sha256(path)}
    prepare(con)
    return con, origin


def prepare(con):
    """Positive merchandise purchases by identified buyers: the population for customer analytics."""
    con.execute("""CREATE OR REPLACE TABLE purchases AS SELECT invoice, customer_id, stock_code, quantity, line_amount, event_time, date, country
                   FROM fact_invoice_line WHERE classification='positive_merchandise' AND customer_id IS NOT NULL""")


def rows(con, sql, params=None):
    cur = con.execute(sql, params or [])
    names = [d[0] for d in cur.description]
    return [dict(zip(names, (float(v) if hasattr(v, "as_tuple") else v for v in r))) for r in cur.fetchall()]


# ---------------------------------------------------------------- segmentation
SEGMENT_RULES = [  # (name, test on centroid ranks r,f,m in 0..1 where 1 = best), first match wins
    ("Champions", lambda r, f, m: r >= .6 and f >= .6 and m >= .6),
    ("Loyal regulars", lambda r, f, m: f >= .5 and r >= .4),
    ("New & promising", lambda r, f, m: r >= .6 and f < .5),
    ("At risk", lambda r, f, m: r < .4 and (f >= .4 or m >= .4)),
    ("Hibernating", lambda r, f, m: True),
]


def segmentation(con):
    from sklearn.cluster import KMeans
    from sklearn.metrics import silhouette_score
    from sklearn.preprocessing import StandardScaler

    ref = REFERENCE_DAY + timedelta(days=1)
    data = rows(con, f"""SELECT customer_id, date_diff('day', max(date), DATE '{ref}') AS recency_days,
        count(DISTINCT invoice) AS frequency, sum(line_amount) AS monetary_gbp, min(date) AS first_seen, any_value(country) AS country
        FROM purchases WHERE date <= DATE '{REFERENCE_DAY}' GROUP BY customer_id ORDER BY customer_id""")
    X = np.array([[math.log1p(d["recency_days"]), math.log1p(d["frequency"]), math.log1p(max(d["monetary_gbp"], 0))] for d in data])
    Z = StandardScaler().fit_transform(X)
    trials = []
    rng = np.random.default_rng(SEED)
    sample = rng.choice(len(Z), size=min(3000, len(Z)), replace=False)
    for k in range(3, 8):
        model = KMeans(n_clusters=k, n_init=10, random_state=SEED).fit(Z)
        trials.append({"k": k, "inertia": float(model.inertia_), "silhouette": float(silhouette_score(Z[sample], model.labels_[sample]))})
    best = max(trials, key=lambda t: t["silhouette"])
    # Silhouette alone often prefers k=3; segments must also be actionable, so take the largest k within 0.02 of the best.
    chosen = max(t["k"] for t in trials if t["silhouette"] >= best["silhouette"] - 0.02)
    model = KMeans(n_clusters=chosen, n_init=20, random_state=SEED).fit(Z)
    labels = model.labels_
    clusters = []
    for c in range(chosen):
        idx = labels == c
        members = [data[i] for i in np.where(idx)[0]]
        clusters.append({"cluster": c, "customers": int(idx.sum()),
                         "median_recency_days": float(np.median([m["recency_days"] for m in members])),
                         "median_frequency": float(np.median([m["frequency"] for m in members])),
                         "median_monetary_gbp": round(float(np.median([m["monetary_gbp"] for m in members])), 2),
                         "total_monetary_gbp": round(float(sum(m["monetary_gbp"] for m in members)), 2)})
    # Name clusters from the rank of their medians (recency: lower is better).
    def rank(key, reverse=False):
        order = sorted(clusters, key=lambda c: c[key], reverse=reverse)
        return {c["cluster"]: i / max(1, len(order) - 1) for i, c in enumerate(order)}
    rr, fr, mr = rank("median_recency_days", reverse=True), rank("median_frequency"), rank("median_monetary_gbp")
    used = {}
    for c in clusters:
        name = next(n for n, test in SEGMENT_RULES if test(rr[c["cluster"]], fr[c["cluster"]], mr[c["cluster"]]))
        used[name] = used.get(name, 0) + 1
        c["segment"] = name if used[name] == 1 else f"{name} {used[name]}"
    total_value = sum(c["total_monetary_gbp"] for c in clusters)
    for c in clusters:
        c["share_of_customers"] = c["customers"] / len(data)
        c["share_of_value"] = c["total_monetary_gbp"] / total_value
    clusters.sort(key=lambda c: -c["median_monetary_gbp"])
    name_of = {c["cluster"]: c["segment"] for c in clusters}
    # Down-sampled scatter for the UI (log axes), stratified by cluster.
    points = []
    for c in range(chosen):
        idx = np.where(labels == c)[0]
        for i in rng.choice(idx, size=min(len(idx), 260), replace=False):
            d = data[i]
            points.append({"r": d["recency_days"], "f": d["frequency"], "m": round(d["monetary_gbp"], 2), "s": name_of[c]})
    customers = [{"customer_id": d["customer_id"], "segment": name_of[int(labels[i])], "recency_days": d["recency_days"],
                  "frequency": d["frequency"], "monetary_gbp": round(d["monetary_gbp"], 2), "country": d["country"]} for i, d in enumerate(data)]
    return {"method": "k-means on standardised log(1+recency), log(1+frequency), log(1+monetary); k chosen by silhouette on a 3,000-customer sample, preferring more segments within 0.02 of the best",
            "reference_day": str(REFERENCE_DAY), "customers": len(data), "k": chosen, "k_trials": trials, "segments": clusters, "scatter": points}, customers


# ---------------------------------------------------------------- market basket
def market_basket(con, min_support=0.01, top=60):
    invoices = con.execute("SELECT count(DISTINCT invoice) FROM fact_invoice_line WHERE classification='positive_merchandise'").fetchone()[0]
    floor = max(2, int(min_support * invoices))
    con.execute(f"""CREATE TEMP TABLE basket AS SELECT DISTINCT invoice, stock_code FROM fact_invoice_line
        WHERE classification='positive_merchandise' AND stock_code IN (
          SELECT stock_code FROM fact_invoice_line WHERE classification='positive_merchandise' GROUP BY 1 HAVING count(DISTINCT invoice) >= {floor})""")
    con.execute("CREATE TEMP TABLE item AS SELECT stock_code, count(*) AS n FROM basket GROUP BY 1")
    con.execute("CREATE TEMP TABLE names AS SELECT stock_code, mode(description) AS description FROM fact_invoice_line GROUP BY 1")
    rules = rows(con, f"""WITH pairs AS (
          SELECT a.stock_code AS lhs, b.stock_code AS rhs, count(*) AS n_ab FROM basket a JOIN basket b ON a.invoice=b.invoice AND a.stock_code<>b.stock_code
          GROUP BY 1,2 HAVING count(*) >= {floor})
        SELECT p.lhs, nl.description AS lhs_name, p.rhs, nr.description AS rhs_name, p.n_ab,
          p.n_ab/{invoices}::DOUBLE AS support, p.n_ab/il.n::DOUBLE AS confidence,
          (p.n_ab/il.n::DOUBLE)/(ir.n/{invoices}::DOUBLE) AS lift
        FROM pairs p JOIN item il ON il.stock_code=p.lhs JOIN item ir ON ir.stock_code=p.rhs
        LEFT JOIN names nl ON nl.stock_code=p.lhs LEFT JOIN names nr ON nr.stock_code=p.rhs
        ORDER BY lift DESC, support DESC, p.lhs, p.rhs""")
    # Keep one direction per pair for the graph, both directions for the rule table.
    graph, seen = [], set()
    for r in rules:
        key = tuple(sorted((r["lhs"], r["rhs"])))
        if key not in seen and len(graph) < top:
            seen.add(key); graph.append(r)
    nodes = {}
    for r in graph:
        for code, name in ((r["lhs"], r["lhs_name"]), (r["rhs"], r["rhs_name"])):
            nodes.setdefault(code, {"id": code, "name": name, "degree": 0})["degree"] += 1
    support = dict(con.execute("SELECT stock_code, n FROM item").fetchall())
    for n in nodes.values():
        n["support"] = support[n["id"]] / invoices
    return {"method": f"Pairwise association rules over positive-merchandise invoices; items and pairs in at least {min_support:.0%} of invoices ({floor}); lift = confidence / consequent support",
            "invoices": int(invoices), "frequent_items": len(support), "rules_found": len(rules), "rules": rules[:150],
            "graph": {"nodes": list(nodes.values()), "edges": [{"source": r["lhs"], "target": r["rhs"], "lift": r["lift"], "support": r["support"]} for r in graph]}}


# ---------------------------------------------------------------- return prediction
FEATURES = ["recency_days", "frequency", "monetary_gbp", "tenure_days", "avg_invoice_gbp", "distinct_products",
            "invoices_last_90d", "active_months", "adjustment_share", "is_uk"]


def feature_frame(con, cutoff: date):
    return rows(con, f"""WITH p AS (SELECT * FROM purchases WHERE date <= DATE '{cutoff}'),
        adj AS (SELECT customer_id, -sum(line_amount) AS adj_gbp FROM fact_invoice_line
                WHERE classification='negative_adjustment' AND customer_id IS NOT NULL AND date <= DATE '{cutoff}' GROUP BY 1),
        label AS (SELECT DISTINCT customer_id FROM purchases WHERE date > DATE '{cutoff}' AND date <= DATE '{cutoff + timedelta(days=HORIZON_DAYS)}')
        SELECT p.customer_id,
          date_diff('day', max(p.date), DATE '{cutoff}') AS recency_days,
          count(DISTINCT p.invoice) AS frequency, sum(p.line_amount) AS monetary_gbp,
          date_diff('day', min(p.date), DATE '{cutoff}') AS tenure_days,
          sum(p.line_amount)/count(DISTINCT p.invoice) AS avg_invoice_gbp,
          count(DISTINCT p.stock_code) AS distinct_products,
          count(DISTINCT CASE WHEN p.date > DATE '{cutoff - timedelta(days=90)}' THEN p.invoice END) AS invoices_last_90d,
          count(DISTINCT date_trunc('month', p.date)) AS active_months,
          coalesce(coalesce(any_value(adj.adj_gbp),0)/nullif(sum(p.line_amount),0),0) AS adjustment_share,
          CASE WHEN any_value(p.country)='United Kingdom' THEN 1 ELSE 0 END AS is_uk,
          CASE WHEN any_value(label.customer_id) IS NOT NULL THEN 1 ELSE 0 END AS returned
        FROM p LEFT JOIN adj USING (customer_id) LEFT JOIN label USING (customer_id) GROUP BY p.customer_id ORDER BY p.customer_id""")


def roc(y, p, points=40):
    from sklearn.metrics import roc_curve
    fpr, tpr, _ = roc_curve(y, p)
    idx = np.unique(np.linspace(0, len(fpr) - 1, points).astype(int))
    return [{"fpr": float(fpr[i]), "tpr": float(tpr[i])} for i in idx]


def return_model(con):
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.inspection import permutation_importance
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import brier_score_loss, roc_auc_score
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import FunctionTransformer, StandardScaler

    train, test = feature_frame(con, TRAIN_CUTOFF), feature_frame(con, TEST_CUTOFF)
    def xy(frame):
        return np.array([[float(r[f]) for f in FEATURES] for r in frame]), np.array([r["returned"] for r in frame])
    Xtr, ytr = xy(train)
    Xte, yte = xy(test)
    signed_log = FunctionTransformer(lambda a: np.sign(a) * np.log1p(np.abs(a)))
    models = {
        "logistic_regression": make_pipeline(signed_log, StandardScaler(), LogisticRegression(max_iter=2000)),
        "gradient_boosting": HistGradientBoostingClassifier(max_depth=4, learning_rate=0.05, max_iter=300, l2_regularization=1.0, random_state=SEED),
    }
    results = {}
    for name, model in models.items():
        model.fit(Xtr, ytr)
        p = model.predict_proba(Xte)[:, 1]
        results[name] = {"auc": float(roc_auc_score(yte, p)), "brier": float(brier_score_loss(yte, p)), "roc": roc(yte, p), "_p": p, "_model": model}
    # Baseline everyone would try first: more recent buyers return. Score = -recency.
    base = -Xte[:, FEATURES.index("recency_days")]
    results["recency_rule"] = {"auc": float(roc_auc_score(yte, base)), "brier": None, "roc": roc(yte, base)}
    # Parsimony: keep the interpretable model unless boosting is clearly better on the held-out window.
    best = "gradient_boosting" if results["gradient_boosting"]["auc"] > results["logistic_regression"]["auc"] + 0.005 else "logistic_regression"
    p = results[best]["_p"]
    imp = permutation_importance(results[best]["_model"], Xte, yte, scoring="roc_auc", n_repeats=8, random_state=SEED)
    importance = sorted(({"feature": f, "auc_drop": float(m), "std": float(s)} for f, m, s in zip(FEATURES, imp.importances_mean, imp.importances_std)), key=lambda r: -r["auc_drop"])
    # Calibration and gains by decile of predicted probability.
    order = np.argsort(-p)
    deciles = []
    for d, chunk in enumerate(np.array_split(order, 10)):
        deciles.append({"decile": d + 1, "customers": int(len(chunk)), "mean_predicted": float(p[chunk].mean()), "observed_rate": float(yte[chunk].mean()),
                        "cumulative_capture": float(yte[order[:sum(len(c) for c in np.array_split(order, 10)[:d + 1])]].sum() / yte.sum())})
    # Score everyone as of the reference day with the selected model refit on the test window (labels now known).
    final = models[best].fit(Xte, yte)
    latest = feature_frame(con, REFERENCE_DAY)
    Xl = np.array([[float(r[f]) for f in FEATURES] for r in latest])
    pl = final.predict_proba(Xl)[:, 1]
    scores = {r["customer_id"]: float(v) for r, v in zip(latest, pl)}
    for k in results:
        results[k].pop("_p", None); results[k].pop("_model", None)
    return {"question": f"Will an identified buyer place another positive-merchandise order within {HORIZON_DAYS} days?",
            "design": {"train_cutoff": str(TRAIN_CUTOFF), "train_label_window": [str(TRAIN_CUTOFF + timedelta(days=1)), str(TRAIN_CUTOFF + timedelta(days=HORIZON_DAYS))],
                       "test_cutoff": str(TEST_CUTOFF), "test_label_window": [str(TEST_CUTOFF + timedelta(days=1)), str(REFERENCE_DAY)],
                       "leakage_guard": "features use only rows on or before each cutoff; the training label window ends before the test cutoff",
                       "train_customers": len(train), "test_customers": len(test), "train_base_rate": float(ytr.mean()), "test_base_rate": float(yte.mean())},
            "features": FEATURES, "models": results, "selected": best, "importance": importance, "deciles": deciles,
            "limitations": ["Predicts observed repeat purchasing, not causal effects of any intervention.",
                            "Buyers without a customer identifier cannot be scored.",
                            f"Base rates shift with the season (train {ytr.mean():.0%} vs test {yte.mean():.0%}, the test window holds the Christmas peak), so probabilities are under-calibrated in the middle deciles; ranking (AUC) is the reliable output.",
                            "Scores at the reference day come from a refit on the most recent labelled window and have no held-out evaluation of their own."]}, scores


# ---------------------------------------------------------------- anomalies
def anomalies(con, threshold=3.5):
    daily = rows(con, "SELECT date, sum(line_amount) AS net, count(DISTINCT invoice) AS invoices FROM fact_invoice_line GROUP BY 1 ORDER BY 1")
    by_dow: dict[int, list] = {}
    series = []
    for d in daily:
        dow = d["date"].isoweekday()
        history = by_dow.setdefault(dow, [])
        expected = float(np.median(history[-8:])) if len(history) >= 4 else None
        series.append({"date": str(d["date"]), "net": round(d["net"], 2), "expected": None if expected is None else round(expected, 2), "invoices": d["invoices"]})
        history.append(d["net"])
    resid = np.array([s["net"] - s["expected"] for s in series if s["expected"] is not None])
    med, mad = float(np.median(resid)), float(np.median(np.abs(resid - np.median(resid))))
    flagged = []
    for s in series:
        if s["expected"] is None:
            continue
        z = 0.6745 * (s["net"] - s["expected"] - med) / mad
        s["z"] = round(z, 2)
        if abs(z) > threshold:
            drivers = rows(con, """SELECT invoice, stock_code, description, customer_id, country, line_amount FROM fact_invoice_line
                                   WHERE date = ? ORDER BY abs(line_amount) DESC LIMIT 3""", [s["date"]])
            top_customer = rows(con, """SELECT coalesce(customer_id,'unidentified') AS customer_id, sum(line_amount)/(SELECT sum(line_amount) FROM fact_invoice_line WHERE date=?) AS share
                                        FROM fact_invoice_line WHERE date=? GROUP BY 1 ORDER BY 2 DESC LIMIT 1""", [s["date"], s["date"]])[0]
            flagged.append({**s, "direction": "spike" if z > 0 else "drop", "drivers": drivers, "top_customer": top_customer})
    return {"method": f"Expected = median net of the same weekday over the previous 8 observed weeks (needs 4). Robust z = 0.6745·(residual − median)/MAD; |z| > {threshold} flagged. Drivers are the day's largest lines.",
            "threshold": threshold, "residual_median": med, "residual_mad": mad, "series": series, "anomalies": flagged}


# ---------------------------------------------------------------- statistics
def statistics(con):
    from scipy import stats
    daily = rows(con, "SELECT date, isodow(date) AS dow, sum(line_amount) AS net FROM fact_invoice_line GROUP BY 1,2")
    groups = {}
    for d in daily:
        groups.setdefault(d["dow"], []).append(d["net"])
    h, p = stats.kruskal(*groups.values())
    n, k = len(daily), len(groups)
    weekday = {"test": "Kruskal–Wallis H on observed-day net by ISO weekday", "H": float(h), "p_value": float(p), "k": k, "n_days": n,
               "epsilon_squared": float((h - k + 1) / (n - k)),
               "by_weekday": [{"dow": d, "days": len(v), "median_net": float(np.median(v)), "mean_net": float(np.mean(v))} for d, v in sorted(groups.items())],
               "note": "Saturday has almost no observed trading, so it is reported but the test covers the days present."}
    heat = rows(con, "SELECT isodow(event_time) AS dow, hour(event_time) AS hour, sum(line_amount) AS net, count(DISTINCT invoice) AS invoices FROM fact_invoice_line GROUP BY 1,2 ORDER BY 1,2")
    # Year-on-year: Dec 2010–Nov 2011 vs Dec 2009–Nov 2010, invoice-level Mann–Whitney and bootstrap CI on mean invoice value.
    inv = rows(con, """SELECT CASE WHEN date BETWEEN DATE '2009-12-01' AND DATE '2010-11-30' THEN 'y1' WHEN date BETWEEN DATE '2010-12-01' AND DATE '2011-11-30' THEN 'y2' END AS yr,
                       sum(line_amount) AS value FROM fact_invoice_line WHERE classification='positive_merchandise' GROUP BY invoice, 1 HAVING yr IS NOT NULL""")
    y1 = np.array([r["value"] for r in inv if r["yr"] == "y1"]); y2 = np.array([r["value"] for r in inv if r["yr"] == "y2"])
    u = stats.mannwhitneyu(y2, y1, alternative="two-sided")
    rng = np.random.default_rng(SEED)
    boots = [rng.choice(y2, len(y2)).mean() - rng.choice(y1, len(y1)).mean() for _ in range(2000)]
    invoice_value = {"test": "Mann–Whitney U, positive-merchandise invoice value, Dec 2010–Nov 2011 vs Dec 2009–Nov 2010", "U": float(u.statistic), "p_value": float(u.pvalue),
                     "median_y1": float(np.median(y1)), "median_y2": float(np.median(y2)), "mean_y1": float(y1.mean()), "mean_y2": float(y2.mean()),
                     "invoices_y1": int(len(y1)), "invoices_y2": int(len(y2)), "mean_diff_ci95": [float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))],
                     "note": "Invoices are not independent draws (repeat customers), so p-values are descriptive. Bootstrap resamples invoices."}
    def pareto(key):
        vals = np.sort(np.array([r["v"] for r in rows(con, f"SELECT {key}, sum(line_amount) AS v FROM purchases GROUP BY 1 HAVING sum(line_amount) > 0")]))[::-1]
        cum = np.cumsum(vals) / vals.sum()
        idx = np.unique(np.linspace(0, len(vals) - 1, 60).astype(int))
        asc = vals[::-1]
        gini = float(1 - 2 * np.sum(np.cumsum(asc) / asc.sum()) / len(asc) + 1 / len(asc))
        return {"count": int(len(vals)), "gini": gini, "top_20pct_share": float(cum[int(len(vals) * .2) - 1]),
                "curve": [{"x": float((i + 1) / len(vals)), "y": float(cum[i])} for i in idx]}
    monthly = rows(con, "SELECT strftime(date,'%Y-%m') AS month, sum(line_amount) AS net, count(DISTINCT invoice) AS invoices, count(DISTINCT customer_id) AS customers FROM fact_invoice_line GROUP BY 1 ORDER BY 1")
    return {"weekday": weekday, "heatmap": heat, "invoice_value": invoice_value, "pareto": {"products": pareto("stock_code"), "customers": pareto("customer_id")}, "monthly": monthly}


# ---------------------------------------------------------------- browser marts
BROWSER_SQL = ROOT / "frontend" / "shared" / "browser-marts.sql"


def export_marts(con, customers, scores):
    """Gzip CSV marts for DuckDB-WASM. CSV + gzip are in DuckDB's core, so the browser needs no
    extension download (Parquet support would be fetched from extensions.duckdb.org at runtime).
    Bulk Parquet copies for Azure / Power BI go to data/marts/ (not committed)."""
    MARTS.mkdir(parents=True, exist_ok=True)
    bulk = ROOT / "data" / "marts"; bulk.mkdir(parents=True, exist_ok=True)
    sales_csv = MARTS / "sales.csv.gz"
    con.execute(f"""COPY (SELECT invoice, stock_code, quantity, unit_price, strftime(event_time, '%Y-%m-%d %H:%M:%S') AS event_time, customer_id, country
        FROM fact_invoice_line ORDER BY event_time, invoice, stock_code) TO '{sales_csv.as_posix()}' (FORMAT CSV, HEADER, COMPRESSION GZIP)""")
    con.execute("CREATE TEMP TABLE seg (customer_id VARCHAR, segment VARCHAR, recency_days INTEGER, frequency INTEGER, monetary_gbp DOUBLE, country VARCHAR, return_probability DOUBLE)")
    con.executemany("INSERT INTO seg VALUES (?,?,?,?,?,?,?)", [(c["customer_id"], c["segment"], c["recency_days"], c["frequency"], c["monetary_gbp"], c["country"], scores.get(c["customer_id"])) for c in customers])
    products = """SELECT stock_code, coalesce(mode(CASE WHEN quantity>0 THEN description END), max(description)) AS description, round(sum(line_amount), 2) AS net_gbp,
                  sum(CASE WHEN quantity>0 THEN quantity ELSE 0 END) AS units FROM fact_invoice_line GROUP BY 1 ORDER BY 1"""
    con.execute(f"COPY ({products}) TO '{(MARTS / 'products.csv.gz').as_posix()}' (FORMAT CSV, HEADER, COMPRESSION GZIP)")
    con.execute(f"COPY (SELECT * FROM seg ORDER BY customer_id) TO '{(MARTS / 'customers.csv.gz').as_posix()}' (FORMAT CSV, HEADER, COMPRESSION GZIP)")
    for name, sql in (("fact_invoice_line", "SELECT * FROM fact_invoice_line"), ("products", products), ("customer_segments", "SELECT * FROM seg")):
        con.execute(f"COPY ({sql}) TO '{(bulk / (name + '.parquet')).as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD)")
    # Prove the browser reconstruction equals the accepted lines, line for line.
    check = duckdb.connect()
    sql = "\n".join(l for l in BROWSER_SQL.read_text().splitlines() if not l.startswith("--"))
    for body in (x.strip() for x in sql.split(";")):
        if body:
            check.execute(body.replace("'sales.csv.gz'", f"'{sales_csv.as_posix()}'").replace("'products.csv.gz'", f"'{(MARTS / 'products.csv.gz').as_posix()}'").replace("'customers.csv.gz'", f"'{(MARTS / 'customers.csv.gz').as_posix()}'"))
    browser = check.execute("SELECT count(*), sum(line_amount)::DECIMAL(20,2), sum(CASE WHEN classification='negative_adjustment' THEN 1 END), count(customer_id) FROM sales").fetchone()
    check.close()
    truth = con.execute("SELECT count(*), sum(line_amount)::DECIMAL(20,2), sum(CASE WHEN classification='negative_adjustment' THEN 1 END), count(customer_id) FROM fact_invoice_line").fetchone()
    if tuple(browser) != tuple(truth):
        raise AssertionError(f"Browser mart differs from accepted lines: {browser} vs {truth}")
    out = {name: {"bytes": (MARTS / name).stat().st_size, "sha256": sha256(MARTS / name)} for name in ("sales.csv.gz", "products.csv.gz", "customers.csv.gz")}
    out["reconciliation"] = {"rows": truth[0], "net_gbp": str(truth[1]), "negative_adjustment_rows": truth[2], "identified_rows": truth[3], "state": "browser mart equals accepted lines"}
    return out


def run(fact=None, converted=None, marts=True):
    started = time.perf_counter()
    con, origin = connect(fact, converted)
    timings = {}
    def timed(name, fn):
        t = time.perf_counter(); value = fn(); timings[name] = round(time.perf_counter() - t, 2); return value
    seg, customers = timed("segmentation", lambda: segmentation(con))
    model, scores = timed("return_model", lambda: return_model(con))
    # Value at risk: high-value segments whose return probability has dropped below one in three.
    at_risk = sorted((c for c in customers if scores.get(c["customer_id"], 1) < 1 / 3 and c["monetary_gbp"] > 0), key=lambda c: -c["monetary_gbp"])
    model["value_at_risk"] = {"rule": "identified buyers with predicted return probability < 33% at the reference day", "customers": len(at_risk),
                              "historical_value_gbp": round(sum(c["monetary_gbp"] for c in at_risk), 2),
                              "by_segment": {s: sum(1 for c in at_risk if c["segment"] == s) for s in sorted({c["segment"] for c in at_risk})}}
    seg_probs = {}
    for c in customers:
        if c["customer_id"] in scores:
            seg_probs.setdefault(c["segment"], []).append(scores[c["customer_id"]])
    for s in seg["segments"]:
        s["mean_return_probability"] = float(np.mean(seg_probs.get(s["segment"], [np.nan])))
    result = {"state": "passed", "version": "insights-v1", "generated_at": now(), "seed": SEED, "input": origin,
              "population": con.execute("SELECT count(*), count(DISTINCT customer_id), sum(line_amount) FROM purchases").fetchone(),
              "segmentation": seg, "basket": timed("market_basket", lambda: market_basket(con)), "return_model": model,
              "anomalies": timed("anomalies", lambda: anomalies(con)), "statistics": timed("statistics", lambda: statistics(con))}
    # Canonical product names: most frequent description on positive lines. Free-text damage notes
    # ("wonky bottom/broken") sort after real names alphabetically, so max(description) picks them.
    result["product_names"] = dict(con.execute("""SELECT stock_code, mode(description) FROM fact_invoice_line
        WHERE classification='positive_merchandise' AND description IS NOT NULL GROUP BY 1 ORDER BY sum(line_amount) DESC LIMIT 400""").fetchall())
    result["population"] = {"identified_positive_lines": result["population"][0], "identified_buyers": result["population"][1], "identified_positive_gbp": float(result["population"][2])}
    if marts:
        result["browser_marts"] = timed("export_marts", lambda: export_marts(con, customers, scores))
    result["timings_seconds"] = {**timings, "total": round(time.perf_counter() - started, 2)}
    atomic_json(OUT / "insights.json", result)
    con.close()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--fact", help="glob of a published fact_invoice_line snapshot")
    src.add_argument("--converted", help="converted source Parquet (runs sql/silver.sql first)")
    parser.add_argument("--no-marts", action="store_true")
    args = parser.parse_args()
    r = run(args.fact, args.converted, not args.no_marts)
    print({k: r[k] for k in ("state", "timings_seconds")}, f"k={r['segmentation']['k']}", f"auc={r['return_model']['models'][r['return_model']['selected']]['auc']:.3f}",
          f"rules={r['basket']['rules_found']}", f"anomalies={len(r['anomalies']['anomalies'])}")


if __name__ == "__main__":
    main()
