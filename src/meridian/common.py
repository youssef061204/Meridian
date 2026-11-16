from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(os.environ.get("MERIDIAN_ROOT", Path(__file__).resolve().parents[2]))
DATA = ROOT / "data"
RUNTIME = ROOT / "runtime"


def now():
    return datetime.now(timezone.utc).isoformat()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as out:
        json.dump(value, out, indent=2, default=str, allow_nan=False)
        out.flush()
        os.fsync(out.fileno())
    os.replace(temp, path)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@contextmanager
def run_record(stage, **details):
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
    target = RUNTIME / "runs" / (run_id + ".json")
    record = dict(run_id=run_id, stage=stage, started_at=now(), state="running", **details)
    if shutil.which("git"):
        revision = subprocess.run(["git", "rev-parse", "--verify", "HEAD"], cwd=ROOT, capture_output=True, text=True)
        record["git_sha"] = revision.stdout.strip() if revision.returncode == 0 else "uncommitted"
    else:
        head = (ROOT / ".git" / "HEAD")
        value = head.read_text().strip() if head.exists() else "uncommitted"
        ref = ROOT / ".git" / value.removeprefix("ref: ")
        record["git_sha"] = (ref.read_text().strip() if ref.is_file() else "uncommitted") if value.startswith("ref: ") else value
    record["python"] = platform.python_version()
    atomic_json(target, record)
    start = time.perf_counter()
    try:
        yield record
        record["state"] = "passed"
    except BaseException as exc:
        record.update(state="failed", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        record.update(finished_at=now(), duration_seconds=round(time.perf_counter() - start, 4))
        atomic_json(target, record)
        print(json.dumps(record, default=str), flush=True)
