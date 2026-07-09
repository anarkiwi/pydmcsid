"""Read a DMC tune from a ``.sid`` (PSID/RSID) or raw ``.prg``/image.

The DMC ``.sid`` IS the player + the per-tune song data (the player binary is
resident, and the song tables are relocated into it).  :func:`read` loads the C64
memory image and its load address; the player walks the resident tables directly.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from pysidtracker import BaseSidParser, SidError, SidImage

from pydmcsid import constants
from pydmcsid.errors import SidParseError


def _play_body_ok(mem, base: int) -> bool:
    """True if the play body at ``base+$85`` is the exact DMC play routine.

    Confirms the opening ``DEC tempo_ctr`` (``CE`` + operand ``base+$718``); this
    is load-independent (the operand tracks the base) and body-specific.
    """
    body = base + constants.DMC_PLAY_BODY_REL
    if body + 3 > len(mem):
        return False
    if mem[body] != constants.DMC_DEC_OPCODE:
        return False
    operand = mem[body + 1] | (mem[body + 2] << 8)
    return operand == ((base + constants.DMC_TEMPO_WORK_REL) & 0xFFFF)


def find_dmc_base(mem, load: int) -> Optional[int]:
    """Locate the DMC player's play-body base, or ``None`` if not a DMC image.

    Scans from ``load`` for the opening dispatch JMP table whose *play* entry
    (the second ``JMP``) targets ``base+$85`` AND whose play body there is the
    exact DMC play routine (see :func:`_play_body_ok`).  This anchor is
    load-independent (the JMP target and the body's own operand both track the
    base) and init-independent (the init entry varies across DMC sub-versions).

    Unlike the original 4-entry ``play/stop/FUN`` anchor it also accepts the
    2-entry (init/play only) dispatch builds that carry the *identical* play
    body -- the same engine with a shorter dispatch table -- while still
    rejecting the reorganised player bodies (different play offset / work-RAM
    layout) that only share the DMC data-table structure.  Returns the base.
    """
    for off in range(constants.DMC_TABLE_SCAN):
        base = load + off
        if base + 6 > len(mem):
            break
        # init entry (a JMP) then the play entry (a JMP to base+$85).
        if mem[base] != 0x4C or mem[base + 3] != 0x4C:
            continue
        play = mem[base + 4] | (mem[base + 5] << 8)
        if ((play - base) & 0xFFFF) != constants.DMC_PLAY_BODY_REL:
            continue
        if _play_body_ok(mem, base):
            return base
    return None


def dmc_byte_exact(mem, base: int) -> bool:
    """True if the body at ``base`` is the generation pydmcsid plays byte-exact.

    The player transcribes the init-``$37`` generation (pattern markers
    ``$fe/$fd/$ff``); the later init-``$1d`` generation shares the play-body
    anchor and data-table layout but re-encodes the markers as ``$7e/$7d/$7f``
    and restructures note setup, so pydmcsid recognises + parses it but does not
    reproduce it byte-exact.  Distinguished by the ``CMP #$xx`` marker operand.
    """
    idx = base + constants.DMC_MARKER_OP_REL
    if idx >= len(mem):
        return False
    return mem[idx] == constants.DMC_MARKER_V37


@dataclass
class Song:
    """A loaded DMC tune: the C64 RAM image + load/base address + subtune count."""

    mem: bytearray  # 64K C64 memory with the tune resident
    load: int  # container load address
    base: int  # DMC player JMP-table base (== load unless a stub is prepended)
    image_len: int  # bytes of the loaded image
    songs: int  # subtune count (PSID header)
    start_song: int  # default subtune (1-based in the header)
    name: str = ""
    author: str = ""

    def is_dmc(self) -> bool:
        """Whether the resident binary carries the DMC JMP-table signature."""
        return find_dmc_base(self.mem, self.load) is not None

    def byte_exact(self) -> bool:
        """Whether pydmcsid's player reproduces this body byte-exact.

        True for the init-``$37`` generation; False for a recognised DMC body of
        a later generation (e.g. init-``$1d``) that parses but is not yet played
        byte-exact.  See :func:`dmc_byte_exact`.
        """
        return dmc_byte_exact(self.mem, self.base)


def parse(data: bytes) -> Song:
    """Parse SID/PRG ``data`` bytes into a :class:`Song`."""
    try:
        image = SidImage.from_bytes(data)
    except SidError as exc:
        raise SidParseError(str(exc)) from exc
    header = image.header
    if header is not None:
        songs = header.songs
        start = header.start_song
        name = header.name
        author = header.author
    else:  # bare .prg: single subtune, no metadata
        songs = 1
        start = 1
        name = author = ""
    base = find_dmc_base(image.mem, image.load)
    if base is None:
        raise SidParseError("image does not carry the DMC player signature")
    return Song(
        mem=image.mem,
        load=image.load,
        base=base,
        image_len=image.end - image.load,
        songs=songs,
        start_song=start,
        name=name,
        author=author,
    )


def read(path) -> Song:
    """Read a DMC tune from a ``.sid``/``.prg`` file path."""
    return parse(Path(path).read_bytes())


class DmcSidParser(BaseSidParser):
    """DMC parser exposing the shared :class:`BaseSidParser` API."""

    error_class = SidParseError

    def parse(self, data: bytes, **kwargs: Any) -> Song:
        """Parse ``data`` into a :class:`Song` (see :func:`parse`)."""
        return parse(data)

    def recognize(self, image: SidImage):
        """Return the DMC player JMP-table base if the image carries it."""
        return find_dmc_base(image.mem, image.load)
