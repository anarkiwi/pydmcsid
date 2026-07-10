"""A faithful integer transcription of the DMC (Demo Music Creator) 6502 player.

Play entry ``$1003 -> FUN_1085``.  Per frame: a tempo divider ($1085), a per-voice
row-advance gate ($10B0), the orderlist walk ($10D2: transpose / loop / stop), the
pattern walk ($110C: note / instrument-select / duration / effect / gate), the
note-trigger instrument setup ($1201: PW init, PW-sweep bounds, arp/wait, vibrato,
flags), the hard-restart window ($1300), the $FF-arp wavetable walk ($1598/$15D5),
the 16-bit PW sweep ($134E), the pitch-slide effect ($13C5), the glide ($141C), the
6-step filter sweep ($13C5/$13D1) and the triangle vibrato ($1520).

Transcribed from the DMC disassembly (``disasm.asm`` $1000..$1830); the per-tune
table bases are read from the player-code operands (see :mod:`pydmcsid.constants`), so
a differently-sized tune (relocated tables) plays without any baked addresses.
Every work byte mirrors the player's zero-page-relocated work RAM exactly; the
per-voice arrays are indexed by ``X`` in 0..2 (the player's voice loop).

This is the same integer player the deplayroutine ``dmctick`` engine transcribes;
keeping it standalone lets pydmcsid serve as an independent validator oracle.
"""

# A faithful integer transcription: the per-frame methods carry the player's exact
# branch/return density (the byte-exactness lives in those branches).
# pylint: disable=too-many-instance-attributes,too-many-branches,too-many-statements
# pylint: disable=too-many-return-statements,too-many-public-methods,too-many-lines

from typing import Iterator, List, Tuple

from pysidtracker.registers import (
    SID_BASE,
    SID_FILTER_HI,
    SID_MODE_VOL,
    SID_RES_FILT,
)

from pydmcsid import constants
from pydmcsid.reader import (
    Song,
    _nn_wrapper_937,
    a1_order_table_base,
    n95_order_table_base,
    order_table_base,
    pw_min_shift,
    release_clears_adsr,
    tail_d418_force,
    v1d_note_onset,
    v37_onset_ctrl,
)


class Player:
    """The DMC per-frame integer player over a loaded :class:`Song`."""

    # Work cells whose authored addresses vary across DMC generations (the
    # id-string layout pushes them around).  These are the $37 defaults; the
    # init-$1d bodies relocate them (see :class:`PlayerV1D._setup_cells`).
    c_note = 0x1012  # per-voice current note number
    c_inst = 0x1015  # per-voice instrument number
    g_filt = 0x1034  # filter voice-routing bits | reso accum (-> $D417)
    g_vibtoggle = 0x1035  # vibrato/pitch-bend half-rate toggle
    g_bendscratch = 0x1036  # pitch-bend abs-step scratch
    order_table_op = constants.ORDER_TABLE_OP
    vib_seed_op = None  # v1d-only note-indexed vib-delta seed table operand

    def __init__(self, song: Song, subtune: int = 0):
        self.song = song
        self.m = bytearray(song.mem)  # working copy (mutated during playback)
        self.load = song.base  # player origin (JMP-table base; the authored $1000)
        self.rel = song.base - 0x1000  # the player is authored at $1000
        self.subtune = subtune
        self._writes: List[Tuple[int, int]] = []
        self._curpat: List[Tuple[int, int]] = [(0, 0)] * 3
        self.finished = False

        def operand(code_off: int) -> int:
            # The per-tune table-base operands are ABSOLUTE addresses the
            # assembler already relinked to the tune's load address (the same
            # relink that moves the JMP-table targets the anchor keys on), so
            # they are used as-is -- NOT shifted by ``rel``.  ``rel`` only
            # relocates the player's fixed work-RAM cells (see ``_a``).
            idx = song.base + code_off
            return (self.m[idx] | (self.m[idx + 1] << 8)) & 0xFFFF

        self.b_freqlo = operand(constants.FREQ_LO_OP)
        self.b_freqhi = operand(constants.FREQ_HI_OP)
        self.b_instr = operand(constants.INSTR_OP)
        self.b_pat_lo = operand(constants.PATTERN_LO_OP)
        self.b_pat_hi = operand(constants.PATTERN_HI_OP)
        # Prefer the init-store-site signature (layout-independent); fall back to
        # the fixed code operand for the standard layouts if it is not found.
        order_sig = order_table_base(self.m, song.base)
        self.b_order_tbl = (
            order_sig if order_sig is not None else operand(self.order_table_op)
        )
        self.b_pwtab = operand(constants.PW_TABLE_OP)
        self.b_arp_ctrl = operand(constants.ARP_CTRL_OP)
        self.b_arp_note = operand(constants.ARP_NOTE_OP)
        self.b_filt_ctrl = operand(constants.FILT_CTRL_OP)
        self.b_filt_step_lo = operand(constants.FILT_STEP_LO_OP)
        self.b_filt_step_hi = operand(constants.FILT_STEP_HI_OP)
        self.b_vibseed = operand(self.vib_seed_op) if self.vib_seed_op else 0
        # Whether the note-release also zeroes AD/SR (an envelope-clearing hard-
        # restart), read from the actual release code -- both generations ship
        # clearing and non-clearing release helpers (see ``release_clears_adsr``).
        self._release_clears_adsr = release_clears_adsr(self.m, song.base) is True
        # Right-shift forming the PW-sweep min bound ($124b): 4 for the stock
        # ``inst[2]>>4`` chain, 2 for the ``$17`` no-op-patched build (read from
        # the code, so hand-patched builds are modelled without regressing stock).
        self._pw_min_shift = pw_min_shift(self.m, song.base)
        # A few builds redirect the play-body tail ($10AC STA $D417) to a helper
        # that also forces the $D418 filter-type nibble every frame; the forced
        # immediate (OR'd with $1717) is read from the code (``None`` = stock).
        self._tail_d418 = tail_d418_force(self.m, song.base)
        # Note-fetch CTRL immediate ($11D9 LDA #imm ; $11DB STA $D404,Y): the
        # init-$37 TEST-bit value ($08 stock, a per-tune code constant).  The
        # init-$1d body overrides this in ``_setup_cells`` (its onset is a
        # helper call that also writes AD/SR, or a ``BIT`` no-op).
        self._onset_ctrl = v37_onset_ctrl(self.m, song.base)
        self._onset_adsr = None
        self._setup_cells(operand)
        self.init()

    def _setup_cells(self, operand) -> None:
        """Variant hook: resolve work cells whose authored address varies."""

    # -- helpers ---------------------------------------------------------
    def _a(self, addr: int) -> int:
        return (addr + self.rel) & 0xFFFF

    def w(self, addr: int, val: int) -> None:
        """Emit a SID register write (absolute $D4xx)."""
        self._writes.append((addr & 0xFFFF, val & 0xFF))

    @staticmethod
    def ptr(f8: int, f9: int) -> int:
        """Compose a 16-bit zero-page pointer."""
        return (f9 << 8) | f8

    # -- init ($1037) ----------------------------------------------------
    def init(self) -> None:
        """Run the DMC init routine for the selected subtune."""
        m = self.m
        self._writes = []
        a = self.subtune & 0xFF
        y = (a << 3) & 0xFF
        ot = self.b_order_tbl
        for x in range(3):
            m[self._a(0x1707) + x] = m[ot + y]
            m[self._a(0x170A) + x] = m[ot + y + 1]
            y = (y + 2) & 0xFF
        m[self._a(0x1716)] = m[ot + y]
        m[self._a(0x1717)] = m[ot + y + 1]
        self.w(SID_MODE_VOL, m[ot + y + 1])
        self._init_extra()
        for x in range(0x86):
            m[self._a(0x1718) + x] = 0
        for x in range(3):
            m[self._a(0x100C) + x] = 1
            m[self._a(0x173B) + x] = 1
        for x in range(0x18):
            self.w(SID_BASE + x, 0)

    def _init_extra(self) -> None:
        """Variant hook: extra init-time clears (v1d clears its new work cells)."""

    @property
    def init_writes(self) -> List[Tuple[int, int]]:
        """The SID writes the init routine emitted (frame-0 baseline)."""
        return list(self._writes)

    # -- per-frame ($1085) -----------------------------------------------
    def play_frame(self) -> List[Tuple[int, int]]:
        """Run one player tick; return ``(reg, value)`` writes (abs $D4xx)."""
        self._writes = []
        m = self.m
        a1718 = self._a(0x1718)
        m[a1718] = (m[a1718] - 1) & 0xFF
        if m[a1718] >= 0x80:
            m[a1718] = m[self._a(0x1716)]
        m[self._a(0x1720)] = 0
        for x in range(3):
            self._voice(x)
        self.w(SID_FILTER_HI, m[self._a(0x171C)])
        self.w(SID_RES_FILT, m[self._a(self.g_filt)] | m[self._a(0x1723)])
        if self._tail_d418 is not None:  # patched tail forces $D418 every frame
            self.w(SID_MODE_VOL, self._tail_d418 | m[self._a(0x1717)])
        self.finished = all(m[self._a(0x100C) + x] == 0 for x in range(3))
        return list(self._writes)

    # -- per-voice gate ($10B0) ------------------------------------------
    def _voice(self, x: int) -> None:
        m = self.m
        if m[self._a(0x100C) + x] == 0:
            self._jmp_11f9(x)
            return
        advance = False
        if m[self._a(0x1716)] == m[self._a(0x1718)]:
            m[self._a(0x173B) + x] = (m[self._a(0x173B) + x] - 1) & 0xFF
            if m[self._a(0x173B) + x] == 0:
                advance = True
        if not advance:
            self._jmp_11f9(x)
            return
        f8 = m[self._a(0x1707) + x]
        f9 = m[self._a(0x170A) + x]
        self._orderwalk(x, f8, f9)

    # -- orderlist walk ($10D2) ------------------------------------------
    def _orderwalk(self, x: int, f8: int, f9: int) -> None:
        m = self.m
        a1726 = self._a(0x1726) + x
        a172c = self._a(0x172C) + x
        a100c = self._a(0x100C) + x
        guard = 0
        while True:
            guard += 1
            if guard > 0x200:
                m[a100c] = 0
                return
            y = m[a1726]
            a = m[self.ptr(f8, f9) + y]
            if a < 0x80:
                break
            if a == 0xFF:
                m[a1726] = 0
                continue
            if a == 0xFE:
                m[a100c] = 0
                return
            a2 = (a - 0xA0) & 0xFF
            if a < 0xA0:
                a2 = a2 ^ 0x1F
                a2 = (a2 + 1) & 0xFF
            m[a172c] = a2
            m[a1726] = (m[a1726] + 1) & 0xFF
            y = (y + 1) & 0xFF
            a = m[self.ptr(f8, f9) + y]
            break
        yp = a
        nf8 = m[self.b_pat_lo + yp]
        nf9 = m[self.b_pat_hi + yp]
        self._curpat[x] = (nf8, nf9)
        self._patternwalk(x, nf8, nf9)

    # -- pattern walk ($110C) --------------------------------------------
    def _patternwalk(self, x: int, f8: int, f9: int) -> None:
        m = self.m
        a1729 = self._a(0x1729) + x
        a172c = self._a(0x172C) + x
        guard = 0
        while True:
            guard += 1
            if guard > 0x200:
                self._output_1591(x)
                return
            y = m[a1729]
            a = m[self.ptr(f8, f9) + y]
            if a >= 0x80:
                res = self._special(x, f8, f9, a, y)
                if res == "loop":
                    continue
                return
            if a >= 0x60:
                m[self._a(self.c_inst) + x] = a & 0x1F
                m[a1729] = (m[a1729] + 1) & 0xFF
                continue
            a = (a + m[a172c]) & 0xFF
            self._note_idx(x, a)
            return

    # -- pattern special bytes ($1125) -----------------------------------
    def _special(self, x: int, f8: int, f9: int, a: int, y: int) -> str:
        m = self.m
        a1729 = self._a(0x1729) + x
        a172c = self._a(0x172C) + x
        a173b = self._a(0x173B) + x
        a173e = self._a(0x173E) + x
        if a == 0xFE:
            m[a173b] = m[a173e]
            m[a1729] = (m[a1729] + 1) & 0xFF
            self._endcheck(x, f8, f9)
            self._output_1591(x)
            return "done"
        if a == 0xFD:
            m[a173b] = m[a173e]
            m[self._a(0x100F) + x] ^= 0x01
            m[a1729] = (m[a1729] + 1) & 0xFF
            self._endcheck(x, f8, f9)
            self._output_1591(x)
            return "done"
        if a < 0xC0:
            m[a173e] = a & 0x3F
            m[a1729] = (m[a1729] + 1) & 0xFF
            return "loop"
        a &= 0x1F
        m[self._a(0x1741) + x] = a & 0x0F
        if (a & 0x10) == 0:
            y = (y + 1) & 0xFF
            m[self._a(0x1744) + x] = (m[self.ptr(f8, f9) + y] + m[a172c]) & 0xFF
            y = (y + 1) & 0xFF
            m[self._a(0x1747) + x] = (m[self.ptr(f8, f9) + y] + m[a172c]) & 0xFF
            m[a1729] = (m[a1729] + 2) & 0xFF
            self._note_idx(x, m[self._a(0x1744) + x])
            return "done"
        y = (y + 1) & 0xFF
        m[self._a(0x1747) + x] = (m[self.ptr(f8, f9) + y] + m[a172c]) & 0xFF
        m[self._a(0x1744) + x] = m[self._a(self.c_note) + x]
        m[a1729] = (m[a1729] + 1) & 0xFF
        m[a173b] = m[a173e]
        m[a1729] = (m[a1729] + 1) & 0xFF
        self._endcheck(x, f8, f9)
        self._output_1591(x)
        return "done"

    # -- note index / freq-table read ($11A6) ----------------------------
    def _note_idx(self, x: int, a: int) -> None:
        m = self.m
        m[self._a(self.c_note) + x] = a
        y = a
        m[self._a(0x172F) + x] = m[self.b_freqlo + y]
        m[self._a(0x1732) + x] = m[self.b_freqhi + y]
        for off in (0x35, 0x38, 0x68, 0x6B, 0x6E, 0x98, 0x9B):
            m[self._a(0x1700 + off) + x] = 0
        m[self._a(0x1729) + x] = (m[self._a(0x1729) + x] + 1) & 0xFF
        m[self._a(0x173B) + x] = m[self._a(0x173E) + x]
        yv = m[self._a(0x170D) + x]
        self.w(SID_BASE + 4 + yv, self._onset_ctrl)
        m[self._a(0x100F) + x] = 0xFF
        m[self._a(0x174A) + x] = 0xFF
        f8, f9 = self._curpat[x]
        self._endcheck(x, f8, f9)

    def _endcheck(self, x: int, f8: int, f9: int) -> None:
        m = self.m
        y = m[self._a(0x1729) + x]
        if m[self.ptr(f8, f9) + y] == 0xFF:
            m[self._a(0x1729) + x] = 0
            m[self._a(0x1726) + x] = (m[self._a(0x1726) + x] + 1) & 0xFF

    # -- non-row tick dispatch ($11F9) -----------------------------------
    def _jmp_11f9(self, x: int) -> None:
        if self.m[self._a(0x174A) + x] != 0:
            self._note_trigger(x)
        else:
            self._sustain_1300(x)

    # -- note-trigger instrument setup ($1201) ---------------------------
    def _note_trigger(self, x: int) -> None:
        m = self.m
        m[self._a(0x174A) + x] = 0
        m[self._a(0x1750) + x] = 0
        m[self._a(0x1789) + x] = 0
        m[self._a(0x1792) + x] = 0
        m[self._a(0x1795) + x] = 0
        ins = m[self._a(self.c_inst) + x]
        idx = (ins * 11) & 0xFF
        m[self._a(0x174D) + x] = idx
        y = idx
        pwlo = m[self.b_instr + y]
        pwhi = m[self.b_instr + 1 + y]
        yv = m[self._a(0x170D) + x]
        self._emit_adsr(x, yv, pwlo, pwhi)
        y = m[self._a(0x174D) + x]
        flags = m[self.b_instr + 0x0A + y]
        if (flags & 0x04) == 0:
            av = m[self.b_instr + 0x02 + y]
            m[self._a(0x1753) + x] = av & 0x0F
            hi = (av >> self._pw_min_shift) & 0xFF
            m[self._a(0x1756) + x] = hi
            m[self._a(0x1759) + x] = hi ^ 0x0F
            m[self._a(0x175F) + x] = (m[self.b_instr + 0x06 + y] >> 4) & 0x0F
            m[self._a(0x1762) + x] = 0
            m[self._a(0x1765) + x] = 0
        flags = m[self.b_instr + 0x0A + y]
        if (flags & 0x20) != 0:
            m[self._a(self.g_filt)] = m[self._a(self.g_filt)] | m[self._a(0x1710) + x]
            if (m[self.b_instr + 0x0A + y] & 0x02) == 0:
                m[self._a(0x1719)] = 0
                m[self._a(0x171A)] = 0
                av = m[self.b_instr + 0x06 + y] & 0x0F
                av = (av << 4) & 0xFF
                m[self._a(0x171B)] = av
                yf = av
                cv = m[self.b_filt_ctrl + yf]
                m[self._a(0x1723)] = cv & 0xF0
                a2 = cv & 0x0F
                a2 = (a2 << 4) & 0xFF
                a2 = a2 | m[self._a(0x1717)]
                self.w(SID_MODE_VOL, a2)
                m[self._a(0x171C)] = m[self.b_filt_ctrl + 1 + yf]
                m[self._a(0x171D)] = m[self.b_filt_ctrl + 2 + yf]
                m[self._a(0x171E)] = m[self.b_filt_ctrl + 3 + yf]
        else:
            m[self._a(self.g_filt)] = m[self._a(self.g_filt)] & m[self._a(0x1713) + x]
        y = m[self._a(0x174D) + x]
        av = m[self.b_instr + 0x07 + y]
        m[self._a(0x1771) + x] = (av & 0xF0) >> 1
        m[self._a(0x1774) + x] = av & 0x0F
        m[self._a(0x1777) + x] = m[self.b_instr + 0x08 + y]
        m[self._a(0x177A) + x] = m[self.b_instr + 0x09 + y]
        m[self._a(0x177D) + x] = m[self.b_instr + 0x0A + y]
        self._seed_vib(x)
        m[self._a(0x1786) + x] = 2
        self._inst_end(x)

    # -- note-trigger variant hooks --------------------------------------
    def _emit_adsr(self, x: int, yv: int, ad: int, sr: int) -> None:
        """Write AD ($D405) / SR ($D406) at instrument-init ($1230/$1234)."""
        del x
        self.w(SID_BASE + 6 + yv, sr)
        self.w(SID_BASE + 5 + yv, ad)

    def _seed_vib(self, x: int) -> None:
        """Seed the vibrato scale/delta ($12EE): ``$178C = NoteFreqHi[note] >> 1``."""
        yn = self.m[self._a(self.c_note) + x]
        self.m[self._a(0x178C) + x] = self.m[self.b_freqhi + yn] >> 1

    def _inst_end(self, x: int) -> None:
        """First frame after instrument-init ($12FD): emit the waveform output."""
        self._output_1591(x)

    # -- sustain tick ($1300) --------------------------------------------
    def _sustain_1300(self, x: int) -> None:
        m = self.m
        a1786 = self._a(0x1786) + x
        if (m[self._a(0x177D) + x] & 0x80) != 0 and m[a1786] == 2:
            yv = m[self._a(0x170D) + x]
            self.w(SID_BASE + yv, 0xFF)
            self.w(SID_BASE + 1 + yv, 0xFF)
            self.w(SID_BASE + 4 + yv, 0x81)
            m[a1786] = (m[a1786] - 1) & 0xFF
            return
        self._sustain_1322(x)

    def _sustain_1322(self, x: int) -> None:
        """Steady-frame tick from ``$1322`` (hard-restart-burst check already done)."""
        m = self.m
        a1786 = self._a(0x1786) + x
        if m[a1786] != 0:
            m[a1786] = (m[a1786] - 1) & 0xFF
            self._tick_134e(x)
            return
        if (m[self._a(0x177D) + x] & 0x10) != 0:
            if m[self._a(0x173B) + x] == 1:
                self._release_gate(x)
            self._tick_134e(x)
            return
        if (m[self._a(0x177D) + x] & 0x08) == 0:
            m[self._a(0x100F) + x] = 0xFE
        self._tick_134e(x)

    def _release_gate(self, x: int) -> None:
        """Gate-off on a note's last frame ($133B): force the gate mask to $FE.

        The release also zeroes AD/SR when the build's release helper does (an
        envelope-clearing hard-restart) -- read from the code, since both DMC
        generations ship clearing and non-clearing releases (see
        :func:`~pydmcsid.reader.release_clears_adsr`).
        """
        m = self.m
        m[self._a(0x100F) + x] = 0xFE
        if self._release_clears_adsr:
            yv = m[self._a(0x170D) + x]
            self.w(SID_BASE + 5 + yv, 0)
            self.w(SID_BASE + 6 + yv, 0)

    # -- 16-bit PW sweep ($134E) -----------------------------------------
    def _tick_134e(self, x: int) -> None:
        m = self.m
        av = m[self._a(0x1762) + x] >> 1
        av = (av + m[self._a(0x174D) + x]) & 0xFF
        m[self._a(0x171F)] = m[self.b_pwtab + av]
        if (m[self._a(0x1762) + x] & 0x01) != 0:
            av = m[self._a(0x171F)] & 0x0F
            av = (av << 4) & 0xFF
        else:
            av = m[self._a(0x171F)] & 0xF0
        av = (av + m[self._a(0x175F) + x]) & 0xFF
        m[self._a(0x175C) + x] = av
        if m[self._a(0x1765) + x] == 0:
            lo = m[self._a(0x1750) + x] + m[self._a(0x175C) + x]
            m[self._a(0x1750) + x] = lo & 0xFF
            hi = m[self._a(0x1753) + x] + (1 if lo > 0xFF else 0)
            m[self._a(0x1753) + x] = hi & 0xFF
            if (hi & 0xFF) == m[self._a(0x1759) + x]:
                m[self._a(0x1765) + x] = 1
                self._t13bb(x)
                return
            self._t13c5(x)
            return
        lo = m[self._a(0x1750) + x] - m[self._a(0x175C) + x]
        m[self._a(0x1750) + x] = lo & 0xFF
        hi = m[self._a(0x1753) + x] - (1 if lo < 0 else 0)
        m[self._a(0x1753) + x] = hi & 0xFF
        if (hi & 0xFF) == m[self._a(0x1756) + x]:
            m[self._a(0x1765) + x] = 0
            self._t13bb(x)
            return
        self._t13c5(x)

    def _t13bb(self, x: int) -> None:
        m = self.m
        if m[self._a(0x1762) + x] != 0x05:
            m[self._a(0x1762) + x] = (m[self._a(0x1762) + x] + 1) & 0xFF
        self._t13c5(x)

    # -- filter sweep ($13C5/$13D1) --------------------------------------
    def _t13c5(self, x: int) -> None:
        m = self.m
        if (m[self._a(0x177D) + x] & 0x20) != 0 and m[self._a(0x1720)] == 0:
            m[self._a(0x1720)] = (x + 1) & 0xFF
            if m[self._a(0x171C)] != m[self._a(0x171E)]:
                y = (m[self._a(0x171B)] + m[self._a(0x1719)]) & 0xFF
                m[self._a(0x1721)] = m[self.b_filt_step_lo + y]
                m[self._a(0x1722)] = m[self.b_filt_step_hi + y]
                m[self._a(0x171C)] = (m[self._a(0x171C)] + m[self._a(0x1721)]) & 0xFF
                m[self._a(0x171A)] = (m[self._a(0x171A)] + 1) & 0xFF
                if m[self._a(0x171A)] == m[self._a(0x1722)]:
                    m[self._a(0x171A)] = 0
                    m[self._a(0x1719)] = (m[self._a(0x1719)] + 1) & 0xFF
                    if m[self._a(0x1719)] == 0x06:
                        m[self._a(0x1719)] = m[self._a(0x171D)]
        self._t141c(x)

    # -- glide ($141C) ---------------------------------------------------
    def _t141c(self, x: int) -> None:
        m = self.m
        if m[self._a(0x1741) + x] != 0:
            self._glide(x)
            return
        if m[self._a(0x1771) + x] != 0:
            m[self._a(0x1771) + x] = (m[self._a(0x1771) + x] - 1) & 0xFF
            self._output_1591(x)
            return
        self._t14aa(x)

    def _glide(self, x: int) -> None:
        m = self.m
        av = (m[self._a(0x1741) + x] << 4) & 0xFF
        m[self._a(0x171F)] = av
        if m[self._a(0x1744) + x] >= m[self._a(0x1747) + x]:
            y = m[self._a(0x1747) + x]
            lo = m[self._a(0x1735) + x] - m[self._a(0x171F)]
            m[self._a(0x1735) + x] = lo & 0xFF
            hi = m[self._a(0x1738) + x] - (1 if lo < 0 else 0)
            m[self._a(0x1738) + x] = hi & 0xFF
        else:
            y = m[self._a(0x1747) + x]
            lo = m[self._a(0x1735) + x] + m[self._a(0x171F)]
            m[self._a(0x1735) + x] = lo & 0xFF
            hi = m[self._a(0x1738) + x] + (1 if lo > 0xFF else 0)
            m[self._a(0x1738) + x] = hi & 0xFF
        s = m[self._a(0x1735) + x] + m[self._a(0x172F) + x]
        chk = (
            m[self._a(0x1738) + x] + m[self._a(0x1732) + x] + (1 if s > 0xFF else 0)
        ) & 0xFF
        if chk != m[self.b_freqhi + y]:
            self._output_1591(x)
            return
        m[self._a(self.c_note) + x] = y
        m[self._a(0x172F) + x] = m[self.b_freqlo + y]
        m[self._a(0x1732) + x] = m[self.b_freqhi + y]
        m[self._a(0x1741) + x] = 0
        m[self._a(0x1735) + x] = 0
        m[self._a(0x1738) + x] = 0
        self._output_1591(x)

    # -- vibrato ($14AA alternate pitch-out / $1520 triangle) ------------
    def _t14aa(self, x: int) -> None:
        m = self.m
        if (m[self._a(0x177D) + x] & 0x40) != 0:
            m[self._a(self.g_vibtoggle)] = (m[self._a(self.g_vibtoggle)] + 1) & 0x01
            if m[self._a(self.g_vibtoggle)] == 0:
                self._output_1591(x)
                return
            yv = m[self._a(0x170D) + x]
            lo = m[self._a(0x172F) + x] + m[self._a(0x1735) + x]
            m[self._a(0x1724)] = lo & 0xFF
            hi = m[self._a(0x1732) + x] + (1 if lo > 0xFF else 0)
            m[self._a(0x1725)] = hi & 0xFF
            d = m[self._a(0x1724)] - m[self._a(0x1798) + x]
            self.w(SID_BASE + yv, d & 0xFF)
            d2 = m[self._a(0x1725)] - m[self._a(0x179B) + x] - (1 if d < 0 else 0)
            self.w(SID_BASE + 1 + yv, d2 & 0xFF)
            if (m[self._a(0x1777) + x] & 0x80) == 0:
                lo = m[self._a(0x1798) + x] + m[self._a(0x1777) + x]
                m[self._a(0x1798) + x] = lo & 0xFF
                hi = m[self._a(0x179B) + x] + (1 if lo > 0xFF else 0)
                m[self._a(0x179B) + x] = hi & 0xFF
                self._out_1619(x)
                return
            m[self._a(self.g_bendscratch)] = m[self._a(0x1777) + x] & 0x7F
            lo = m[self._a(0x1798) + x] - m[self._a(self.g_bendscratch)]
            m[self._a(0x1798) + x] = lo & 0xFF
            hi = m[self._a(0x179B) + x] - (1 if lo < 0 else 0)
            m[self._a(0x179B) + x] = hi & 0xFF
            self._out_1619(x)
            return
        self._t1520(x)

    def _t1520(self, x: int) -> None:
        m = self.m
        if m[self._a(0x1768) + x] == 0:
            lo = m[self._a(0x1735) + x] + m[self._a(0x1792) + x]
            m[self._a(0x1735) + x] = lo & 0xFF
            hi = (
                m[self._a(0x1738) + x]
                + m[self._a(0x1795) + x]
                + (1 if lo > 0xFF else 0)
            )
            m[self._a(0x1738) + x] = hi & 0xFF
        else:
            lo = m[self._a(0x1735) + x] - m[self._a(0x1792) + x]
            m[self._a(0x1735) + x] = lo & 0xFF
            hi = m[self._a(0x1738) + x] - m[self._a(0x1795) + x] - (1 if lo < 0 else 0)
            m[self._a(0x1738) + x] = hi & 0xFF
        m[self._a(0x176B) + x] = (m[self._a(0x176B) + x] + 1) & 0xFF
        if m[self._a(0x176B) + x] == m[self._a(0x1774) + x]:
            self._t1567(x)
            return
        self._output_1591(x)

    def _t1567(self, x: int) -> None:
        m = self.m
        m[self._a(0x176B) + x] = 0
        m[self._a(0x1768) + x] ^= 0x01
        if m[self._a(0x176E) + x] == m[self._a(0x1777) + x]:
            self._output_1591(x)
            return
        m[self._a(0x176E) + x] = (m[self._a(0x176E) + x] + 1) & 0xFF
        lo = m[self._a(0x1792) + x] + m[self._a(0x178C) + x]
        m[self._a(0x1792) + x] = lo & 0xFF
        hi = m[self._a(0x1795) + x] + (1 if lo > 0xFF else 0)
        m[self._a(0x1795) + x] = hi & 0xFF
        self._output_1591(x)

    # -- final freq compose + wavetable walk ($1591/$1598/$15D5) ---------
    def _output_1591(self, x: int) -> None:
        if (self.m[self._a(0x177D) + x] & 0x01) != 0:
            self._compose_15d5(x)
        else:
            self._compose_1598(x)

    def _compose_1598(self, x: int) -> None:
        m = self.m
        a177a = self._a(0x177A) + x
        while True:
            y = m[a177a]
            a = m[self.b_arp_ctrl + y]
            if a < 0x90:
                break
            m[self._a(0x171F)] = (a - 0x90) & 0xFF
            m[a177a] = (m[a177a] - m[self._a(0x171F)]) & 0xFF
        m[self._a(0x1780) + x] = a
        bb = (m[self.b_arp_note + y] + m[self._a(self.c_note) + x]) & 0xFF
        m[self._a(0x1783) + x] = bb
        m[self._a(0x172F) + x] = m[self.b_freqlo + bb]
        m[self._a(0x1732) + x] = m[self.b_freqhi + bb]
        m[a177a] = (m[a177a] + 1) & 0xFF
        self._t1603(x)

    def _compose_15d5(self, x: int) -> None:
        m = self.m
        a177a = self._a(0x177A) + x
        while True:
            y = m[a177a]
            a = m[self.b_arp_ctrl + y]
            if a < 0x90:
                break
            m[self._a(0x171F)] = (a - 0x90) & 0xFF
            m[a177a] = (m[a177a] - m[self._a(0x171F)]) & 0xFF
        m[self._a(0x1780) + x] = a
        m[self._a(0x172F) + x] = 0
        m[self._a(0x1732) + x] = m[self.b_arp_note + y]
        m[a177a] = (m[a177a] + 1) & 0xFF
        self._t1603(x)

    def _t1603(self, x: int) -> None:
        m = self.m
        yv = m[self._a(0x170D) + x]
        lo = m[self._a(0x172F) + x] + m[self._a(0x1735) + x]
        self.w(SID_BASE + yv, lo & 0xFF)
        hi = m[self._a(0x1732) + x] + m[self._a(0x1738) + x] + (1 if lo > 0xFF else 0)
        self.w(SID_BASE + 1 + yv, hi & 0xFF)
        self._out_1619(x)

    def _out_1619(self, x: int) -> None:
        m = self.m
        yv = m[self._a(0x170D) + x]
        self.w(SID_BASE + 2 + yv, m[self._a(0x1750) + x])
        self.w(SID_BASE + 3 + yv, m[self._a(0x1753) + x])
        self.w(SID_BASE + 4 + yv, m[self._a(0x1780) + x] & m[self._a(0x100F) + x])


class PlayerV1D(Player):
    """The later init-``$1d`` DMC play body (see :func:`pydmcsid.reader.dmc_variant`).

    Same core engine as the init-``$37`` :class:`Player`, differing only in the
    localized ways the $1d body re-encoded the pattern stream and restructured
    note onset (transcribed from the disassembly):

    * global work cells relocated out of the $37 id-string region
      (``$1034/$1035/$1036`` -> ``$1018/$1019/$101a``);
    * pattern markers re-encoded ``$fe/$fd/$ff`` -> ``$7e/$7d/$7f`` and two new
      pattern commands: ``$f0..$ff`` set a per-voice sustain override (``$17b3``)
      and ``$7c`` toggles a per-voice legato flag (``$17b0``);
    * note onset writes ``AD=SR=$0f`` (with ``CTRL=$08``) via the ``$17fb`` helper;
      a set legato flag skips the whole retrigger (freq-only update);
    * the vibrato delta is seeded from a note-indexed table and its depth ramp
      doubles the vibrato period instead of growing the delta by a scale;
    * the hard-restart ``$ffff``/``CTRL=$81`` burst fires on the instrument-init
      frame (not one frame later) and the SR gets the sustain override.
    """

    order_table_op = constants.ORDER_TABLE_OP_V1D
    vib_seed_op = constants.VIB_SEED_OP

    def _setup_cells(self, operand) -> None:
        # The $1d sub-layouts relocate a 9-cell block (note[3], inst[3], filt,
        # vib-toggle, bend) as a unit; its base is read from the note-store
        # operand at $11A6.  inst=note+3, filt=note+6, toggle=note+7, bend=note+8.
        note = operand(constants.NOTE_CELL_OP) - self.rel
        self.c_note = note & 0xFFFF
        self.c_inst = (note + 3) & 0xFFFF
        self.g_filt = (note + 6) & 0xFFFF
        self.g_vibtoggle = (note + 7) & 0xFFFF
        self.g_bendscratch = (note + 8) & 0xFFFF
        # rest/tie/legato tail ($1180): full steady tick ($1322) or re-output only.
        tail = (operand(constants.REST_TAIL_OP) - self.load) & 0xFFFF
        self._rest_via_1322 = tail == constants.REST_TAIL_1322
        # hard-restart burst FREQ immediate ($130A: LDA #imm) -- $ff for nearly
        # all tunes, but a per-tune code constant.
        self._burst_imm = self.m[self.load + constants.BURST_IMM_REL]
        # Note-onset SID writes ($11DB helper call): the stock ``JSR $17FB``
        # emits CTRL then AD=SR=$0F; a ``BIT`` no-op patch emits nothing (the
        # onset slips a frame).  Read from the code (see ``v1d_note_onset``).
        self._onset_ctrl, self._onset_adsr = v1d_note_onset(self.m, self.load)

    def _rest_tail(self, x: int) -> None:
        """The shared $117D tail: $1322 steady tick or $1591 re-output per build."""
        if self._rest_via_1322:
            self._sustain_1322(x)
        else:
            self._output_1591(x)

    def _init_extra(self) -> None:
        for x in range(8):  # clear the new per-voice cells ($17b0/$17b3) -- $1870
            self.m[self._a(0x17B0) + x] = 0

    # -- pattern walk ($110C -> $17C0/$1837 dispatch) --------------------
    def _patternwalk(self, x: int, f8: int, f9: int) -> None:
        m = self.m
        a1729 = self._a(0x1729) + x
        a172c = self._a(0x172C) + x
        ptr = self.ptr(f8, f9)
        guard = 0
        while True:
            guard += 1
            if guard > 0x200:
                self._output_1591(x)
                return
            y = m[a1729]
            a = m[ptr + y]
            if a >= 0xF0:  # volume: per-voice sustain override ($1840)
                m[self._a(0x17B3) + x] = a & 0x0F
                m[a1729] = (m[a1729] + 1) & 0xFF
                continue
            if a == 0x7C:  # legato toggle ($17CC)
                m[self._a(0x17B0) + x] ^= 0x01
                m[a1729] = (m[a1729] + 1) & 0xFF
                continue
            if a == 0x7E:  # rest / continue (was $fe)
                self._v1d_rest(x, f8, f9)
                return
            if a == 0x7D:  # tie + gate toggle (was $fd)
                self._v1d_tie(x, f8, f9)
                return
            if a >= 0xC0:  # slide / portamento ($1131)
                self._v1d_slide(x, f8, f9, a, y)
                return
            if a >= 0x80:  # set note-duration ($17DE)
                m[self._a(0x173E) + x] = a & 0x3F
                m[a1729] = (m[a1729] + 1) & 0xFF
                continue
            if a >= 0x60:  # select instrument ($1117)
                m[self._a(self.c_inst) + x] = a & 0x1F
                m[a1729] = (m[a1729] + 1) & 0xFF
                continue
            a = (a + m[a172c]) & 0xFF  # note ($11A2: + transpose)
            self._note_idx(x, a)
            return

    def _v1d_rest(self, x: int, f8: int, f9: int) -> None:
        m = self.m
        m[self._a(0x173B) + x] = m[self._a(0x173E) + x]
        m[self._a(0x1729) + x] = (m[self._a(0x1729) + x] + 1) & 0xFF
        self._endcheck(x, f8, f9)
        self._rest_tail(x)

    def _v1d_tie(self, x: int, f8: int, f9: int) -> None:
        m = self.m
        m[self._a(0x173B) + x] = m[self._a(0x173E) + x]
        m[self._a(0x100F) + x] ^= 0x01
        m[self._a(0x1729) + x] = (m[self._a(0x1729) + x] + 1) & 0xFF
        self._endcheck(x, f8, f9)
        self._rest_tail(x)

    def _v1d_slide(self, x: int, f8: int, f9: int, a: int, y: int) -> None:
        m = self.m
        a1729 = self._a(0x1729) + x
        a172c = self._a(0x172C) + x
        ptr = self.ptr(f8, f9)
        a &= 0x1F
        m[self._a(0x1741) + x] = a & 0x0F
        if (a & 0x10) == 0:
            y = (y + 1) & 0xFF
            m[self._a(0x1744) + x] = (m[ptr + y] + m[a172c]) & 0xFF
            y = (y + 1) & 0xFF
            m[self._a(0x1747) + x] = (m[ptr + y] + m[a172c]) & 0xFF
            m[a1729] = (m[a1729] + 2) & 0xFF
            self._note_idx(x, m[self._a(0x1744) + x])
            return
        y = (y + 1) & 0xFF
        m[self._a(0x1747) + x] = (m[ptr + y] + m[a172c]) & 0xFF
        m[self._a(0x1744) + x] = m[self._a(self.c_note) + x]
        m[a1729] = (m[a1729] + 1) & 0xFF
        self._v1d_rest(x, f8, f9)

    # -- end-of-pattern peek ($11E6): marker $7f, also clears $17b0 ($182D)
    def _endcheck(self, x: int, f8: int, f9: int) -> None:
        m = self.m
        y = m[self._a(0x1729) + x]
        if m[self.ptr(f8, f9) + y] == 0x7F:
            m[self._a(0x1729) + x] = 0
            m[self._a(0x1726) + x] = (m[self._a(0x1726) + x] + 1) & 0xFF
            m[self._a(0x17B0) + x] = 0

    # -- note setup ($11A2/$11A6) ----------------------------------------
    def _note_idx(self, x: int, a: int) -> None:
        m = self.m
        m[self._a(self.c_note) + x] = a
        y = a
        m[self._a(0x172F) + x] = m[self.b_freqlo + y]
        m[self._a(0x1732) + x] = m[self.b_freqhi + y]
        m[self._a(0x173B) + x] = m[self._a(0x173E) + x]
        m[self._a(0x1729) + x] = (m[self._a(0x1729) + x] + 1) & 0xFF
        f8, f9 = self._curpat[x]
        if m[self._a(0x17B0) + x] != 0:  # legato: freq-only, no retrigger ($11BF)
            self._endcheck(x, f8, f9)
            self._rest_tail(x)
            return
        for off in (0x35, 0x38, 0x68, 0x6B, 0x6E, 0x98, 0x9B):
            m[self._a(0x1700 + off) + x] = 0
        yv = m[self._a(0x170D) + x]
        if self._onset_ctrl is not None:  # $17fb: CTRL=$08, AD=$0f, SR=$0f
            self.w(SID_BASE + 4 + yv, self._onset_ctrl)
            self.w(SID_BASE + 5 + yv, self._onset_adsr)
            self.w(SID_BASE + 6 + yv, self._onset_adsr)
        m[self._a(0x100F) + x] = 0xFF
        m[self._a(0x174A) + x] = 0xFF
        self._endcheck(x, f8, f9)

    # -- non-row tick dispatch ($11F9): steady path is $1322 -------------
    def _jmp_11f9(self, x: int) -> None:
        if self.m[self._a(0x174A) + x] != 0:
            self._note_trigger(x)
        else:
            self._sustain_1322(x)

    # -- note-trigger hooks ----------------------------------------------
    def _emit_adsr(self, x: int, yv: int, ad: int, sr: int) -> None:
        over = self.m[self._a(0x17B3) + x]  # $184b sustain-nibble override
        if over != 0:
            sr = ((over << 4) & 0xF0) | (sr & 0x0F)
        self.w(SID_BASE + 6 + yv, sr)
        self.w(SID_BASE + 5 + yv, ad)

    def _seed_vib(self, x: int) -> None:
        yn = self.m[self._a(self.c_note) + x]  # $12EE: $1792 = VibScale[note]
        self.m[self._a(0x1792) + x] = self.m[self.b_vibseed + yn]

    def _inst_end(self, x: int) -> None:
        m = self.m
        if m[self._a(0x1774) + x] == 0:  # $1885: no vib period -> clear vib delta
            m[self._a(0x1792) + x] = 0
        if (m[self._a(0x177D) + x] & 0x80) != 0:  # $1300: hard-restart burst now
            yv = m[self._a(0x170D) + x]
            self.w(SID_BASE + yv, self._burst_imm)
            self.w(SID_BASE + 1 + yv, self._burst_imm)
            self.w(SID_BASE + 4 + yv, 0x81)
            return
        self._output_1591(x)

    # $133B release ($17EC helper: gate mask $FE, and AD/SR=0 when the helper
    # clears them) is generation-agnostic -- inherited from ``Player``.

    # -- vibrato depth ramp ($1567): doubles the vib period --------------
    def _t1567(self, x: int) -> None:
        m = self.m
        m[self._a(0x176B) + x] = 0
        m[self._a(0x1768) + x] ^= 0x01
        if m[self._a(0x176E) + x] == m[self._a(0x1777) + x]:
            self._output_1591(x)
            return
        m[self._a(0x176E) + x] = (m[self._a(0x176E) + x] + 1) & 0xFF
        m[self._a(0x1774) + x] = (m[self._a(0x1774) + x] * 2) & 0xFF
        self._output_1591(x)


class PlayerA1:
    """The reorganised ``$a1`` DMC engine (see :func:`pydmcsid.reader.dmc_variant`).

    A genuinely different, V5-era player body (play routine at ``base+$a1``, not
    ``base+$85``) with its own work-RAM map, transcribed from the 6502.  Notable
    departures from the ``base+$85`` engine (:class:`Player`):

    * per-voice state lives in a ``$17cf..$1845`` block (orderptr ``$17cf/$17d2``,
      order index ``$17d5``, pattern index ``$17d8``, duration ``$17db/$17de``,
      transpose ``$17e4``) with globals in ``$1012..$101f``; the SID voice stride
      is ``$1009,X`` = ``{0,7,14}``;
    * a two-frame startup gate (``$1842``), a single global tempo divider
      (``$1013`` reload ``$1012``), and a note is retriggered by writing
      ``CTRL=$09`` (gate+test) for one frame, then the steady tick clears test and
      emits the real waveform;
    * orderlist markers ``$ff``(jump)/``$fe``(stop)/``$fd``/``$fc``(transpose) and
      a rich pattern-command set (``$fd`` duration, ``$fc`` instrument, ``$fb/$fa``
      portamento, ``$f9`` filter-res+volume, ``$f8`` cutoff, ``$f2/$f1`` direct
      AD/SR, ``$f7/$f6`` volume-fade speeds, ``$f5`` tie, ``$f4`` gate, ``$f3``
      sustain override);
    * per-voice wavetable (``$199e`` ctrl / ``$19ab`` arg), 16-bit PW sweep
      (``$19b8/$19bf``), triangle vibrato with a one-shot depth-doubling ramp, and
      a portamento; a GLOBAL filter-cutoff sweep (``$19c6/$19c7``, driven once per
      frame off voice 2) and a global volume fade (``$1018`` up / ``$1019`` down)
      compose the final ``$d418``;
    * two per-build patchable release stores (``$16c7`` SR-clear, ``$16e3``
      gate-off mask) read from the opcode (``STA`` vs ``BIT`` no-op).

    Conforms to the :class:`Player` playback interface (``init_writes`` +
    ``play_frame``) so :func:`iter_frames` drives it identically.
    """

    # pylint: disable=too-many-instance-attributes

    def __init__(self, song: Song, subtune: int = 0):
        self.song = song
        self.m = bytearray(song.mem)
        base = song.base
        self.base = base
        self.rel = base - 0x1000  # the player is authored at $1000
        self.sub = subtune & 0xFF
        self._writes: List[Tuple[int, int]] = []
        self._sid_setup: List[Tuple[int, int]] = []  # startup-gate frame writes
        self.finished = False
        self.f8 = 0  # pattern/orderlist pointer (zero-page $f8/$f9)
        self.f9 = 0
        m = self.m

        def op(off: int) -> int:
            idx = base + off
            return (m[idx] | (m[idx + 1] << 8)) & 0xFFFF

        order = a1_order_table_base(m, base)
        self.b_order = order if order is not None else 0
        self.b_patlo = op(constants.A1_PATTERN_LO_OP)
        self.b_pathi = op(constants.A1_PATTERN_HI_OP)
        self.b_inst = op(constants.A1_INST_OP)
        self.b_wtctrl = op(constants.A1_WT_CTRL_OP)
        self.b_wtarg = op(constants.A1_WT_ARG_OP)
        self.b_pwa = op(constants.A1_PW_A_OP)
        self.b_pwb = op(constants.A1_PW_B_OP)
        self.b_filta = op(constants.A1_FILT_A_OP)
        self.b_filtb = op(constants.A1_FILT_B_OP)
        self.b_freqlo = (base + constants.A1_FREQ_LO_REL) & 0xFFFF
        self.b_freqhi = (base + constants.A1_FREQ_HI_REL) & 0xFFFF
        # Per-build patchable release SR-clear ($16c7 STA $d406,Y == $99).
        self._rel_clears_sr = m[(base + constants.A1_REL_SR_CLEAR_REL) & 0xFFFF] == 0x99
        self.init()

    def _a(self, addr: int) -> int:
        return (addr + self.rel) & 0xFFFF

    def w(self, reg: int, val: int) -> None:
        """Emit a SID register write (absolute $D4xx)."""
        self._writes.append((reg & 0xFFFF, val & 0xFF))

    def _ld(self, y: int) -> int:
        """``($f8),Y`` pattern/orderlist byte read (f8/f9 are absolute)."""
        return self.m[(((self.f9 << 8) | self.f8) + (y & 0xFF)) & 0xFFFF]

    # -- init ($1040) ----------------------------------------------------
    def init(self) -> None:
        """Run the ``$a1`` init for the selected subtune."""
        m = self.m
        a = self._a
        self._writes = []
        y = (self.sub * 8) & 0xFF
        for x in range(3):
            m[a(0x17CF) + x] = m[(self.b_order + y) & 0xFFFF]
            m[a(0x17D2) + x] = m[(self.b_order + y + 1) & 0xFFFF]
            y = (y + 2) & 0xFF
        m[a(0x1012)] = m[(self.b_order + y) & 0xFFFF]
        m[a(0x101B)] = m[(self.b_order + y + 1) & 0xFFFF]
        for i in range(0x71):
            m[a(0x17D5) + i] = 0
        m[a(0x1018)] = 0
        m[a(0x1019)] = 0
        for x in range(3):
            m[a(0x17DB) + x] = 1
            m[a(0x1006) + x] = 1
        for i in range(0x18):
            self.w(SID_BASE + i, 0)
        self.w(SID_BASE + 0x04, 0x08)
        self.w(SID_BASE + 0x0B, 0x08)
        self.w(SID_BASE + 0x12, 0x08)
        m[a(0x1842)] = 2
        # The two startup-gate play frames ($1842) run before real playback and
        # touch no SID register (the play body JMPs past its writes at $10ac), so
        # the chip holds this init register state for those frames.  Keep the
        # writes so ``play_frame`` re-emits them for the gate frames -- otherwise
        # they are silent and the per-VBI write framing (which anchors frame 0 on
        # the first post-init write) would drop them, shifting playback two frames
        # early relative to the chip.
        self._sid_setup = list(self._writes)

    @property
    def init_writes(self) -> List[Tuple[int, int]]:
        """The SID writes the init routine emitted (frame-0 baseline)."""
        return list(self._writes)

    # -- play ($10a1) ----------------------------------------------------
    def play_frame(self) -> List[Tuple[int, int]]:
        """Run one player tick; return ``(reg, value)`` writes (abs $D4xx)."""
        self._writes = []
        m = self.m
        a = self._a
        if m[a(0x1842)] != 0:  # two-frame startup gate: chip holds the init state
            m[a(0x1842)] = (m[a(0x1842)] - 1) & 0xFF
            self._writes = list(self._sid_setup)
            return list(self._writes)
        m[a(0x1013)] = (m[a(0x1013)] - 1) & 0xFF  # global tempo divider
        if m[a(0x1013)] & 0x80:
            m[a(0x1013)] = m[a(0x1012)]
        for x in range(3):
            self._voice(x)
        self.w(SID_BASE + 0x15, m[a(0x1017)])  # filter cutoff lo
        self.w(SID_BASE + 0x16, m[a(0x1016)])  # filter cutoff hi
        self.finished = all(m[a(0x1006) + x] == 0 for x in range(3))
        return list(self._writes)

    # -- per-voice gate ($10dd) ------------------------------------------
    def _voice(self, x: int) -> None:
        m = self.m
        a = self._a
        if m[a(0x1012)] == m[a(0x1013)] and m[a(0x1006) + x] != 0:
            m[a(0x17DB) + x] = (m[a(0x17DB) + x] - 1) & 0xFF
            if m[a(0x17DB) + x] == 0:
                self._advance(x)
                return
        self._steady(x)

    # -- orderlist advance ($10f2) ---------------------------------------
    def _advance(self, x: int) -> None:
        m = self.m
        a = self._a
        self.f8 = m[a(0x17CF) + x]
        self.f9 = m[a(0x17D2) + x]
        y = m[a(0x17D5) + x]
        v = self._ld(y)
        if v < 0x80:
            self._pattern_setup(x, v)
            return
        if v == 0xFF:  # jump: next byte is the new order index
            y = (y + 1) & 0xFF
            v = self._ld(y)
            m[a(0x17D5) + x] = v
            y = v
            v = self._ld(y)
        elif v == 0xFE:  # stop
            m[a(0x1006) + x] = 0
            self._wt_output(x)
            return
        if v == 0xFD:  # set transpose (positive)
            y = (y + 1) & 0xFF
            m[a(0x17D5) + x] = (m[a(0x17D5) + x] + 2) & 0xFF
            m[a(0x17E4) + x] = self._ld(y)
            y = (y + 1) & 0xFF
            v = self._ld(y)
        elif v == 0xFC:  # set transpose (negated)
            y = (y + 1) & 0xFF
            m[a(0x17D5) + x] = (m[a(0x17D5) + x] + 2) & 0xFF
            m[a(0x17E4) + x] = ((self._ld(y) ^ 0xFF) + 1) & 0xFF
            y = (y + 1) & 0xFF
            v = self._ld(y)
        self._pattern_setup(x, v)

    def _pattern_setup(self, x: int, num: int) -> None:  # $114d
        self.f8 = self.m[(self.b_patlo + num) & 0xFFFF]
        self.f9 = self.m[(self.b_pathi + num) & 0xFFFF]
        self._patwalk(x)

    # -- pattern walk ($1158) --------------------------------------------
    def _patwalk(self, x: int) -> None:
        m = self.m
        a = self._a
        for _ in range(0x400):
            y = m[a(0x17D8) + x]
            v = self._ld(y)
            if v < 0x80:  # note
                self._note(x, v)
                return
            if v == 0xFD:  # set note-duration
                m[a(0x17DE) + x] = self._ld((y + 1) & 0xFF)
                m[a(0x17D8) + x] = (m[a(0x17D8) + x] + 2) & 0xFF
                continue
            if v == 0xFC:  # select instrument
                m[a(0x17E1) + x] = self._ld((y + 1) & 0xFF)
                m[a(0x17D8) + x] = (m[a(0x17D8) + x] + 2) & 0xFF
                continue
            if v == 0xFE:  # end of row
                self._pat_end_tail(x)
                return
            if v == 0xF4:  # gate-toggle + end of row
                m[a(0x1817) + x] ^= 0x01
                self._pat_end_tail(x)
                return
            if v == 0xF5:  # tie toggle
                m[a(0x17EA) + x] ^= 0xFF
                m[a(0x17D8) + x] = (m[a(0x17D8) + x] + 1) & 0xFF
                continue
            if v == 0xF3:  # sustain-nibble override
                m[a(0x17E7) + x] = self._ld((y + 1) & 0xFF)
                m[a(0x17D8) + x] = (m[a(0x17D8) + x] + 2) & 0xFF
                continue
            if v == 0xFB:  # portamento: speed, from-note, to-note
                m[a(0x17ED) + x] = self._ld((y + 1) & 0xFF)
                m[a(0x100F) + x] = (self._ld((y + 2) & 0xFF) + m[a(0x17E4) + x]) & 0xFF
                tgt = (self._ld((y + 3) & 0xFF) + m[a(0x17E4) + x]) & 0xFF
                m[a(0x17F0) + x] = tgt
                if tgt == 0:
                    m[a(0x17F0) + x] = tgt
                    m[a(0x17D8) + x] = (m[a(0x17D8) + x] + 2) & 0xFF
                    self._pat_end_tail(x)
                    return
                m[a(0x17D8) + x] = (m[a(0x17D8) + x] + 3) & 0xFF
                self._note_onset(x)
                return
            if v == 0xFA:  # portamento: speed, to-note (from current)
                m[a(0x17ED) + x] = self._ld((y + 1) & 0xFF)
                m[a(0x17F0) + x] = (self._ld((y + 2) & 0xFF) + m[a(0x17E4) + x]) & 0xFF
                m[a(0x17D8) + x] = (m[a(0x17D8) + x] + 2) & 0xFF
                self._pat_end_tail(x)
                return
            if v == 0xF9:  # filter res ($d417) + volume-hi ($1015)
                b = self._ld((y + 1) & 0xFF)
                self.w(SID_BASE + 0x17, b if b == 0 else (((b << 4) & 0xFF) | 0x04))
                m[a(0x1015)] = b & 0xF0
                m[a(0x17D8) + x] = (m[a(0x17D8) + x] + 2) & 0xFF
                continue
            if v == 0xF8:  # filter cutoff-hi override
                m[a(0x1843)] = self._ld((y + 1) & 0xFF)
                m[a(0x17D8) + x] = (m[a(0x17D8) + x] + 2) & 0xFF
                continue
            if v == 0xF2:  # direct AD
                self.w(SID_BASE + 0x05 + m[a(0x1009) + x], self._ld((y + 1) & 0xFF))
                m[a(0x17D8) + x] = (m[a(0x17D8) + x] + 2) & 0xFF
                continue
            if v == 0xF1:  # direct SR
                self.w(SID_BASE + 0x06 + m[a(0x1009) + x], self._ld((y + 1) & 0xFF))
                m[a(0x17D8) + x] = (m[a(0x17D8) + x] + 2) & 0xFF
                continue
            if v == 0xF7:  # volume fade-up speed
                m[a(0x1018)] = self._ld((y + 1) & 0xFF)
                m[a(0x17D8) + x] = (m[a(0x17D8) + x] + 2) & 0xFF
                continue
            if v == 0xF6:  # volume fade-down speed
                m[a(0x1019)] = self._ld((y + 1) & 0xFF)
                m[a(0x17D8) + x] = (m[a(0x17D8) + x] + 2) & 0xFF
                continue
            m[a(0x17D8) + x] = (m[a(0x17D8) + x] + 2) & 0xFF  # unknown: skip 2
        self._wt_output(x)

    def _pat_end_tail(self, x: int) -> None:  # $118c
        m = self.m
        a = self._a
        m[a(0x17DB) + x] = m[a(0x17DE) + x]
        m[a(0x17D8) + x] = (m[a(0x17D8) + x] + 1) & 0xFF
        v = self._ld(m[a(0x17D8) + x])
        m[a(0x181D) + x] = v
        if v == 0xFF:  # end of pattern -> advance order index
            m[a(0x17D8) + x] = 0
            m[a(0x17E7) + x] = 0
            m[a(0x17EA) + x] = 0
            m[a(0x17D5) + x] = (m[a(0x17D5) + x] + 1) & 0xFF
        self._wt_output(x)

    # -- note ($12b5) ----------------------------------------------------
    def _note(self, x: int, v: int) -> None:
        m = self.m
        a = self._a
        m[a(0x100F) + x] = (v + m[a(0x17E4) + x]) & 0xFF
        if m[a(0x17EA) + x] != 0:  # tie: freq-only, no retrigger
            self._pat_end_tail(x)
            return
        self._note_onset(x)

    def _note_onset(self, x: int) -> None:  # $12c4
        m = self.m
        a = self._a
        y = (m[a(0x17E1) + x] * 8) & 0xFF  # inst*8
        ad = self.m[(self.b_inst + y) & 0xFFFF]
        sr = self.m[(self.b_inst + y + 1) & 0xFFFF]
        yv = m[a(0x1009) + x]
        if m[a(0x17E7) + x] != 0:  # sustain-nibble override into SR
            sr = (sr & 0x0F) | ((m[a(0x17E7) + x] << 4) & 0xFF)
        self.w(SID_BASE + 0x06 + yv, sr)
        self.w(SID_BASE + 0x05 + yv, ad)
        m[a(0x17DB) + x] = m[a(0x17DE) + x]
        m[a(0x1808) + x] = 0
        self.w(SID_BASE + 0x04 + yv, 0x09)  # gate+test
        m[a(0x180B) + x] = 0x09
        self.w(SID_BASE + 0x00 + yv, 0)
        self.w(SID_BASE + 0x01 + yv, 0)
        m[a(0x17D8) + x] = (m[a(0x17D8) + x] + 1) & 0xFF
        v = self._ld(m[a(0x17D8) + x])
        m[a(0x181D) + x] = v
        if v == 0xFF:
            m[a(0x17D8) + x] = 0
            m[a(0x17E7) + x] = 0
            m[a(0x17EA) + x] = 0
            m[a(0x17D5) + x] = (m[a(0x17D5) + x] + 1) & 0xFF

    # -- steady ($1332) --------------------------------------------------
    def _steady(self, x: int) -> None:
        if self.m[self._a(0x180B) + x] != 0:  # first frame after onset
            self._note_init(x)
        else:
            self._tick(x)

    def _note_init(self, x: int) -> None:  # $133a
        m = self.m
        a = self._a
        m[a(0x180B) + x] = 0
        self.w(SID_BASE + 0x18, m[a(0x1015)] | m[a(0x101B)])
        y = (m[a(0x17E1) + x] * 8) & 0xFF
        m[a(0x17FC) + x] = self.m[(self.b_inst + y + 5) & 0xFFFF]  # vib delay
        m[a(0x17FF) + x] = self.m[(self.b_inst + y + 6) & 0xFFFF]  # vib period
        m[a(0x101A)] = self.m[(self.b_inst + y + 7) & 0xFFFF] & 0x07  # pw shift
        m[a(0x17F3) + x] = self.m[(self.b_inst + y + 2) & 0xFFFF]  # wavetable start
        v = self.m[(self.b_inst + y + 3) & 0xFFFF]  # pw table start
        m[a(0x1841)] = v
        if v != 0:
            m[a(0x17F6) + x] = v
        v = self.m[(self.b_inst + y + 4) & 0xFFFF]  # filter table start
        m[a(0x183F)] = v
        if v != 0:
            m[a(0x17F9)] = v
        self._wt_step(x, check_loop=False)  # $137f: first step, no loop marker
        m[a(0x1817) + x] = 0xF7
        if m[a(0x1841)] != 0:  # seed PW sweep
            yp = m[a(0x17F6) + x]
            if yp != 0:
                m[a(0x1823) + x] = self.m[(self.b_pwa + yp) & 0xFFFF]
                m[a(0x1820) + x] = self.m[(self.b_pwb + yp) & 0xFFFF]
                m[a(0x1826) + x] = 0
                m[a(0x1829) + x] = 0
                m[a(0x17F6) + x] = (m[a(0x17F6) + x] + 1) & 0xFF
        if m[a(0x183F)] != 0:  # seed global filter cutoff
            yf = m[a(0x17F9)]
            if m[a(0x1843)] != 0:
                m[a(0x1016)] = m[a(0x1843)]
                m[a(0x1017)] = 0
            else:
                m[a(0x1016)] = self.m[(self.b_filta + yf) & 0xFFFF]
                m[a(0x1017)] = self.m[(self.b_filtb + yf) & 0xFFFF]
            m[a(0x183B)] = 0
            m[a(0x183C)] = 0
            m[a(0x17F9)] = (m[a(0x17F9)] + 1) & 0xFF
        m[a(0x1805) + x] = 0
        m[a(0x182C) + x] = 0
        m[a(0x182F) + x] = 0
        m[a(0x1832) + x] = 0
        m[a(0x1835) + x] = 0
        m[a(0x1838) + x] = 0
        yn = m[a(0x100F) + x]  # vibrato step base = freqhi[note] << pw shift
        m[a(0x1802) + x] = self.m[(self.b_freqhi + yn) & 0xFFFF]
        for _ in range(m[a(0x101A)]):
            lo = (m[a(0x1802) + x] << 1) & 0xFF
            hi = ((m[a(0x1805) + x] << 1) | (m[a(0x1802) + x] >> 7)) & 0xFF
            m[a(0x1802) + x] = lo
            m[a(0x1805) + x] = hi
        self._output(x)

    def _wt_step(self, x: int, check_loop: bool = True) -> None:
        """Advance the waveform wavetable one step and set $180e/$1811/$1814.

        The steady output ($165b) honours the ``$90`` loop marker; the note-init
        first step ($137f) does NOT -- there ``$90`` is a literal waveform value --
        so ``check_loop=False`` reproduces that (`_note_init`)."""
        m = self.m
        a = self._a
        y = m[a(0x17F3) + x]
        if check_loop and self.m[(self.b_wtctrl + y) & 0xFFFF] == 0x90:  # loop marker
            m[a(0x17F3) + x] = self.m[(self.b_wtarg + y) & 0xFFFF]
            y = m[a(0x17F3) + x]
        ctrl = self.m[(self.b_wtctrl + y) & 0xFFFF]
        m[a(0x1814) + x] = ctrl
        if ctrl & 0x08:  # arg = absolute freq hi
            m[a(0x1811) + x] = self.m[(self.b_wtarg + y) & 0xFFFF]
            m[a(0x180E) + x] = 0
        else:  # arg + current note -> freq table
            yn = (self.m[(self.b_wtarg + y) & 0xFFFF] + m[a(0x100F) + x]) & 0xFF
            m[a(0x180E) + x] = self.m[(self.b_freqlo + yn) & 0xFFFF]
            m[a(0x1811) + x] = self.m[(self.b_freqhi + yn) & 0xFFFF]
        m[a(0x17F3) + x] = (m[a(0x17F3) + x] + 1) & 0xFF

    # -- steady tick ($1439) --------------------------------------------
    def _tick(self, x: int) -> None:
        m = self.m
        a = self._a
        y = m[a(0x17F6) + x]  # PW sweep
        if self.m[(self.b_pwa + y) & 0xFFFF] == 0x90:
            m[a(0x17F6) + x] = self.m[(self.b_pwb + y) & 0xFFFF]
            y = m[a(0x17F6) + x]
        m[a(0x101F)] = self.m[(self.b_pwa + y) & 0xFFFF]
        m[a(0x101E)] = self.m[(self.b_pwb + y) & 0xFFFF]
        y = (y + 1) & 0xFF
        lo = m[a(0x1820) + x] + m[a(0x101E)]
        m[a(0x1820) + x] = lo & 0xFF
        m[a(0x1823) + x] = (
            m[a(0x1823) + x] + m[a(0x101F)] + (1 if lo > 0xFF else 0)
        ) & 0xFF
        lo = m[a(0x1826) + x] + 1
        m[a(0x1826) + x] = lo & 0xFF
        m[a(0x1829) + x] = (m[a(0x1829) + x] + (1 if lo > 0xFF else 0)) & 0xFF
        if (
            m[a(0x1829) + x] == self.m[(self.b_pwa + y) & 0xFFFF]
            and m[a(0x1826) + x] == self.m[(self.b_pwb + y) & 0xFFFF]
        ):
            m[a(0x1826) + x] = 0
            m[a(0x1829) + x] = 0
            m[a(0x17F6) + x] = (m[a(0x17F6) + x] + 2) & 0xFF
        if x == 2:  # global filter cutoff sweep runs off voice 2
            self._filter_sweep()
        if m[a(0x17ED) + x] != 0:
            self._slide(x)
            return
        self._vibrato(x)

    def _filter_sweep(self) -> None:  # $149a
        m = self.m
        a = self._a
        y = m[a(0x17F9)]
        if self.m[(self.b_filta + y) & 0xFFFF] == 0x90:
            m[a(0x17F9)] = self.m[(self.b_filtb + y) & 0xFFFF]
            y = m[a(0x17F9)]
        m[a(0x101F)] = self.m[(self.b_filta + y) & 0xFFFF]
        m[a(0x101E)] = self.m[(self.b_filtb + y) & 0xFFFF]
        y = (y + 1) & 0xFF
        lo = m[a(0x1017)] + m[a(0x101E)]
        m[a(0x1017)] = lo & 0xFF
        m[a(0x1016)] = (m[a(0x1016)] + m[a(0x101F)] + (1 if lo > 0xFF else 0)) & 0xFF
        lo = m[a(0x183B)] + 1
        m[a(0x183B)] = lo & 0xFF
        m[a(0x183C)] = (m[a(0x183C)] + (1 if lo > 0xFF else 0)) & 0xFF
        if (
            m[a(0x183C)] == self.m[(self.b_filta + y) & 0xFFFF]
            and m[a(0x183B)] == self.m[(self.b_filtb + y) & 0xFFFF]
        ):
            m[a(0x183B)] = 0
            m[a(0x183C)] = 0
            m[a(0x17F9)] = (m[a(0x17F9)] + 2) & 0xFF

    # -- portamento ($14ff) ----------------------------------------------
    def _slide(self, x: int) -> None:
        up = self.m[self._a(0x100F) + x] < self.m[self._a(0x17F0) + x]
        m = self.m
        a = self._a
        if up:
            lo = m[a(0x1835) + x] + m[a(0x17ED) + x]
            m[a(0x1835) + x] = lo & 0xFF
            m[a(0x1838) + x] = (m[a(0x1838) + x] + (1 if lo > 0xFF else 0)) & 0xFF
        else:
            lo = m[a(0x1835) + x] - m[a(0x17ED) + x]
            m[a(0x1835) + x] = lo & 0xFF
            m[a(0x1838) + x] = (m[a(0x1838) + x] - (1 if lo < 0 else 0)) & 0xFF
        s = m[a(0x180E) + x] + m[a(0x1835) + x]
        m[a(0x183D)] = s & 0xFF
        chi = (m[a(0x1811) + x] + m[a(0x1838) + x] + (1 if s > 0xFF else 0)) & 0xFF
        m[a(0x183E)] = chi
        y = m[a(0x17F0) + x]
        tgt_hi = self.m[(self.b_freqhi + y) & 0xFFFF]
        if up:
            if chi != tgt_hi:
                self._vol_fade_out(x)
            else:
                self._slide_reach(x, y)
            return
        if chi < tgt_hi or (
            chi == tgt_hi and m[a(0x183D)] < self.m[(self.b_freqlo + y) & 0xFFFF]
        ):
            self._slide_reach(x, y)
        else:
            self._vol_fade_out(x)

    def _slide_reach(self, x: int, y: int) -> None:  # $1534: falls into vibrato
        m = self.m
        a = self._a
        m[a(0x100F) + x] = y
        m[a(0x180E) + x] = self.m[(self.b_freqlo + y) & 0xFFFF]
        m[a(0x1811) + x] = self.m[(self.b_freqhi + y) & 0xFFFF]
        m[a(0x1835) + x] = 0
        m[a(0x1838) + x] = 0
        m[a(0x17ED) + x] = 0
        self._vibrato(x)

    # -- triangle vibrato ($1592) ----------------------------------------
    def _vibrato(self, x: int) -> None:
        m = self.m
        a = self._a
        if m[a(0x17EA) + x] != 0:  # tie: no vibrato
            m[a(0x1835) + x] = 0
            m[a(0x1838) + x] = 0
            self._vol_fade_out(x)
            return
        if m[a(0x17FF) + x] == 0:  # no vibrato period
            self._vol_fade_out(x)
            return
        if m[a(0x17FC) + x] != 0:  # vibrato onset delay
            m[a(0x17FC) + x] = (m[a(0x17FC) + x] - 1) & 0xFF
            self._vol_fade_out(x)
            return
        if m[a(0x182F) + x] == 0:  # ascending half-cycle ($15b7)
            lo = m[a(0x1835) + x] + m[a(0x1802) + x]
            m[a(0x1835) + x] = lo & 0xFF
            m[a(0x1838) + x] = (
                m[a(0x1838) + x] + m[a(0x1805) + x] + (1 if lo > 0xFF else 0)
            ) & 0xFF
            m[a(0x1832) + x] = (m[a(0x1832) + x] + 1) & 0xFF
            if m[a(0x1832) + x] != m[a(0x17FF) + x]:
                self._vol_fade_out(x)
                return
            m[a(0x1832) + x] = 0
            m[a(0x182F) + x] = (m[a(0x182F) + x] + 1) & 0xFF
            if m[a(0x182C) + x] != 0:  # one-shot depth doubling
                self._vol_fade_out(x)
                return
            lo = (m[a(0x1802) + x] << 1) & 0xFF
            hi = ((m[a(0x1805) + x] << 1) | (m[a(0x1802) + x] >> 7)) & 0xFF
            m[a(0x1802) + x] = lo
            m[a(0x1805) + x] = hi
            m[a(0x182C) + x] = (m[a(0x182C) + x] + 1) & 0xFF
            self._vol_fade_out(x)
            return
        # descending half-cycle ($15ee)
        lo = m[a(0x1835) + x] - m[a(0x1802) + x]
        m[a(0x1835) + x] = lo & 0xFF
        m[a(0x1838) + x] = (
            m[a(0x1838) + x] - m[a(0x1805) + x] - (1 if lo < 0 else 0)
        ) & 0xFF
        m[a(0x1832) + x] = (m[a(0x1832) + x] + 1) & 0xFF
        if m[a(0x1832) + x] != m[a(0x17FF) + x]:
            self._vol_fade_out(x)
            return
        m[a(0x1832) + x] = 0
        m[a(0x182F) + x] = (m[a(0x182F) + x] - 1) & 0xFF
        self._vol_fade_out(x)

    # -- global volume fade ($1614) --------------------------------------
    def _vol_fade_out(self, x: int) -> None:
        m = self.m
        a = self._a
        if m[a(0x1019)] != 0:  # fade down
            lo = m[a(0x101C)] - m[a(0x1019)]
            m[a(0x101C)] = lo & 0xFF
            m[a(0x101B)] = (m[a(0x101B)] - (1 if lo < 0 else 0)) & 0xFF
            if m[a(0x101B)] == 0:
                m[a(0x1019)] = 0
        if m[a(0x1018)] != 0:  # fade up
            lo = m[a(0x101C)] + m[a(0x1018)]
            m[a(0x101C)] = lo & 0xFF
            m[a(0x101B)] = (m[a(0x101B)] + (1 if lo > 0xFF else 0)) & 0xFF
            if m[a(0x101B)] == 0x0F:
                m[a(0x1018)] = 0
        self.w(SID_BASE + 0x18, m[a(0x101B)] | m[a(0x1015)])
        self._wt_output(x)

    def _wt_output(self, x: int) -> None:  # $165b: wavetable step then output
        self._wt_step(x)
        self._output(x)

    # -- final output ($169b/$16e6) --------------------------------------
    def _output(self, x: int) -> None:
        m = self.m
        a = self._a
        yv = m[a(0x1009) + x]
        term = m[a(0x181D) + x]
        if term not in (0xFE, 0xF4, 0xFA, 0xF2, 0xF1):
            self._release(x, term)
        lo = m[a(0x180E) + x] + m[a(0x1835) + x]
        self.w(SID_BASE + 0x00 + yv, lo & 0xFF)
        self.w(
            SID_BASE + 0x01 + yv,
            (m[a(0x1811) + x] + m[a(0x1838) + x] + (1 if lo > 0xFF else 0)) & 0xFF,
        )
        self.w(SID_BASE + 0x02 + yv, m[a(0x1820) + x])
        self.w(SID_BASE + 0x03 + yv, m[a(0x1823) + x])
        self.w(SID_BASE + 0x04 + yv, m[a(0x1814) + x] & m[a(0x1817) + x])

    def _release(self, x: int, term: int) -> None:  # $16b9 chain
        m = self.m
        a = self._a
        yv = m[a(0x1009) + x]
        if term == 0xF5:
            if m[a(0x17EA) + x] != 0:
                self._release_dur(x, yv)
            return
        if m[a(0x17EA) + x] != 0:  # tie: no release
            return
        self._release_dur(x, yv)

    def _release_dur(self, x: int, yv: int) -> None:  # $16be
        m = self.m
        a = self._a
        if m[a(0x17DB) + x] == 1:  # last frame: clear SR (if not patched out)
            if self._rel_clears_sr:
                self.w(SID_BASE + 0x06 + yv, 0)
            return
        if m[a(0x17DB) + x] == 2 and m[a(0x1013)] == 0:  # gate-off mask
            m[a(0x1817) + x] = 0xF6


class Player95:
    """The compact, self-modifying ``$95`` DMC engine (an earlier lineage).

    Play routine at ``base+$95`` (the dispatch play-JMP target), with its own
    work-RAM map, transcribed from the 6502.  Notable departures from the
    ``base+$85``/``base+$a1`` engines:

    * a single GLOBAL tempo divider (``$1016``) selects, per frame, between a
      row-advance pass ($10e1) and a steady tick ($1373) for all three voices; the
      duration counter ``$17e5,X`` only decrements on row frames;
    * the SID voice stride is the preset table ``$100c,X`` = {0,7,14} (Y-indexed
      register writes), and per-voice state lives in a ``$17d9..$1857`` block
      (orderptr ``$17d9/$17dc``, order index ``$17df``, pattern index ``$17e2``,
      duration ``$17e5/$17e8``, transpose ``$17ee``, instrument ``$17eb``);
    * a note is retriggered by writing AD/SR + ``CTRL=$09`` (gate+test) for one
      frame (no freq/pw), then the note-init frame ($1373 via the ``$1815`` flag)
      writes the waveform and clears test;
    * orderlist markers ``$ff``(jump)/``$fe``(stop)/``$fd``/``$fc``(transpose) and
      a rich pattern-command set (``$fd`` duration, ``$fc`` instrument, ``$fb/$fa``
      portamento, ``$f9`` filter-res+volume, ``$f8`` cutoff base, ``$f7/$f6``
      volume-fade speeds, ``$f5`` tie, ``$f4`` gate, ``$f3`` sustain override,
      ``$f2/$f1`` direct AD/SR, ``$f0`` instant-vibrato, ``$ef`` fine detune);
    * per-voice wavetable (``$1a72`` ctrl / ``$1a8e`` arg), 16-bit PW sweep
      (``$1aaa/$1ac2``), triangle vibrato with a one-shot depth ramp and a
      portamento; a GLOBAL filter-cutoff sweep (voice 2, ``$1ada/$1adf``) whose
      accumulator ``$1019`` plus a per-tune base ``$1853`` composes ``$d416``, and
      a global volume fade (``$1854`` up / ``$1855`` down) composing ``$d418``;
    * the note-freq tables sit at a FIXED offset (``base+$719`` lo / ``base+$779``
      hi) ahead of the work RAM, so they are base-relative constants.

    Conforms to the :class:`Player` playback interface (``init_writes`` +
    ``play_frame``).
    """

    # pylint: disable=too-many-instance-attributes

    def __init__(self, song: Song, subtune: int = 0):
        self.song = song
        self.m = bytearray(song.mem)
        base = song.base
        self.base = base
        self.rel = base - 0x1000  # the player is authored at $1000
        self.sub = subtune & 0xFF
        self._writes: List[Tuple[int, int]] = []
        self.finished = False
        self.fa = 0  # pattern/orderlist pointer (zero-page $fa/$fb)
        self.fb = 0
        m = self.m

        def op(off: int) -> int:
            idx = base + off
            return (m[idx] | (m[idx + 1] << 8)) & 0xFFFF

        order = n95_order_table_base(m, base)
        self.b_order = order if order is not None else op(constants.N95_ORDER_OP)
        self.b_patlo = op(constants.N95_PATTERN_LO_OP)
        self.b_pathi = op(constants.N95_PATTERN_HI_OP)
        self.b_inst = op(constants.N95_INST_OP)
        self.b_wtctrl = op(constants.N95_WT_CTRL_OP)
        self.b_wtarg = op(constants.N95_WT_ARG_OP)
        self.b_pwa = op(constants.N95_PW_A_OP)
        self.b_pwb = op(constants.N95_PW_B_OP)
        self.b_filta = op(constants.N95_FILT_A_OP)
        self.b_filtb = op(constants.N95_FILT_B_OP)
        self.b_freqlo = (base + constants.N95_FREQ_LO_REL) & 0xFFFF
        self.b_freqhi = (base + constants.N95_FREQ_HI_REL) & 0xFFFF
        self.init()

    def _a(self, addr: int) -> int:
        return (addr + self.rel) & 0xFFFF

    def w(self, reg: int, val: int) -> None:
        """Emit a SID register write (absolute $D4xx)."""
        self._writes.append((reg & 0xFFFF, val & 0xFF))

    def _ld(self, y: int) -> int:
        """``($fa),Y`` pattern/orderlist byte read (fa/fb are absolute)."""
        return self.m[(((self.fb << 8) | self.fa) + (y & 0xFF)) & 0xFFFF]

    def _yv(self, x: int) -> int:
        """The preset SID voice stride ``$100c,X`` = {0,7,14}."""
        return self.m[self._a(0x100C) + x]

    # -- init ($1040) ----------------------------------------------------
    def init(self) -> None:
        """Run the ``$95`` init for the selected subtune."""
        m = self.m
        a = self._a
        self._writes = []
        y = (self.sub * 2) & 0xFF
        for x in range(3):
            m[a(0x17D9) + x] = m[(self.b_order + y) & 0xFFFF]
            m[a(0x17DC) + x] = m[(self.b_order + y + 1) & 0xFFFF]
            y = (y + 2) & 0xFF
        # subtune record byte 6 seeds the tempo reload ($10bf, the self-modified
        # ``LDA #imm`` operand); byte 7 seeds the global volume-hi accumulator.
        m[a(0x10BF)] = m[(self.b_order + y) & 0xFFFF]
        m[a(0x101A)] = m[(self.b_order + y + 1) & 0xFFFF]
        for i in range(0x79):  # clear the $17df..$1857 work block
            m[a(0x17DF) + i] = 0
        for x in range(3):
            m[a(0x17E5) + x] = 2
            m[a(0x1009) + x] = 2
        for i in range(0x18):
            self.w(SID_BASE + i, 0)
        self.w(SID_BASE + 0x04, 0x08)  # test bit on each voice's CTRL
        self.w(SID_BASE + 0x0B, 0x08)
        self.w(SID_BASE + 0x12, 0x08)

    @property
    def init_writes(self) -> List[Tuple[int, int]]:
        """The SID writes the init routine emitted (frame-0 baseline)."""
        return list(self._writes)

    # -- play ($1095) ----------------------------------------------------
    def play_frame(self) -> List[Tuple[int, int]]:
        """Run one player tick; return ``(reg, value)`` writes (abs $D4xx)."""
        self._writes = []
        m = self.m
        a = self._a
        m[a(0x1016)] = (m[a(0x1016)] - 1) & 0xFF  # global tempo divider
        if m[a(0x1016)] & 0x80:  # expired -> row-advance frame
            m[a(0x1016)] = m[a(0x10BF)]  # reload
            for x in range(3):
                self._advance(x)
        else:  # steady frame
            for x in range(3):
                self._tick_1373(x)
        # filter cutoff hi = global accumulator + per-tune base offset
        self.w(SID_BASE + 0x16, (m[a(0x1019)] + m[a(0x1853)]) & 0xFF)
        self.finished = all(m[a(0x1009) + x] == 0 for x in range(3))
        return list(self._writes)

    # -- row advance ($10e1) ---------------------------------------------
    def _advance(self, x: int) -> None:
        m = self.m
        a = self._a
        if m[a(0x1009) + x] == 0:  # inactive voice -> steady tick
            self._tick_1373(x)
            return
        m[a(0x17E5) + x] = (m[a(0x17E5) + x] - 1) & 0xFF
        if m[a(0x17E5) + x] != 0:  # duration not expired -> steady tick
            self._tick_1373(x)
            return
        self._orderwalk(x)

    # -- orderlist walk ($10ee) ------------------------------------------
    def _orderwalk(self, x: int) -> None:
        m = self.m
        a = self._a
        self.fa = m[a(0x17D9) + x]
        self.fb = m[a(0x17DC) + x]
        y = m[a(0x17DF) + x]
        v = self._ld(y)
        if v < 0x80:  # pattern number
            self._pattern_setup(x, v, y)
            return
        if v == 0xFF:  # jump: next byte is the new order index
            y = (y + 1) & 0xFF
            v = self._ld(y)
            m[a(0x17DF) + x] = v
            y = v
            v = self._ld(y)
        if v == 0xFD:  # set transpose (positive)
            y = (y + 1) & 0xFF
            m[a(0x17EE) + x] = self._ld(y)
            y = (y + 1) & 0xFF
            m[a(0x17DF) + x] = y
            v = self._ld(y)
        elif v == 0xFC:  # set transpose (negated)
            y = (y + 1) & 0xFF
            m[a(0x17EE) + x] = ((self._ld(y) ^ 0xFF) + 1) & 0xFF
            y = (y + 1) & 0xFF
            m[a(0x17DF) + x] = y
            v = self._ld(y)
        elif v == 0xFE:  # stop
            m[a(0x1009) + x] = 0
            self._wt_output(x)
            return
        self._pattern_setup(x, v, y)

    def _pattern_setup(self, x: int, num: int, y: int) -> None:  # $1145
        del y
        self.fa = self.m[(self.b_patlo + num) & 0xFFFF]
        self.fb = self.m[(self.b_pathi + num) & 0xFFFF]
        self._patwalk(x)

    # -- pattern walk ($1150) --------------------------------------------
    def _patwalk(self, x: int) -> None:
        m = self.m
        a = self._a
        for _ in range(0x400):
            y = m[a(0x17E2) + x]
            v = self._ld(y)
            if v < 0x80:  # note ($1314)
                self._note(x, v, y)
                return
            if v == 0xFD:  # set note-duration
                m[a(0x17E8) + x] = self._ld((y + 1) & 0xFF)
                m[a(0x17E2) + x] = (y + 2) & 0xFF
                continue
            if v == 0xFC:  # select instrument
                m[a(0x17EB) + x] = self._ld((y + 1) & 0xFF)
                m[a(0x17E2) + x] = (y + 2) & 0xFF
                continue
            if v == 0xF0:  # instant-vibrato setup ($1182)
                self._cmd_f0(x, y)
                continue
            if v == 0xFE:  # end of row
                self._row_end(x, y)
                return
            if v == 0xF4:  # gate-toggle + end of row
                m[a(0x1821) + x] ^= 0x01
                self._row_end(x, y)
                return
            if v == 0xF5:  # tie toggle
                m[a(0x17F4) + x] ^= 0xFF
                m[a(0x17E2) + x] = (y + 1) & 0xFF
                continue
            if v == 0xF3:  # sustain-nibble override
                m[a(0x17F1) + x] = self._ld((y + 1) & 0xFF)
                m[a(0x17E2) + x] = (y + 2) & 0xFF
                continue
            if v == 0xFB:  # portamento: speed, from-note, to-note
                m[a(0x17F7) + x] = self._ld((y + 1) & 0xFF)
                m[a(0x1012) + x] = (self._ld((y + 2) & 0xFF) + m[a(0x17EE) + x]) & 0xFF
                m[a(0x17FA) + x] = (self._ld((y + 3) & 0xFF) + m[a(0x17EE) + x]) & 0xFF
                m[a(0x17E2) + x] = (y + 3) & 0xFF
                self._note_onset(x, (y + 3) & 0xFF)
                return
            if v == 0xFA:  # portamento: speed, to-note (from current)
                m[a(0x17F7) + x] = self._ld((y + 1) & 0xFF)
                m[a(0x17FA) + x] = (self._ld((y + 2) & 0xFF) + m[a(0x17EE) + x]) & 0xFF
                m[a(0x17E2) + x] = (y + 2) & 0xFF
                m[a(0x183C) + x] = 0
                m[a(0x183F) + x] = 0
                self._row_end(x, (y + 2) & 0xFF)
                return
            if v == 0xF9:  # filter res ($d417) + volume-hi ($1018) + sweep gate
                b = self._ld((y + 1) & 0xFF)
                m[a(0x1857)] = b  # $1857 also gates the global filter sweep
                self.w(SID_BASE + 0x17, b if b == 0 else (((b << 4) & 0xFF) | 0x04))
                m[a(0x1018)] = b & 0xF0
                m[a(0x17E2) + x] = (y + 2) & 0xFF
                continue
            if v == 0xF8:  # filter cutoff-hi base
                m[a(0x1853)] = self._ld((y + 1) & 0xFF)
                m[a(0x17E2) + x] = (y + 2) & 0xFF
                continue
            if v == 0xF2:  # direct AD
                self.w(SID_BASE + 0x05 + self._yv(x), self._ld((y + 1) & 0xFF))
                m[a(0x17E2) + x] = (m[a(0x17E2) + x] + 2) & 0xFF
                continue
            if v == 0xF1:  # direct SR
                self.w(SID_BASE + 0x06 + self._yv(x), self._ld((y + 1) & 0xFF))
                m[a(0x17E2) + x] = (m[a(0x17E2) + x] + 2) & 0xFF
                continue
            if v == 0xF7:  # volume fade-up speed
                m[a(0x1854)] = self._ld((y + 1) & 0xFF)
                m[a(0x17E2) + x] = (y + 2) & 0xFF
                continue
            if v == 0xF6:  # volume fade-down speed
                m[a(0x1855)] = self._ld((y + 1) & 0xFF)
                m[a(0x17E2) + x] = (y + 2) & 0xFF
                continue
            if v == 0xEF:  # fine detune (added to freq lo)
                m[a(0x1842) + x] = self._ld((y + 1) & 0xFF)
                m[a(0x17E2) + x] = (y + 2) & 0xFF
                continue
            m[a(0x17E2) + x] = (y + 1) & 0xFF  # unknown: skip 1
        self._wt_output(x)

    def _cmd_f0(self, x: int, y: int) -> None:  # $1182
        m = self.m
        a = self._a
        v = self._ld((y + 1) & 0xFF)
        shift = v & 0x07
        yn = m[a(0x1012) + x]
        m[a(0x180C) + x] = self.m[(self.b_freqhi + yn) & 0xFFFF]
        if shift != 0:
            m[a(0x180F) + x] = 0
            m[a(0x1806) + x] = 0
            m[a(0x1833) + x] = 0
            m[a(0x1836) + x] = 0
            m[a(0x1839) + x] = 0
            for _ in range(shift):
                lo = (m[a(0x180C) + x] << 1) & 0xFF
                hi = ((m[a(0x180F) + x] << 1) | (m[a(0x180C) + x] >> 7)) & 0xFF
                m[a(0x180C) + x] = lo
                m[a(0x180F) + x] = hi
        m[a(0x1809) + x] = (v >> 4) & 0x0F
        m[a(0x17E2) + x] = (m[a(0x17E2) + x] + 2) & 0xFF

    def _row_end(self, x: int, y: int) -> None:  # $11d0
        m = self.m
        a = self._a
        m[a(0x17E5) + x] = m[a(0x17E8) + x]
        m[a(0x17E2) + x] = (y + 1) & 0xFF
        v = self._ld((y + 1) & 0xFF)
        m[a(0x1827) + x] = v
        if v == 0xFF:  # end of pattern -> advance order index
            m[a(0x17E2) + x] = 0
            m[a(0x17F1) + x] = 0
            m[a(0x17F4) + x] = 0
            m[a(0x17DF) + x] = (m[a(0x17DF) + x] + 1) & 0xFF
        self._wt_output(x)

    # -- note ($1314) ----------------------------------------------------
    def _note(self, x: int, v: int, y: int) -> None:
        m = self.m
        a = self._a
        m[a(0x1012) + x] = (v + m[a(0x17EE) + x]) & 0xFF
        if m[a(0x17F4) + x] != 0:  # tie: freq-only, no retrigger
            self._row_end(x, y)
            return
        self._note_onset(x, y)

    def _note_onset(self, x: int, y: int) -> None:  # $1323
        m = self.m
        a = self._a
        m[a(0x1827) + x] = self._ld((y + 1) & 0xFF)  # terminator peek
        idx = (m[a(0x17EB) + x] * 8) & 0xFF  # inst*8
        m[a(0x184B) + x] = idx
        ad = self.m[(self.b_inst + idx) & 0xFFFF]
        sr = self.m[(self.b_inst + idx + 1) & 0xFFFF]
        yv = self._yv(x)
        if m[a(0x17F1) + x] != 0:  # sustain override: SR hi nibble, AD forced 0
            sr = (sr & 0x0F) | ((m[a(0x17F1) + x] << 4) & 0xFF)
            self.w(SID_BASE + 0x06 + yv, sr)
            self.w(SID_BASE + 0x05 + yv, 0)
        else:
            self.w(SID_BASE + 0x06 + yv, sr)
            self.w(SID_BASE + 0x05 + yv, ad)
        self.w(SID_BASE + 0x04 + yv, 0x09)  # gate+test
        m[a(0x1815) + x] = 0x09  # note-init pending

    # -- steady tick / note-init dispatch ($1373) ------------------------
    def _tick_1373(self, x: int) -> None:
        if self.m[self._a(0x1815) + x] != 0:  # first frame after onset
            self._note_init(x)
        else:
            self._tick(x)

    def _note_init(self, x: int) -> None:  # $137b
        m = self.m
        a = self._a
        m[a(0x1815) + x] = 0
        m[a(0x183C) + x] = 0
        m[a(0x183F) + x] = 0
        m[a(0x17E5) + x] = m[a(0x17E8) + x]  # duration = reload
        m[a(0x17E2) + x] = (m[a(0x17E2) + x] + 1) & 0xFF
        idx = m[a(0x184B) + x]
        m[a(0x1809) + x] = self.m[(self.b_inst + idx + 6) & 0xFFFF] & 0x0F  # vib period
        if m[a(0x1809) + x] != 0:  # seed vibrato
            m[a(0x1806) + x] = self.m[(self.b_inst + idx + 5) & 0xFFFF]  # vib delay
            m[a(0x1812) + x] = (self.m[(self.b_inst + idx + 7) & 0xFFFF] & 0xF0) >> 3
            shift = self.m[(self.b_inst + idx + 7) & 0xFFFF] & 0x07
            yn = m[a(0x1012) + x]
            m[a(0x180C) + x] = self.m[(self.b_freqhi + yn) & 0xFFFF]
            m[a(0x180F) + x] = 0
            m[a(0x1833) + x] = 0
            m[a(0x1836) + x] = 0
            m[a(0x1839) + x] = 0
            for _ in range(shift):
                lo = (m[a(0x180C) + x] << 1) & 0xFF
                hi = ((m[a(0x180F) + x] << 1) | (m[a(0x180C) + x] >> 7)) & 0xFF
                m[a(0x180C) + x] = lo
                m[a(0x180F) + x] = hi
        m[a(0x1845) + x] = self.m[(self.b_inst + idx + 6) & 0xFFFF] >> 4  # wt speed
        m[a(0x1848) + x] = m[a(0x1845) + x]
        m[a(0x17FD) + x] = self.m[(self.b_inst + idx + 2) & 0xFFFF]  # wavetable start
        pw = self.m[(self.b_inst + idx + 3) & 0xFFFF]  # pw table start
        if pw != 0:
            m[a(0x1800) + x] = pw
            m[a(0x182D) + x] = self.m[(self.b_pwa + pw) & 0xFFFF]
            m[a(0x182A) + x] = self.m[(self.b_pwb + pw) & 0xFFFF]
            m[a(0x1830) + x] = 0
            m[a(0x1800) + x] = (m[a(0x1800) + x] + 1) & 0xFF
        if x == 2:  # global filter cutoff (voice 2)
            flt = self.m[(self.b_inst + idx + 4) & 0xFFFF]  # filter table start
            if flt != 0:
                m[a(0x1803)] = flt
                m[a(0x1019)] = self.m[(self.b_filta + flt) & 0xFFFF]
                m[a(0x184E)] = 0
                m[a(0x1803)] = (m[a(0x1803)] + 1) & 0xFF
        self._wt_step(x, check_loop=False)  # $142f: first wt read, no loop marker
        m[a(0x1821) + x] = 0xF7  # gate mask
        v = m[a(0x1827) + x]
        if v == 0xFF:
            m[a(0x17E2) + x] = 0
            m[a(0x17F1) + x] = 0
            m[a(0x17F4) + x] = 0
            m[a(0x17DF) + x] = (m[a(0x17DF) + x] + 1) & 0xFF
        self._wt_speed(x)  # $1696

    def _wt_step(self, x: int, check_loop: bool = True) -> None:
        """Advance/read the waveform wavetable, setting $181e/$1818/$181b.

        The steady step ($1654/$167d) honours the ``$90`` loop marker and, on the
        relative branch, adds the fine detune ``$1842`` PLUS the carry out of the
        ``arg + note`` sum (the 6502 keeps that carry live across the ``LDA``); the
        note-init first read ($142f/$144a) does neither and takes the freq bytes
        straight -- ``check_loop=False`` reproduces that.
        """
        m = self.m
        a = self._a
        y = m[a(0x17FD) + x]
        if check_loop and self.m[(self.b_wtctrl + y) & 0xFFFF] == 0x90:  # loop marker
            m[a(0x17FD) + x] = self.m[(self.b_wtarg + y) & 0xFFFF]
            y = m[a(0x17FD) + x]
        ctrl = self.m[(self.b_wtctrl + y) & 0xFFFF]
        m[a(0x181E) + x] = ctrl
        if ctrl & 0x08:  # arg = absolute freq hi
            m[a(0x181B) + x] = self.m[(self.b_wtarg + y) & 0xFFFF]
            m[a(0x1818) + x] = 0
            return
        s = self.m[(self.b_wtarg + y) & 0xFFFF] + m[a(0x1012) + x]  # arg + note
        yn = s & 0xFF
        if not check_loop:  # note-init: plain freq bytes, no detune/carry
            m[a(0x1818) + x] = self.m[(self.b_freqlo + yn) & 0xFFFF]
            m[a(0x181B) + x] = self.m[(self.b_freqhi + yn) & 0xFFFF]
            return
        lo = (
            self.m[(self.b_freqlo + yn) & 0xFFFF]
            + m[a(0x1842) + x]
            + (1 if s > 0xFF else 0)
        )
        m[a(0x1818) + x] = lo & 0xFF
        m[a(0x181B) + x] = (
            self.m[(self.b_freqhi + yn) & 0xFFFF] + (1 if lo > 0xFF else 0)
        ) & 0xFF

    # -- steady tick ($147b) ---------------------------------------------
    def _tick(self, x: int) -> None:
        m = self.m
        a = self._a
        if x == 2:  # global filter cutoff sweep (voice 2 only)
            self._filter_sweep()
        # PW sweep
        y = m[a(0x1800) + x]
        if self.m[(self.b_pwa + y) & 0xFFFF] == 0x90:  # loop marker
            m[a(0x1800) + x] = self.m[(self.b_pwb + y) & 0xFFFF]
            y = m[a(0x1800) + x]
        lo = m[a(0x182A) + x] + self.m[(self.b_pwb + y) & 0xFFFF]
        m[a(0x182A) + x] = lo & 0xFF
        m[a(0x182D) + x] = (
            m[a(0x182D) + x]
            + self.m[(self.b_pwa + y) & 0xFFFF]
            + (1 if lo > 0xFF else 0)
        ) & 0xFF
        y = (y + 1) & 0xFF
        m[a(0x1830) + x] = (m[a(0x1830) + x] + 1) & 0xFF
        if m[a(0x1830) + x] == self.m[(self.b_pwb + y) & 0xFFFF]:
            m[a(0x1830) + x] = 0
            m[a(0x1800) + x] = (y + 1) & 0xFF
        if m[a(0x17F7) + x] != 0:  # portamento
            self._slide(x)
            return
        self._vibrato(x)

    def _filter_sweep(self) -> None:  # $147f (voice 2)
        m = self.m
        a = self._a
        if m[a(0x1857)] == 0:  # no filter-res set -> skip
            return
        y = m[a(0x1803)]
        if self.m[(self.b_filta + y) & 0xFFFF] == 0x90:  # loop marker
            m[a(0x1803)] = self.m[(self.b_filtb + y) & 0xFFFF]
            y = m[a(0x1803)]
        m[a(0x1019)] = (m[a(0x1019)] + self.m[(self.b_filta + y) & 0xFFFF]) & 0xFF
        y = (y + 1) & 0xFF
        m[a(0x184E)] = (m[a(0x184E)] + 1) & 0xFF
        if m[a(0x184E)] == self.m[(self.b_filtb + y) & 0xFFFF]:
            m[a(0x184E)] = 0
            m[a(0x1803)] = (y + 1) & 0xFF

    # -- portamento ($14f6) ----------------------------------------------
    def _slide(self, x: int) -> None:
        m = self.m
        a = self._a
        up = m[a(0x1012) + x] < m[a(0x17FA) + x]  # note < target -> slide up
        # reached-target test (high byte only): compare freqhi+offset-hi (with the
        # carry out of freqlo+offset-lo) against the target note's freq-hi.
        lo = m[a(0x1818) + x] + m[a(0x183C) + x]
        chi = (m[a(0x181B) + x] + m[a(0x183F) + x] + (1 if lo > 0xFF else 0)) & 0xFF
        y = m[a(0x17FA) + x]
        if chi == self.m[(self.b_freqhi + y) & 0xFFFF]:
            self._slide_reach(x, y)
            return
        if up:
            s = m[a(0x183C) + x] + m[a(0x17F7) + x]
            m[a(0x183C) + x] = s & 0xFF
            m[a(0x183F) + x] = (m[a(0x183F) + x] + (1 if s > 0xFF else 0)) & 0xFF
        else:
            s = m[a(0x183C) + x] - m[a(0x17F7) + x]
            m[a(0x183C) + x] = s & 0xFF
            m[a(0x183F) + x] = (m[a(0x183F) + x] - (1 if s < 0 else 0)) & 0xFF
        self._wt_output(x)

    def _slide_reach(self, x: int, y: int) -> None:  # $1558
        m = self.m
        a = self._a
        m[a(0x1012) + x] = y
        m[a(0x183C) + x] = 0
        m[a(0x183F) + x] = 0
        m[a(0x17F7) + x] = 0
        self._wt_output(x)

    # -- triangle vibrato ($156c) ----------------------------------------
    def _vibrato(self, x: int) -> None:
        m = self.m
        a = self._a
        if m[a(0x17F4) + x] != 0:  # tie: no vibrato
            m[a(0x183C) + x] = 0
            m[a(0x183F) + x] = 0
            self._wt_output(x)
            return
        if m[a(0x1809) + x] == 0:  # no vibrato period
            self._vol_fade(x)
            return
        if m[a(0x1806) + x] != 0:  # vibrato onset delay
            m[a(0x1806) + x] = (m[a(0x1806) + x] - 1) & 0xFF
            self._vol_fade(x)
            return
        if m[a(0x1836) + x] == 0:  # ascending half-cycle ($1594)
            lo = m[a(0x183C) + x] + m[a(0x180C) + x]
            m[a(0x183C) + x] = lo & 0xFF
            m[a(0x183F) + x] = (
                m[a(0x183F) + x] + m[a(0x180F) + x] + (1 if lo > 0xFF else 0)
            ) & 0xFF
            m[a(0x1839) + x] = (m[a(0x1839) + x] + 1) & 0xFF
            if m[a(0x1839) + x] != m[a(0x1809) + x]:
                self._vol_fade(x)
                return
            m[a(0x1836) + x] = (m[a(0x1836) + x] + 1) & 0xFF
            if m[a(0x1812) + x] != 0:  # depth ramp: grow the step
                lo = m[a(0x180C) + x] + m[a(0x1812) + x]
                m[a(0x180C) + x] = lo & 0xFF
                m[a(0x180F) + x] = (m[a(0x180F) + x] + (1 if lo > 0xFF else 0)) & 0xFF
                self._wt_output(x)
                return
            if m[a(0x1833) + x] == 0:  # one-shot step doubling
                lo = (m[a(0x180C) + x] << 1) & 0xFF
                hi = ((m[a(0x180F) + x] << 1) | (m[a(0x180C) + x] >> 7)) & 0xFF
                m[a(0x180C) + x] = lo
                m[a(0x180F) + x] = hi
                m[a(0x1833) + x] = (m[a(0x1833) + x] + 1) & 0xFF
            self._wt_output(x)
            return
        # descending half-cycle ($15dd)
        lo = m[a(0x183C) + x] - m[a(0x180C) + x]
        m[a(0x183C) + x] = lo & 0xFF
        m[a(0x183F) + x] = (
            m[a(0x183F) + x] - m[a(0x180F) + x] - (1 if lo < 0 else 0)
        ) & 0xFF
        m[a(0x1839) + x] = (m[a(0x1839) + x] - 1) & 0xFF
        if m[a(0x1839) + x] != 0:
            self._vol_fade(x)
            return
        m[a(0x1836) + x] = (m[a(0x1836) + x] - 1) & 0xFF
        if m[a(0x1812) + x] != 0:  # depth ramp: grow the step
            lo = m[a(0x180C) + x] + m[a(0x1812) + x]
            m[a(0x180C) + x] = lo & 0xFF
            m[a(0x180F) + x] = (m[a(0x180F) + x] + (1 if lo > 0xFF else 0)) & 0xFF
            self._wt_output(x)
            return
        self._vol_fade(x)

    # -- global volume fade ($1612) --------------------------------------
    def _vol_fade(self, x: int) -> None:
        m = self.m
        a = self._a
        if m[a(0x1855)] != 0:  # fade down
            lo = m[a(0x101B)] - m[a(0x1855)]
            m[a(0x101B)] = lo & 0xFF
            m[a(0x101A)] = (m[a(0x101A)] - (1 if lo < 0 else 0)) & 0xFF
            if m[a(0x101A)] == 0:
                m[a(0x1855)] = 0
        if m[a(0x1854)] != 0:  # fade up
            lo = m[a(0x101B)] + m[a(0x1854)]
            m[a(0x101B)] = lo & 0xFF
            m[a(0x101A)] = (m[a(0x101A)] + (1 if lo > 0xFF else 0)) & 0xFF
            if m[a(0x101A)] == 0x0F:
                m[a(0x1854)] = 0
        self.w(SID_BASE + 0x18, m[a(0x101A)] | m[a(0x1018)])
        self._wt_output(x)

    def _wt_output(self, x: int) -> None:  # $1654: wt step then output
        self._wt_step(x)
        self._wt_speed(x)

    def _wt_speed(self, x: int) -> None:  # $1696: wavetable speed counter
        m = self.m
        a = self._a
        if m[a(0x1848) + x] != 0:
            m[a(0x1848) + x] = (m[a(0x1848) + x] - 1) & 0xFF
        else:
            m[a(0x17FD) + x] = (m[a(0x17FD) + x] + 1) & 0xFF  # advance wt pointer
            m[a(0x1848) + x] = m[a(0x1845) + x]
        self._output(x)

    # -- final output + release ($16aa) ----------------------------------
    def _output(self, x: int) -> None:
        m = self.m
        a = self._a
        yv = self._yv(x)
        self._release(x, m[a(0x1827) + x])
        lo = m[a(0x1818) + x] + m[a(0x183C) + x]
        self.w(SID_BASE + 0x00 + yv, lo & 0xFF)
        self.w(
            SID_BASE + 0x01 + yv,
            (m[a(0x181B) + x] + m[a(0x183F) + x] + (1 if lo > 0xFF else 0)) & 0xFF,
        )
        self.w(SID_BASE + 0x02 + yv, m[a(0x182A) + x])
        self.w(SID_BASE + 0x03 + yv, m[a(0x182D) + x])
        self.w(SID_BASE + 0x04 + yv, m[a(0x181E) + x] & m[a(0x1821) + x])

    def _release(self, x: int, term: int) -> None:  # $16aa chain
        m = self.m
        a = self._a
        yv = self._yv(x)
        if term in (0xFE, 0xFA, 0xF4):  # these terminators skip release
            return
        if term == 0xF5:  # tie-toggle terminator: release only WHILE tied
            if m[a(0x17F4) + x] == 0:
                return
        elif 0x80 <= term < 0xF3:  # other high terminators skip release
            return
        else:  # a normal note (< $80) or a $f3+ terminator: honour the tie flag
            if m[a(0x17F4) + x] != 0:
                return
        if m[a(0x17E5) + x] == 1:  # last frame of the note: clear SR
            self.w(SID_BASE + 0x06 + yv, 0)
            return
        if m[a(0x17E5) + x] == 2 and m[a(0x1016)] == 0:  # gate-off mask on penult.
            m[a(0x1821) + x] = 0xF6


class PlayerNN(PlayerV1D):
    """The ``$94a`` DMC family: the init-``$1d`` (``$85``) body behind a 2-level dispatch.

    The ``$94a`` generation (the sidid ``$947/$94a/$937`` cluster, ~224 HVSC tunes)
    interposes a SECOND JMP table between the PSID dispatch and the resident play
    body -- dispatch play ``-> base+$94a -> JMP real_play`` -- and authors the
    standard init-``$1d`` engine at a VIRTUAL base (``base+1``..``base+13``, shifted
    by the family's longer id/dispatch stub).  :func:`pydmcsid.reader.find_dmc_base`
    follows that second JMP and returns the derived engine base, so ``song.base`` is
    already the virtual base and :class:`PlayerV1D`'s cell derivation + playback
    reproduce the body unchanged -- this subclass only names the family for routing
    and the ``$94a`` byte-exact gate (:func:`pydmcsid.reader._nn_byte_exact`).

    Two sub-variants are recognised as this family but deferred (NOT byte-exact, so
    routed here purely so recognition is uniform): the appended multispeed /
    second-engine wrapper builds, whose header init/play drive the reorganised
    ``base+$937`` steady body instead of the ``$85`` body; and the ``$85``
    sub-variant whose note onset writes CTRL inline (``STA``) rather than the
    modelled ``JMP``/``BIT`` form.  Both are gated out of the byte-exact claim.
    """


class Player937(PlayerNN):
    """The ``$937`` CIA-multispeed appended-wrapper sub-family of the ``$94a`` line.

    The PSID header play/init resolve into an appended ``$2xxx`` wrapper, not the
    resident dispatch: a divide-by-N multispeed divider (``DEC counter`` per play
    call) that runs the resident MAIN play (the standard init-``$1d`` body, once
    every N calls) or, on the intermediate calls, a reorganised SECONDARY body.
    The oracle samples one wrapper call per grid row (no CIA emulation), so
    :meth:`play_frame` reproduces exactly one wrapper call.

    Both the main and the refresh path drive the same modelled body, so playback
    inherits :class:`PlayerV1D` wholesale; only the per-frame loop is new.  The
    refresh path (:data:`constants.NN937_BODY_REL`) runs, for each voice whose
    per-phase mask is set, the standard NON-ROW per-voice tick (:meth:`_jmp_11f9`),
    WITHOUT the full play's tempo divider, filter-flag reset or filter-register
    tail -- so filter sweeps and the tempo advance only step on the main call.
    """

    def __init__(self, song: Song, subtune: int = 0):
        super().__init__(song, subtune=subtune)
        m = self.m
        # The wrapper (header play/init) is appended past the resident player; its
        # divider counter cell + reload/seed immediates float with the build, so
        # they are read from the wrapper code rather than assumed.
        self._ctr = (m[song.play + 1] | (m[song.play + 2] << 8)) & 0xFFFF
        self._reload = self._wrap_imm(song.play)  # counter reload (divider period)
        self._ms = self._wrap_imm(song.init)  # counter seed (from the wrapper init)

    def _wrap_imm(self, lo: int) -> int:
        """The ``LDX #imm`` seed/reload immediate feeding ``STX counter`` at ``lo``.

        Scans the short wrapper for the ``A2 imm : 8E <counter>`` pair; falls back
        to 1 (the observed seed) if absent, so a malformed wrapper never raises.
        """
        m = self.m
        hi = min(len(m), lo + constants.NN937_WRAP_SCAN)
        for i in range(lo, hi - 4):
            if (
                m[i] == 0xA2
                and m[i + 2] == 0x8E
                and ((m[i + 3] | (m[i + 4] << 8)) & 0xFFFF) == self._ctr
            ):
                return m[i + 1]
        return 1

    def play_frame(self) -> List[Tuple[int, int]]:
        """Run one wrapper call: the resident main play, or the refresh body."""
        self._ms = (self._ms - 1) & 0xFF
        if self._ms != 0:
            return self._refresh_937()
        self._ms = self._reload
        return super().play_frame()

    def _refresh_937(self) -> List[Tuple[int, int]]:
        m = self.m
        a = self._a
        if m[a(constants.NN937_FLAG)] == 0:  # disabled -> the full play runs anyway
            return super().play_frame()
        self._writes = []
        phase = m[a(constants.NN937_PHASE)]
        for x, mask in enumerate(constants.NN937_MASK):
            if m[(a(mask) + phase) & 0xFFFF] != 0:
                self._jmp_11f9(x)
        phase += 1
        m[a(constants.NN937_PHASE)] = 0 if phase == constants.NN937_MOD else phase
        return list(self._writes)


def _player_for(song: Song, subtune: int):
    """Instantiate the play body matching ``song``'s DMC generation."""
    variant = song.variant()
    if variant == "a1":
        return PlayerA1(song, subtune=subtune)
    if variant == "n95":
        return Player95(song, subtune=subtune)
    if variant == "nn":
        if _nn_wrapper_937(song.mem, song.base, song.play, song.init):
            return Player937(song, subtune=subtune)
        return PlayerNN(song, subtune=subtune)
    if variant == "v1d":
        return PlayerV1D(song, subtune=subtune)
    return Player(song, subtune=subtune)


def iter_frames(
    song: Song, max_frames: int = 50 * 60, subtune: int = 0
) -> Iterator[List[Tuple[int, int]]]:
    """Yield each VBI play call's ``(reg, val)`` SID writes (reg = $D4xx 0..24).

    The first yielded burst is the INIT burst (the player's setup writes -- it
    forms the per-frame grid's frame-0 baseline, matching the cycle-exact
    emulator: init runs once, then the steady per-VBI play loop); each subsequent
    burst is one play call.  The play body is selected by the tune's DMC
    generation (init-``$37`` vs init-``$1d``).
    """
    player = _player_for(song, subtune)
    yield [(reg - SID_BASE, val) for reg, val in player.init_writes]
    for _ in range(max_frames):
        yield [(reg - SID_BASE, val) for reg, val in player.play_frame()]
