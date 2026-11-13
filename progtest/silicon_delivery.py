#!/usr/bin/env python3
"""Silicon delivery: execute the two emitted whole binaries (gzip, cat)
in their own processes on real Armv8 hardware, against the original
x86-64 binaries under qemu-x86_64 -- closing the "verified, not
executed" gap (paper sec 8.7, sec 10.3).

Prepared 2026-11-14, pre-registered; arch-guarded like
silicon_pilot.py. On any aarch64 Linux box with this bundle unpacked
and qemu-user-x86_64 installed:

    python3 progtest/silicon_delivery.py

writes results/silicon_delivery.json.

Assets per tool (delivery/<tool>/):
  casar.aarch64 : the emitted translation, statically linked (runs
                  bare on the host);
  orig.x86      : the exact source binary, sha256-pinned below to the
                  provenance recorded in build/emit/*.obligations.json
                  (/usr/bin/gzip cc789a57..., /usr/bin/cat 407a78e2...),
                  dynamically linked -- the script prefixes the
                  emulator with -L /usr/x86_64-linux-gnu when that
                  cross-runtime directory exists (installed by the CI
                  workflow as libc6-amd64-cross), since qemu-user
                  translates instructions only, not the target libc.

Inputs are generated deterministically by this script (no external
fixtures); every input gets mtime 0 via os.utime so gzip's header
stores a reproducible timestamp, and both configurations receive the
same argv. Gate per case: stdout byte-exact and exit codes equal
across the two configurations; stderr is recorded for the error
cases. Cases per tool: five real inputs plus one missing-file error
case (exit != 0, empty stdout); cat additionally concatenates two
real inputs in one invocation.
"""
import hashlib
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT_JSON = os.path.join(ROOT, "results", "silicon_delivery.json")
TIMEOUT = 120
EXPECTED_SHA = {
    "gzip": "cc789a57a20d5d48bc5eecb914dff03ccfa1f35962112093a85fb67d29812d5b",
    "cat": "407a78e2ebac6ca926e8f567c44dcc4e39d964836b863e0172b71967060bace3",
}
TEXT = b"The quick brown fox jumps over the lazy dog.\n" * 40
INPUTS = {
    "text.txt": TEXT,
    "empty.bin": b"",
    "binary.bin": bytes(range(256)) * 256,
    "zeros.bin": b"\x00" * (1 << 20),
    "random.bin": random.Random(20261114).randbytes(1 << 18),
}
# (label, argv) -- relative to the workdir; error case last.
GZIP_CASES = [(f, [f, "-c"]) for f in INPUTS] \
    + [("missing", ["does_not_exist.bin", "-c"])]
CAT_CASES = [(f, [f]) for f in INPUTS] \
    + [("concat", ["random.bin", "text.txt"]),
       ("missing", ["does_not_exist.bin"])]


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def emu_cmd(orig):
    cmd = ["qemu-x86_64"]
    if os.path.isdir("/usr/x86_64-linux-gnu"):
        cmd += ["-L", "/usr/x86_64-linux-gnu"]
    return cmd + [orig]


def run_case(cwd, casar, orig, args):
    res = {}
    for tag, cmd in (("casar_silicon", [casar] + args),
                     ("full_emu_silicon", emu_cmd(orig) + args)):
        r = subprocess.run(cmd, capture_output=True, cwd=cwd,
                           timeout=TIMEOUT)
        res[tag] = {"exit": r.returncode, "stdout": r.stdout,
                    "stderr": r.stderr}
    gate = (res["casar_silicon"]["exit"] == res["full_emu_silicon"]["exit"]
            and res["casar_silicon"]["stdout"]
            == res["full_emu_silicon"]["stdout"])
    return gate, res


def main():
    if os.uname().machine not in ("aarch64", "arm64"):
        print("REFUSING: this experiment needs a real Armv8 host; "
              "current machine is %s" % os.uname().machine)
        return 1
    wd = tempfile.mkdtemp(prefix="silideli_")
    tools = {}
    for tool, sha in EXPECTED_SHA.items():
        d = os.path.join(HERE, "delivery", tool)
        casar = os.path.join(d, "casar.aarch64")
        orig = os.path.join(d, "orig.x86")
        if not (os.path.exists(casar) and os.path.exists(orig)):
            print("skip", tool, "(missing binaries)")
            continue
        got = sha256(orig)
        if got != sha:
            print("REFUSING %s: orig.x86 sha256 %s != pinned %s"
                  % (tool, got, sha))
            return 1
        tools[tool] = (casar, orig)
        print("== %s: orig.x86 sha256 pinned OK" % tool, flush=True)
    if not tools:
        print("nothing to do")
        return 1
    for name, data in INPUTS.items():
        p = os.path.join(wd, name)
        with open(p, "wb") as fh:
            fh.write(data)
        os.utime(p, (0, 0))
    report = {}
    all_pass = True
    for tool, (casar, orig) in sorted(tools.items()):
        cases = GZIP_CASES if tool == "gzip" else CAT_CASES
        rows = []
        for label, args in cases:
            gate, res = run_case(wd, casar, orig, args)
            row = {
                "case": label,
                "argv": args,
                "gate_stdout_byte_exact": gate,
                "exit_casar": res["casar_silicon"]["exit"],
                "exit_emu": res["full_emu_silicon"]["exit"],
                "stdout_bytes": len(res["casar_silicon"]["stdout"]),
                "stdout_sha256": hashlib.sha256(
                    res["casar_silicon"]["stdout"]).hexdigest()[:16],
                "stderr_sha256": hashlib.sha256(
                    res["casar_silicon"]["stderr"]).hexdigest()[:16],
            }
            rows.append(row)
            print("== %s %-8s gate=%s exit=%s stdout=%dB"
                  % (tool, label, gate, row["exit_casar"],
                     row["stdout_bytes"]), flush=True)
            all_pass = all_pass and gate
        report[tool] = rows
    doc = {
        "method": ("emitted whole binaries (gzip, cat) executed in "
                   "their own processes on real Armv8 silicon; casar "
                   "column = statically linked emitted AArch64 binary "
                   "natively, full_emu column = pinned original x86-64 "
                   "binary under qemu-x86_64 on the same host. Gate: "
                   "stdout byte-exact and exit codes equal across the "
                   "two configurations, per case."),
        "host": {"machine": os.uname().machine,
                 "release": os.uname().release},
        "orig_sha256": {t: EXPECTED_SHA[t] for t in tools},
        "cases": report,
        "gates_all_pass": all_pass,
    }
    with open(OUT_JSON, "w") as fh:
        json.dump(doc, fh, indent=1)
    print("wrote", OUT_JSON, "gates_all_pass=", doc["gates_all_pass"])
    shutil.rmtree(wd, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
