#!/bin/bash
# Cross-check the Swift port's byte-level logic against the canonical mxw01.py.
# Compiles the real MXW01Protocol.swift + BitmapCore.swift with a test driver,
# runs it and the Python reference, and diffs the results.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
IOS="$ROOT/ios/ThermalPrint/Model"
HERE="$ROOT/tools/verify"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "Compiling Swift core…"
swiftc -O \
  "$IOS/MXW01Protocol.swift" \
  "$IOS/BitmapCore.swift" \
  "$HERE/main.swift" \
  -o "$TMP/verify_swift"

"$TMP/verify_swift" > "$TMP/swift.txt"
"$ROOT/.venv/bin/python" "$HERE/py_reference.py" > "$TMP/py.txt"

python3 - "$TMP/swift.txt" "$TMP/py.txt" <<'PY'
import sys
sw = dict(l.split(" = ", 1) for l in open(sys.argv[1]) if " = " in l)
py = dict(l.split(" = ", 1) for l in open(sys.argv[2]) if " = " in l)

# Keys that MUST match byte-for-byte between Swift and Python.
exact = ["CRC 00", "CRC af", "CRC 03003000", "CRC 0102030405",
         "CMD_A1", "CMD_A2", "REQ_PAYLOAD", "CMD_A9",
         "PACK_LEN", "PACK_SHA"]
fails = 0
print("── Exact byte-level checks (Swift vs mxw01.py) ──")
for k in exact:
    s, p = sw.get(k, "?").strip(), py.get(k, "?").strip()
    ok = s == p
    fails += not ok
    print(f"  [{'OK ' if ok else 'FAIL'}] {k}: {s}" + ("" if ok else f"  != {p}"))

# Floyd–Steinberg must match the standard algorithm exactly.
s, p = sw.get("FS_SPEC", "?").strip(), py.get("FS_SPEC", "?").strip()
ok = s == p; fails += not ok
print(f"  [{'OK ' if ok else 'FAIL'}] FS_SPEC (Swift == spec): {ok}")

# Informational: how close our dither is to PIL's on the same gradient.
pil = py.get("FS_PIL", "").strip()
if pil and len(pil) == len(s):
    same = sum(a == b for a, b in zip(s, pil))
    print(f"  [i]  FS vs PIL agreement: {same}/{len(s)} px")

# Tone parity: Swift within ±1 of PIL.
def nums(x): return [int(v) for v in x.split()]
for key, sk, pk in [("Brightness", "BRIGHT_SW", "BRIGHT_PIL"),
                    ("Contrast", "CONTRAST_SW", "CONTRAST_PIL")]:
    a, b = nums(sw[sk]), nums(py[pk])
    md = max(abs(x - y) for x, y in zip(a, b))
    ok = md <= 1; fails += not ok
    print(f"  [{'OK ' if ok else 'FAIL'}] {key} max|Δ| vs PIL = {md}")
    print(f"        swift={a}")
    print(f"        pil  ={b}")

print()
print("RESULT:", "ALL PASS" if fails == 0 else f"{fails} FAILURE(S)")
sys.exit(1 if fails else 0)
PY