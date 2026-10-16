"""Diagnose one (HVSC relpath, 1-based song) of a DMC tune vs the sidtrace oracle.

Renders the pydmcsid player (``subtune = song - 1``) and the sidplayfp oracle
(``-o<song>``) for the same subtune, aligns them, and prints the verdict plus the
frames around the first divergence (honours ``$HVSC`` then ``tests/.tunecache``).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import numpy as np

from pysidtracker.oracle import (
    cached_sidtrace,
    sidtrace_cadence,
    sidtrace_grid,
)
from pysidtracker.sidid import identify, sidid_signatures
from pysidtracker.testing import fetch_tune, resolve_tune

from pydmcsid import DmcPlayer, DmcParseError, parse

REPO = Path(__file__).resolve().parent.parent
CACHE = Path(os.environ.get("DMC_TUNECACHE", str(REPO / "tests" / ".tunecache")))
ORACLE = Path(os.environ.get("DMC_ORACLE_DIAG", str(REPO / ".oracle-diag")))
LEAD = 8

REG_NAMES = [
    f"v{v+1}_{r}"
    for v in range(3)
    for r in ("freq_lo", "freq_hi", "pw_lo", "pw_hi", "ctrl", "ad", "sr")
] + ["fc_lo", "fc_hi", "res_filt", "mode_vol"]


def render_lib(data: bytes, song: int, nframes: int) -> np.ndarray:
    player = DmcPlayer(parse(data), subtune=song - 1)
    return np.asarray(player.render_grid(nframes + LEAD), np.int16)


def render_oracle(path: Path, song: int, seconds: float) -> np.ndarray:
    csv = ORACLE / f"{path.stem}_s{song}.csv.zst"
    rows = cached_sidtrace(
        path,
        csv,
        seconds=seconds,
        song=song,
        user=f"{os.getuid()}:{os.getgid()}",
        extra_args=("-f8000",),
    )
    cpf = sidtrace_cadence(rows)
    grid = np.asarray(sidtrace_grid(rows, cycles_per_frame=cpf), np.int16)
    return grid.reshape(-1, 25)


def aligned(oracle: np.ndarray, lib: np.ndarray, lead: int):
    if lead >= 0:
        return oracle, lib[lead : lead + len(oracle)]
    return oracle[-lead:], lib[: len(oracle) + lead]


def scan(oracle: np.ndarray, lib: np.ndarray):
    n = len(oracle)
    leads = sorted(range(-min(LEAD, n - 1), LEAD + 1), key=abs)
    best = None
    for lead in leads:
        o, l = aligned(oracle, lib, lead)
        bad = (o != l).any(axis=1)
        first = int(np.argmax(bad)) if bad.any() else n
        nbad = int(bad.sum())
        if best is None or first > best[1]:
            best = (lead, first, nbad, o, l)
    return best


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="DMC vs sidtrace oracle diagnostic")
    ap.add_argument("relpath")
    ap.add_argument("song", nargs="?", type=int, default=1)
    ap.add_argument("frames", nargs="?", type=int, default=300)
    ap.add_argument("-c", "--context", type=int, default=3)
    args = ap.parse_args(argv)

    path = resolve_tune(args.relpath, cache_dir=CACHE) or fetch_tune(
        args.relpath, cache_dir=CACHE
    )
    data = path.read_bytes()

    name = identify(data, sidid_signatures())
    try:
        tune = parse(data)
    except DmcParseError as exc:
        print(f"relpath : {args.relpath}\nsidid   : {name}\nparse   : {exc}")
        return 2
    print(f"relpath   : {args.relpath}")
    print(f"sidid     : {name}")
    print(f"song      : {args.song} (1-based) of {tune.songs}")
    print(f"engine    : {type(DmcPlayer(tune)).__name__}")
    print(f"variant   : {tune.variant()}")
    print(f"byte_exact: {tune.byte_exact()}")
    print(f"wrapped   : {tune.play != (tune.base + 0x3) & 0xFFFF}")

    lib = render_lib(data, args.song, args.frames)
    oracle = render_oracle(path, args.song, args.frames / 50.0 + 2)[: args.frames]
    print(f"oracle frames: {len(oracle)}")

    lead, first, nbad, o, l = scan(oracle, lib)
    verdict = "exact" if nbad == 0 else "differs"
    print(
        f"verdict   : {verdict}  lead={lead} "
        f"first_bad={first if nbad else '-'} bad_frames={nbad}"
    )
    if nbad == 0:
        return 0
    for f in range(max(0, first - args.context), min(len(l), first + args.context + 1)):
        ov = o[f] if f < len(o) else o[-1]
        lv = l[f]
        print(f"{f:6d} O {' '.join(f'{x:02x}' for x in ov)}")
        print(f"{'':6} L {' '.join(f'{x:02x}' for x in lv)}")
        if (ov != lv).any():
            print("".join("^^ " if x != y else "   " for x, y in zip(ov, lv)))
    firstdiff = int(np.flatnonzero((o != l).any(axis=1))[0])
    print(
        "first-diff regs:",
        " ".join(REG_NAMES[r] for r in np.flatnonzero(o[firstdiff] != l[firstdiff])),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
