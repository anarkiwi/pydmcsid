"""Byte-exact playback: the DMC player reproduces the committed oracle grids.

Frames the player's ``iter_register_writes`` stream with the STANDARD per-frame
forward-fill (one row per VBI play call at the PAL period, the leading init burst
as frame-0 baseline -- identical to deplayroutine's ``oracle.grid_from_writes``)
and asserts it equals the committed frozen oracle grid byte-for-byte (lead aligned
over the leading silent call).  This is the player's correctness gate.
"""

import pydmcsid
from helpers import NREG, grid_from_writes, load_grid

SID_BASE = 0xD400


def _grid_from_calls(song, nframes):
    """Frame the player's register-write stream into the STANDARD per-frame grid."""
    writes = [
        (w.clock, w.reg, w.val)
        for w in pydmcsid.iter_register_writes(song, max_frames=nframes)
    ]
    return grid_from_writes(writes)


def _lead_aligned_equal(oracle, rendered):
    """True if some lead in 0..4 makes ``rendered`` reproduce ``oracle`` exactly."""
    if not rendered:
        return False
    baseline = rendered[0]
    for lead in range(5):
        if lead and (lead > len(rendered) or rendered[lead - 1] != baseline):
            break
        aligned = rendered[lead : lead + len(oracle)]
        if len(aligned) >= len(oracle) and aligned[: len(oracle)] == oracle:
            return True
    return False


def test_is_dmc(tune_path):
    """A DMC tune carries the player signature and is a byte-exact generation."""
    song = pydmcsid.read(tune_path)
    assert song.is_dmc()
    assert song.byte_exact()  # every frozen-grid reference is reproduced byte-exact


def test_byte_exact_vs_oracle(tune_id, tune_path):
    """The player reproduces the committed (standard-framed) oracle grid byte-exact."""
    oracle = load_grid(tune_id)
    song = pydmcsid.read(tune_path)
    rendered = _grid_from_calls(song, len(oracle) + 8)
    assert _lead_aligned_equal(oracle, rendered), (
        "player diverges from oracle for %r" % tune_id
    )


def test_frames_are_register_writes(tune_path):
    """Each yielded frame is a list of ``(reg, val)`` with reg in 0..24."""
    song = pydmcsid.read(tune_path)
    frames = list(pydmcsid.iter_frames(song, max_frames=4))
    assert frames
    for writes in frames:
        for reg, val in writes:
            assert 0 <= reg < NREG
            assert 0 <= val <= 0xFF
