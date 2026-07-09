"""Corpus test for the DMC body clusters the generalised recogniser now handles.

Complements ``test_corpus.py`` with real HVSC representatives of the clusters
the original 4-JMP anchor rejected (2-entry-dispatch same-body builds) or the
absolute-operand fix repaired (relocated builds), plus the later-generation
(init-``$1d``) body that is recognised + parsed but not yet byte-exact.  Paths
only (``fixtures/dmc_clusters.txt``); resolved under ``$HVSC`` and skips cleanly
when unset or when a listed tune is absent from this HVSC revision.
"""

import os
from pathlib import Path

import pytest

import pydmcsid
from pydmcsid import DmcSidParser
from pysidtracker import PlayroutineKind

FIXTURES = Path(__file__).resolve().parent / "fixtures"
HVSC = os.environ.get("HVSC")


def _cluster_rows():
    rows = []
    text = (FIXTURES / "dmc_clusters.txt").read_text(encoding="utf-8")
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        flag, rel = line.split(None, 1)
        rows.append((rel, flag == "1"))
    return rows


CLUSTERS = _cluster_rows()


def _resolve(rel):
    if not HVSC:
        pytest.skip("HVSC not set; DMC cluster corpus test needs a local HVSC tree")
    path = Path(HVSC) / rel
    if not path.exists():
        pytest.skip("tune absent from this HVSC revision: %s" % rel)
    return path


def test_clusters_nonempty():
    """The committed cluster list is non-trivial."""
    assert len(CLUSTERS) >= 8
    assert any(bx for _rel, bx in CLUSTERS)
    assert any(not bx for _rel, bx in CLUSTERS)


@pytest.mark.parametrize("rel,byte_exact", CLUSTERS)
def test_cluster_recognised(rel, byte_exact):
    """Every cluster representative parses, is DMC, and detects as direct-load."""
    path = _resolve(rel)
    song = pydmcsid.read(str(path))
    assert song.is_dmc()
    assert song.byte_exact() is byte_exact
    detection = DmcSidParser().detect(str(path))
    assert detection.kind is PlayroutineKind.DIRECT


@pytest.mark.parametrize("rel", [rel for rel, byte_exact in CLUSTERS if byte_exact])
def test_cluster_plays_structurally_valid(rel):
    """A byte-exact-generation cluster tune plays N frames of valid SID writes."""
    path = _resolve(rel)
    song = pydmcsid.read(str(path))
    frames = list(pydmcsid.iter_frames(song, max_frames=200))
    assert len(frames) == 201  # init burst + 200 play calls
    assert any(frame for frame in frames)  # non-empty output
    for frame in frames:
        for reg, val in frame:
            assert 0 <= reg < 25
            assert 0 <= val < 256
