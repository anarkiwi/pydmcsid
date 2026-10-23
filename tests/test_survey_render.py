"""The ``scripts/oracle_hvsc.py`` survey renderer and its render contract."""

import json
import re

import numpy as np
import oracle_hvsc
import pytest

import pysidtracker
from pysidtracker.sidid import sidid_signatures

from helpers import NREG, TUNES

from pydmcsid import DmcPlayer, DmcParseError


def test_render_matches_player(tune_path):
    data = tune_path.read_bytes()
    grid, info = oracle_hvsc.render(data, 1, 120)
    assert grid.shape == (120, NREG)
    assert grid.dtype == np.int16
    np.testing.assert_array_equal(grid, DmcPlayer(data).render_grid(120))
    assert json.loads(json.dumps(info)) == info
    assert info["engine"] == type(DmcPlayer(data)).__name__


@pytest.mark.parametrize(
    "tune_id, expected",
    [
        (
            "ode",
            {"engine": "DmcPlayer", "variant": "v37", "wrapped": False},
        ),
        (
            "katusha",
            {"engine": "PlayerA1", "variant": "a1", "release_clears_adsr": None},
        ),
        (
            "dude",
            {
                "engine": "Player937",
                "variant": "nn",
                "wrapped": True,
                "rsid": False,
                "trigger": "cia_timer",
                "speed": 6,
                "dynamic": False,
            },
        ),
    ],
)
def test_render_reports_build(tune_id, expected, hvsc):
    info = oracle_hvsc.render(hvsc.read(TUNES[tune_id]), 1, 1)[1]
    assert {k: info[k] for k in expected} == expected
    assert info["byte_exact"] is True


def test_render_selects_one_based_subtune(hvsc):
    data = hvsc.read(TUNES["sharkz"])
    second = oracle_hvsc.render(data, 2, 200)[0]
    np.testing.assert_array_equal(second, DmcPlayer(data, subtune=1).render_grid(200))
    assert (second != oracle_hvsc.render(data, 1, 200)[0]).any()


@pytest.mark.parametrize("data", [b"PSID", b"\x00\x10" + bytes(0x900)])
def test_non_dmc_raises_sid_parse_error(data):
    with pytest.raises(pysidtracker.SidParseError) as exc:
        oracle_hvsc.render(data, 1, 10)
    assert isinstance(exc.value, DmcParseError)


def test_player_regex_selects_dmc_signatures():
    names = [name for name, _ in sidid_signatures()]
    assert [n for n in names if re.search(oracle_hvsc.PLAYER, n, re.I)] == [
        "DMC",
        "(DMC_V4.x)",
        "(DMC_V5.x)",
    ]
