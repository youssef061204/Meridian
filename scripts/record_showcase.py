"""Record real API responses for the static, read-only public showcase.

Drives the local demo through baseline -> incident -> repair using the allow-listed worker jobs,
saves each response verbatim to frontend/showcase/recorded/, then resets the local demo to its
baseline. Nothing is synthesised here: every file is an HTTP response from the running stack.

Usage (API + Kafka + worker running): python scripts/record_showcase.py
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

API = "http://127.0.0.1:8010"
OUT = Path(__file__).resolve().parents[1] / "frontend" / "showcase" / "recorded"
METRICS = ["gross_positive_gbp", "negative_adjustments_gbp", "net_recorded_gbp"]


def get(path: str):
    with urlopen(API + path, timeout=120) as response:
        return json.load(response)


def save(name: str, data) -> None:
    (OUT / f"{name}.json").write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")


def run_job(kind: str) -> dict:
    request = Request(API + "/api/demo/jobs", data=json.dumps({"kind": kind}).encode(), method="POST",
                      headers={"content-type": "application/json", "X-Meridian-Demo": "local"})
    with urlopen(request, timeout=30) as response:
        job_id = json.load(response)["job_id"]
    started = time.monotonic()
    while time.monotonic() - started < 900:
        job = get(f"/api/demo/jobs/{job_id}")
        if job["state"] in ("passed", "failed"):
            print(kind, job["state"], round(time.monotonic() - started, 1), "s", flush=True)
            if job["state"] != "passed":
                raise SystemExit(f"{kind} failed: {job.get('error')}")
            return job
        time.sleep(3)
    raise SystemExit(f"{kind} timed out")


def record_replay(stage: str) -> None:
    save(f"replay-{stage}", get("/api/replay"))
    save(f"correction-{stage}", get("/api/replay/correction"))


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    save("overview", get("/api/overview"))
    for metric in METRICS:
        save(f"lineage-{metric}", get(f"/api/lineage?metric={metric}&limit=50"))
    save("forecasts", get("/api/forecasts"))
    save("trust", get("/api/trust"))
    record_replay("baseline")
    jobs = {"incident": run_job("incident")}
    record_replay("incident")
    jobs["repair"] = run_job("repair")
    record_replay("repaired")
    jobs["reset"] = run_job("reset")
    save("jobs", jobs)
    save("manifest", {"recorded_at": datetime.now(timezone.utc).isoformat(), "api": "local MERIDIAN API (loopback)",
                      "stages": ["baseline", "incident", "repaired"],
                      "note": "Verbatim responses from the local stack; the public showcase replays them read-only."})
    print("recorded to", OUT)


if __name__ == "__main__":
    main()
