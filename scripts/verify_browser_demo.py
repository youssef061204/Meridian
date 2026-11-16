"""Exercise the signature demo sequence and the Gate 3-4 views in real Chromium.

Requires: api + worker + kafka running, an initialised demo replay generation, and
executed forecasts. Every action goes through the real UI -> API -> worker -> Kafka/Spark path.
"""
from __future__ import annotations

import importlib.metadata
import json
import platform
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from decimal import Decimal

from playwright.sync_api import expect, sync_playwright

from verify_browser import ROOT, integer, pounds, read_api

BASE = "http://localhost:8010"


def wait_job(kind, timeout=420):
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = read_api(BASE, "/api/replay")
        job = next((j for j in state["jobs"] if j["kind"] == kind), None)
        if job and job["state"] in ("passed", "failed"):
            return job, state
        time.sleep(2)
    raise TimeoutError(f"{kind} job did not finish")


def main() -> int:
    started = time.perf_counter()
    output = ROOT / "evidence" / "browser-demo.json"
    shots = ROOT / "evidence" / "screenshots"
    evidence = {"started_at": datetime.now(timezone.utc).isoformat(), "command": subprocess.list2cmdline([sys.executable, "scripts/verify_browser_demo.py"]),
                "python": platform.python_version(), "playwright": importlib.metadata.version("playwright"),
                "git_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                "verification": "Real Chromium; real API, worker, Kafka, Spark and Delta; no mocked responses",
                "steps": [], "console_errors": [], "page_errors": [], "screenshots": []}

    def record(name, **detail):
        evidence["steps"].append({"name": name, "status": "passed", "at_seconds": round(time.perf_counter() - started, 1), **detail})
        print(f"PASS {name}", flush=True)

    def shot(page, name):
        path = shots / f"demo-{name}.png"
        page.screenshot(path=str(path), full_page=True)
        evidence["screenshots"].append(str(path.relative_to(ROOT)).replace("\\", "/"))

    try:
        replay = read_api(BASE, "/api/replay")
        assert replay["state"] == "ready" and replay["worker"]["state"] == "ready", "Start kafka, worker and initialise replay first"
        if replay["timeline"]:   # leave a clean baseline before the sequence
            subprocess.run([sys.executable, "-c", "import requests;requests.post('http://localhost:8010/api/demo/jobs',json={'kind':'reset'},headers={'X-Meridian-Demo':'local'})"], check=True)
            wait_job("reset")
        baseline = read_api(BASE, "/api/replay")
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.on("console", lambda m: evidence["console_errors"].append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: evidence["page_errors"].append(str(e)))
            page.goto(BASE)
            ready = lambda: expect(page.locator(".loading-state")).to_have_count(0, timeout=60000)
            ready()
            sequence_start = time.perf_counter()

            # 1. KPI -> contributing source evidence
            overview = read_api(BASE, "/api/overview")
            page.locator(".metric-card").nth(2).click()
            ready()
            expect(page.locator("h1")).to_have_text("Metric explorer")
            expect(page.locator(".lineage-table tbody tr").first).to_be_visible()
            expect(page.locator(".sql-code")).to_contain_text("positive_merchandise")
            record("kpi_opens_source_evidence", net=overview["metrics"]["net_recorded_gbp"])
            shot(page, "1-kpi-evidence")

            # 2-4. Inject incident: unsafe discrepancy isolated, quarantine, blocked publication
            page.get_by_role("button", name="Data trust & recovery", exact=True).click()
            ready()
            expect(page.locator(".replay-status")).to_contain_text(baseline["snapshot"]["id"])
            page.get_by_role("button", name="Inject controlled incident").click()
            expect(page.locator(".job-progress")).to_contain_text("incident", timeout=10000)
            job, state = wait_job("incident")
            assert job["state"] == "passed", job
            page.get_by_role("button", name="Refresh persisted data").click()
            ready()
            expect(page.locator(".job-progress .status").first).to_have_text("passed", timeout=20000)
            status = state["status"]
            expect(page.locator(".unsafe-card .big-number")).to_have_text(pounds(status["unsafe_comparison"]["unsafe_net_gbp"]))
            expect(page.locator(".trusted-card .big-number")).to_have_text(pounds(status["trusted_net_recorded_gbp"]))
            assert Decimal(status["unsafe_comparison"]["discrepancy_gbp"]) > 0
            expect(page.locator(".unsafe-card")).to_contain_text("NEVER SERVED")
            record("unsafe_calculation_isolated_with_discrepancy", **status["unsafe_comparison"], trusted=status["trusted_net_recorded_gbp"])
            for reason in ("malformed json", "missing required field", "incompatible schema", "identity payload conflict"):
                expect(page.locator(".inner-tables")).to_contain_text(reason)
            assert state["timeline"]["publication"] == "blocked"
            expect(page.locator(".timeline")).to_contain_text("Publication blocked")
            expect(page.locator(".replay-status")).to_contain_text("last validated snapshot retained")
            expect(page.locator(".replay-status")).to_contain_text(baseline["snapshot"]["id"])
            detected = state["timeline"]["detected"]
            assert detected["within_batch_duplicates"] > 0 and detected["cross_batch_duplicates"] > 0 and detected["late_routed_to_backfill"] > 0, detected
            record("incident_detected_contained_and_publication_blocked", detected=detected, quarantine=status["quarantine_by_reason"], late=status["late_arrivals"])
            shot(page, "2-incident-contained")

            # 5-6. Repair -> reconciled, new snapshot, recovery duration
            page.get_by_role("button", name="Repair and reprocess").click()
            job, state = wait_job("repair")
            assert job["state"] == "passed", job
            page.get_by_role("button", name="Refresh persisted data").click()
            ready()
            rec = state["snapshot"]["reconciliation"]
            assert rec["state"] == "reconciled" and state["snapshot"]["id"] != baseline["snapshot"]["id"], rec
            expect(page.locator(".replay-status")).to_contain_text("reconciled")
            expect(page.locator(".replay-status")).to_contain_text(state["snapshot"]["id"])
            expect(page.locator(".panel-heading .status-good").filter(has_text="recovered in")).to_be_visible()
            record("repair_reconciles_and_publishes_new_snapshot", snapshot=state["snapshot"]["id"], recovery_seconds=state["timeline"]["recovery_seconds"], reconciliation=rec)
            shot(page, "3-recovered")

            # Explorer: correction before/after
            page.get_by_role("button", name="Metric explorer", exact=True).click()
            ready()
            correction = read_api(BASE, "/api/replay/correction")
            expect(page.get_by_text("Revision history of corrected lines")).to_be_visible()
            expect(page.locator("table").filter(has_text="superseded, retained")).to_be_visible()
            expect(page.get_by_text(pounds(correction["reconciliation"]["correction_delta_gbp"]), exact=True)).to_be_visible()
            record("explorer_shows_reconciliation_before_and_after_correction", delta=correction["reconciliation"]["correction_delta_gbp"], lines=len(correction["lines"]))
            shot(page, "4-correction")
            evidence["signature_sequence_seconds"] = round(time.perf_counter() - sequence_start, 1)

            # Forecast view
            forecast = read_api(BASE, "/api/forecasts")
            page.get_by_role("button", name="Forecast & planning", exact=True).click()
            ready()
            seven = round(sum(p["prediction"] for p in forecast["latest_forecast"]["__ALL__"][:7]))
            expect(page.locator(".plan-grid")).to_contain_text(f"{integer(seven)} units")
            test_rows = page.locator("table[aria-label='test metrics'] tbody tr")
            expect(test_rows).to_have_count(len(forecast["metrics"]["test"]))
            page.get_by_role("button", name="Validation (6 origins)").click()
            expect(page.locator("table[aria-label='validation metrics'] tbody tr")).to_have_count(len(forecast["metrics"]["validation"]))
            page.locator(".plan-grid").page.get_by_label("Series").select_option("22197")
            expect(page.locator("svg[aria-label*='SMALL POPCORN HOLDER']").first).to_be_visible()
            expect(page.locator(".simulated")).to_contain_text("SIMULATED")
            record("forecast_view_matches_api", promoted=forecast["promoted_model"], next7_units=seven)
            shot(page, "5-forecast")

            # Interrupted publish keeps the last validated snapshot; reset restores baseline
            page.get_by_role("button", name="Data trust & recovery", exact=True).click()
            ready()
            current = read_api(BASE, "/api/replay")["snapshot"]["id"]
            page.get_by_role("button", name="Interrupt a publish").click()
            job, state = wait_job("interrupted-publish")
            assert job["state"] == "passed" and state["snapshot"]["id"] == current
            page.get_by_role("button", name="Refresh persisted data").click()
            ready()
            expect(page.locator(".replay-status")).to_contain_text("last validated snapshot retained: latest attempt failed")
            record("interrupted_publish_retains_last_validated_snapshot", snapshot=current)
            shot(page, "6-interrupted-publish")
            page.get_by_role("button", name="Reset to baseline").click()
            job, state = wait_job("reset")
            assert job["state"] == "passed" and state["snapshot"]["id"] == baseline["snapshot"]["id"]
            page.get_by_role("button", name="Refresh persisted data").click()
            ready()
            expect(page.locator(".replay-status")).to_contain_text("restored to recorded baseline")
            record("reset_restores_baseline", snapshot=state["snapshot"]["id"])

            # Keyboard navigation and mobile layout
            page.goto(BASE); ready()
            page.locator(".nav-item").first.focus()
            page.keyboard.press("Tab"); page.keyboard.press("Enter")
            expect(page.locator("h1")).to_have_text("Forecast & planning")
            record("keyboard_navigation")
            page.set_viewport_size({"width": 390, "height": 844})
            sizes = {}
            for name in ("Forecast & planning", "Data trust & recovery", "Metric explorer"):
                page.get_by_role("button", name=name, exact=True).click(); ready()
                page.wait_for_timeout(800)
                size = page.evaluate("({viewport:innerWidth, document:document.documentElement.scrollWidth})")
                assert size["document"] == size["viewport"], (name, size)
                sizes[name] = size
            shot(page, "7-mobile-trust")
            record("no_mobile_page_overflow", sizes=sizes)
            assert not evidence["console_errors"], evidence["console_errors"]
            assert not evidence["page_errors"], evidence["page_errors"]
            record("no_browser_errors")
            browser.close()
        evidence["status"] = "passed"
    except Exception as error:
        evidence["status"] = "failed"
        evidence["failure"] = {"type": type(error).__name__, "message": str(error)[:2000], "traceback": traceback.format_exc()[-4000:]}
    evidence["finished_at"] = datetime.now(timezone.utc).isoformat()
    evidence["duration_seconds"] = round(time.perf_counter() - started, 1)
    output.write_text(json.dumps(evidence, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": evidence["status"], "steps": len(evidence["steps"]), "failure": evidence.get("failure", {}).get("message")}, ensure_ascii=False))
    return 0 if evidence["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
