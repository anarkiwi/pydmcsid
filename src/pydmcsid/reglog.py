"""SID register write logs (the shared py* ``iter_register_writes`` convention).

A register log is the player's output flattened to timed chip writes: one
:class:`RegWrite` per SID register write, with an absolute clock in C64 CPU
cycles.  The DMC player emits one tight write burst per VBI play call; successive
calls are one VBI period (``cycles_per_frame``) apart, and within a call the
writes are spaced ``write_spacing`` cycles.  The framing/diffing is the shared
:func:`pysidtracker.register_writes_from_player` surface driven by
:class:`~pydmcsid.player.DmcPlayer` (a :class:`~pysidtracker.MemPlayer`); this
module keeps only the thin DMC-specific wrapper.
"""

from typing import Iterator

from pysidtracker.reglog import (
    DEFAULT_WRITE_SPACING,
    RegWrite,
    register_writes_from_player,
)

from pydmcsid import constants
from pydmcsid.player import DmcPlayer
from pydmcsid.reader import Song

__all__ = ["RegWrite", "iter_register_writes"]


def iter_register_writes(
    song: Song,
    max_frames: int = 50 * 60,
    cycles_per_frame: int = constants.PAL_CYCLES_PER_FRAME,
    write_spacing: int = DEFAULT_WRITE_SPACING,
    subtune: int = 0,
) -> Iterator[RegWrite]:
    """Yield :class:`RegWrite` for ``song``, frame by frame (VBI play calls).

    The player's post-init SID register file is the frame-0 baseline (spaced
    ``write_spacing`` apart at clock 0); each subsequent VBI play call's changed
    registers follow one frame later, at ``cycles_per_frame`` spacing -- the
    shared :func:`~pysidtracker.register_writes_from_player` framing over the
    :class:`~pydmcsid.player.DmcPlayer` for this tune's DMC generation.
    """
    player = DmcPlayer(song, subtune=subtune)
    return register_writes_from_player(
        player, max_frames, cycles_per_frame, write_spacing
    )
