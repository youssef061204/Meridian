"""Capture reproducible, non-secret local resource/version evidence.

Run from the repository: .venv/Scripts/python.exe scripts/capture_environment.py
The script never dumps environment variables or a complete Docker inspection.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time


def command(argv: list[str], cwd: Path) -> dict:
    started = time.perf_counter()
    try:
        result = subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=20)
        return {"argv": argv, "returncode": result.returncode,
                "stdout": result.stdout.strip(), "stderr": result.stderr.strip(),
                "duration_seconds": round(time.perf_counter() - started, 4)}
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return {"argv": argv, "returncode": None,
                "error": f"{type(exc).__name__}: {exc}",
                "duration_seconds": round(time.perf_counter() - started, 4)}


def capture(root: Path) -> dict:
    disk = shutil.disk_usage(root)
    hardware = {"cpu_logical": os.cpu_count(), "cpu_description": platform.processor(),
                "disk_total_bytes": disk.total, "disk_used_bytes": disk.used,
                "disk_free_bytes": disk.free, "disk_measured_at": str(root)}
    try:
        import psutil
        memory = psutil.virtual_memory()
        hardware.update(cpu_physical=psutil.cpu_count(logical=False),
                        memory_total_bytes=memory.total,
                        memory_available_bytes=memory.available,
                        memory_used_bytes=memory.used,
                        memory_percent=memory.percent)
    except ImportError:
        hardware["memory_measurement_error"] = "psutil unavailable; RAM not measured"
    versions = {}
    for package in ["meridian-retail", "psutil", "pyspark", "delta-spark", "duckdb",
                    "pyarrow", "openpyxl", "fastapi", "uvicorn", "requests",
                    "kafka-python", "scikit-learn", "numpy", "pandas", "pytest"]:
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    commands = {
        "git_commit": ["git", "rev-parse", "HEAD"],
        "git_status": ["git", "status", "--short"],
        "git_version": ["git", "--version"],
        "java_version": ["java", "-version"],
        "node_version": ["node", "--version"],
        "uv_version": ["uv", "--version"],
        "docker_client": ["docker", "version", "--format", "{{.Client.Version}}"],
        "docker_server": ["docker", "version", "--format", "{{.Server.Version}}"],
        "docker_memory_bytes": ["docker", "info", "--format", "{{.MemTotal}}"],
        "docker_cpu_logical": ["docker", "info", "--format", "{{.NCPU}}"],
        "docker_os": ["docker", "info", "--format", "{{.OSType}}/{{.Architecture}}"],
        "docker_compose": ["docker", "compose", "version", "--short"],
    }
    results = {name: command(argv, root) for name, argv in commands.items()}
    docker_resources = {}
    for label, key in [("memory_total_bytes", "docker_memory_bytes"),
                       ("cpu_logical", "docker_cpu_logical")]:
        try:
            docker_resources[label] = int(results[key].get("stdout", ""))
        except ValueError:
            docker_resources[label] = None
    return {
        "schema_version": 1, "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "exact_command_argv": [sys.executable, *sys.argv], "working_directory": str(Path.cwd()),
        "repository": str(root), "git_commit": results["git_commit"].get("stdout") if results["git_commit"].get("returncode") == 0 else None,
        "git_working_tree_clean": not results["git_status"].get("stdout")
                                  if results["git_status"].get("returncode") == 0 else None,
        "host": {"system": platform.system(), "release": platform.release(),
                 "version": platform.version(), "machine": platform.machine(),
                 "python_version": platform.python_version(), "python_executable": sys.executable,
                 "resources": hardware},
        "docker_resources": docker_resources, "installed_python_packages": versions,
        "commands": results,
        "scope": "Single-machine point-in-time resource availability; not peak pipeline requirements or a benchmark.",
        "privacy": "No environment-variable dump, credentials, Docker registry configuration, or container environment inspected.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="evidence/environment.json")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = Path(args.output)
    if not output.is_absolute():
        output = root / output
    report = capture(root)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    temporary.replace(output)
    print(json.dumps({"output": str(output), "git_commit": report["git_commit"],
                      "host_memory_bytes": report["host"]["resources"].get("memory_total_bytes"),
                      "docker_memory_bytes": report["docker_resources"].get("memory_total_bytes")}))


if __name__ == "__main__":
    main()

