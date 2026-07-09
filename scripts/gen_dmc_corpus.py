#!/usr/bin/env python3
"""Regenerate ``tests/fixtures/dmc_corpus.txt`` from a local HVSC tree.

Emits a deterministic, representative sample of HVSC DMC tunes that carry the
canonical DMC player body pydmcsid models (recognised by
:func:`pydmcsid.reader.find_dmc_base`).  Only relative HVSC PATHS are written --
HVSC works are copyright and are never committed.

The candidate paths come from a sidid enumeration (the DMC / DMC_V4.x / DMC_V5.x /
DMC_V6.x families), one relative path per line::

    SIDIDCFG=.../sidid.cfg sidid $HVSC | grep -E 'DMC' | ... > dmc_candidates.txt
    HVSC=/path/to/C64Music python scripts/gen_dmc_corpus.py dmc_candidates.txt

Every candidate is re-checked with the real recogniser, so the sample only ever
contains tunes pydmcsid actually recognises; unsupported DMC sub-versions (whose
player body differs) are dropped.  The selection spans both init sub-versions and
the base-at-load / stub-prepended layouts.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from pysidtracker import SidImage

from pydmcsid.reader import find_dmc_base

REPO = Path(__file__).resolve().parent.parent
CORPUS = REPO / "tests" / "fixtures" / "dmc_corpus.txt"

HEADER = [
    "Representative sample of HVSC DMC (Demo Music Creator) tunes carrying the",
    "canonical DMC player body this reader models: a 4-entry JMP table whose",
    "play/stop/FUN targets sit at the fixed $85/$62f/$63e offsets from the table",
    "base (init entry varies: $1d or $37).  Paths only (HVSC works are copyright,",
    "never committed); the corpus test resolves them under $HVSC and skips cleanly",
    "when $HVSC is unset.  Selection is deterministic (see scripts/gen_dmc_corpus.py):",
    "it spans both init sub-versions, base-at-load and stub-prepended (base != load)",
    "layouts, and a range of load addresses and HVSC collections.",
]


def _stride(seq, count):
    if len(seq) <= count:
        return list(seq)
    step = len(seq) / count
    return [seq[int(i * step)] for i in range(count)]


def build(candidates, hvsc):
    """Return the sampled relative paths for ``candidates`` under ``hvsc``."""
    rows = []
    for rel in sorted(set(candidates)):
        path = hvsc / rel
        if not path.exists():
            continue
        try:
            image = SidImage.from_bytes(path.read_bytes())
        except Exception:  # pylint: disable=broad-except
            continue
        base = find_dmc_base(image.mem, image.load)
        if base is None:
            continue
        off = base - image.load
        init_rel = ((image.mem[base + 1] | (image.mem[base + 2] << 8)) - base) & 0xFFFF
        rows.append((rel, off, init_rel))
    nonzero = [r for r in rows if r[1] != 0]
    init37 = [r for r in rows if r[1] == 0 and r[2] == 0x37]
    init1d = [r for r in rows if r[1] == 0 and r[2] == 0x1D]
    other = [r for r in rows if r[1] == 0 and r[2] not in (0x1D, 0x37)]
    picked = (
        _stride(nonzero, 25)
        + _stride(init37, 55)
        + _stride(init1d, 60)
        + _stride(other, 10)
    )
    return sorted({r[0] for r in picked})


def main(argv=None) -> int:
    """CLI entry point."""
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print("usage: gen_dmc_corpus.py <candidates.txt>", file=sys.stderr)
        return 2
    hvsc = Path(os.environ["HVSC"])
    candidates = [l.strip() for l in open(argv[0], encoding="utf-8") if l.strip()]
    sample = build(candidates, hvsc)
    with open(CORPUS, "w", encoding="utf-8") as handle:
        for line in HEADER:
            handle.write("# " + line + "\n")
        for rel in sample:
            handle.write(rel + "\n")
    print("%d tunes -> %s" % (len(sample), CORPUS))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
