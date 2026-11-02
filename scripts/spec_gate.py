"""Record or check a playspec's audible output over a tune list, a chunk of tunes at a time.

    python scripts/spec_gate.py record|check REFDIR SPEC.play TUNES.csv --trace PYST/tools/spec_trace.py
A check compares registers and per-voice gate and test edges; where the reference ran the tune's own 6502 only registers.
"""

import argparse
import csv
import gzip
import importlib
import os
import pickle
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from tqdm import tqdm


def first_difference(trace, want, have):
    """``(call, kind)`` of the first audible difference, else None.

    Edges differing on a call whose reference has no writes (the tune's own 6502 ran it) are
    ``blind``: only the registers can be compared there.
    """
    if isinstance(want, str) or isinstance(have, str):
        return (
            None if isinstance(want, str) == isinstance(have, str) else (-1, "refusal")
        )
    blind = None
    for call, (a, b) in enumerate(zip(trace.audible(want), trace.audible(have))):
        if a[0] != b[0]:
            return call, "registers"
        if a[1] != b[1]:
            if want[call][0]:
                return call, "edges"
            blind = blind or (call, "blind")
    return blind


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("mode", choices=("record", "check"))
    parser.add_argument("refdir")
    parser.add_argument("spec")
    parser.add_argument("tunes")
    parser.add_argument("--trace", required=True)
    parser.add_argument("--chunk", type=int, default=500)
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--frames", type=int, default=3000)
    args = parser.parse_args()
    sys.path.insert(0, os.path.dirname(os.path.abspath(args.trace)))
    trace = importlib.import_module(Path(args.trace).stem)
    with open(args.tunes, encoding="utf-8") as handle:
        paths = [row["path"] for row in csv.DictReader(handle)]
    refdir = Path(args.refdir)
    refdir.mkdir(parents=True, exist_ok=True)
    bad, blind = [], []
    with ProcessPoolExecutor(args.jobs) as pool:
        for start in tqdm(range(0, len(paths), args.chunk), unit="chunk"):
            jobs = [
                (p, args.spec, args.frames) for p in paths[start : start + args.chunk]
            ]
            got = dict(pool.map(trace.trace, jobs))
            ref = refdir / f"{start:05d}.pkl.gz"
            if args.mode == "record":
                with gzip.open(ref, "wb") as out:
                    pickle.dump(got, out)
                continue
            with gzip.open(ref, "rb") as handle:
                want = pickle.load(handle)
            for path, have in got.items():
                found = first_difference(trace, want[path], have)
                if found and found[1] == "blind":
                    blind.append((path, found))
                elif found:
                    bad.append((path, found))
    for path, (call, kind) in bad + blind:
        print(Path(path).name, "call", call, kind)
    print("differ", len(bad), "of", len(paths), "; edges unseen only:", len(blind))
    return len(bad)


if __name__ == "__main__":
    sys.exit(main())
