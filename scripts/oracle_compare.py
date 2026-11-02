"""List the tunes whose spec player's register grid differs from the tune's own 6502 run.

    python scripts/oracle_compare.py TUNES.csv OUT.csv [--frames N] [--jobs J]

``TUNES.csv`` is a list from ``gate_tunes.py``. Each row of ``OUT.csv`` is a tune with the first
call that differs, the refusal, or that the player left the native core. Run it with two installs of the package to find the tunes both
differ on.
"""

import argparse
import csv
import sys
from concurrent.futures import ProcessPoolExecutor

from tqdm import tqdm

MASK = {3: 0x0F, 10: 0x0F, 17: 0x0F, 21: 0x07}


def audible(grid):
    """``grid`` with the bits the chip ignores cleared."""
    return [tuple(r & ~MASK.get(i, 0) for i, r in enumerate(row)) for row in grid]


def compare(job):
    """``(path, first differing call or None, refusal, python)`` of a tune against its 6502; the
    last is whether the spec player fell back to Python."""
    path, frames = job
    # pylint: disable=import-outside-toplevel
    from pysidtracker.oracle import EmuPlayer

    from pydmcsid import DmcPlayer, parse

    try:
        song = parse(open(path, "rb").read())
        subtune = song.start_song - 1
        got = DmcPlayer(song, subtune)
        image = bytes(got.song.mem[got.song.load : got.song.load + got.song.image_len])
        want = EmuPlayer(image, got.song.load, got.song.init, got.song.play, subtune)
        have, ref = audible(got.render_grid(frames)), audible(want.render_grid(frames))
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return path, None, f"{type(exc).__name__}: {str(exc)[:80]}", False
    call = next((i for i, (a, b) in enumerate(zip(have, ref)) if a != b), None)
    return path, call, "", not got._fast  # pylint: disable=protected-access


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("tunes")
    parser.add_argument("out")
    parser.add_argument("--frames", type=int, default=1000)
    parser.add_argument("--jobs", type=int, default=2)
    args = parser.parse_args()
    with open(args.tunes, encoding="utf-8") as handle:
        jobs = [(row["path"], args.frames) for row in csv.DictReader(handle)]
    with ProcessPoolExecutor(args.jobs) as pool:
        rows = list(
            tqdm(pool.map(compare, jobs, chunksize=4), total=len(jobs), unit="tune")
        )
    bad = [row for row in rows if row[1] is not None or row[2] or row[3]]
    with open(args.out, "w", newline="", encoding="utf-8") as out:
        writer = csv.writer(out)
        writer.writerow(["path", "call", "refusal", "python"])
        writer.writerows(bad)
    print("differ", len(bad), "of", len(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
