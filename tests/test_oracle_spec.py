"""The spec player against the sidtrace oracle over whole songs (``oracle`` marker).

Same tunes and subtunes as ``test_oracle_hvsc`` that the spec states (the
init-``$37``, init-``$1d`` and ``$94a`` bodies, including the play-wrapper builds).
"""

import pytest

from pysidtracker import make_oracle_fixtures
from pysidtracker.testing import assert_oracle_match

from pydmcsid.spec import spec_player

TUNES = {
    "ode": "MUSICIANS/A/Ass_It/Ode_to_Music.sid",
    "insider": "MUSICIANS/W/Willi/Insider_01.sid",
    "rock_zak": "MUSICIANS/B/Brian/Rock_Zak_1.sid",
    "glorious": "MUSICIANS/B/Bakker_Nantco/Glorious.sid",
    "nop_years": "MUSICIANS/A/Aomeba/20_Years_of_NOP.sid",
    "for_vandalism": "MUSICIANS/R/Rorschach/For_Vandalism_27.sid",
    "day_noter": "MUSICIANS/G/Glover/Day_Noter.sid",
    "poeci": "MUSICIANS/W/Wodnik/Poeci.sid",
    "dude": "MUSICIANS/P/Psych858o/Dude_with_Attitude.sid",
    "losing": "MUSICIANS/P/Psych858o/Losing_Control.sid",
    "krupa_mix": "DEMOS/G-L/Krupa_Mix.sid",
}

tune_id, oracle_match = make_oracle_fixtures(TUNES, player=spec_player, max_lead=8)


@pytest.mark.oracle
def test_spec_matches_oracle(oracle_match):  # noqa: F811
    """The spec player reproduces the sidtrace oracle grid over the whole song."""
    oracle_match()


@pytest.mark.oracle
@pytest.mark.parametrize(
    "rel,song",
    [
        ("GAMES/S-Z/Session.sid", 1),
        ("GAMES/S-Z/Session.sid", 2),
        ("DEMOS/A-F/FST.sid", 1),
        ("DEMOS/A-F/Axel_F_Remix.sid", 1),
    ],
    ids=["Session_s1", "Session_s2", "FST_s1", "Axel_F_Remix_s1"],
)
def test_spec_song_full_length(rel, song):
    """A whole subtune, including the subtune-selector wrapper, matches the oracle."""
    assert_oracle_match(
        rel,
        lambda data: spec_player(data, subtune=song - 1),
        song=song,
        max_lead=8,
    )
