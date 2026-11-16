"""Follow the README startup path in a fresh clone and record the result.

Usage: python scripts/verify_clean_checkout.py <empty target directory>
Uses a separate Compose project (meridian-clean) and API port 8011 so it cannot touch the
working copy's containers or data. The full-history batch is skipped (documented, ~2 min);
the demo profile, fixture oracle, UI build and API are exercised.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]


def main():
    target = Path(sys.argv[1]).resolve()
    if target.exists() and any(target.iterdir()):
        raise SystemExit("target must be empty")
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    env = {**os.environ, "COMPOSE_PROJECT_NAME": "meridian-clean", "MERIDIAN_PORT": "8011", "MERIDIAN_KAFKA_PORT": "9093"}
    npm = "npm.cmd" if os.name == "nt" else "npm"
    steps = [
        ("clone", ["git", "clone", "--quiet", str(ROOT), str(target)], ROOT),
        ("build_locked_image", ["docker", "compose", "build", "tools"], target),
        ("download_and_convert_source", ["docker", "compose", "run", "--rm", "--no-deps", "tools", "python", "-m", "meridian.cli", "convert"], target),
        ("fixture_batch", ["docker", "compose", "run", "--rm", "--no-deps", "tools", "python", "-m", "meridian.cli", "batch", "--profile", "fixture"], target),
        ("fixture_oracle_check", ["docker", "compose", "run", "--rm", "--no-deps", "tools", "python", "tests/check_spark_fixture.py"], target),
        ("demo_batch", ["docker", "compose", "run", "--rm", "--no-deps", "tools", "python", "-m", "meridian.cli", "batch", "--profile", "demo"], target),
        ("frontend_ci", [npm, "--prefix", "frontend", "ci", "--no-fund", "--no-audit"], target),
        ("frontend_build", [npm, "--prefix", "frontend", "run", "build"], target),
        ("api_up", ["docker", "compose", "up", "-d", "api"], target),
    ]
    record = {"started_at": datetime.now(timezone.utc).isoformat(), "source_commit": sha, "target": str(target),
              "compose_project": "meridian-clean", "api_port": 8011, "steps": [],
              "skipped": ["batch --profile full (same code path, ~130 s; verified in the working copy)", "Kafka replay steps (verified by replay-drill and browser-demo in the working copy)"]}
    try:
        for name, command, cwd in steps:
            started = time.perf_counter()
            result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
            step = {"step": name, "command": " ".join(command), "exit_code": result.returncode, "seconds": round(time.perf_counter() - started, 1),
                    "tail": (result.stdout + result.stderr)[-600:] if result.returncode else ""}
            record["steps"].append(step)
            print(name, result.returncode, step["seconds"], flush=True)
            if result.returncode:
                raise RuntimeError(f"{name} failed")
        for _ in range(60):
            try:
                health = json.load(urlopen("http://localhost:8011/api/health", timeout=5)); break
            except Exception:
                time.sleep(2)
        overview = json.load(urlopen("http://localhost:8011/api/overview", timeout=60))
        page = urlopen("http://localhost:8011/", timeout=10).read().decode()
        record["api_checks"] = {"health": health, "snapshot_profile": overview["snapshot"]["profile"], "net_recorded_gbp": str(overview["metrics"]["net_recorded_gbp"]),
                                "accepted_rows": overview["metrics"]["accepted_rows"], "ui_served": "<div id=\"root\">" in page}
        assert str(overview["metrics"]["net_recorded_gbp"]) == "69754.43", overview["metrics"]
        assert record["api_checks"]["ui_served"]
        record["state"] = "passed"
    except Exception as exc:
        record.update(state="failed", error=str(exc))
    finally:
        subprocess.run(["docker", "compose", "down"], cwd=target, env=env, capture_output=True)
        record["finished_at"] = datetime.now(timezone.utc).isoformat()
        (ROOT / "evidence" / "clean-checkout.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        print(record["state"], record.get("error", ""))


if __name__ == "__main__":
    main()
