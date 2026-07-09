"""Corpus test: real HVSC DMC tunes parse and detect as DIRECT.

Runs the reader against a deterministic, representative sample of real HVSC DMC
tunes (``fixtures/dmc_corpus.txt`` -- paths only; HVSC works are copyright and are
never committed).  Each tune is FETCHED + CACHED on demand (HVSC mirror, honouring
a local ``$HVSC`` tree) into the gitignored cache, so the test RUNS for real in CI
-- it skips an individual tune only if that tune is genuinely unreachable.

Each sampled tune must ``parse``/``read`` successfully and classify as
:attr:`PlayroutineKind.DIRECT` -- the DMC player is resident, so recognition
succeeds statically with no emulated init.
"""

from pathlib import Path

import pytest

import fetch_tunes
import pydmcsid
from pydmcsid import DmcSidParser, constants
from pysidtracker import PlayroutineKind

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _corpus_paths():
    lines = (FIXTURES / "dmc_corpus.txt").read_text(encoding="utf-8").splitlines()
    return [l.strip() for l in lines if l.strip() and not l.startswith("#")]


CORPUS = _corpus_paths()


def _resolve(rel):
    try:
        return fetch_tunes.fetch(rel)
    except fetch_tunes.FetchError as exc:
        pytest.skip("tune unreachable: %s" % exc)


def test_corpus_nonempty():
    """The committed corpus list is non-trivial (guards an emptied fixture)."""
    assert len(CORPUS) >= 100


@pytest.mark.parametrize("rel", CORPUS)
def test_corpus_parses(rel):
    """A real DMC tune parses into a resident-image Song carrying the signature."""
    path = _resolve(rel)
    song = pydmcsid.read(str(path))
    assert song.is_dmc()
    assert song.load <= song.base < song.load + constants.DMC_TABLE_SCAN
    assert song.image_len > 0
    assert song.songs >= 1


@pytest.mark.parametrize("rel", CORPUS)
def test_corpus_detect_direct(rel):
    """A real DMC tune classifies as direct-load (resident player, no init run)."""
    path = _resolve(rel)
    detection = DmcSidParser().detect(str(path))
    assert detection.kind is PlayroutineKind.DIRECT
