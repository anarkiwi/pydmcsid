"""Corpus test: real HVSC DMC tunes parse and detect as DIRECT.

Runs the reader against a deterministic, representative sample of real HVSC DMC
tunes (``fixtures/dmc_corpus.txt`` -- paths only; HVSC works are copyright and are
never committed).  Resolved under a local ``$HVSC`` tree: the test RUNS for real
when ``$HVSC`` is present and SKIPS cleanly when it is not (or when a listed tune
is absent from this HVSC revision).

Each sampled tune must ``parse``/``read`` successfully and classify as
:attr:`PlayroutineKind.DIRECT` -- the DMC player is resident, so recognition
succeeds statically with no emulated init.
"""

import os
from pathlib import Path

import pytest

import pydmcsid
from pydmcsid import DmcSidParser, constants
from pysidtracker import PlayroutineKind

FIXTURES = Path(__file__).resolve().parent / "fixtures"
HVSC = os.environ.get("HVSC")


def _corpus_paths():
    lines = (FIXTURES / "dmc_corpus.txt").read_text(encoding="utf-8").splitlines()
    return [l.strip() for l in lines if l.strip() and not l.startswith("#")]


CORPUS = _corpus_paths()


def _resolve(rel):
    if not HVSC:
        pytest.skip("HVSC not set; DMC corpus test needs a local HVSC tree")
    path = Path(HVSC) / rel
    if not path.exists():
        pytest.skip("tune absent from this HVSC revision: %s" % rel)
    return path


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
