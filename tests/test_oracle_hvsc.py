"""Byte-exact comparison of :class:`~pydmcsid.DmcPlayer` against the sidtrace oracle.

Marked ``oracle``: these tests need Docker (the ``anarkiwi/sidtrace`` image) and
network access to HVSC, so the default suite excludes them (see ``pyproject``); a
dedicated CI job runs ``pytest -m oracle``.  They are never skipped -- an
unavailable tune or a failed oracle render fails the test rather than hiding a
regression.  HVSC ``.sid`` files are copyright works: they are downloaded to a
cache (or a local ``$HVSC`` tree), never committed.

The ``TUNES`` list carries at least one real HVSC representative of every DMC
generation and play-routine variation pydmcsid reproduces -- init-``$37`` and
init-``$1d`` (single- and multi-frame builds, the code-read scene patches), the
reorganised ``$a1`` and ``$95`` engines, the ``$94a`` 2-level-dispatch family and
its ``$937`` CIA-multispeed wrapper, and the benign play-wrapper builds -- each
confirmed frame-for-frame against the deterministic ``sidplayfp`` oracle.
"""

import os
from pathlib import Path

import numpy as np
import oracle_hvsc
import pytest

from pysidtracker import make_oracle_fixtures
from pysidtracker.oracle import (
    SIDTRACE_IMAGE,
    aligned_match,
    cached_sidtrace,
    sidtrace_cadence,
    sidtrace_grid,
)
from pysidtracker.testing import TuneFetchError, resolve_tune

# Cache under the workspace (a Docker-daemon-visible path, and what CI persists via
# actions/cache).  ``$DMC_ORACLE_CACHE`` overrides it; the committed tunecache is
# reused as the HVSC cache so a local run needs no re-download.
_CACHE = Path(os.environ.get("DMC_ORACLE_CACHE", ".oracle-cache"))
_HVSC = Path(os.environ.get("DMC_TUNECACHE", "tests/.tunecache"))

# One representative per DMC generation / play-routine variation, byte-exact
# against the sidtrace oracle.
TUNES = {
    # init-$37 body: canonical, the release-clears-AD/SR scene patch, and the
    # patched note-onset CTRL immediate.
    "ode": "MUSICIANS/A/Ass_It/Ode_to_Music.sid",
    "insider": "MUSICIANS/W/Willi/Insider_01.sid",
    "rock_zak": "MUSICIANS/B/Brian/Rock_Zak_1.sid",
    # init-$1d body: canonical, the >>2 pw_min shift patch, and the $D418
    # filter-type tail force.
    "glorious": "MUSICIANS/B/Bakker_Nantco/Glorious.sid",
    "nop_years": "MUSICIANS/A/Aomeba/20_Years_of_NOP.sid",
    "for_vandalism": "MUSICIANS/R/Rorschach/For_Vandalism_27.sid",
    # $a1 engine: canonical (two-frame startup gate), release-SR-clear patched,
    # and the portamento-into-vibrato path.
    "katusha": "DEMOS/G-L/Katusha.sid",
    "blutal": "MUSICIANS/C/CreaMD/Blutal_Haldcole.sid",
    "short_fusion": "MUSICIANS/P/PRI/Short_Fusion.sid",
    # $95 engine: canonical + a second author's build.
    "happy_rave": "DEMOS/G-L/Happy_Rave.sid",
    "i_love_dmc": "MUSICIANS/B/Bayliss_Richard/I_Love_DMC.sid",
    # $94a family (init-$1d body behind a 2-level dispatch): both virtual-base
    # layouts.
    "day_noter": "MUSICIANS/G/Glover/Day_Noter.sid",
    "poeci": "MUSICIANS/W/Wodnik/Poeci.sid",
    # $937 CIA-multispeed wrapper: all-voices-every-phase + per-voice phase masks.
    "dude": "MUSICIANS/P/Psych858o/Dude_with_Attitude.sid",
    "losing": "MUSICIANS/P/Psych858o/Losing_Control.sid",
    # Benign play-wrapper build (a transparent CIA-multispeed divider that
    # re-enters the resident body on every call).
    "krupa_mix": "DEMOS/G-L/Krupa_Mix.sid",
}


def _render(data, nframes):
    # Render a few extra frames so ``aligned_match`` can skip the silent lead-in a
    # startup gate (the $a1 two-frame gate) or a silent first play call produces.
    return oracle_hvsc.render(data, 1, nframes + 8)[0].tolist()


tune_id, oracle_match = make_oracle_fixtures(
    TUNES,
    hvsc_cache=_HVSC,
    oracle_cache=_CACHE / "csv",
    render=_render,
    frames=250,
)


@pytest.mark.oracle
def test_render_matches_oracle(oracle_match):  # noqa: F811
    """The DMC player reproduces the sidtrace oracle grid frame-for-frame."""
    oracle_match()


def _song_oracle(path, song, frames, cache):
    """A song's (1-based) oracle grid, rendered with ``-o<song>`` (per-song cache)."""
    csv = cache / f"oracle/song{song}/{path.stem}.csv.zst"
    rows = cached_sidtrace(
        path, csv, seconds=frames // 50 + 2, image=SIDTRACE_IMAGE, song=song
    )
    cpf = sidtrace_cadence(rows)
    return np.asarray(sidtrace_grid(rows, cycles_per_frame=cpf, reg_count=25), np.int16)


@pytest.mark.oracle
@pytest.mark.parametrize(
    "rel,song",
    [("MUSICIANS/C/Chotaire/Old_Times.sid", 2)],
    ids=["Old_Times_s2"],
)
def test_multi_song_full_length(rel, song):
    """A later subtune of a multi-song DMC reproduces the oracle full-length.

    The $95 per-subtune init record (order-list pointers, tempo, $D418 base) is
    indexed by ``subtune * 8``; song 1 is right at any stride, but songs 2+ were
    mis-indexed and diverged from frame 0 -- this pins that whole-song class.
    """
    path = resolve_tune(rel, cache_dir=_HVSC)
    if path is None:
        raise TuneFetchError(f"tune {rel} unavailable (offline, not cached)")
    frames = 1500
    rendered = np.asarray(
        oracle_hvsc.render(path.read_bytes(), song, frames + 8)[0], np.int16
    ).tolist()
    oracle = _song_oracle(path, song, frames, _CACHE)[:frames].tolist()
    assert aligned_match(
        oracle, rendered, max_lead=8
    ), f"tune {rel} song {song} diverges from the sidtrace oracle"


@pytest.mark.oracle
@pytest.mark.parametrize(
    "rel,song,frames",
    [
        ("GAMES/S-Z/Session.sid", 1, 500),
        ("GAMES/S-Z/Session.sid", 2, 500),
    ],
    ids=["Session_s1", "Session_s2"],
)
def test_wrapper_selector_full_length(rel, song, frames):
    """A subtune-selector wrapper's chosen body section reproduces the oracle.

    Session maps each PSID song onto a distinct DMC body section (the init
    self-modifies the play-JMP high byte) plus a subtune (a side cell); the
    player must follow init's self-patches to the per-song (body, subtune)
    pair, not pass the song straight to the primary body.
    """
    path = resolve_tune(rel, cache_dir=_HVSC)
    if path is None:
        raise TuneFetchError(f"tune {rel} unavailable (offline, not cached)")
    rendered = np.asarray(
        oracle_hvsc.render(path.read_bytes(), song, frames + 8)[0], np.int16
    ).tolist()
    oracle = _song_oracle(path, song, frames, _CACHE)[:frames].tolist()
    assert aligned_match(
        oracle, rendered, max_lead=8
    ), f"tune {rel} song {song} diverges from the sidtrace oracle"


@pytest.mark.oracle
@pytest.mark.parametrize(
    "rel,song",
    [("DEMOS/A-F/FST.sid", 1), ("DEMOS/A-F/Axel_F_Remix.sid", 1)],
    ids=["FST_s1", "Axel_F_Remix_s1"],
)
def test_orderlist_loop_full_length(rel, song):
    """A song rendered past its orderlist loop still reproduces the oracle.

    The orderlist ``$ff`` handler either jumps to a loop target (the byte after
    ``$ff``, a JSR build) or resets the order pointer to 0 (a ``STA $00`` build);
    both build families ship in HVSC and the target must be read per-body.
    """
    path = resolve_tune(rel, cache_dir=_HVSC)
    if path is None:
        raise TuneFetchError(f"tune {rel} unavailable (offline, not cached)")
    frames = 6000  # past the song's orderlist loop for both tunes
    rendered = np.asarray(
        oracle_hvsc.render(path.read_bytes(), song, frames + 8)[0], np.int16
    ).tolist()
    oracle = _song_oracle(path, song, frames, _CACHE)[:frames].tolist()
    assert aligned_match(
        oracle, rendered, max_lead=8
    ), f"tune {rel} song {song} diverges from the sidtrace oracle"
