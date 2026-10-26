#!/usr/bin/env python3
"""Stress harness for CorbanuTerminal #1201 on a Windows runner.

Runs codex-login's `unauthorized_recovery_uses_external_refresh_for_bearer_manager` alone,
N times per load condition, and records every provider-auth helper wait from the #1203
trace lines (`provider_auth_helper attempt=.. phase=complete outcome=.. elapsed_us=..`).

Conditions:
  idle       - nothing else running
  cpu_io     - one busy-loop process per logical CPU + one process churning temp files
  selfspawn  - the rest of the codex-login unit tests looping concurrently (they include the
               self-spawning storage-gate tests that #1229 identified as the stall source)

usage: python stress_1201.py <test-binary> <out-dir> <runs-per-condition>
"""
import json
import multiprocessing
import os
import re
import statistics
import subprocess
import sys
import tempfile
import threading
import time

TEST_NAME = "unauthorized_recovery_uses_external_refresh_for_bearer_manager"
TEST = None  # full path, discovered with --list
TRACE = re.compile(r"provider_auth_helper attempt=(\d+) phase=complete outcome=(\w+) elapsed_us=(\d+)")
TIMEOUT_US = 10_000_000


def burn(stop):
    x = 0
    while not stop.is_set():
        for i in range(200_000):
            x = (x * 31 + i) % 1_000_003


def churn(stop, root):
    payload = os.urandom(1 << 20)
    n = 0
    while not stop.is_set():
        path = os.path.join(root, f"churn-{n % 64}.bin")
        with open(path, "wb") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.remove(path)
        n += 1


def selfspawn_loop(binary, stop, counter):
    while not stop.is_set():
        subprocess.run([binary, "--skip", TEST], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=900)
        counter[0] += 1


def run_once(binary):
    started = time.perf_counter()
    try:
        proc = subprocess.run([binary, TEST, "--exact", "--nocapture", "--test-threads=1"],
                              capture_output=True, text=True, timeout=120)
        out, rc = proc.stdout + proc.stderr, proc.returncode
    except subprocess.TimeoutExpired as exc:
        def text(v):
            return v.decode("utf8", "replace") if isinstance(v, bytes) else (v or "")
        out, rc = text(exc.stdout) + text(exc.stderr), "harness_timeout"
    wall = time.perf_counter() - started
    waits = [{"attempt": int(a), "outcome": o, "elapsed_us": int(e)} for a, o, e in TRACE.findall(out)]
    passed = rc == 0 and "1 passed" in out
    return {"passed": passed, "returncode": rc, "wall_s": round(wall, 4), "helper_waits": waits,
            "timed_out_10s": "timed out after 10000 ms" in out}, out


def pct(values, q):
    if not values:
        return None
    s = sorted(values)
    return s[min(len(s) - 1, int(round(q * (len(s) - 1))))]


def summarize(rows):
    waits = [w["elapsed_us"] for r in rows for w in r["helper_waits"]]
    return {
        "runs": len(rows),
        "passed": sum(r["passed"] for r in rows),
        "failed": sum(not r["passed"] for r in rows),
        "helper_timeouts_10s": sum(r["timed_out_10s"] for r in rows),
        "helper_calls": len(waits),
        "helper_wait_ms": {k: (None if v is None else round(v / 1000, 2)) for k, v in {
            "p50": pct(waits, 0.50), "p95": pct(waits, 0.95), "p99": pct(waits, 0.99),
            "max": max(waits) if waits else None, "mean": statistics.mean(waits) if waits else None}.items()},
        "min_headroom_to_10s_ms": None if not waits else round((TIMEOUT_US - max(waits)) / 1000, 2),
        "wall_s_p50": pct([r["wall_s"] for r in rows], 0.5),
        "wall_s_max": max(r["wall_s"] for r in rows),
    }


def condition(name, binary, runs, out_dir):
    stop = threading.Event()
    helpers, counter, tmp = [], [0], None
    if name == "cpu_io":
        mstop = multiprocessing.Event()
        tmp = tempfile.mkdtemp(prefix="churn-")
        helpers = [multiprocessing.Process(target=burn, args=(mstop,), daemon=True)
                   for _ in range(os.cpu_count() or 2)]
        helpers.append(multiprocessing.Process(target=churn, args=(mstop, tmp), daemon=True))
        for p in helpers:
            p.start()
    elif name == "selfspawn":
        t = threading.Thread(target=selfspawn_loop, args=(binary, stop, counter), daemon=True)
        t.start()
        helpers = [t]
        time.sleep(5)
    rows = []
    with open(os.path.join(out_dir, f"{name}.log"), "w", encoding="utf8") as raw:
        for i in range(1, runs + 1):
            row, out = run_once(binary)
            row["run"] = i
            rows.append(row)
            raw.write(f"===== {name} run {i} passed={row['passed']} rc={row['returncode']} wall={row['wall_s']}s\n{out}\n")
            print(f"[{name}] run {i}/{runs} passed={row['passed']} waits_ms="
                  f"{[round(w['elapsed_us'] / 1000, 1) for w in row['helper_waits']]}", flush=True)
    if name == "cpu_io":
        mstop.set()
        for p in helpers:
            p.join(timeout=10)
    elif name == "selfspawn":
        stop.set()
    with open(os.path.join(out_dir, f"{name}.jsonl"), "w", encoding="utf8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    s = summarize(rows)
    if name == "selfspawn":
        s["background_suite_runs_completed"] = counter[0]
    return s


def discover(binary):
    listing = subprocess.run([binary, "--list"], capture_output=True, text=True, timeout=120).stdout
    names = [l.split(": test")[0].strip() for l in listing.splitlines() if l.endswith(": test")]
    hits = [n for n in names if n.split("::")[-1] == TEST_NAME]
    if len(hits) != 1:
        raise SystemExit(f"expected exactly one test named {TEST_NAME}, found {hits}")
    return hits[0], len(names)


def main():
    global TEST
    binary, out_dir, runs = sys.argv[1], sys.argv[2], int(sys.argv[3])
    TEST, total_tests = discover(binary)
    print(f"target test: {TEST} (binary has {total_tests} tests)", flush=True)
    os.makedirs(out_dir, exist_ok=True)
    summary = {"test": TEST, "tests_in_binary": total_tests, "binary": os.path.basename(binary), "runs_per_condition": runs,
               "logical_cpus": os.cpu_count(), "conditions": {}}
    for name in ("idle", "cpu_io", "selfspawn"):
        summary["conditions"][name] = condition(name, binary, runs, out_dir)
        print(json.dumps({name: summary["conditions"][name]}), flush=True)
    with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf8") as f:
        json.dump(summary, f, indent=1)
    total_fail = sum(c["failed"] for c in summary["conditions"].values())
    print(f"TOTAL runs={sum(c['runs'] for c in summary['conditions'].values())} failed={total_fail}")


if __name__ == "__main__":
    main()
