"""Run an exact command, retain logs/exit/duration and sampled container memory."""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from meridian.common import ROOT, atomic_json, now

p = argparse.ArgumentParser()
p.add_argument("label")
p.add_argument("--container")
p.add_argument("command", nargs=argparse.REMAINDER)
args = p.parse_args()
command = args.command
if command and command[0] == "--":
    command = command[1:]
if not command:
    p.error("command required")
if Path(command[0]).exists():
    command[0] = str(Path(command[0]).resolve())
folder = ROOT / "evidence" / "execution"
folder.mkdir(parents=True, exist_ok=True)
path = folder / (args.label + ".log")
start = time.perf_counter()
record = dict(command=command, cwd=str(ROOT), started_at=now(), log=str(path.relative_to(ROOT)), cache_condition="not controlled; see run notes")
revision = subprocess.run(["git", "rev-parse", "--verify", "HEAD"], capture_output=True, text=True)
record["git_sha"] = revision.stdout.strip() if revision.returncode == 0 else "uncommitted"
memory_samples = []
with path.open("w", encoding="utf-8") as log:
    process = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
    while process.poll() is None:
        if args.container:
            sample = subprocess.run(["docker", "stats", "--no-stream", "--format", "{{json .}}", args.container], capture_output=True, text=True)
            if sample.returncode == 0 and sample.stdout.strip():
                try:
                    memory_samples.append({"at":now(), **json.loads(sample.stdout)})
                except ValueError:
                    pass
        time.sleep(2)
record.update(exit_code=process.returncode, finished_at=now(), duration_seconds=round(time.perf_counter()-start,4), container_samples=memory_samples)
atomic_json(folder / (args.label+".json"), record)
print(path.read_text(encoding="utf-8", errors="replace")[-7000:])
print(json.dumps({k:v for k,v in record.items() if k != "container_samples"}, indent=2))
sys.exit(process.returncode)
