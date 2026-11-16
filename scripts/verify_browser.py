"""Exercise MERIDIAN's real local API and rendered browser flows.

Run from the repository: .venv\Scripts\python.exe scripts\verify_browser.py
Requires the local API on port 8010 and Playwright's installed Chromium.
No application fixtures or mocked responses are used.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal
import importlib.metadata
import json
from pathlib import Path
import platform
import re
import subprocess
import sys
import time
import traceback
from urllib.parse import urlencode
from urllib.request import urlopen

from playwright.sync_api import expect, sync_playwright


ROOT = Path(__file__).resolve().parents[1]


def read_api(base: str, endpoint: str, **params: str) -> dict:
    query = f"?{urlencode(params)}" if params else ""
    with urlopen(f"{base}{endpoint}{query}", timeout=30) as response:
        return json.load(response)


def pounds(value: object) -> str:
    amount = Decimal(str(value))
    return f"{'-' if amount < 0 else ''}£{abs(amount):,.2f}"


def integer(value: object) -> str:
    return f"{int(value):,}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8010")
    parser.add_argument("--output", default="evidence/browser-gate1.json")
    args = parser.parse_args()
    started = time.perf_counter()
    output = ROOT / args.output
    shots = ROOT / "evidence" / "screenshots"
    shots.mkdir(parents=True, exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    evidence: dict = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "command": subprocess.list2cmdline([sys.executable, "scripts/verify_browser.py", *sys.argv[1:]]),
        "base_url": args.base_url,
        "python": platform.python_version(),
        "playwright": importlib.metadata.version("playwright"),
        "git_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "verification": "Real browser against live local API; no mocked responses",
        "steps": [], "console_errors": [], "page_errors": [], "screenshots": [],
    }
    evidence["prior_attempts"] = []
    for path in sorted((ROOT / "evidence").glob("browser-gate1-attempt*.json")):
        previous = json.loads(path.read_text(encoding="utf-8"))
        evidence["prior_attempts"].append({"evidence": str(path.relative_to(ROOT)).replace("\\", "/"), "status": previous["status"], "failure": previous.get("failure", {}).get("message")})
    evidence["repair"] = "Mobile navigation now stacks each icon above its wrapping label; actual 390px viewport previously overflowed to402px. Original failure and before screenshot retained."

    def record(name: str, **details: object) -> None:
        evidence["steps"].append({"name": name, "status": "passed", **details})
        print(f"PASS {name}", flush=True)

    def screenshot(page, name: str) -> None:
        path = shots / f"gate1-{name}.png"
        page.screenshot(path=str(path), full_page=True)
        evidence["screenshots"].append(str(path.relative_to(ROOT)).replace("\\", "/"))

    try:
        overview = read_api(args.base_url, "/api/overview")
        snapshot = overview["snapshot"]
        evidence["snapshot_id"] = snapshot["id"]
        evidence["source_sha256"] = snapshot["source_sha256"]
        evidence["expected_metrics"] = overview["metrics"]
        assert snapshot["mode"] == "historical", "Gate1 verification requires an observed historical snapshot"

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            evidence["browser"] = browser.version
            context = browser.new_context(viewport={"width": 1440, "height": 1000}, device_scale_factor=1)
            page = context.new_page()
            page.on("console", lambda message: evidence["console_errors"].append(message.text) if message.type == "error" else None)
            page.on("pageerror", lambda error: evidence["page_errors"].append(str(error)))

            def ready() -> None:
                expect(page.locator('[aria-live="polite"]')).to_have_attribute("aria-busy", "false", timeout=30000)
                expect(page.locator(".error-state")).to_have_count(0)

            def check_overview(expected: dict) -> None:
                ready()
                expect(page.locator(".metric-featured strong")).to_have_text(pounds(expected["metrics"]["net_recorded_gbp"]))
                for index, key in enumerate(("gross_positive_gbp", "negative_adjustments_gbp", "net_recorded_gbp")):
                    expect(page.locator(".metric-card strong").nth(index)).to_have_text(pounds(expected["metrics"][key]))
                known, accepted = expected["metrics"]["known_customer_rows"], expected["metrics"]["accepted_rows"]
                expect(page.locator(".coverage-strip p")).to_contain_text(f"{integer(known)} of {integer(accepted)} accepted lines")
                expected_percentage = f"{known / accepted * 100:.1f}%" if accepted else "—"
                expect(page.locator(".coverage-percent")).to_have_text(expected_percentage)

            response = page.goto(args.base_url, wait_until="networkidle")
            assert response and response.status == 200
            check_overview(overview)
            expect(page.locator(".mode").first).to_have_text("historical")
            expect(page.locator("h1")).to_have_text("Business overview")
            expect(page.locator(".source-ribbon")).to_contain_text(snapshot["id"][:28])
            record("overview_kpis_match_persisted_api", metrics=overview["metrics"], coverage_rows=overview["metrics"]["accepted_rows"])

            # The initial small profile has disjoint yearly source windows. Its
            # chart must not draw a continuous line across unobserved dates.
            segments = page.locator(".sales-chart > g").count() - 3  # three y-axis grid groups
            expected_segments = 0
            previous = None
            for row in sorted(overview["daily"], key=lambda row: row["date"]):
                current = datetime.fromisoformat(row["date"][:10])
                if previous is None or (current - previous).days > 1:
                    expected_segments += 1
                previous = current
            assert segments == expected_segments, (segments, expected_segments)
            expect(page.locator(".chart-caption")).to_contain_text("missing periods left blank")
            record("calendar_chart_breaks_unobserved_gaps", observed_days=len(overview["daily"]), segments=segments)
            screenshot(page, "overview")

            country = next((name for name in overview["filters"]["countries"] if name == "France"), overview["filters"]["countries"][0])
            filtered = read_api(args.base_url, "/api/overview", country=country)
            page.get_by_label(re.compile(r"^Country")).select_option(country)
            page.get_by_role("button", name="Apply filters", exact=True).click()
            check_overview(filtered)
            assert filtered["metrics"]["net_recorded_gbp"] != overview["metrics"]["net_recorded_gbp"], "Choose a country that changes the KPI"
            record("country_filter_changes_kpi_and_customer_coverage", country=country, metrics=filtered["metrics"])

            page.get_by_role("button", name="Reset", exact=True).click()
            check_overview(overview)
            expect(page.get_by_label(re.compile(r"^Country"))).to_have_value("")
            record("filter_reset_restores_full_snapshot", net_recorded_gbp=overview["metrics"]["net_recorded_gbp"])

            # Inclusive date filters must use the same definition in API and UI.
            start = overview["daily"][0]["date"][:10]
            dated = read_api(args.base_url, "/api/overview", start=start, end=start)
            page.get_by_label("From", exact=True).fill(start)
            page.get_by_label("Through", exact=True).fill(start)
            page.get_by_role("button", name="Apply filters", exact=True).click()
            check_overview(dated)
            record("inclusive_date_filter", start=start, end=start, metrics=dated["metrics"])
            page.get_by_role("button", name="Reset", exact=True).click()
            check_overview(overview)

            page.locator(".metric-featured").click()
            ready()
            expect(page.locator("h1")).to_have_text("Metric explorer")
            net = read_api(args.base_url, "/api/lineage", metric="net_recorded_gbp", limit="50")
            expect(page.locator(".lineage-table tbody tr")).to_have_count(len(net["rows"]))
            expect(page.locator(".details")).to_contain_text(snapshot["id"])
            expect(page.locator(".details")).to_contain_text(snapshot["source_sha256"])
            expect(page.locator(".sql-code")).to_contain_text(net["sql"])
            for index, source_row in enumerate(net["rows"][:5]):
                row = page.locator(".lineage-table tbody tr").nth(index)
                expect(row).to_contain_text(f"{source_row['source_sheet']}:{source_row['source_row']}")
                expect(row).to_contain_text(pounds(source_row["line_amount"]))
            record("kpi_drillthrough_has_sql_snapshot_delta_and_source_anchors", metric="net_recorded_gbp", visible_rows=len(net["rows"]), total_rows=net["total_rows"], delta_versions=snapshot["delta_versions"])
            screenshot(page, "explorer")

            page.get_by_label(re.compile(r"^Metric")).select_option("negative_adjustments_gbp")
            ready()
            expect(page.locator(".panel-heading h2").first).to_have_text("Negative adjustments")
            negative = read_api(args.base_url, "/api/lineage", metric="negative_adjustments_gbp", limit="50")
            assert negative["rows"] and all(Decimal(str(row["line_amount"])) < 0 for row in negative["rows"])
            rendered_negative = page.locator(".lineage-table tbody tr td:nth-child(7)").all_text_contents()
            assert rendered_negative and all(Decimal(value.replace("£", "").replace(",", "")) < 0 for value in rendered_negative)
            record("negative_adjustments_selector_has_only_negative_contributions", visible_rows=len(rendered_negative), total_rows=negative["total_rows"])

            page.get_by_role("button", name="Data trust & recovery", exact=True).click()
            ready()
            trust = read_api(args.base_url, "/api/trust")
            assert trust["snapshot"]["id"] == snapshot["id"], "The serving pointer changed during verification"
            expected_counts = [snapshot[key] for key in ("source_rows", "accepted_rows", "quarantined_rows", "excluded_rows", "repeat_deliveries")]
            for index, count in enumerate(expected_counts):
                expect(page.locator(".trust-counts strong").nth(index)).to_have_text(integer(count))
            expect(page.locator(".status")).to_have_text("Counts reconciled")
            expect(page.locator(".json-evidence")).to_contain_text("last_validated_at")
            expect(page.locator(".details")).to_contain_text(snapshot["id"])
            assert trust["runs"], "No persisted pipeline runs"
            record("trust_counts_and_persisted_runs", counts=dict(zip(("source_rows", "accepted_rows", "quarantined_rows", "excluded_rows", "repeat_deliveries"), expected_counts)), persisted_runs=len(trust["runs"]))
            screenshot(page, "trust")

            page.get_by_role("button", name="Forecast & planning", exact=True).click()
            ready()
            expect(page.locator("h1")).to_have_text("Forecast & planning")
            expect(page.locator(".forecast-empty")).to_contain_text("No forecast has been trained or evaluated yet")
            expect(page.locator(".forecast-empty")).to_contain_text("IMPLEMENTATION GATE 4")
            record("forecast_has_truthful_unexecuted_state")

            page.goto(args.base_url, wait_until="networkidle")
            ready()
            page.keyboard.press("Tab")
            expect(page.get_by_role("link", name="Skip to main content", exact=True)).to_be_focused()
            page.keyboard.press("Enter")
            assert page.url.endswith("#main-content")
            # A native navigation button must work through keyboard activation.
            explorer_button = page.get_by_role("button", name="Metric explorer", exact=True)
            explorer_button.focus()
            page.keyboard.press("Enter")
            ready()
            expect(page.locator("h1")).to_have_text("Metric explorer")
            record("keyboard_skip_link_and_navigation")

            page.set_viewport_size({"width": 390, "height": 844})
            page.get_by_role("button", name="Business overview", exact=True).click()
            check_overview(overview)
            screenshot(page, "overview-mobile")
            mobile_dimensions = {}
            for view in ("Business overview", "Metric explorer", "Data trust & recovery", "Forecast & planning"):
                page.get_by_role("button", name=view, exact=True).click()
                ready()
                dimensions = page.evaluate("({viewport:innerWidth, document:document.documentElement.scrollWidth, body:document.body.scrollWidth})")
                assert dimensions["document"] <= dimensions["viewport"], f"Mobile document overflow in {view}: {dimensions}"
                mobile_dimensions[view] = dimensions
            record("mobile_390px_all_views_have_no_horizontal_page_overflow", dimensions=mobile_dimensions)

            assert not evidence["console_errors"], evidence["console_errors"]
            assert not evidence["page_errors"], evidence["page_errors"]
            record("no_console_or_page_errors")
            browser.close()
        evidence["status"] = "passed"
    except Exception as error:
        evidence["status"] = "failed"
        evidence["failure"] = {"type": type(error).__name__, "message": str(error), "traceback": traceback.format_exc()}
    evidence["finished_at"] = datetime.now(timezone.utc).isoformat()
    evidence["duration_seconds"] = round(time.perf_counter() - started, 3)
    output.write_text(json.dumps(evidence, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": evidence["status"], "steps": len(evidence["steps"]), "evidence": str(output), "failure": evidence.get("failure", {}).get("message")}, ensure_ascii=False))
    return 0 if evidence["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
