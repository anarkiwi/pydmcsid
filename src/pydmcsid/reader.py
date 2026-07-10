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


def _play_body_ok(mem, play: int, base: int) -> bool:
    """True if the play routine at ``play`` is the exact DMC play body.

    Confirms the opening ``DEC tempo_ctr`` (``CE`` + operand ``base+$718``); this
    is load-independent (the operand tracks the base) and body-specific.  ``play``
    is the dispatch's play-JMP target: it is ``base+$85`` for the standard layout
    but relocates (e.g. ``base+$50``) in builds whose id-string/init region pushes
    the play entry -- the engine downstream is byte-identical, so the same tempo
    check identifies it wherever it sits, while still rejecting the reorganised
    bodies whose tempo counter is a different work cell.
    """
    if play + 3 > len(mem):
        return False
    if mem[play] != constants.DMC_DEC_OPCODE:
        return False
    operand = mem[play + 1] | (mem[play + 2] << 8)
    return operand == ((base + constants.DMC_TEMPO_WORK_REL) & 0xFFFF)


def find_dmc_base(mem, load: int) -> Optional[int]:
    """Locate the DMC player's JMP-table base, or ``None`` if not a DMC image.

    Scans from ``load`` for the opening dispatch JMP table whose *play* entry
    (the second ``JMP``) targets the exact DMC play routine -- an opening
    ``DEC base+$718`` (see :func:`_play_body_ok`).  This anchor is
    load-independent (the JMP target and the body's own operand both track the
    base), init-independent (the init entry varies across DMC sub-versions) and
    play-offset-independent (the play body is followed to wherever the dispatch
    points, so builds that relocate the play entry to ``base+$50`` etc. -- the
    same engine with a shifted id-string/init region -- are found).

    It accepts the 4-entry (init/play/stop/FUN) and 2-entry (init/play only)
    dispatch builds carrying the identical body, while still rejecting the
    reorganised player bodies (different tempo work cell / work-RAM layout) that
    only share the DMC data-table structure.  Returns the base.
    """
    image = SidImage(mem, load, min(len(mem), 0x10000), None, b"", b"")
    end = min(load + constants.DMC_TABLE_SCAN, len(mem))
    for match in find_code_all(image, _DMC_DISPATCH, start=load, end=end):
        base = match.addr
        if _play_body_ok(image.mem, match.captures["play"], base):
            return base
    return None


def order_table_base(mem, base: int) -> Optional[int]:
    """Return the per-subtune order-list pointer-table base, or ``None``.

    The init routine copies each voice's order-list pointer from the per-subtune
    table into the fixed shared work cell ``base+$707`` -- the
    ``LDA <ordertable>,Y : STA base+$707,X`` pair (``B9 lo hi : 9D lo hi``).
    Reading the table base from that store site (instead of a fixed code offset)
    locates it whatever the init/id-string layout, so the relocated-play-entry
    builds resolve it too.  ``base+$707`` is in the engine's shared work RAM, so
    the store target tracks the base.  The returned operand is an absolute
    (already load-relinked) address, used as-is.
    """
    store = (base + 0x707) & 0xFFFF
    sig = bytes((0x9D, store & 0xFF, store >> 8))
    idx = mem.find(sig, base, min(len(mem), base + 0x900))
    if idx >= 3 and mem[idx - 3] == 0xB9:  # LDA abs,Y immediately before the store
        return mem[idx - 2] | (mem[idx - 1] << 8)
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
    """True if a $1d body uses the modelled contiguous note/inst block + rest tail.

    The $1d generation has a few sub-layouts that relocate the 9-cell work block
    as a unit; all modelled ones keep ``inst == note+3`` (the player derives the
    whole block -- incl. ``filt = note+6`` -- from the note-store operand at
    ``$11A6``).  The note-store, inst-store and rest-tail operands all live in the
    engine's shared code region (``>= base+$103``), so this check holds whether
    the play entry sits at ``base+$85`` or is relocated earlier.  A body that
    breaks the invariant is a $1d we do not reproduce byte-exact.
    """
    note = _operand(mem, base, constants.NOTE_CELL_OP)
    inst = _operand(mem, base, constants.INST_CELL_OP)
    tail = _operand(mem, base, constants.REST_TAIL_OP)
    if None in (note, inst, tail):
        return False
    if (inst - note) & 0xFFFF != 3:
        return False
    if mem[base + constants.REST_TAIL_OP - 1] != 0x4C:  # a JMP at $1180
        return False
    return (tail - base) & 0xFFFF in (
        constants.REST_TAIL_1322,
        constants.REST_TAIL_1591,
    )


# 6502 instruction lengths by opcode (1/2/3 bytes), for the short linear walk of
# a release helper up to its ``RTS`` (validated against py65 for all legal
# opcodes).  Undefined/illegal opcodes default to the legal opcode sharing their
# column's addressing mode, which is enough to keep the tiny-helper walk aligned.
_OP_LEN = bytes.fromhex(
    "0102010202020202"
    "0102010203030303"
    "0202010202020202"
    "0103010303030303"
    "0302010202020202"
    "0102010203030303"
    "0202010202020202"
    "0103010303030303"
    "0102010202020202"
    "0102010203030303"
    "0202010202020202"
    "0103010303030303"
    "0102010202020202"
    "0102010203030303"
    "0202010202020202"
    "0103010303030303"
    "0202020202020202"
    "0102010203030303"
    "0202010202020202"
    "0103010303030303"
    "0202020202020202"
    "0102010203030303"
    "0202010202020202"
    "0103010303030303"
    "0202020202020202"
    "0102010203030303"
    "0202010202020202"
    "0103010303030303"
    "0202020202020202"
    "0102010203030303"
    "0202010202020202"
    "0103010303030303"
)


def _helper_zeros_adsr(mem, addr: int) -> bool:
    """True if the tiny release helper at ``addr`` stores to BOTH ``$D405,Y`` and
    ``$D406,Y`` (``99 05/06 D4``) before its first ``RTS`` -- i.e. zeroes AD/SR.

    A linear walk (helpers are straight-line) that stops at ``RTS``; this rejects
    the ``STA $100f,X : RTS`` no-clear helper whose image happens to be followed
    by unrelated ``STA $D405/$D406`` bytes past the ``RTS``.
    """
    seen_ad = seen_sr = False
    pc = addr
    for _ in range(24):  # release helpers are a handful of instructions
        if pc + 2 >= len(mem):
            break
        op = mem[pc]
        if op == 0x60:  # RTS -- end of the helper
            break
        if op == 0x99:  # STA abs,Y
            operand = mem[pc + 1] | (mem[pc + 2] << 8)
            seen_ad |= operand == 0xD405
            seen_sr |= operand == 0xD406
        pc += _OP_LEN[op]
    return seen_ad and seen_sr


def release_clears_adsr(mem, base: int) -> Optional[bool]:
    """Whether the note-release (``base+$33d``) also zeroes AD/SR, or ``None``.

    The release gate-off is either an inline store or a ``JSR`` to a small helper,
    and either form may or may not zero AD/SR (an envelope-clearing hard-restart).
    Both DMC generations ship both variants, so the behaviour is read from the
    code rather than assumed per generation:

    * inline ``STA $100f,X`` (opcode ``$9D``) -- the stock ``$37`` gate-off, AD/SR
      left static -> ``False``.
    * ``JSR`` (opcode ``$20``) to a helper -- follow it: ``True`` if the helper
      stores ``$D405,Y``/``$D406,Y`` before ``RTS`` (the ``$1d`` ``$17ec`` clear,
      or a ``$37`` scene edit), else ``False`` (a bare ``STA $100f,X : RTS``).
    * any other opcode -- an unrecognised release edit -> ``None`` (recognised
      but not reproduced byte-exact).
    """
    site = base + constants.V37_RELEASE_SITE_REL
    if site + 2 >= len(mem):
        return False
    op = mem[site]
    if op == 0x9D:  # STA $100f,X -- stock inline gate-off store
        return False
    if op == 0x20:  # JSR <helper>
        return _helper_zeros_adsr(mem, mem[site + 1] | (mem[site + 2] << 8))
    return None


def dmc_byte_exact(mem, base: int, play: Optional[int] = None) -> bool:
    """True if the body at ``base`` is a generation pydmcsid plays byte-exact.

    True for the init-``$37`` generation and the modelled init-``$1d`` bodies;
    False for a recognised DMC of an unmodelled generation.  A handful of
    hand-customized init-``$1d`` builds share the marker+layout but wrap the play
    entry (``play != base+3``) or relocate the AD/SR write out of the modelled
    ``$184B`` helper -- these are recognised as ``$1d`` but not reproduced
    byte-exact, so they are gated out here (``play`` is the header play vector;
    ``None`` skips the wrapper check, e.g. for a bare PRG with no header).  An
    init-``$37`` build whose release is patched to an unrecognised routine (see
    :func:`v37_release_mode`) is likewise recognised but not byte-exact.
    """
    variant = dmc_variant(mem, base)
    if variant is None:
        return False
    if release_clears_adsr(mem, base) is None:  # an unrecognised release edit
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
    init: int  # header init vector (== base, the JMP dispatch table)
    name: str = ""
    author: str = ""
    header: Optional[Any] = None  # source PSID/RSID header (None for a bare .prg)

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
        init = header.init_address or base
    else:  # bare .prg: single subtune, no metadata, standard init/play entries
        songs = 1
        start = 1
        name = author = ""
        play = (base + constants.STD_PLAY_REL) & 0xFFFF
        init = base
    return Song(
        mem=image.mem,
        load=image.load,
        base=base,
        image_len=image.end - image.load,
        songs=songs,
        start_song=start,
        play=play,
        init=init,
        name=name,
        author=author,
        header=header,
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
