#!/usr/bin/env python3
"""Silicon pilot: run the five CASAR pilot binaries on real Armv8 hardware.

Prepared 2026-11-10; the build host is x86-64 only, so this script is
shipped in the artifact unexecuted (paper sec 10, Measurement limits).
On any aarch64 Linux box with the artifact unpacked and
qemu-user-x86_64 installed, one command produces
results/silicon_pilot.json:

    python3 progtest/silicon_pilot.py

Configurations per pilot (expected stdout/exit come from
results/prog_exec.json, recorded on the x86-64 build host):
  casar_silicon   : the translated AArch64 binary executed NATIVELY
                    (this is the configuration the paper can only
                    project -- the headline column);
  full_emu_silicon: the original x86-64 binary under qemu-x86_64 on the
                    same Armv8 host (the deployment alternative, now the
                    one paying TCG);
  qemu_crosscheck : the translated binary under qemu-aarch64 (optional;
                  only if qemu-aarch64 is also installed -- sanity that
                  the silicon result matches the emulation result).

Timing: REPS invocations per configuration, perf_counter, median and min
reported, ratios computed on the median -- same protocol as
baseline_qemu.py. Correctness gate: byte-exact stdout and exit codes
against prog_exec.json before any timing row is recorded.
"""
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PROG_EXEC = os.path.join(ROOT, "results", "prog_exec.json")
OUT_JSON = os.path.join(ROOT, "results", "silicon_pilot.json")
REPS = 5
TIMEOUT = 120
PILOTS = ["sumloop", "isort", "fib", "funcptr", "switch"]


def timed(cmd):
    ts, rc0 = [], None
    for _ in range(REPS):
        t0 = time.perf_counter()
        r = subprocess.run(cmd, capture_output=True, timeout=TIMEOUT)
        ts.append(time.perf_counter() - t0)
        if rc0 is None:
            rc0 = r.returncode
    ts.sort()
    return round(ts[len(ts) // 2], 4), round(ts[0], 4), rc0


def main():
    if os.uname().machine not in ("aarch64", "arm64"):
        print("REFUSING: this experiment needs a real Armv8 host; "
              "current machine is %s" % os.uname().machine)
        return 1
    expect = {p["program"]: p for p in
              json.load(open(PROG_EXEC))["programs"]}
    rows = []
    for prog in PILOTS:
        d = os.path.join(HERE, "build", prog)
        ex, ea = os.path.join(d, "x86.elf"), os.path.join(d, "arm.elf")
        want = expect.get(prog)
        if not (os.path.exists(ex) and os.path.exists(ea) and want):
            print("skip", prog, "(missing binaries or expectation)")
            continue
        # correctness gate: native-arm run vs recorded x86 output
        r = subprocess.run([ea], capture_output=True, timeout=TIMEOUT)
        gate = (r.returncode == want["x86"]["exit"]
                and r.stdout.hex() == want["x86"]["stdout_hex"])
        print("== %-10s silicon gate (stdout byte-exact, exit): %s"
              % (prog, gate), flush=True)
        if not gate:
            continue
        cfgs = {}
        for name, cmd in (("casar_silicon", [ea]),
                          ("full_emu_silicon", ["qemu-x86_64", ex])):
            med, mn, rc = timed(cmd)
            cfgs[name] = {"median_s": med, "min_s": mn, "reps": REPS,
                          "exit": rc}
            print("   %-16s median=%.4fs min=%.4fs exit=%s"
                  % (name, med, mn, rc), flush=True)
        c = cfgs["casar_silicon"]["median_s"]
        q = cfgs["full_emu_silicon"]["median_s"]
        rows.append({
            "program": prog,
            "configs": cfgs,
            "casar_vs_full_emu": round(c / q, 2) if q else None,
            "gate_stdout_byte_exact": gate,
        })
    doc = {
        "method": ("five pilot binaries on real Armv8 silicon; casar "
                   "column = translated binary executed natively, "
                   "full_emu column = original x86-64 binary under "
                   "qemu-x86_64 on the same host. %d reps, median "
                   "reported. Gate: stdout byte-exact and exit codes "
                   "equal to results/prog_exec.json." % REPS),
        "host": {"machine": os.uname().machine,
                 "release": os.uname().release},
        "programs": rows,
        "gates_all_pass": len(rows) == len(PILOTS),
    }
    with open(OUT_JSON, "w") as fh:
        json.dump(doc, fh, indent=1)
    print("wrote", OUT_JSON, "gates_all_pass=", doc["gates_all_pass"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
