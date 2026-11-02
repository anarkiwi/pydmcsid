"""Split a survey ``find`` CSV into one tune list per playspec file, for ``spec_trace.py``.

    python scripts/gate_tunes.py HVSC SURVEY.csv OUTDIR

Writes ``OUTDIR/<spec>.csv`` (``path`` absolute, ``generation``) for the tunes whose
start subtune the package binds, and ``OUTDIR/refused.csv`` for those it does not.
"""

import argparse
import csv
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from tqdm import tqdm

from pydmcsid.reader import parse
from pydmcsid.spec import SPEC_FILES, binding


def classify(path):
    """``(path, generation, spec file)``, or ``(path, reason, None)`` when refused."""
    try:
        song = parse(Path(path).read_bytes())
        body = binding(song, max(song.start_song - 1, 0))[1]
        generation = body.variant()
        return path, generation, SPEC_FILES[generation]
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return path, f"{type(exc).__name__}: {exc}"[:80], None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("hvsc")
    parser.add_argument("survey")
    parser.add_argument("out")
    parser.add_argument("--jobs", type=int, default=os.cpu_count())
    args = parser.parse_args()
    with open(args.survey, encoding="utf-8") as handle:
        paths = [os.path.join(args.hvsc, row["path"]) for row in csv.DictReader(handle)]
    os.makedirs(args.out, exist_ok=True)
    rows = {}
    with ProcessPoolExecutor(args.jobs) as pool:
        for path, what, spec in tqdm(
            pool.map(classify, paths, chunksize=32), total=len(paths), unit="tune"
        ):
            rows.setdefault(spec, []).append((path, what))
    for spec, items in rows.items():
        name = "refused" if spec is None else spec.removesuffix(".play")
        with open(os.path.join(args.out, f"{name}.csv"), "w", encoding="utf-8") as out:
            writer = csv.writer(out)
            writer.writerow(["path", "generation" if spec else "reason"])
            writer.writerows(sorted(items))
        print(name, len(items))
    return 0


if __name__ == "__main__":
    sys.exit(main())
