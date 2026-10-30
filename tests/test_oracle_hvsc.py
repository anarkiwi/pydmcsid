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

import pytest

from pysidtracker import make_oracle_fixtures
from pysidtracker.testing import assert_oracle_match

from pydmcsid import DmcPlayer

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


tune_id, oracle_match = make_oracle_fixtures(TUNES, player=DmcPlayer, max_lead=8)


@pytest.mark.oracle
def test_render_matches_oracle(oracle_match):  # noqa: F811
    """The DMC player reproduces the sidtrace oracle grid frame-for-frame."""
    oracle_match()


@pytest.mark.oracle
@pytest.mark.parametrize(
    "rel,song",
    [
        ("MUSICIANS/C/Chotaire/Old_Times.sid", 2),
        ("GAMES/S-Z/Session.sid", 1),
        ("GAMES/S-Z/Session.sid", 2),
        ("DEMOS/A-F/FST.sid", 1),
        ("DEMOS/A-F/Axel_F_Remix.sid", 1),
    ],
    ids=["Old_Times_s2", "Session_s1", "Session_s2", "FST_s1", "Axel_F_Remix_s1"],
)
def test_song_full_length(rel, song):
    """A whole subtune reproduces the oracle, each pinning a class of song-level bug.

    * Old_Times song 2: the $95 per-subtune init record (order-list pointers,
      tempo, $D418 base) is indexed by ``subtune * 8``; songs 2+ were mis-indexed.
    * Session: a subtune-selector wrapper maps each PSID song onto a distinct DMC
      body section (init self-modifies the play-JMP high byte) plus a subtune; the
      player follows init's self-patches to the per-song (body, subtune) pair.
    * FST / Axel_F_Remix: rendered past the orderlist loop. The ``$ff`` handler
      either jumps to a loop target (a JSR build) or resets the order pointer to
      0 (a ``STA $00`` build); both ship in HVSC and the target is read per-body.
    """
    assert_oracle_match(
        rel,
        lambda data: DmcPlayer(data, subtune=song - 1),
        song=song,
        max_lead=8,
    )
