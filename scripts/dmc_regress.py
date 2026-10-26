"""Behaviour-neutrality check: dump every DMC tune's decode and grid digests, then diff.

``dump ROOT OUT`` writes, per DMC tune under ROOT, the parse outcome, variant,
byte-exactness and per subtune the engine, its decoded scalar attributes and the
SHA-256 of the first ``--frames`` grid rows; ``diff BEFORE AFTER`` lists changes;
``oracle REL SONG`` checks one HVSC subtune against the sidtrace oracle.
"""

import argparse
import hashlib
import json
import os
import sys
from multiprocessing import Pool
from pathlib import Path

import numpy as np
from pysidtracker import SidImage
from pysidtracker.errors import SidError
from pysidtracker.testing import assert_oracle_match
from tqdm import tqdm

from pydmcsid import DmcPlayer, find_dmc_base, parse
from pydmcsid.errors import DmcParseError


def _scalars(player) -> dict:
    return {
        k: v
        for k, v in sorted(vars(player).items())
        if isinstance(v, (bool, int, str, type(None)))
    }


def _subtune(song, sub: int, frames: int) -> dict:
    try:
        player = DmcPlayer(song, subtune=sub)
        attrs = _scalars(player)
        grid = player.render_grid(frames)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return {"exc": f"{type(exc).__name__}: {exc}"}
    return {
        "engine": type(player).__name__,
        "attrs": attrs,
        "grid": hashlib.sha256(np.asarray(grid, np.int16).tobytes()).hexdigest(),
    }


def record(path: Path, root: Path, frames: int):
    """The regression record of the tune at ``path``, or ``None`` if not a DMC."""
    data = path.read_bytes()
    try:
        image = SidImage.from_bytes(data)
    except SidError:
        return None
    if find_dmc_base(image.mem, image.load) is None:
        return None
    rec = {"path": str(path.relative_to(root))}
    try:
        song = parse(data)
    except DmcParseError as exc:
        return {**rec, "error": str(exc)}
    rec.update(variant=song.variant(), byte_exact=song.byte_exact())
    rec["subtunes"] = [_subtune(song, sub, frames) for sub in range(song.songs)]
    return rec


def _record(args):
    return record(*args)


def dump(root: Path, out: Path, frames: int, jobs: int) -> None:
    """Write one sorted-key JSON line per DMC tune under ``root`` to ``out``."""
    paths = sorted(root.rglob("*.sid"))
    work = [(p, root, frames) for p in paths]
    with Pool(jobs) as pool, out.open("w", encoding="utf-8") as fh:
        for rec in tqdm(
            pool.imap(_record, work, chunksize=8), total=len(work), unit="tune"
        ):
            if rec is not None:
                fh.write(json.dumps(rec, sort_keys=True) + "\n")


def diff(before: Path, after: Path) -> int:
    """Print the tunes whose records differ; return how many do."""

    def load(path):
        with path.open(encoding="utf-8") as fh:
            return {r["path"]: r for r in map(json.loads, fh)}

    old, new = load(before), load(after)
    keys = sorted(old.keys() | new.keys())
    bad = [k for k in keys if old.get(k) != new.get(k)]
    for key in bad:
        print(f"{key}\n  - {old.get(key)}\n  + {new.get(key)}")
    print(f"{len(bad)} of {len(keys)} tunes differ")
    return len(bad)


def oracle(rel: str, song: int, frames: int) -> bool:
    """Whether 1-based ``song`` of HVSC tune ``rel`` matches the sidtrace oracle."""
    try:
        assert_oracle_match(
            rel,
            lambda data: DmcPlayer(data, subtune=song - 1),
            frames=frames,
            song=song,
            max_lead=8,
        )
    except AssertionError as exc:
        print(f"{rel} song {song}: MISMATCH {str(exc).splitlines()[0]}")
        return False
    print(f"{rel} song {song}: match ({frames} frames)")
    return True


def main(argv=None) -> int:
    """CLI entry point."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    dp = sub.add_parser("dump")
    dp.add_argument("root", type=Path)
    dp.add_argument("out", type=Path)
    dp.add_argument("--frames", type=int, default=3000)
    dp.add_argument("--jobs", type=int, default=os.cpu_count())
    cp = sub.add_parser("diff")
    cp.add_argument("before", type=Path)
    cp.add_argument("after", type=Path)
    op = sub.add_parser("oracle")
    op.add_argument("rel")
    op.add_argument("song", type=int)
    op.add_argument("--frames", type=int, default=1500)
    args = ap.parse_args(argv)
    if args.cmd == "oracle":
        return 0 if oracle(args.rel, args.song, args.frames) else 1
    if args.cmd == "dump":
        dump(args.root, args.out, args.frames, args.jobs)
        return 0
    return 1 if diff(args.before, args.after) else 0


if __name__ == "__main__":
    sys.exit(main())
