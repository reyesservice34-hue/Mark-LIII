#!/usr/bin/env python3
"""Write MIA_LEERPLAN_v1.1.txt from this script."""
import sys

PATH = "/root/Mark-LIII/knowledge_src/MIA_LEERPLAN_v1.1.txt"

data = sys.stdin.read()
if not data:
    print("ERROR: pipe content in", file=sys.stderr)
    sys.exit(1)

with open(PATH, "w", encoding="utf-8") as f:
    f.write(data)

print(f"Wrote {len(data)} chars -> {PATH}")
