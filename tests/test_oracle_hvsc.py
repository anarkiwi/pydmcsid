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

import pytest

from pysidtracker import make_oracle_fixtures

from pydmcsid import DmcPlayer

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
    return DmcPlayer(data).render_grid(nframes + 8)


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
