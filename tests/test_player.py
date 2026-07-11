"""Player tests: :class:`~pydmcsid.DmcPlayer` renders every DMC generation.

Byte-exact correctness against the sidtrace oracle lives in
``test_oracle_hvsc.py`` (Docker, the ``oracle`` marker).  These non-oracle tests
drive ``DmcPlayer.render_grid`` over a real HVSC representative of every modelled
generation -- init-``$37``/``$1d``, the ``$a1``/``$95`` engines, and the ``$94a`` /
``$937`` families, plus the scene-patched builds -- asserting the grid is the
MemPlayer 25-register forward-filled shape, is deterministic, and produces real
output (so the whole play transcription is exercised without an emulator).
"""

from pysidtracker import MemPlayer

import pydmcsid
from pydmcsid import DmcPlayer
from pydmcsid.player import (
    Player95,
    PlayerA1,
    PlayerNN,
    Player937,
    PlayerV1D,
    _engine_class,
)
from helpers import NREG

_ENGINE = {
    "v37": DmcPlayer,
    "v1d": PlayerV1D,
    "a1": PlayerA1,
    "n95": Player95,
    "nn": (PlayerNN, Player937),
}


def test_dmcplayer_is_memplayer():
    """The one DMC player class derives from the shared :class:`MemPlayer`."""
    assert issubclass(DmcPlayer, MemPlayer)
    assert MemPlayer in DmcPlayer.__bases__  # the single MemPlayer-derived root


def test_is_dmc_and_byte_exact(tune_path):
    """Every listed tune is a recognised, byte-exact DMC generation."""
    song = pydmcsid.read(tune_path)
    assert song.is_dmc()
    assert song.byte_exact()


def test_dispatch_matches_variant(tune_path):
    """``DmcPlayer(source)`` instantiates the engine for the tune's generation."""
    song = pydmcsid.read(tune_path)
    player = DmcPlayer(song)
    expected = _ENGINE[song.variant()]
    assert isinstance(player, expected)
    assert type(player) is _engine_class(song)


def test_render_grid_shape_and_output(tune_id, tune_path):
    """``render_grid`` is the 25-register forward-filled MemPlayer grid with output."""
    data = tune_path.read_bytes()
    grid = DmcPlayer(data).render_grid(200)
    assert len(grid) == 200
    baseline = grid[0]
    for row in grid:
        assert len(row) == NREG
        assert all(0 <= v <= 0xFF for v in row)
        for pw_hi in (0x03, 0x0A, 0x11):  # masked to 4 bits (oracle shape)
            assert row[pw_hi] <= 0x0F
    assert any(row != baseline for row in grid), "tune %r produced no output" % tune_id


def test_render_grid_deterministic(tune_path):
    """Two renders of the same tune are identical (no shared mutable state)."""
    data = tune_path.read_bytes()
    assert DmcPlayer(data).render_grid(120) == DmcPlayer(data).render_grid(120)


def test_play_frame_writes_in_range(tune_path):
    """Each ``play_frame`` diff is ``(reg, val)`` with reg in 0..24, val a byte."""
    player = DmcPlayer(tune_path.read_bytes())
    for _ in range(8):
        for reg, val in player.play_frame():
            assert 0 <= reg < NREG
            assert 0 <= val <= 0xFF
