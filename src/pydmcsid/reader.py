"""Read a DMC tune from a ``.sid`` (PSID/RSID) or raw ``.prg``/image.

The DMC ``.sid`` IS the player + the per-tune song data (the player binary is
resident, and the song tables are relocated into it).  :func:`read` loads the C64
memory image and its load address; the player walks the resident tables directly.
"""

import hashlib
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
        play = match.captures["play"]
        if (
            _play_body_ok(image.mem, play, base)
            or _play_body_a1_ok(image.mem, play, base)
            or _play_body_95_ok(image.mem, play, base)
        ):
            return base
        nn_base = _nn_engine_base(image.mem, play, base)
        if nn_base is not None:
            return nn_base
    return None


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


def _norm_n95_body(mem, base: int) -> Optional[bytes]:
    """The ``$95`` play body ($95..$718) with per-tune bytes zeroed, or ``None``.

    Zeroes the operand of every 3-byte instruction whose target lies in the
    per-tune data region (``>= base+$858``, past the fixed note-freq tables and
    work RAM) plus the self-modified tempo-reload seed at ``$10bf`` (init
    overwrites it from the subtune record, so its source value never matters),
    leaving only the load-invariant engine opcodes.  Walking with :data:`_OP_LEN`
    keeps the operand positions aligned; a non-family body normalises differently
    and fails the hash.
    """
    hi = base + constants.N95_BODY_HI
    if hi > len(mem):
        return None
    out = bytearray(mem[base + constants.N95_BODY_LO : hi])
    pc = constants.N95_BODY_LO
    while pc < constants.N95_BODY_HI:
        op = mem[base + pc]
        length = _OP_LEN[op]
        if pc + length > constants.N95_BODY_HI:  # instr straddles the body end:
            return None  # walk desynced from the canonical layout -- not a match
        if length == 3:
            operand = mem[base + pc + 1] | (mem[base + pc + 2] << 8)
            if ((operand - base) & 0xFFFF) >= constants.N95_DATA_REL:
                out[pc - constants.N95_BODY_LO + 1] = 0
                out[pc - constants.N95_BODY_LO + 2] = 0
        pc += length
    out[constants.N95_RELOAD_SEED_REL - constants.N95_BODY_LO] = 0
    return bytes(out)


def _n95_body_ok(mem, base: int) -> bool:
    """True if ``base+$95`` carries the modelled ``$95`` engine body signature."""
    body = _norm_n95_body(mem, base)
    if body is None:
        return False
    return hashlib.sha256(body).hexdigest() == constants.N95_BODY_SHA256


def n95_order_table_base(mem, base: int) -> Optional[int]:
    """Return the ``$95`` per-subtune orderlist pointer-table base, or ``None``.

    Read from the init copy ``LDA <ordertable>,Y : STA $17d9,X`` (the store
    ``9D`` to ``base+$17d9``, low byte of the per-voice orderlist-ptr array),
    which locates it whatever the init layout."""
    store_addr = (base + constants.N95_ORDER_STORE_REL - 0x1000) & 0xFFFF
    sig = bytes((0x9D, store_addr & 0xFF, store_addr >> 8))
    idx = mem.find(sig, base, min(len(mem), base + 0x2000))
    if idx >= 3 and mem[idx - 3] == 0xB9:
        return mem[idx - 2] | (mem[idx - 1] << 8)
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


# Conditional-branch opcodes (2-byte, PC-relative) a benign wrapper may use.
_WRAP_BRANCH = frozenset((0x10, 0x30, 0x50, 0x70, 0x90, 0xB0, 0xD0, 0xF0))
# Side-effect-free opcodes a benign wrapper may carry (immediate loads, register
# transfers, in-register arithmetic/shift, compares, flag ops) -- none touch memory
# or divert control, so they cannot change what the player observes per frame.
_WRAP_INERT = frozenset(
    (
        0xA9,
        0xA2,
        0xA0,
        0xAD,
        0xAE,
        0xAC,
        0xA5,
        0xA6,
        0xA4,
        0xBD,
        0xB9,
        0xBC,
        0xBE,
        0xB5,
        0xB4,
        0xB6,
        0xAA,
        0xA8,
        0x8A,
        0x98,
        0xE8,
        0xC8,
        0xCA,
        0x88,
        0x4A,
        0x0A,
        0x6A,
        0x2A,
        0x18,
        0x38,
        0xEA,
        0xC9,
        0xE0,
        0xC0,
        0x29,
        0x09,
        0x49,
        0x69,
        0xE9,
    )
)
# Store / read-modify-write opcodes, split by operand width (abs = 3-byte, zp =
# 2-byte).  A benign wrapper may store to its OWN counter cell (a multispeed
# divider), but never to a SID register or to a followed JMP's operand bytes.
_WRAP_STORE_ABS = frozenset(
    (0x8D, 0x8E, 0x8C, 0xCE, 0xDE, 0xEE, 0xFE, 0x0E, 0x4E, 0x2E, 0x6E)
)
_WRAP_STORE_ZP = frozenset(
    (
        0x85,
        0x95,
        0x86,
        0x96,
        0x84,
        0x94,
        0xC6,
        0xD6,
        0xE6,
        0xF6,
        0x06,
        0x16,
        0x46,
        0x56,
        0x26,
        0x36,
        0x66,
        0x76,
    )
)


def _wrap_read(mem, overlay: dict, addr: int) -> Optional[int]:
    """Byte at ``addr`` after applying ``overlay`` (init's self-patches), or None."""
    addr &= 0xFFFF
    if addr in overlay:
        return overlay[addr]
    return mem[addr] if addr < len(mem) else None


def _sim_init_patches(mem, base: int, init: Optional[int], subtune: int) -> dict:
    """Bytes the init routine writes into the wrapper before playback begins.

    The subtune-selector wrappers self-modify the play-JMP operand from init (a
    ``TAX : LDA table,X : STA <play+2>`` patch keyed on the selected subtune), so
    the header play-JMP's STATIC operand is not what runs.  Model init as a small
    straight-line interpreter (following JMPs, tracking ``A``/``X``/``Y`` and known
    stores) to recover the STABLE patched bytes for the followed subtune; every
    read is bounds-guarded and unknown values are simply not recorded, so the
    follower stays conservative (an unresolved patch leaves the JMP target unknown
    and the tune deferred).  Stops on ``JSR``/``RTS``/``BRK`` or when control
    reaches the resident player.
    """
    patches: dict = {}
    if init is None:
        return patches
    acc, xreg, yreg = subtune & 0xFF, 0, 0
    pc = init & 0xFFFF
    for _ in range(constants.WRAP_INIT_BUDGET):
        if pc + 2 >= len(mem):
            break
        op = mem[pc]
        if op == 0x4C:  # JMP abs
            tgt = mem[pc + 1] | (mem[pc + 2] << 8)
            if ((tgt - base) & 0xFFFF) < 0x900 or tgt == pc:  # into resident / self
                break
            pc = tgt
            continue
        if op in (0x20, 0x60, 0x00, 0x6C):  # JSR / RTS / BRK / JMP() -- stop
            break
        length = _OP_LEN[op]
        acc, xreg, yreg = _sim_init_step(mem, patches, op, pc, acc, xreg, yreg)
        pc = (pc + length) & 0xFFFF
    return patches


def _sim_init_step(mem, patches, op, pc, acc, xreg, yreg):
    """One init instruction: update ``(A, X, Y)`` and record known stores."""
    # pylint: disable=too-many-branches,too-many-return-statements
    val = mem[pc + 1] if pc + 1 < len(mem) else 0
    abs_addr = (mem[pc + 1] | (mem[pc + 2] << 8)) & 0xFFFF if pc + 2 < len(mem) else 0
    if op == 0xA9:
        return val, xreg, yreg
    if op == 0xA2:
        return acc, val, yreg
    if op == 0xA0:
        return acc, xreg, val
    if op == 0xAA:
        return acc, acc, yreg
    if op == 0xA8:
        return acc, xreg, acc
    if op == 0x8A:
        return xreg, xreg, yreg
    if op == 0x98:
        return yreg, xreg, yreg
    if op == 0xAD:
        return _wrap_read(mem, patches, abs_addr), xreg, yreg
    if op == 0xA5:
        return _wrap_read(mem, patches, val), xreg, yreg
    if op == 0xBD:  # LDA abs,X
        return (
            None if xreg is None else _wrap_read(mem, patches, abs_addr + xreg),
            xreg,
            yreg,
        )
    if op == 0xB9:  # LDA abs,Y
        return (
            None if yreg is None else _wrap_read(mem, patches, abs_addr + yreg),
            xreg,
            yreg,
        )
    if op in (0x8D, 0x8E, 0x8C):  # STA/STX/STY abs
        src = acc if op == 0x8D else (xreg if op == 0x8E else yreg)
        if src is not None:
            patches[abs_addr] = src & 0xFF
        else:
            patches.pop(abs_addr, None)
    elif op in (0x85, 0x86, 0x84):  # STA/STX/STY zp
        src = acc if op == 0x85 else (xreg if op == 0x86 else yreg)
        if src is not None:
            patches[val] = src & 0xFF
        else:
            patches.pop(val, None)
    return acc, xreg, yreg


def _wrapper_reaches_play(mem, base: int, play: int, overlay: dict) -> bool:
    """True if EVERY path through the wrapper at ``play`` tail-JMPs to base+3.

    A register-free control-flow walk (both sides of each conditional branch are
    explored, bounded by :data:`constants.WRAP_FOLLOW_BUDGET`).  A path is benign
    only if it reaches a ``JMP`` whose operand (after init's ``overlay`` patches)
    is exactly the standard play entry ``base+3``.  It is rejected -- the wrapper
    genuinely alters per-frame behaviour -- on any ``JSR`` (the body would run more
    than once), any store to a SID register, any unmodelled opcode, or a ``JMP``
    that resolves elsewhere.  A store that lands on a followed ``JMP``'s operand
    bytes (a per-call self-modifying selector) is rejected too: its runtime target
    is not the static one.  Counter self-modification (a divider rewriting its own
    ``LDX`` immediate) is allowed -- it never touches a JMP operand, and every
    branch still lands on a ``base+3`` JMP.
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
            length = _OP_LEN[op]
            if pcx + length > len(mem):
                return False
            if op == 0x4C:  # JMP abs -- a tail (or an in-wrapper hop)
                jmp_operands.add((pcx + 1) & 0xFFFF)
                jmp_operands.add((pcx + 2) & 0xFFFF)
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
            if op in (0x20, 0x60, 0x00, 0x6C):  # JSR / RTS / BRK / JMP() -- not benign
                return False
            if op in _WRAP_BRANCH:
                rel = mem[pcx + 1]
                dest = (pcx + 2 + (rel - 256 if rel >= 0x80 else rel)) & 0xFFFF
                stack.append(dest)
                pcx = (pcx + 2) & 0xFFFF
                continue
            if op in _WRAP_STORE_ABS:
                tgt = (mem[pcx + 1] | (mem[pcx + 2] << 8)) & 0xFFFF
                if 0xD400 <= tgt <= 0xD41F:  # a SID write -- alters the frame
                    return False
                stores.add(tgt)
            elif op in _WRAP_STORE_ZP:
                stores.add(mem[pcx + 1] & 0xFFFF)
            elif op not in _WRAP_INERT:
                return False  # an unmodelled opcode -- not provably benign
            pcx = (pcx + length) & 0xFFFF
    if stores & jmp_operands:  # a per-call self-modifying JMP target
        return False
    return reached


def _play_wrapper_benign(
    mem, base: int, play: Optional[int], init: Optional[int]
) -> bool:
    """True if ``play`` is base+3 or a benign wrapper that follows to base+3.

    ``play`` is the header play vector; ``None`` (a bare PRG) is the unwrapped
    standard entry.  See :func:`_wrapper_reaches_play`.
    """
    if play is None or (play & 0xFFFF) == (base + constants.STD_PLAY_REL) & 0xFFFF:
        return True
    overlay = _sim_init_patches(mem, base, init, 0)
    return _wrapper_reaches_play(mem, base, play, overlay)


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


def v37_onset_ctrl(mem, base: int) -> int:
    """The CTRL immediate the init-$37 note-fetch writes to ``$D404`` ($11D9).

    The stock body silences the oscillator with ``LDA #$08 : STA $D404,Y`` (the
    TEST bit); a couple of builds patch the immediate (e.g. ``#$40``).  Read it
    from the code when the site is the modelled ``LDA #imm`` feeding an inline
    ``STA $D404,Y`` ($99); otherwise fall back to the stock ``$08``.
    """
    site = (base + constants.V1D_ONSET_CTRL_REL) & 0xFFFF
    if site + 2 < len(mem) and mem[site] == 0xA9 and mem[site + 2] == 0x99:
        return mem[site + 1]
    return 0x08


def v1d_note_onset(mem, base: int):
    """The init-$1d note-onset SID writes emitted at note-fetch ($11DB helper call).

    The stock body reaches the onset via ``JSR $17FB``: the helper stores
    ``CTRL`` (the ``LDA #$08`` immediate at $11D9) then ``AD=SR=$0F``.  A
    hand-patched build overwrites the ``JSR`` opcode with an illegal 3-byte
    ``BIT`` no-op ($2C), so the note-fetch frame emits NO SID write and the note
    onsets a frame later (at instrument-init).  Read from the code so both are
    modelled.  Returns ``(ctrl_val, adsr_imm)`` for the stock call, or
    ``(None, None)`` when the call is the ``BIT`` no-op.
    """
    site = (base + constants.V1D_ONSET_CALL_REL) & 0xFFFF
    if site + 2 >= len(mem):
        return (0x08, constants.V1D_ONSET_ADSR_IMM)
    ctrl_val = 0x08
    imm = (base + constants.V1D_ONSET_CTRL_REL) & 0xFFFF
    if imm + 1 < len(mem) and mem[imm] == 0xA9:  # LDA #imm -> CTRL byte
        ctrl_val = mem[imm + 1]
    if mem[site] == constants.V1D_ONSET_BIT_OP:  # BIT abs -- onset call no-op'd
        return (None, None)
    return (ctrl_val, constants.V1D_ONSET_ADSR_IMM)


def tail_d418_force(mem, base: int):
    """The per-frame ``$D418`` filter-type value a patched play-body tail forces.

    The stock play body ends the frame with ``STA $D417`` (opcode ``$8D``) at
    $10AC.  A few hand-patched builds overwrite that store with ``JSR <helper>``
    where the helper does the moved ``STA $D417`` then forces the filter-type
    nibble every frame: ``LDA #imm ; ORA $1717 ; STA $D418``.  Returns ``imm``
    (OR'd with the base ``$1717`` value at playback) when the tail matches that
    exact shape, else ``None`` (stock tail -- no extra ``$D418`` write).
    """
    site = (base + constants.TAIL_STORE_REL) & 0xFFFF
    if site + 2 >= len(mem) or mem[site] != 0x20:  # not a JSR-redirected tail
        return None
    tgt = mem[site + 1] | (mem[site + 2] << 8)
    if tgt + 10 >= len(mem):
        return None
    # helper: 8D 17 D4 (STA $D417) | A9 imm | 0D lo hi (ORA base d418) | 8D 18 D4
    if (
        mem[tgt] == 0x8D
        and (mem[tgt + 1] | (mem[tgt + 2] << 8)) == 0xD417
        and mem[tgt + 3] == 0xA9
        and mem[tgt + 5] == 0x0D
        and mem[tgt + 8] == 0x8D
        and (mem[tgt + 9] | (mem[tgt + 10] << 8)) == 0xD418
    ):
        return mem[tgt + 4]
    return None


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
    pc = base + constants.PW_MIN_SHIFT_REL
    shift = 0
    for _ in range(8):  # the shift chain is a handful of bytes
        if pc >= len(mem):
            break
        op = mem[pc]
        if op == constants.PW_MIN_STORE_OP:  # STA $1756,X -- end of the chain
            break
        if op == 0x4A:  # LSR A
            shift += 1
        pc += _OP_LEN[op]
    return shift


def _norm_a1_body(mem, base: int) -> Optional[bytes]:
    """The ``$a1`` play body ($a1..$70e) with per-tune bytes zeroed, or ``None``.

    Zeroes the operand of every 3-byte instruction whose target lies in the
    per-tune data region (``>= base+$846``) plus the two patchable release
    opcodes, leaving only the load-invariant engine opcodes -- identical across
    the whole family.  Walking with :data:`_OP_LEN` keeps the operand positions
    aligned; a non-family body normalises differently and fails the hash.
    """
    hi = base + constants.A1_BODY_HI
    if hi > len(mem):
        return None
    out = bytearray(mem[base + constants.A1_BODY_LO : hi])
    pc = constants.A1_BODY_LO
    while pc < constants.A1_BODY_HI:
        op = mem[base + pc]
        length = _OP_LEN[op]
        if pc + length > constants.A1_BODY_HI:  # instr straddles the body end:
            return None  # walk desynced from the canonical layout -- not a match
        if length == 3:
            operand = mem[base + pc + 1] | (mem[base + pc + 2] << 8)
            if ((operand - base) & 0xFFFF) >= constants.A1_DATA_REL:
                out[pc - constants.A1_BODY_LO + 1] = 0
                out[pc - constants.A1_BODY_LO + 2] = 0
        pc += length
    out[constants.A1_REL_SR_CLEAR_REL - constants.A1_BODY_LO] = 0
    out[constants.A1_REL_GATE_REL - constants.A1_BODY_LO] = 0
    return bytes(out)


def _a1_body_ok(mem, base: int) -> bool:
    """True if ``base+$a1`` carries the modelled ``$a1`` engine body signature."""
    body = _norm_a1_body(mem, base)
    if body is None:
        return False
    return hashlib.sha256(body).hexdigest() == constants.A1_BODY_SHA256


def a1_order_table_base(mem, base: int) -> Optional[int]:
    """Return the ``$a1`` per-subtune orderlist pointer-table base, or ``None``.

    Read from the init copy ``LDA <ordertable>,Y : STA $17cf,X`` (the store
    ``9D`` to ``base+$17cf``, low byte of the per-voice orderlist-ptr array),
    which locates it whatever the init layout (the init region floats with the
    id-string, and some builds relocate it wholesale past the data)."""
    store_addr = (base + constants.A1_ORDER_STORE_REL - 0x1000) & 0xFFFF
    sig = bytes((0x9D, store_addr & 0xFF, store_addr >> 8))
    idx = mem.find(sig, base, min(len(mem), base + 0x2000))
    if idx >= 3 and mem[idx - 3] == 0xB9:
        return mem[idx - 2] | (mem[idx - 1] << 8)
    return None


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
    if mem[base + constants.A1_REL_SR_CLEAR_REL] not in (0x99, 0x2C):
        return False
    if mem[base + constants.A1_REL_GATE_REL] not in (0x9D, 0x2C):
        return False
    return a1_order_table_base(mem, base) is not None


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
    return n95_order_table_base(mem, base) is not None


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
    body = (base + constants.NN937_BODY_REL) & 0xFFFF
    if body + 8 >= len(mem):
        return False
    if mem[body] != 0xAD or mem[body + 3] != 0xF0 or mem[body + 5] != 0x20:
        return False  # not ``LDA flag / BEQ full / JSR refresh``
    steady = mem[body + 6] | (mem[body + 7] << 8)
    if ((steady - base) & 0xFFFF) != constants.NN937_STEADY_REL:
        return False
    if steady + 4 >= len(mem) or mem[steady] != 0xAC or mem[steady + 3] != 0xB9:
        return False  # refresh body is not ``LDY phase / LDA mask,Y``
    return play + 2 < len(mem) and mem[play] == constants.NN937_DEC_OP


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
    reorganised ``$a1`` engine; False for a recognised DMC of an unmodelled
    generation.  When an init-``$1d`` build wraps the play entry (``play != base+3``)
    the wrapper is FOLLOWED (:func:`_play_wrapper_benign`): a benign pass-through
    (a pure relocator/thunk, or a transparent multispeed divider whose every branch
    re-enters the standard play body) is admitted, since the resident body is
    reproduced byte-exact; a genuine wrapper that writes SID registers, runs the
    body more than once, or self-modifies its own play-JMP target is gated out.  A
    build that instead relocates the AD/SR write out of the modelled ``$184B``
    helper is gated out by the final operand check (``play``/``init`` are the header
    vectors; ``None`` is the unwrapped standard entry, e.g. a bare PRG).  An
    init-``$37`` build whose release is patched to an unrecognised routine is
    likewise recognised but not byte-exact.  A ``$a1`` build wrapped by a subtune
    selector / multispeed divider is admitted only when the wrapper follows to the
    standard play entry, else gated out.
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
    if release_clears_adsr(mem, base) is None:  # an unrecognised release edit
        return False
    if variant == "v37":
        return True
    if not _play_wrapper_benign(mem, base, play, init):
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
        """The DMC body generation (``v37``/``v1d``/``a1``/``n95``/``nn``) or ``None``."""
        return dmc_variant(self.mem, self.base)

    def byte_exact(self) -> bool:
        """Whether pydmcsid's player reproduces this body byte-exact.

        True for the init-``$37`` generation and the modelled init-``$1d`` bodies,
        including builds behind a benign play-wrapper (a thin relocator/thunk or a
        transparent multispeed divider that follows to the standard play entry);
        False for an unmodelled generation, a genuine wrapper that alters per-frame
        behaviour, or a ``$1d`` build that relocates the AD/SR helper.  See
        :func:`dmc_byte_exact`.
        """
        return dmc_byte_exact(self.mem, self.base, self.play, self.init)


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
