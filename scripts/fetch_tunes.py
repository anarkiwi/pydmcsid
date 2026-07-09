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
import time
import urllib.error
import urllib.request
from pathlib import Path

# Download attempts before a tune is declared unreachable (override via env).
RETRIES = int(os.environ.get("FETCH_RETRIES", "3"))


class FetchError(RuntimeError):
    """A tune could not be fetched (mirror unreachable or the tune not found)."""


REPO = Path(__file__).resolve().parent.parent
CACHE = Path(os.environ.get("DMC_TUNECACHE", str(REPO / "tests" / ".tunecache")))

# Public HVSC mirror.  Override with ``$HVSC_MIRROR``; the relative HVSC path
# is appended verbatim.
MIRROR = os.environ.get("HVSC_MIRROR", "https://hvsc.brona.dk/HVSC/C64Music").rstrip(
    "/"
)

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


def _is_sid(data: bytes) -> bool:
    return data[:4] in (b"PSID", b"RSID")


def _download(relpath: str) -> bytes:
    """Download ``relpath`` from the mirror, retrying transient failures.

    Raises :class:`FetchError` on a genuine 404 (tune not on the mirror) or when
    the mirror stays unreachable after :data:`RETRIES` attempts.
    """
    url = f"{MIRROR}/{relpath}"
    req = urllib.request.Request(url, headers={"User-Agent": "pydmcsid/fetch_tunes"})
    last_err = None
    for attempt in range(RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:  # nosec B310 (https)
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise FetchError("%s: not found on mirror" % relpath) from exc
            last_err = exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_err = exc
        if attempt + 1 < RETRIES:
            time.sleep(min(2**attempt, 5))
    raise FetchError(
        "%s: mirror unreachable after %d attempts (%s)" % (relpath, RETRIES, last_err)
    )


def fetch(relpath: str, *, force: bool = False) -> Path:
    """Fetch ``relpath`` from the HVSC mirror into the cache; return its path.

    Honours a local HVSC tree via ``$HVSC`` (copied into the cache) before
    hitting the network, so a developer with a local mirror needs no download.
    Raises :class:`FetchError` when the tune is genuinely unreachable (so callers
    can skip only that tune, not the whole suite).
    """
    relpath = relpath.lstrip("/")
    dest = CACHE / relpath
    if dest.exists() and not force:
        return dest
    local = os.environ.get("HVSC")
    if local and (Path(local) / relpath).exists():
        data = (Path(local) / relpath).read_bytes()
    else:
        data = _download(relpath)
    if not _is_sid(data):
        raise FetchError("%s: not a SID file (magic %r)" % (relpath, data[:4]))
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return dest


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
