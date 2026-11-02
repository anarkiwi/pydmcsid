"""Record or check a playspec against a reference over a tune list in chunks.

    python scripts/spec_gate.py record REFDIR OLD.play TUNES.csv --trace PYST/tools/spec_trace.py
    python scripts/spec_gate.py check  REFDIR NEW.play TUNES.csv --trace PYST/tools/spec_trace.py

``TUNES.csv`` is a list from ``gate_tunes.py``. Each chunk of tunes is one
``spec_trace.py`` run and one reference file in ``REFDIR``, so memory stays bounded.
``check`` exits with the number of tunes whose audible output differs.
"""

import argparse
import csv
import subprocess
import sys
import tempfile
from pathlib import Path

from tqdm import tqdm


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
    with open(args.tunes, encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    refdir = Path(args.refdir)
    refdir.mkdir(parents=True, exist_ok=True)
    bad = 0
    chunks = range(0, len(rows), args.chunk)
    for start in tqdm(chunks, unit="chunk"):
        with tempfile.NamedTemporaryFile("w", suffix=".csv", newline="") as part:
            writer = csv.DictWriter(part, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows[start : start + args.chunk])
            part.flush()
            cmd = [sys.executable, args.trace, args.mode]
            cmd += [str(refdir / f"{start:05d}.pkl.gz"), args.spec, part.name]
            cmd += ["--jobs", str(args.jobs), "--frames", str(args.frames)]
            done = subprocess.run(cmd, check=False, text=True, capture_output=True)
        if args.mode == "check":
            bad += done.returncode
            sys.stdout.write(done.stdout)
        elif done.returncode:
            sys.stderr.write(done.stderr)
            return done.returncode
    print("differ", bad, "of", len(rows))
    return bad


if __name__ == "__main__":
    sys.exit(main())
