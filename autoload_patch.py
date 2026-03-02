import os
from pathlib import Path

AUTOLOAD_BLOCK = """\
████████████████████████████████████████████████████████
POLY ALPHA v2 — AUTONOMOUS CONVEX ENGINE (AUTOLOAD)
████████████████████████████████████████████████████████

MODE: FULLY AUTONOMOUS
PHASE: PAPER VALIDATION
CAPITAL: LOCKED (NO LIVE EXECUTION)

MAX_RISK_PER_TRADE = 0.02
MAX_THEME_EXPOSURE = 0.05
MAX_TOTAL_EXPOSURE = 0.15
SOFT_DRAWDOWN = 0.10
HARD_DRAWDOWN = 0.20
MIN_EDGE_THRESHOLD = 0.05

PRIORITY:
1. Survival
2. Edge
3. Correlation
4. Convexity
5. Aggression
6. Ego = 0

████████████████████████████████████████████████████████
END AUTOLOAD
████████████████████████████████████████████████████████

"""


def inject_autoload(target_file):
    path = Path(target_file)

    if not path.exists():
        print(f"[ERROR] File {target_file} does not exist.")
        return

    original = path.read_text()

    if "POLY ALPHA v2 — AUTONOMOUS CONVEX ENGINE" in original:
        print("[INFO] AUTOLOAD already present.")
        return

    new_content = AUTOLOAD_BLOCK + original
    path.write_text(new_content)

    print(f"[OK] AUTOLOAD injected into {target_file}")


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        print("Usage: python autoload_patch.py <file>")
    else:
        inject_autoload(sys.argv[1])
