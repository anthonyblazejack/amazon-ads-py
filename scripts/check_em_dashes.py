"""Fail if any tracked text file contains an em dash (U+2014).

The project writes with commas, colons, semicolons and parentheses instead. Run from the
repository root; CI runs it on every push.
"""

from __future__ import annotations

import subprocess
import sys

EM_DASH = chr(0x2014)
TEXT_SUFFIXES = (".py", ".md", ".yml", ".yaml", ".toml", ".json", ".txt", ".cfg")


def main() -> int:
    files = subprocess.run(
        ["git", "ls-files"], capture_output=True, text=True, check=True
    ).stdout.splitlines()
    found = 0
    for name in files:
        if not name.endswith(TEXT_SUFFIXES):
            continue
        with open(name, encoding="utf-8", errors="replace") as handle:
            for number, line in enumerate(handle, 1):
                if EM_DASH in line:
                    print(f"{name}:{number}: {line.rstrip()}")
                    found += 1
    if found:
        print(f"{found} line(s) with an em dash; use a comma, colon, semicolon or parentheses.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
