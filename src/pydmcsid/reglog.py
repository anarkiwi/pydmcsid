"""SID register write logs (the shared py* ``iter_register_writes`` convention).

A register log is the player's output flattened to timed chip writes: one
:class:`RegWrite` per SID register write, with an absolute clock in C64 CPU
cycles.  The DMC player emits one tight write burst per VBI play call; successive
calls are one VBI period (``cycles_per_frame``) apart, and within a call the
writes are spaced ``write_spacing`` cycles.  ``RegWrite`` and the per-frame
framing loop are the shared :mod:`pysidtracker.reglog` surface; this module keeps
only the thin DMC-specific ``iter_register_writes`` wrapper.
"""

from typing import Iterator

from pysidtracker.reglog import DEFAULT_WRITE_SPACING, RegWrite, frame_writes

from pydmcsid import constants
from pydmcsid.player import iter_frames
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

    ``max_frames`` bounds the (looping) player; :func:`iter_frames` yields one
    per-frame ``(reg, val)`` iterable per call (reg already a ``0..24`` SID
    offset), which the shared :func:`~pysidtracker.reglog.frame_writes` frames at
    ``write_spacing`` within a frame and ``cycles_per_frame`` between frames.
    """
    frames = iter_frames(song, max_frames=max_frames, subtune=subtune)
    return frame_writes(
        frames,
        cycles_per_frame=cycles_per_frame,
        write_spacing=write_spacing,
        sid_reg_base=0,
    )
