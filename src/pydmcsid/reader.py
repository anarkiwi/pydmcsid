"""Read a DMC tune from a ``.sid`` (PSID/RSID) or raw ``.prg``/image.

The DMC ``.sid`` IS the player + the per-tune song data (the player binary is
resident, and the song tables are relocated into it).  :func:`read` loads the C64
memory image and its load address; the player walks the resident tables directly.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from pysidtracker import BaseSidParser, CodePattern, SidError, SidImage, find_code_all

from pydmcsid import constants
from pydmcsid.errors import SidParseError

# The DMC dispatch opening: an init ``JMP`` then the play ``JMP`` (whose target
# is captured as ``play``).  The 2-JMP opcode gate is a masked code-fragment
# match (pysidtracker ``codescan``); the base-relative offset check on the
# captured play target stays caller-side in :func:`find_dmc_base`.
_DMC_DISPATCH = CodePattern("4C {init:w} 4C {play:w}")


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
    image = SidImage(mem, load, min(len(mem), 0x10000), None, b"", b"")
    end = min(load + constants.DMC_TABLE_SCAN, len(mem))
    for match in find_code_all(image, _DMC_DISPATCH, start=load, end=end):
        base = match.addr
        # Caller-side base-relative check: the play entry must target base+$85.
        if ((match.captures["play"] - base) & 0xFFFF) != constants.DMC_PLAY_BODY_REL:
            continue
        if _play_body_ok(image.mem, base):
            return base
    return None


def dmc_variant(mem, base: int) -> Optional[str]:
    """Return the DMC body generation at ``base`` (``"v37"``/``"v1d"``) or ``None``.

    The two byte-exact generations are distinguished by the first pattern-command
    ``CMP #$xx`` marker operand (``base+$126``): the original init-``$37`` body
    uses pattern markers ``$fe/$fd/$ff`` (``CMP #$fe``), the later init-``$1d``
    body re-encodes them as ``$7e/$7d/$7f`` (``CMP #$7e``) and restructures note
    setup / adds a volume + legato command.  A body whose marker is neither is a
    recognised DMC of an unmodelled generation (not reproduced byte-exact).
    """
    idx = base + constants.DMC_MARKER_OP_REL
    if idx >= len(mem):
        return None
    marker = mem[idx]
    if marker == constants.DMC_MARKER_V37:
        return "v37"
    if marker == constants.DMC_MARKER_V1D and _v1d_layout_ok(mem, base):
        return "v1d"
    return None


def _operand(mem, base: int, code_off: int) -> Optional[int]:
    idx = base + code_off
    if idx + 1 >= len(mem):
        return None
    return (mem[idx] | (mem[idx + 1] << 8)) & 0xFFFF


def _v1d_layout_ok(mem, base: int) -> bool:
    """True if a $1d body uses the modelled contiguous note/inst/filter block.

    The $1d generation has a few sub-layouts that relocate the 9-cell work block
    as a unit; all modelled ones keep ``inst == note+3`` and ``filt == note+6``
    (the player derives the whole block from the note-store operand).  A body
    that breaks this invariant is a $1d we do not reproduce byte-exact.
    """
    note = _operand(mem, base, constants.NOTE_CELL_OP)
    inst = _operand(mem, base, constants.INST_CELL_OP)
    filt = _operand(mem, base, constants.FILT_CELL_OP)
    tail = _operand(mem, base, constants.REST_TAIL_OP)
    if None in (note, inst, filt, tail):
        return False
    if (inst - note) & 0xFFFF != 3 or (filt - note) & 0xFFFF != 6:
        return False
    if mem[base + constants.REST_TAIL_OP - 1] != 0x4C:  # a JMP at $1180
        return False
    return (tail - base) & 0xFFFF in (
        constants.REST_TAIL_1322,
        constants.REST_TAIL_1591,
    )


def dmc_byte_exact(mem, base: int, play: Optional[int] = None) -> bool:
    """True if the body at ``base`` is a generation pydmcsid plays byte-exact.

    True for the init-``$37`` generation and the modelled init-``$1d`` bodies;
    False for a recognised DMC of an unmodelled generation.  A handful of
    hand-customized init-``$1d`` builds share the marker+layout but wrap the play
    entry (``play != base+3``) or relocate the AD/SR write out of the modelled
    ``$184B`` helper -- these are recognised as ``$1d`` but not reproduced
    byte-exact, so they are gated out here (``play`` is the header play vector;
    ``None`` skips the wrapper check, e.g. for a bare PRG with no header).
    """
    variant = dmc_variant(mem, base)
    if variant is None:
        return False
    if variant == "v37":
        return True
    if play is not None and play != (base + constants.STD_PLAY_REL) & 0xFFFF:
        return False
    return (
        _operand(mem, base, constants.INST_ADSR_SUB_OP)
        == (base + constants.INST_ADSR_SUB_REL) & 0xFFFF
    )


@dataclass
class Song:
    """A loaded DMC tune: the C64 RAM image + load/base address + subtune count."""

    mem: bytearray  # 64K C64 memory with the tune resident
    load: int  # container load address
    base: int  # DMC player JMP-table base (== load unless a stub is prepended)
    image_len: int  # bytes of the loaded image
    songs: int  # subtune count (PSID header)
    start_song: int  # default subtune (1-based in the header)
    play: int  # header play vector (== base+3 for the standard, unwrapped body)
    name: str = ""
    author: str = ""

    def is_dmc(self) -> bool:
        """Whether the resident binary carries the DMC JMP-table signature."""
        return find_dmc_base(self.mem, self.load) is not None

    def variant(self) -> Optional[str]:
        """The DMC body generation (``"v37"``/``"v1d"``) or ``None`` if unmodelled."""
        return dmc_variant(self.mem, self.base)

    def byte_exact(self) -> bool:
        """Whether pydmcsid's player reproduces this body byte-exact.

        True for the init-``$37`` generation and the modelled init-``$1d`` bodies;
        False for an unmodelled generation or a hand-customized ``$1d`` build that
        wraps the play entry / relocates the AD/SR helper.  See
        :func:`dmc_byte_exact`.
        """
        return dmc_byte_exact(self.mem, self.base, self.play)


def parse(data: bytes) -> Song:
    """Parse SID/PRG ``data`` bytes into a :class:`Song`."""
    try:
        image = SidImage.from_bytes(data)
    except SidError as exc:
        raise SidParseError(str(exc)) from exc
    base = find_dmc_base(image.mem, image.load)
    if base is None:
        raise SidParseError("image does not carry the DMC player signature")
    header = image.header
    if header is not None:
        songs = header.songs
        start = header.start_song
        name = header.name
        author = header.author
        play = header.play_address
    else:  # bare .prg: single subtune, no metadata, standard play entry
        songs = 1
        start = 1
        name = author = ""
        play = (base + constants.STD_PLAY_REL) & 0xFFFF
    return Song(
        mem=image.mem,
        load=image.load,
        base=base,
        image_len=image.end - image.load,
        songs=songs,
        start_song=start,
        play=play,
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
