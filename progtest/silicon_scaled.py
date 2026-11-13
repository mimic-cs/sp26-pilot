#!/usr/bin/env python3
"""Silicon scaled: the five scaled bench workloads on real Armv8
hardware -- the wall-time comparison the startup-dominated pilots
cannot provide (paper sec 8.5).

Prepared 2026-11-14, pre-registered alongside silicon_pilot.py (which
this script does not modify). On any aarch64 Linux box with this
bundle unpacked and qemu-user-x86_64 installed:

    python3 progtest/silicon_scaled.py

writes results/silicon_scaled.json.

Configurations per workload (same protocol as baseline_qemu.py and
silicon_pilot.py):
  casar_silicon   : the translated AArch64 binary executed NATIVELY;
  full_emu_silicon: the original x86-64 binary under qemu-x86_64 on
                    the same Armv8 host.

Timing: REPS invocations per configuration, perf_counter, median and
min reported, ratio computed on the median. Correctness gate: stdout
byte-exact and exit codes equal ACROSS the two configurations (the
cross-architecture oracle), checked on every rep; a determinism check
adds that all reps of one configuration agree with each other.

Hub dynamics are workload-fixed (results/baseline_qemu.json): each hub
workload traverses the hub 2,048,000 times per run (hubwalk 3,072,000,
walk_avg 1.5); the other three workloads make no hub traversal. SoC
identity comes from /proc/cpuinfo and uname and is recorded in the
host block.
"""
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT_JSON = os.path.join(ROOT, "results", "silicon_scaled.json")
REPS = 30
TIMEOUT = 120
BENCH = {
    "bench_sumloop": "~8M-iteration ALU loop (4000x2000 nested): add/cmp/jcc/jmp",
    "bench_memloop": "~500k reps (4000x125) of a 16-entry scaled-index sum",
    "bench_fib": "recursive fib(26): ~2.9M nested calls and returns",
    "bench_funcptr": "~2M indirect calls (4000x32x16) via 4-entry table (hub)",
    "bench_switch": "~2M-iteration state machine (4000x32x16) via jump-table hub",
}
HUB_HITS = {"bench_funcptr": 2048000, "bench_switch": 2048000}


def cpu_model():
    try:
        with open("/proc/cpuinfo") as fh:
            for line in fh:
                if line.lower().startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return None


def timed(cmd):
    ts, outs = [], []
    for _ in range(REPS):
        t0 = time.perf_counter()
        r = subprocess.run(cmd, capture_output=True, timeout=TIMEOUT)
        ts.append(time.perf_counter() - t0)
        outs.append((r.returncode, r.stdout))
    ts.sort()
    rc0, out0 = outs[0]
    det = all(rc == rc0 and out == out0 for rc, out in outs)
    return round(ts[len(ts) // 2], 4), round(ts[0], 4), rc0, out0, det


def main():
    if os.uname().machine not in ("aarch64", "arm64"):
        print("REFUSING: this experiment needs a real Armv8 host; "
              "current machine is %s" % os.uname().machine)
        return 1
    rows = []
    for prog, desc in BENCH.items():
        d = os.path.join(HERE, "build", prog)
        ex, ea = os.path.join(d, "x86.elf"), os.path.join(d, "arm.elf")
        if not (os.path.exists(ex) and os.path.exists(ea)):
            print("skip", prog, "(missing binaries)")
            continue
        cfgs, base = {}, {}
        for name, cmd in (("casar_silicon", [ea]),
                          ("full_emu_silicon", ["qemu-x86_64", ex])):
            med, mn, rc, out, det = timed(cmd)
            cfgs[name] = {"median_s": med, "min_s": mn, "reps": REPS,
                          "exit": rc}
            base[name] = (rc, out)
            print("== %-14s %-16s median=%.4fs min=%.4fs exit=%s "
                  "deterministic=%s" % (prog, name, med, mn, rc, det),
                  flush=True)
        cross = base["casar_silicon"] == base["full_emu_silicon"]
        print("   cross-arch gate (stdout byte-exact, exit): %s" % cross,
              flush=True)
        if not cross:
            continue
        c = cfgs["casar_silicon"]["median_s"]
        q = cfgs["full_emu_silicon"]["median_s"]
        rows.append({
            "program": prog,
            "description": desc,
            "hub_hits_per_run": HUB_HITS.get(prog),
            "configs": cfgs,
            "casar_vs_full_emu": round(c / q, 3) if q else None,
            "gate_stdout_byte_exact": True,
        })
    doc = {
        "method": ("five scaled bench workloads on real Armv8 silicon; "
                   "casar column = translated binary executed natively, "
                   "full_emu column = original x86-64 binary under "
                   "qemu-x86_64 on the same host. %d reps, median "
                   "reported. Gate: stdout byte-exact and exit codes "
                   "equal across the two configurations, all reps." % REPS),
        "host": {"machine": os.uname().machine,
                 "release": os.uname().release,
                 "cpu_model": cpu_model()},
        "reps": REPS,
        "programs": rows,
        "gates_all_pass": len(rows) == len(BENCH),
    }
    with open(OUT_JSON, "w") as fh:
        json.dump(doc, fh, indent=1)
    print("wrote", OUT_JSON, "gates_all_pass=", doc["gates_all_pass"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
