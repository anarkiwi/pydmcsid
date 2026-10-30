"""Read a DMC tune from a ``.sid`` (PSID/RSID) or raw ``.prg``/image.

The DMC ``.sid`` IS the player + the per-tune song data (the player binary is
resident, and the song tables are relocated into it).  :func:`read` loads the C64
memory image and its load address; the player walks the resident tables directly.
"""

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, NamedTuple, Optional, Tuple

from pysidtracker import (
    BaseSidParser,
    CodePattern,
    Located,
    SidError,
    SidImage,
    SitePattern,
    Strategy,
    find_code_all,
    init_effects,
    locate,
)
from pysidtracker.facets import Finding, detected
from pysidtracker.header import SidHeader
from pysidtracker.locate import STATIC
from pysidtracker.mos6502 import (
    ABS_STORE_OPS,
    BRANCH_OPS,
    INERT_OPS,
    OP_JMP,
    OP_JSR,
    OP_LEN,
    RETURN_OPS,
    ZP_STORE_OPS,
    s8,
    walk_until,
)
from pysidtracker.registers import SID_BASE, SID_REG_COUNT

from pydmcsid import constants
from pydmcsid.errors import DmcParseError

# The DMC dispatch: an init ``JMP`` then the play ``JMP`` (target captured as ``play``).
_DMC_DISPATCH = CodePattern("4C {init:w} 4C {play:w}")
# The init copy of a per-subtune order-list pointer into a per-voice work cell.
_ORDER_COPY = CodePattern("B9 {table:w} 9D {store:w}")
# The note-release gate-off: the stock inline store, or a call to a helper.
_RELEASE_SITE = SitePattern("( inline: 9D | call: 20 {helper:w} )")
# The init-$37 note-fetch TEST-bit write: ``LDA #ctrl : STA $D404,Y``.
_ONSET_CTRL_SITE = CodePattern("A9 {ctrl} 99")
# The patched tail helper: ``STA $D417 : LDA #imm : ORA <d418> : STA $D418``.
_TAIL_D418_HELPER = CodePattern("8D 17 D4 A9 {imm} 0D ?? ?? 8D 18 D4")
# The ``$937`` secondary dispatch ``LDA flag : BEQ : JSR refresh``.
_NN937_DISPATCH = CodePattern("AD ?? ?? F0 ?? 20 {steady:w}")
# The ``$937`` refresh body opening ``LDY phase : LDA mask,Y``.
_NN937_REFRESH = CodePattern("AC ?? ?? B9")
# The ``$937`` divider seed/reload ``LDX #imm : STX counter``.
_NN937_DIVIDER = CodePattern("A2 {imm} 8E {counter:w}")
# The ``$a1`` per-build patchable release stores: the stock store or a ``BIT`` no-op.
_A1_SR_SITE = SitePattern("( clear: 99 | nop: 2C )")
_A1_GATE_SITE = SitePattern("( mask: 9D | nop: 2C )")
# Opcodes that end a benign-wrapper path: a call or any return.
_WRAP_STOP = RETURN_OPS | {OP_JSR}
_SITE_SPAN = 16  # bytes a variable-length site pattern may span


def _site(pattern, mem, addr: int):
    """The match of ``pattern`` starting exactly at ``addr``, or ``None``."""
    if isinstance(pattern, SitePattern):
        hit = pattern.find_first(mem, start=addr, end=addr + _SITE_SPAN)
    else:
        hits = find_code_all(mem, pattern, start=addr, end=addr + pattern.length)
        hit = hits[0] if hits else None
    return hit if hit is not None and hit.addr == addr else None


class Dispatch(NamedTuple):
    """A located DMC dispatch: the JMP table, its play target and the engine base."""

    table: int
    play: int
    base: int


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
    located = locate_dmc(mem, load)
    return located.layout.base if located.ok else None


def _dispatches(image: SidImage):
    end = min(image.load + constants.DMC_TABLE_SCAN, len(image.mem))
    return find_code_all(image, _DMC_DISPATCH, start=image.load, end=end)


def _direct_dispatch(image: SidImage, match) -> Dispatch:
    mem, table, play = image.mem, match.addr, match.captures["play"]
    if not (
        _play_body_ok(mem, play, table)
        or _play_body_a1_ok(mem, play, table)
        or _play_body_95_ok(mem, play, table)
    ):
        raise DmcParseError(f"play ${play:04x} is not a modelled DMC body")
    return Dispatch(table, play, table)


def _nn_dispatch(image: SidImage, match) -> Dispatch:
    table, play = match.addr, match.captures["play"]
    base = _nn_engine_base(image.mem, play, table)
    if base is None:
        raise DmcParseError(f"play ${play:04x} is not a $94a second-level dispatch")
    return Dispatch(table, play, base)


LAYOUTS = (
    Strategy("dispatch", STATIC, _direct_dispatch, _dispatches),
    Strategy("nn-dispatch", STATIC, _nn_dispatch, _dispatches),
)


def locate_dmc(mem, load: int) -> Located:
    """:func:`~pysidtracker.locate` the DMC :class:`Dispatch` in the image at ``load``."""
    image = SidImage(mem, load, min(len(mem), 0x10000), None, b"", b"")
    return locate(image, LAYOUTS, init=False)


def _nn_engine_base(mem, play: int, base: int) -> Optional[int]:
    """Return the resident engine base for a ``$94a``-family dispatch, or ``None``.

    The ``$94a`` generation interposes a SECOND JMP table between the PSID dispatch
    and the resident play body (see :data:`constants.DMC_PLAY_NN_REL`): the
    dispatch play-JMP targets ``base+$94a``, which is itself a ``JMP real_play``
    into the standard init-``$1d`` (``$85``) body authored at a virtual base
    (``base+1``..``base+13``).  Follow that second JMP, derive the engine base
    (``real_play-$85``) and confirm the modelled body sits there.  This only fires
    when the dispatch play target is a ``JMP`` -- the ``$85``/``$a1``/``$95``
    generations' play targets are the body itself -- so they are unaffected.
    """
    if (play - base) & 0xFFFF != constants.DMC_PLAY_NN_REL:
        return None
    if play + 2 >= len(mem) or mem[play] != 0x4C:  # second-level JMP into the body
        return None
    real = mem[play + 1] | (mem[play + 2] << 8)
    eng = (real - constants.DMC_PLAY_BODY_REL) & 0xFFFF
    if _play_body_ok(mem, real, eng):
        return eng
    return None


def _nn_table_base(mem, base: int) -> Optional[int]:
    """Return the ``$94a``-family 2-level dispatch table base for ``base``, or ``None``.

    Self-contained ``(mem, base)`` detector (used by :func:`dmc_variant`): scans
    just below the engine ``base`` for the PSID JMP table whose play entry
    (``table+$94a``) is a second ``JMP`` reaching ``base+$85`` (the modelled body).
    Tightly gated -- both dispatch entries are ``JMP``, the family play offset is
    exact, the followed target lands on ``base`` and the body validates -- so it
    never fires for the ``$85``/``$a1``/``$95`` bodies (which carry no such stub).
    """
    lo = (base - constants.NN_BASE_SCAN) & 0xFFFF
    for table in range(lo, base + 1):
        if table + 5 >= len(mem):
            continue
        if mem[table] != 0x4C or mem[table + 3] != 0x4C:  # JMP init / JMP play
            continue
        play = mem[table + 4] | (mem[table + 5] << 8)
        nn = _nn_engine_base(mem, play, table)
        if nn == base:
            return table
    return None


def _play_body_a1_ok(mem, play: int, base: int) -> bool:
    """True if the play routine is the reorganised ``$a1`` engine body.

    The ``$a1`` generation (see :func:`_a1_body_ok`) dispatches play to
    ``base+$a1`` (not ``base+$85``) and has an entirely different work-RAM map, so
    it is recognised by a separate anchor: the dispatch play-JMP target is exactly
    ``base+$a1`` and the body there matches the family signature.  This never
    fires for the ``base+$85`` body (whose play target is elsewhere), so the
    existing generations are unaffected.
    """
    if play != (base + constants.DMC_PLAY_A1_REL) & 0xFFFF:
        return False
    return _a1_body_ok(mem, base)


def _play_body_95_ok(mem, play: int, base: int) -> bool:
    """True if the play routine is the reorganised ``$95`` engine body.

    The ``$95`` generation (see :func:`_n95_body_ok`) dispatches play to
    ``base+$95`` (not ``base+$85``/``base+$a1``) and has its own compact,
    self-modifying work-RAM map, so it is recognised by a separate anchor: the
    dispatch play-JMP target is exactly ``base+$95`` and the body there matches
    the family signature.  This never fires for the other bodies (whose play
    target is elsewhere), so the existing generations are unaffected.
    """
    if play != (base + constants.DMC_PLAY_95_REL) & 0xFFFF:
        return False
    return _n95_body_ok(mem, base)


class _Body(NamedTuple):
    """A reorganised engine body's signature: the SHA-256 of its normalised bytes.

    ``lo``..``hi`` is the body; operands at or past ``data_rel`` (per-tune data) and
    the ``wild`` offsets (per-build patched bytes) are zeroed before hashing.
    """

    lo: int
    hi: int
    data_rel: int
    wild: Tuple[int, ...]
    sha256: str


A1_BODY = _Body(
    constants.A1_BODY_LO,
    constants.A1_BODY_HI,
    constants.A1_DATA_REL,
    (constants.A1_REL_SR_CLEAR_REL, constants.A1_REL_GATE_REL),
    constants.A1_BODY_SHA256,
)
N95_BODY = _Body(
    constants.N95_BODY_LO,
    constants.N95_BODY_HI,
    constants.N95_DATA_REL,
    (constants.N95_RELOAD_SEED_REL,),
    constants.N95_BODY_SHA256,
)


def norm_body(mem, base: int, body: _Body) -> Optional[bytes]:
    """``body`` at ``base`` with its per-tune bytes zeroed, or ``None``.

    Walks with :data:`OP_LEN` so operand positions stay aligned; a walk that
    straddles the body end has desynced from the family layout.
    """
    if base + body.hi > len(mem):
        return None
    out = bytearray(mem[base + body.lo : base + body.hi])
    pc = body.lo
    while pc < body.hi:
        length = OP_LEN[mem[base + pc]]
        if pc + length > body.hi:
            return None
        if length == 3:
            operand = mem[base + pc + 1] | (mem[base + pc + 2] << 8)
            if ((operand - base) & 0xFFFF) >= body.data_rel:
                out[pc - body.lo + 1 : pc - body.lo + 3] = b"\0\0"
        pc += length
    for off in body.wild:
        out[off - body.lo] = 0
    return bytes(out)


def body_ok(mem, base: int, body: _Body) -> bool:
    """True if ``base`` carries ``body`` (its normalised bytes hash to the signature)."""
    norm = norm_body(mem, base, body)
    return norm is not None and hashlib.sha256(norm).hexdigest() == body.sha256


def _a1_body_ok(mem, base: int) -> bool:
    return body_ok(mem, base, A1_BODY)


def _n95_body_ok(mem, base: int) -> bool:
    return body_ok(mem, base, N95_BODY)


# Per generation: ``(order-list pointer work cell, span past base holding the copy)``.
ORDER_STORES = {
    "a1": (constants.A1_ORDER_STORE_REL - 0x1000, 0x2000),
    "n95": (constants.N95_ORDER_STORE_REL - 0x1000, 0x2000),
}
_ORDER_STORE = (constants.ORDER_STORE_REL, 0x900)


def order_table_site(mem, base: int, variant: Optional[str] = None):
    """The init's ``LDA <ordertable>,Y : STA <cell>,X`` copy match, or ``None``.

    ``<cell>`` is the generation's per-voice order-list pointer work cell (see
    :data:`ORDER_STORES`), so the copy is found whatever the init layout.
    """
    rel, span = ORDER_STORES.get(variant, _ORDER_STORE)
    store = (base + rel) & 0xFFFF
    for hit in find_code_all(mem, _ORDER_COPY, start=base - 3, end=base + span):
        if hit.captures["store"] == store:
            return hit
    return None


def order_table_base(mem, base: int, variant: Optional[str] = None) -> Optional[int]:
    """The per-subtune order-list pointer-table base read from the init copy, or ``None``.

    The returned operand is an absolute (already load-relinked) address.
    """
    hit = order_table_site(mem, base, variant)
    return None if hit is None else hit.captures["table"]


def dmc_variant(mem, base: int) -> Optional[str]:
    """Return the DMC body generation at ``base`` (``v37``/``v1d``/``a1``/``n95``/``nn``).

    The two byte-exact generations are distinguished by the first pattern-command
    ``CMP #$xx`` marker operand (``base+$126``): the original init-``$37`` body
    uses pattern markers ``$fe/$fd/$ff`` (``CMP #$fe``), the later init-``$1d``
    body re-encodes them as ``$7e/$7d/$7f`` (``CMP #$7e``) and restructures note
    setup / adds a volume + legato command.  A body whose marker is neither is a
    recognised DMC of an unmodelled generation (not reproduced byte-exact).

    The reorganised ``$a1`` generation (play body at ``base+$a1``, an entirely
    different work-RAM map) is detected first, by its own body signature.  The
    ``$94a`` family (the init-``$1d`` body behind a 2-level dispatch) is detected
    by that dispatch stub sitting just below ``base`` (see :func:`_nn_table_base`).
    """
    if _nn_table_base(mem, base) is not None:
        return "nn"
    if _a1_body_ok(mem, base):
        return "a1"
    if _n95_body_ok(mem, base):
        return "n95"
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


_RTS_STOP = frozenset((0x60,))  # RTS -- terminates a linear helper walk


def _wrap_read(mem, overlay: dict, addr: int) -> Optional[int]:
    """Byte at ``addr`` after applying ``overlay`` (init's self-patches), or None."""
    addr &= 0xFFFF
    if addr in overlay:
        return overlay[addr]
    return mem[addr] if addr < len(mem) else None


def init_writes(mem, init: Optional[int], subtune: int) -> Dict[int, Any]:
    """The final store init (run with ``A = subtune``) makes to each RAM cell.

    ``{addr: CellWrite}`` from :func:`~pysidtracker.init_effects` on a private copy
    of ``mem``; empty when there is no init to run.
    """
    if init is None:
        return {}
    header = SidHeader(
        b"PSID", 2, 0x7C, 0, init & 0xFFFF, 0, 1, 1, "", "", "", 0, 0, 0, 0, 0x7C
    )
    image = SidImage(bytearray(mem), 0, len(mem), header, b"", b"")
    return init_effects(image, subtune, max_cycles=constants.WRAP_INIT_CYCLES).writes


def _wrapper_reaches_play(mem, base: int, play: int, overlay: dict) -> bool:
    """True if EVERY path through the wrapper at ``play`` tail-JMPs to base+3.

    A register-free walk of both sides of each branch (bounded by
    :data:`constants.WRAP_FOLLOW_BUDGET`); a ``JSR``/return, SID store, unmodelled
    opcode, foreign ``JMP`` or store onto a followed ``JMP`` operand rejects it.
    """
    entry = (base + constants.STD_PLAY_REL) & 0xFFFF
    stack = [play & 0xFFFF]
    visited: set = set()
    stores: set = set()
    jmp_operands: set = set()
    reached = False
    budget = constants.WRAP_FOLLOW_BUDGET
    while stack:
        pcx = stack.pop()
        while True:
            budget -= 1
            if budget <= 0 or pcx + 2 >= len(mem):
                return False
            if pcx in visited:
                break
            visited.add(pcx)
            op = mem[pcx]
            length = OP_LEN[op]
            if pcx + length > len(mem):
                return False
            if op == OP_JMP:
                jmp_operands.update(((pcx + 1) & 0xFFFF, (pcx + 2) & 0xFFFF))
                lo = _wrap_read(mem, overlay, pcx + 1)
                hi = _wrap_read(mem, overlay, pcx + 2)
                if lo is None or hi is None:
                    return False
                tgt = (lo | (hi << 8)) & 0xFFFF
                if tgt == entry:
                    reached = True
                    break
                if ((tgt - play) & 0xFFFF) < 0x100 or ((play - tgt) & 0xFFFF) < 0x100:
                    pcx = tgt  # a short hop that stays inside the wrapper -- follow
                    continue
                return False
            if op in _WRAP_STOP:
                return False
            if op in BRANCH_OPS:
                stack.append((pcx + 2 + s8(mem[pcx + 1])) & 0xFFFF)
                pcx = (pcx + 2) & 0xFFFF
                continue
            if op in ABS_STORE_OPS:
                tgt = (mem[pcx + 1] | (mem[pcx + 2] << 8)) & 0xFFFF
                if SID_BASE <= tgt < SID_BASE + SID_REG_COUNT:
                    return False
                stores.add(tgt)
            elif op in ZP_STORE_OPS:
                stores.add(mem[pcx + 1])
            elif op not in INERT_OPS:
                return False
            pcx = (pcx + length) & 0xFFFF
    return reached and not stores & jmp_operands


def _play_wrapper_benign(
    mem, base: int, play: Optional[int], init: Optional[int]
) -> bool:
    """True if ``play`` is base+3 or a benign wrapper that follows to base+3.

    ``play`` is the header play vector; ``None`` (a bare PRG) is the unwrapped
    standard entry.  See :func:`_wrapper_reaches_play`.
    """
    if play is None or (play & 0xFFFF) == (base + constants.STD_PLAY_REL) & 0xFFFF:
        return True
    overlay = {a: w.value for a, w in init_writes(mem, init, 0).items()}
    return _wrapper_reaches_play(mem, base, play, overlay)


def wrapper_body(song: "Song", subtune: int) -> Tuple[int, int]:
    """The ``(body base, subtune)`` a subtune-selector wrapper selects for the song.

    A selector init self-modifies the play ``JMP`` to another page-aligned DMC body
    and stashes the chosen subtune in a side cell; the primary ``song.base`` and
    ``subtune`` are returned for any other entry.
    """
    mem = song.mem
    base = song.base
    play = song.play & 0xFFFF
    if play == (base + constants.STD_PLAY_REL) & 0xFFFF or mem[play] != OP_JMP:
        return base, subtune
    writes = init_writes(mem, song.init, subtune)
    operand = (play + 1, play + 2)
    if not any(a in writes for a in operand):
        return base, subtune  # not a self-modifying play selector
    lo, hi = (writes[a].value if a in writes else mem[a] for a in operand)
    body_base = (lo | (hi << 8)) & 0xFF00  # DMC bodies are page-aligned
    if body_base == (base & 0xFF00) or find_dmc_base(mem, body_base) is None:
        return base, subtune  # same body, or no DMC body there -- keep the primary
    page = body_base >> 8
    bodies = (base, body_base)
    chosen = {
        w.value
        for a, w in writes.items()
        if a not in operand
        and w.value != page
        and not any(0 <= w.pc - b < constants.WRAP_BODY_SPAN for b in bodies)
    }
    return body_base, (next(iter(chosen)) if len(chosen) == 1 else 0)


def _helper_zeros_adsr(mem, addr: int) -> bool:
    """True if the tiny release helper at ``addr`` stores to BOTH ``$D405,Y`` and
    ``$D406,Y`` (``99 05/06 D4``) before its first ``RTS`` -- i.e. zeroes AD/SR.

    A linear walk (helpers are straight-line) that stops at ``RTS``; this rejects
    the ``STA $100f,X : RTS`` no-clear helper whose image happens to be followed
    by unrelated ``STA $D405/$D406`` bytes past the ``RTS``.
    """
    seen_ad = seen_sr = False
    for pc in walk_until(mem, addr, _RTS_STOP, budget=24):
        if pc + 2 >= len(mem):
            break
        if mem[pc] == 0x99:  # STA abs,Y
            operand = mem[pc + 1] | (mem[pc + 2] << 8)
            seen_ad |= operand == 0xD405
            seen_sr |= operand == 0xD406
    return seen_ad and seen_sr


def release_site(mem, base: int) -> Optional[Finding]:
    """The note-release (``base+$33d``) gate-off form, or ``None`` if unrecognised.

    ``inline`` (``STA $100f,X``), ``clear`` (a ``JSR`` to a helper that zeroes
    AD/SR before ``RTS``) or ``keep`` (a helper that leaves AD/SR), read from code.
    """
    site = (base + constants.V37_RELEASE_SITE_REL) & 0xFFFF
    hit = _site(_RELEASE_SITE, mem, site)
    if hit is None:
        return None
    if hit.alt == "call":
        clears = _helper_zeros_adsr(mem, hit.captures["helper"])
        return Finding("clear" if clears else "keep", detected(site))
    return Finding("inline", detected(site))


def release_clears_adsr(mem, base: int) -> Optional[bool]:
    """Whether the note-release also zeroes AD/SR, or ``None`` (an unrecognised edit).

    Both generations ship clearing and non-clearing releases (see :func:`release_site`).
    """
    found = release_site(mem, base)
    return None if found is None else found.value == "clear"


def onset_ctrl_site(mem, base: int) -> Optional[Finding]:
    """The init-$37 note-fetch ``LDA #ctrl : STA $D404,Y`` immediate ($11D9), if modelled."""
    site = (base + constants.V1D_ONSET_CTRL_REL) & 0xFFFF
    hit = _site(_ONSET_CTRL_SITE, mem, site)
    return None if hit is None else Finding(hit.captures["ctrl"], detected(site))


def v37_onset_ctrl(mem, base: int) -> int:
    """The CTRL immediate the init-$37 note-fetch writes to ``$D404`` (stock ``$08``)."""
    found = onset_ctrl_site(mem, base)
    return constants.V37_ONSET_CTRL if found is None else found.value


def v1d_onset_site(mem, base: int) -> Optional[Finding]:
    """``nop`` when the init-$1d onset-helper call ($11DB) is a ``BIT`` no-op, else ``None``."""
    site = (base + constants.V1D_ONSET_CALL_REL) & 0xFFFF
    if site + 2 < len(mem) and mem[site] == constants.V1D_ONSET_BIT_OP:
        return Finding("nop", detected(site))
    return None


def v1d_note_onset(mem, base: int):
    """``(ctrl, adsr)`` the init-$1d note-fetch onset helper writes, or ``(None, None)``.

    The stock ``JSR $17FB`` writes ``CTRL`` (the ``LDA #imm`` at $11D9, else ``$08``)
    then ``AD=SR=$0F``; a ``BIT`` no-op patch writes nothing (the onset slips a frame).
    """
    if v1d_onset_site(mem, base) is not None:
        return (None, None)
    imm = (base + constants.V1D_ONSET_CTRL_REL) & 0xFFFF
    ctrl = mem[imm + 1] if imm + 1 < len(mem) and mem[imm] == 0xA9 else 0x08
    return (ctrl, constants.V1D_ONSET_ADSR_IMM)


def tail_d418_site(mem, base: int) -> Optional[Finding]:
    """The ``$D418`` immediate a ``JSR``-patched play-body tail ($10AC) forces, if any.

    The helper does the moved ``STA $D417`` then ``LDA #imm : ORA $1717 : STA $D418``.
    """
    site = (base + constants.TAIL_STORE_REL) & 0xFFFF
    if site + 2 >= len(mem) or mem[site] != OP_JSR:
        return None
    hit = _site(_TAIL_D418_HELPER, mem, mem[site + 1] | (mem[site + 2] << 8))
    return None if hit is None else Finding(hit.captures["imm"], detected(site))


def tail_d418_force(mem, base: int) -> Optional[int]:
    """The per-frame ``$D418`` value a patched tail forces, or ``None`` (stock tail)."""
    found = tail_d418_site(mem, base)
    return None if found is None else found.value


def pw_min_shift(mem, base: int) -> int:
    """Right-shift applied to ``inst[2]`` to form the PW-sweep min bound ($124b).

    The stock body computes ``pw_min = inst[2] >> 4`` with four ``LSR A``
    (``4a 4a 4a 4a``) ahead of the ``STA $1756,X`` store; a hand-patched build
    overwrites the third ``LSR`` with an illegal 2-byte no-op (``$17``, decoded by
    py65 as a 2-byte NOP that also consumes the following ``LSR``), leaving two
    ``LSR A`` -> ``inst[2] >> 2``.  The shift is read from the code -- the count of
    ``LSR A`` executed before the store -- so both encodings are modelled exactly
    (the store operand ``$1756`` is fixed work RAM, so the chain sits at a fixed
    offset).
    """
    shift = 0
    stop = frozenset((constants.PW_MIN_STORE_OP,))  # STA $1756,X ends the chain
    for pc in walk_until(mem, base + constants.PW_MIN_SHIFT_REL, stop, budget=8):
        if mem[pc] == 0x4A:  # LSR A
            shift += 1
    return shift


def _a1_byte_exact(mem, base: int, play, init) -> bool:
    """Whether the ``$a1`` body at ``base`` is reproduced byte-exact.

    Gated out (recognised but not byte-exact) when the header init/play vectors
    resolve outside the resident player (a self-modifying subtune selector or a
    multispeed divider wrapper) AND that wrapper is not a benign pass-through to the
    standard play entry (:func:`_play_wrapper_benign`), when a patchable release
    store carries an unmodelled opcode, or when the orderlist-table anchor is
    missing."""
    win = constants.A1_DISPATCH_WINDOW
    resident = (play is None or base <= (play & 0xFFFF) < base + win) and (
        init is None or base <= (init & 0xFFFF) < base + win
    )
    if not resident and not _play_wrapper_benign(mem, base, play, init):
        return False
    if a1_release_sr(mem, base) is None or a1_release_gate(mem, base) is None:
        return False
    return order_table_base(mem, base, "a1") is not None


def _labelled_site(pattern, mem, addr: int) -> Optional[Finding]:
    hit = _site(pattern, mem, addr)
    return None if hit is None else Finding(hit.alt, detected(addr))


def a1_release_sr(mem, base: int) -> Optional[Finding]:
    """The ``$a1`` release SR-clear ($16c7): ``clear`` (``STA $d406,Y``) or ``nop``."""
    return _labelled_site(_A1_SR_SITE, mem, (base + constants.A1_REL_SR_CLEAR_REL))


def a1_release_gate(mem, base: int) -> Optional[Finding]:
    """The ``$a1`` release gate-off mask ($16e3): ``mask`` (``STA $1817,X``) or ``nop``."""
    return _labelled_site(_A1_GATE_SITE, mem, (base + constants.A1_REL_GATE_REL))


def _n95_byte_exact(mem, base: int, play, init) -> bool:
    """Whether the ``$95`` body at ``base`` is reproduced byte-exact.

    Gated out (recognised but not byte-exact) when the header init/play vectors
    resolve outside the resident player -- a self-modifying subtune selector or a
    multispeed divider wrapper -- or when the orderlist-table anchor is missing.
    """
    win = constants.N95_DISPATCH_WINDOW
    if play is not None and not base <= play < base + win:
        return False
    if init is not None and not base <= init < base + win:
        return False
    return order_table_base(mem, base, "n95") is not None


def _nn_body_modelled(mem, base: int) -> bool:
    """True if the ``$94a`` body at ``base`` is the modelled init-``$1d`` encoding.

    Read from the two opcode discriminators (not a whole-body SHA: the ``$85``
    body embeds the note-freq tables mid-range, so a linear-walk normalisation
    desyncs): the modelled body reaches note onset via a ``JMP`` at ``base+$318``
    and no-ops a vibrato-setup store with an illegal ``BIT`` at ``base+$58e``; the
    unmodelled sub-variant re-encodes both as inline ``STA``.
    """
    if mem[(base + constants.NN_NOTE_ONSET_REL) & 0xFFFF] != constants.NN_NOTE_ONSET_OP:
        return False
    return mem[(base + constants.NN_VIBRATO_REL) & 0xFFFF] == constants.NN_VIBRATO_OP


def _nn_wrapper_937(mem, base: int, play, init) -> bool:
    """True if this is the ``$937`` CIA-multispeed appended-wrapper sub-family.

    The header play/init resolve OUTSIDE the resident dispatch, into an appended
    ``$2xxx`` divide-by-N multispeed wrapper (its play vector opens ``DEC counter``)
    that drives the resident SECONDARY-dispatch body at ``base+$936`` -- an
    ``LDA flag / BEQ / JSR refresh`` whose refresh entry is the per-voice masked
    non-row tick at ``base+$8f0`` (see :data:`constants.NN937_BODY_REL`).  Both
    entries drive the modelled ``$85``/``$1d`` body, so it is reproduced byte-exact
    by :class:`~pydmcsid.player.Player937`.  All reads are bounds-guarded so a
    truncated image returns ``False`` rather than raising.
    """
    if play is None or init is None:
        return False
    if not _nn_body_modelled(mem, base):
        return False
    hit = _site(_NN937_DISPATCH, mem, (base + constants.NN937_BODY_REL) & 0xFFFF)
    if hit is None:
        return False
    steady = hit.captures["steady"]
    if ((steady - base) & 0xFFFF) != constants.NN937_STEADY_REL:
        return False
    if _site(_NN937_REFRESH, mem, steady) is None:
        return False
    return play + 2 < len(mem) and mem[play] == constants.NN937_DEC_OP


def nn937_divider(mem, play: int, init: int) -> Tuple[Optional[Finding], ...]:
    """The ``$937`` divider ``(reload, seed)``: each wrapper's ``LDX #imm : STX counter``.

    ``counter`` is the play wrapper's ``DEC`` operand; a missing pair is ``None``.
    """
    counter = (mem[play + 1] | (mem[play + 2] << 8)) & 0xFFFF
    found = []
    for lo in (play, init):
        hits = find_code_all(
            mem, _NN937_DIVIDER, start=lo, end=lo + constants.NN937_WRAP_SCAN
        )
        hit = next((h for h in hits if h.captures["counter"] == counter), None)
        found.append(
            None if hit is None else Finding(hit.captures["imm"], detected(hit.addr))
        )
    return tuple(found)


def _nn_byte_exact(mem, base: int, play, init) -> bool:
    """Whether the ``$94a``-family body at ``base`` is reproduced byte-exact.

    The reproducible members are the init-``$1d`` (``$85``) body relocated behind
    the 2-level dispatch, played by :class:`~pydmcsid.player.PlayerNN` at the
    derived base.  A build whose header play/init resolve outside the resident
    dispatch is byte-exact only when it is the ``$937`` CIA-multispeed appended
    wrapper (:func:`_nn_wrapper_937`, played by :class:`Player937`); any other
    outside-window wrapper (a second engine / self-modifying selector) is gated
    out.  A resident-dispatch build is byte-exact only when its note-onset /
    vibrato-setup encoding is the modelled form (:func:`_nn_body_modelled`); the
    unmodelled inline-``STA`` sub-variant is gated out.
    """
    win = constants.NN_DISPATCH_WINDOW
    resident = (play is None or base - win <= play < base + win) and (
        init is None or base - win <= init < base + win
    )
    if not resident:
        return _nn_wrapper_937(mem, base, play, init)
    return _nn_body_modelled(mem, base)


def dmc_byte_exact(
    mem, base: int, play: Optional[int] = None, init: Optional[int] = None
) -> bool:
    """True if the body at ``base`` is a generation pydmcsid plays byte-exact.

    True for the init-``$37`` generation, the modelled init-``$1d`` bodies and the
    reorganised ``$a1``, ``$95`` (n95) and ``$94a`` (nn) engines; False for a
    recognised DMC of an unmodelled generation.  For the init-``$37`` and
    init-``$1d`` builds, when the play entry is wrapped (``play != base+3``) the
    wrapper is FOLLOWED
    (:func:`_play_wrapper_benign`): a benign pass-through (a pure relocator/thunk,
    or a transparent multispeed divider whose every branch re-enters the standard
    play body) is admitted, since the resident body is reproduced byte-exact; a
    genuine wrapper that writes SID registers, runs the body more than once, or
    self-modifies its own play-JMP target is gated out.  A build that instead
    relocates the AD/SR write out of the modelled ``$184B`` helper is gated out by
    the final operand check (``play``/``init`` are the header vectors; ``None`` is
    the unwrapped standard entry, e.g. a bare PRG).  A build whose release is
    patched to an unrecognised routine is likewise recognised but not byte-exact.
    A ``$a1`` build wrapped by a subtune selector / multispeed divider is admitted
    only when the wrapper follows to the standard play entry, else gated out.
    """
    variant = dmc_variant(mem, base)
    if variant is None:
        return False
    if variant == "a1":
        return _a1_byte_exact(mem, base, play, init)
    if variant == "n95":
        return _n95_byte_exact(mem, base, play, init)
    if variant == "nn":
        return _nn_byte_exact(mem, base, play, init)
    if not _play_wrapper_benign(mem, base, play, init):
        return False
    if release_clears_adsr(mem, base) is None:
        return False
    if variant == "v37":
        return True
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
    layout: Optional[str] = None  # the :data:`LAYOUTS` strategy that located the base
    dispatch: Optional[Dispatch] = None  # the located dispatch (None when re-based)
    # Per-song memo of the code-read decode (variant, byte-exactness), so the
    # body is decoded once however many consumers ask.
    _decoded: dict = field(default_factory=dict, init=False, repr=False, compare=False)

    def is_dmc(self) -> bool:
        """Whether the resident binary carries the DMC JMP-table signature."""
        return find_dmc_base(self.mem, self.load) is not None

    def variant(self) -> Optional[str]:
        """The DMC body generation (``v37``/``v1d``/``a1``/``n95``/``nn``) or ``None``."""
        if "variant" not in self._decoded:
            self._decoded["variant"] = dmc_variant(self.mem, self.base)
        return self._decoded["variant"]

    def byte_exact(self) -> bool:
        """Whether pydmcsid's player reproduces this body byte-exact.

        True for the init-``$37`` generation, the modelled init-``$1d`` bodies and
        the ``$a1``, ``$95`` (n95) and ``$94a`` (nn) engines, including builds
        behind a benign play-wrapper (a thin relocator/thunk or a transparent
        multispeed divider that follows to the standard play entry);
        False for an unmodelled generation, a genuine wrapper that alters per-frame
        behaviour, or a ``$1d`` build that relocates the AD/SR helper.  See
        :func:`dmc_byte_exact`.
        """
        if "byte_exact" not in self._decoded:
            self._decoded["byte_exact"] = dmc_byte_exact(
                self.mem, self.base, self.play, self.init
            )
        return self._decoded["byte_exact"]

    def streams(self, subtune: int = 0):
        """``(name, stream, rule)`` for every stream ``subtune`` reaches, each parsed
        by :data:`pydmcsid.grammar.GRAMMAR`.

        Raises:
          DmcParseError: a reachable stream does not parse.
        """
        from pydmcsid.grammar import (  # pylint: disable=import-outside-toplevel
            song_streams,
        )

        return list(song_streams(self, subtune))

    def facets(self):
        """The song's :class:`~pysidtracker.facets.Variant` (see :mod:`pydmcsid.variant`)."""
        if "facets" not in self._decoded:
            from pydmcsid.variant import (
                FACETS,
            )  # pylint: disable=import-outside-toplevel

            self._decoded["facets"] = FACETS.detect(self)
        return self._decoded["facets"]


def parse(data: bytes) -> Song:
    """Parse SID/PRG ``data`` bytes into a :class:`Song`.

    Raises :class:`~pydmcsid.errors.DmcParseError` when the image is not a DMC tune
    or carries a DMC signature of a body generation pydmcsid does not model (so
    callers distinguish "not a DMC" from "a DMC we do not reproduce byte-exact").
    """
    try:
        image = SidImage.from_bytes(data)
    except SidError as exc:
        raise DmcParseError(str(exc)) from exc
    located = locate_dmc(image.mem, image.load)
    if not located.ok:
        raise DmcParseError(
            "image does not carry the DMC player signature: " + located.describe()
        )
    base = located.layout.base
    variant = dmc_variant(image.mem, base)
    if variant is None:
        raise DmcParseError(
            "DMC signature found but the body is a generation pydmcsid does not model"
        )
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
    song = Song(
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
        layout=located.strategy,
        dispatch=located.layout,
    )
    song._decoded["variant"] = variant  # pylint: disable=protected-access
    return song


def read(path) -> Song:
    """Read a DMC tune from a ``.sid``/``.prg`` file path."""
    return parse(Path(path).read_bytes())


class DmcSidParser(BaseSidParser):
    """DMC parser exposing the shared :class:`BaseSidParser` API."""

    error_class = DmcParseError

    def parse(self, data: bytes, **kwargs: Any) -> Song:
        """Parse ``data`` into a :class:`Song` (see :func:`parse`)."""
        return parse(data)

    def recognize(self, image: SidImage):
        """Return the DMC player JMP-table base if the image carries it."""
        return find_dmc_base(image.mem, image.load)
