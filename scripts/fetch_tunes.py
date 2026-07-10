#!/usr/bin/env python3
"""Download DMC ``.sid`` test tunes into a gitignored cache.

DMC tunes are HVSC copyright works and are **never** committed (see
``.gitignore``).  They are fetched on demand from a public HVSC mirror into
``tests/.tunecache/`` (gitignored), so a fresh clone / CI run obtains them
with no machine-specific paths.  The byte-exact player tests FETCH the tune
(they do not skip): the tunes are required for the byte-exact validation.

Usage::

    python scripts/fetch_tunes.py            # fetch every test tune
    python scripts/fetch_tunes.py --list     # print id -> HVSC path

Programmatic::

    from fetch_tunes import fetch, TUNES
    sid_path = fetch(TUNES["ode"])
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from pysidtracker.testing import (
    DEFAULT_MIRROR,
    TuneFetchError,
    fetch_tune,
    resolve_tune,
)

# Back-compat name: the corpus tests catch ``fetch_tunes.FetchError``.
FetchError = TuneFetchError

REPO = Path(__file__).resolve().parent.parent
CACHE = Path(os.environ.get("DMC_TUNECACHE", str(REPO / "tests" / ".tunecache")))

# Public HVSC mirror (override with ``$HVSC_MIRROR``, honoured by the shared
# fetcher).
MIRROR = DEFAULT_MIRROR

# id -> HVSC relative path (the DMC byte-exact validation references).
TUNES = {
    "ode": "MUSICIANS/A/Ass_It/Ode_to_Music.sid",
    "faces": "DEMOS/A-F/Faces.sid",
    "fear": "DEMOS/A-F/Fear_Me.sid",
    "wladca": "GAMES/S-Z/Wladca.sid",
    "summertime": "MUSICIANS/B/Bart/Summertime.sid",
    "action_tank": "MUSICIANS/R/Robric/Action_Tank_2.sid",
    "glorious": "MUSICIANS/B/Bakker_Nantco/Glorious.sid",
    "rocket": "MUSICIANS/B/Bayliss_Richard/Rocket_n_Roll.sid",
    "techno_bah": "MUSICIANS/D/Doxx/Techno_BAH.sid",
}


def fetch(relpath: str, *, force: bool = False) -> Path:
    """Resolve ``relpath`` to a cached ``.sid`` path via the shared fetcher.

    Honours a local HVSC tree (``$HVSC``) first, then the gitignored cache, then
    downloads from the mirror. Raises :class:`FetchError` when the tune is
    genuinely unreachable (so callers can skip only that tune, not the whole
    suite). ``force`` re-downloads even when cached.
    """
    relpath = relpath.lstrip("/")
    if not force:
        path = resolve_tune(relpath, cache_dir=CACHE)
        if path is not None:
            return path
    return fetch_tune(relpath, cache_dir=CACHE, mirror=MIRROR, force=force)


def main(argv=None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--id", help="only this tune id")
    parser.add_argument("--force", action="store_true", help="re-download")
    parser.add_argument("--list", action="store_true", help="print id -> path")
    args = parser.parse_args(argv)
    if args.list:
        for tid, rel in TUNES.items():
            print("%s\t%s" % (tid, rel))
        return 0
    for tid in [args.id] if args.id else list(TUNES):
        print("%s: %s -> %s" % (tid, TUNES[tid], fetch(TUNES[tid], force=args.force)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
