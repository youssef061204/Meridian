"""Verify Gate 2's published full-history analysis in a real local browser.

Requires the full historical snapshot to be active on the local API. This does
not publish a snapshot, change data, or call mocked endpoints.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal
import importlib.metadata
import json
import platform
import re
import subprocess
import sys
import time
import traceback

from playwright.sync_api import expect, sync_playwright

from verify_browser import ROOT, integer, pounds, read_api


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8010")
    args = parser.parse_args()
    started = time.perf_counter()
    output = ROOT / "evidence" / "browser-gate2.json"
    screenshots = ROOT / "evidence" / "screenshots"
    screenshots.mkdir(parents=True, exist_ok=True)
    evidence = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "command": subprocess.list2cmdline([sys.executable, "scripts/verify_browser_gate2.py", *sys.argv[1:]]),
        "base_url": args.base_url,
        "python": platform.python_version(),
        "playwright": importlib.metadata.version("playwright"),
        "git_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "verification": "Real Chromium against full historical local API, with no mocked responses",
        "steps": [], "console_errors": [], "page_errors": [], "screenshots": [],
    }

    def record(name: str, **detail: object) -> None:
        evidence["steps"].append({"name": name, "status": "passed", **detail})
        print(f"PASS {name}", flush=True)

    def screenshot(page, name: str) -> None:
        path = screenshots / f"gate2-{name}.png"
        page.screenshot(path=str(path), full_page=True)
        evidence["screenshots"].append(str(path.relative_to(ROOT)).replace("\\", "/"))

    try:
        overview = read_api(args.base_url, "/api/overview")
        snapshot = overview["snapshot"]
        assert snapshot["profile"] == "full" and snapshot["mode"] == "historical", "Activate a full historical snapshot before verification"
        assert overview.get("cohorts"), "Published customer cohorts are missing"
        assert overview.get("comparison"), "The full-history comparable-period analysis is missing"
        evidence["snapshot_id"] = snapshot["id"]
        evidence["source_sha256"] = snapshot["source_sha256"]
        evidence["source_rows"] = snapshot["source_rows"]
        evidence["expected_metrics"] = overview["metrics"]
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            evidence["browser"] = browser.version
            page = browser.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=1)
            page.on("console", lambda message: evidence["console_errors"].append(message.text) if message.type == "error" else None)
            page.on("pageerror", lambda error: evidence["page_errors"].append(str(error)))

            def ready() -> None:
                expect(page.locator('[aria-live="polite"]')).to_have_attribute("aria-busy", "false", timeout=30000)
                expect(page.locator(".error-state")).to_have_count(0)

            def comparison_matches(data: dict) -> None:
                comparison = data["comparison"]
                expect(page.locator(".period-comparison")).to_have_count(1)
                expected = [pounds(comparison["previous_net_gbp"]), pounds(comparison["current_net_gbp"])]
                change = pounds(comparison["change_gbp"])
                expected.append(("+" if Decimal(str(comparison["change_gbp"])) > 0 else "") + change)
                for index, value in enumerate(expected):
                    expect(page.locator(".period-grid > div > strong").nth(index)).to_have_text(value)
                for key in ("previous_start", "previous_end", "current_start", "current_end"):
                    expect(page.locator(".period-comparison")).to_contain_text(comparison[key][:10])
                if comparison["change_pct"] is not None:
                    value = Decimal(str(comparison["change_pct"]))
                    expect(page.locator(".period-change")).to_contain_text(f"{'+' if value > 0 else ''}{value:.1f}%")
                expect(page.locator(".period-comparison .panel-footnote")).to_have_text(comparison["limitation"])

            response = page.goto(args.base_url, wait_until="networkidle")
            assert response and response.status == 200
            ready()
            expect(page.locator(".metric-featured strong")).to_have_text(pounds(overview["metrics"]["net_recorded_gbp"]))
            warning = overview.get("quality", {}).get("source_overlap_warning")
            assert warning, "Cross-sheet source uncertainty must be visible"
            expect(page.locator(".source-warning")).to_contain_text(warning)
            record("full_history_kpi_and_source_overlap_caveat", net_recorded_gbp=overview["metrics"]["net_recorded_gbp"], caveat=warning)
            comparison_matches(overview)
            record("comparable_28day_windows_match_api", comparison=overview["comparison"])

            cohorts = overview["cohorts"]
            available = sorted({row["month_index"] for row in cohorts})
            for month in sorted({1 if 1 in available else available[0], 6 if 6 in available else available[-1]}):
                page.locator(".cohort-panel select").select_option(str(month))
                expected = sorted((row for row in cohorts if row["month_index"] == month), key=lambda row: row["cohort_month"])
                expect(page.locator(".cohort-panel tbody tr")).to_have_count(len(expected))
                for index, row in enumerate(expected):
                    rendered = page.locator(".cohort-panel tbody tr").nth(index)
                    expect(rendered.locator("td").nth(0)).to_have_text(row["cohort_month"][:7])
                    expect(rendered.locator("td").nth(1)).to_have_text(row["activity_month"][:7])
                    expect(rendered.locator("td").nth(2)).to_have_text(integer(row["cohort_customers"]))
                    expect(rendered.locator("td").nth(3)).to_have_text(integer(row["active_customers"]))
                    retention = "Unavailable" if row["retention_rate"] is None else f"{Decimal(str(row['retention_rate'])) * 100:.1f}%"
                    expect(rendered.locator("td").nth(4)).to_have_text(retention)
                    expect(rendered.locator("td").nth(5)).to_have_text("Complete calendar month" if row["is_complete_month"] else "Partial calendar month")
                record("cohort_month_selection_and_retention_denominator", month_index=month, rows=len(expected))
            expect(page.locator(".cohort-definition")).to_contain_text("not proof of acquisition")
            expect(page.locator(".cohort-footnote")).to_contain_text("not evidence of zero purchasing")
            partial = next((row for row in cohorts if not row["is_complete_month"]), None)
            if partial:
                page.locator(".cohort-panel select").select_option(str(partial["month_index"]))
                expect(page.locator(".cohort-panel .period-partial").first).to_have_text("Partial calendar month")
                record("incomplete_final_month_label", month_index=partial["month_index"], activity_month=partial["activity_month"])
            screenshot(page, "overview")

            country = "France" if "France" in overview["filters"]["countries"] else overview["filters"]["countries"][0]
            filtered = read_api(args.base_url, "/api/overview", country=country)
            page.get_by_label(re.compile(r"^Country")).select_option(country)
            page.get_by_role("button", name="Apply filters", exact=True).click()
            ready()
            expect(page.locator(".metric-featured strong")).to_have_text(pounds(filtered["metrics"]["net_recorded_gbp"]))
            comparison_matches(filtered)
            record("country_filter_updates_full_history_comparison", country=country, comparison=filtered["comparison"])
            page.get_by_role("button", name="Reset", exact=True).click()
            ready()
            expect(page.locator(".metric-featured strong")).to_have_text(pounds(overview["metrics"]["net_recorded_gbp"]))

            page.get_by_role("button", name="Data trust & recovery", exact=True).click()
            ready()
            trust = read_api(args.base_url, "/api/trust")
            assert trust["snapshot"]["id"] == snapshot["id"], "Serving snapshot changed during verification"
            totals = trust["snapshot"]["reconciliation_totals"]
            for index, key in enumerate(("source_parsed_amount_gbp", "accepted_parsed_amount_gbp", "quarantined_parsed_amount_gbp", "excluded_parsed_amount_gbp")):
                expect(page.locator(".amount-reconciliation tbody tr").nth(index).locator("td").nth(1)).to_have_text(pounds(totals[key]))
            expect(page.locator(".unparseable-amounts strong")).to_have_text(integer(totals["unparseable_amount_rows"]))
            assert sum(Decimal(str(totals[key])) for key in ("accepted_parsed_amount_gbp", "quarantined_parsed_amount_gbp", "excluded_parsed_amount_gbp")) == Decimal(str(totals["source_parsed_amount_gbp"]))
            record("trust_signed_value_reconciliation_matches_persisted_manifest", reconciliation_totals=totals)
            screenshot(page, "trust")

            page.set_viewport_size({"width": 390, "height": 844})
            dimensions = {}
            for name in ("Business overview", "Data trust & recovery"):
                page.get_by_role("button", name=name, exact=True).click()
                ready()
                size = page.evaluate("({viewport:innerWidth, document:document.documentElement.scrollWidth})")
                assert size["document"] == size["viewport"], (name, size)
                dimensions[name] = size
            record("gate2_panels_have_no_mobile_page_overflow", dimensions=dimensions)
            assert not evidence["console_errors"], evidence["console_errors"]
            assert not evidence["page_errors"], evidence["page_errors"]
            record("no_browser_errors")
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
