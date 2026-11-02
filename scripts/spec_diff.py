"""Play one tune with two playspec files and show the first call whose audible output differs.

python scripts/spec_diff.py OLD.play NEW.play TUNE.sid [--frames N] [--subtune S]
"""

import argparse
import dataclasses
import sys
from pathlib import Path

from pysidtracker import find_format
from pysidtracker.playspec import load


def player(raw: bytes, path: str, subtune):
    """The format's spec player for the tune with the spec file at ``path``."""
    fmt = find_format(raw)
    alt = load(path)
    use = dataclasses.replace(
        fmt, spec=lambda m: dataclasses.replace(fmt.spec(m), spec=alt)
    )
    return use.spec_player(fmt.parser.parse(raw), native=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("old")
    parser.add_argument("new")
    parser.add_argument("tune")
    parser.add_argument("--frames", type=int, default=3000)
    args = parser.parse_args()
    raw = Path(args.tune).read_bytes()
    old, new = player(raw, args.old, None), player(raw, args.new, None)
    for call in range(args.frames):
        old.play_frame()
        new.play_frame()
        if tuple(old.snapshot()) != tuple(new.snapshot()):
            print("call", call)
            print("old", old.writes, tuple(old.snapshot()))
            print("new", new.writes, tuple(new.snapshot()))
            return 1
    print("equal over", args.frames, "calls")
    return 0


if __name__ == "__main__":
    sys.exit(main())
