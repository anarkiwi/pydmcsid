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

from pydmcsid import constants
from pydmcsid.reader import Song

SID_BASE = 0xD400


class Player:
    """The DMC per-frame integer player over a loaded :class:`Song`."""

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
            idx = song.base + code_off
            stored = self.m[idx] | (self.m[idx + 1] << 8)
            return (stored + self.rel) & 0xFFFF

        self.b_freqlo = operand(constants.FREQ_LO_OP)
        self.b_freqhi = operand(constants.FREQ_HI_OP)
        self.b_instr = operand(constants.INSTR_OP)
        self.b_pat_lo = operand(constants.PATTERN_LO_OP)
        self.b_pat_hi = operand(constants.PATTERN_HI_OP)
        self.b_order_tbl = operand(constants.ORDER_TABLE_OP)
        self.b_pwtab = operand(constants.PW_TABLE_OP)
        self.b_arp_ctrl = operand(constants.ARP_CTRL_OP)
        self.b_arp_note = operand(constants.ARP_NOTE_OP)
        self.b_filt_ctrl = operand(constants.FILT_CTRL_OP)
        self.b_filt_step_lo = operand(constants.FILT_STEP_LO_OP)
        self.b_filt_step_hi = operand(constants.FILT_STEP_HI_OP)
        self.init()

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
        self.w(0xD418, m[ot + y + 1])
        for x in range(0x86):
            m[self._a(0x1718) + x] = 0
        for x in range(3):
            m[self._a(0x100C) + x] = 1
            m[self._a(0x173B) + x] = 1
        for x in range(0x18):
            self.w(0xD400 + x, 0)

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
        self.w(0xD416, m[self._a(0x171C)])
        self.w(0xD417, m[self._a(0x1034)] | m[self._a(0x1723)])
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
                m[self._a(0x1015) + x] = a & 0x1F
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
        m[self._a(0x1744) + x] = m[self._a(0x1012) + x]
        m[a1729] = (m[a1729] + 1) & 0xFF
        m[a173b] = m[a173e]
        m[a1729] = (m[a1729] + 1) & 0xFF
        self._endcheck(x, f8, f9)
        self._output_1591(x)
        return "done"

    # -- note index / freq-table read ($11A6) ----------------------------
    def _note_idx(self, x: int, a: int) -> None:
        m = self.m
        m[self._a(0x1012) + x] = a
        y = a
        m[self._a(0x172F) + x] = m[self.b_freqlo + y]
        m[self._a(0x1732) + x] = m[self.b_freqhi + y]
        for off in (0x35, 0x38, 0x68, 0x6B, 0x6E, 0x98, 0x9B):
            m[self._a(0x1700 + off) + x] = 0
        m[self._a(0x1729) + x] = (m[self._a(0x1729) + x] + 1) & 0xFF
        m[self._a(0x173B) + x] = m[self._a(0x173E) + x]
        yv = m[self._a(0x170D) + x]
        self.w(0xD404 + yv, 0x08)
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
        ins = m[self._a(0x1015) + x]
        idx = (ins * 11) & 0xFF
        m[self._a(0x174D) + x] = idx
        y = idx
        pwlo = m[self.b_instr + y]
        pwhi = m[self.b_instr + 1 + y]
        yv = m[self._a(0x170D) + x]
        self.w(0xD406 + yv, pwhi)
        self.w(0xD405 + yv, pwlo)
        y = m[self._a(0x174D) + x]
        flags = m[self.b_instr + 0x0A + y]
        if (flags & 0x04) == 0:
            av = m[self.b_instr + 0x02 + y]
            m[self._a(0x1753) + x] = av & 0x0F
            hi = (av >> 4) & 0x0F
            m[self._a(0x1756) + x] = hi
            m[self._a(0x1759) + x] = hi ^ 0x0F
            m[self._a(0x175F) + x] = (m[self.b_instr + 0x06 + y] >> 4) & 0x0F
            m[self._a(0x1762) + x] = 0
            m[self._a(0x1765) + x] = 0
        flags = m[self.b_instr + 0x0A + y]
        if (flags & 0x20) != 0:
            m[self._a(0x1034)] = m[self._a(0x1034)] | m[self._a(0x1710) + x]
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
                self.w(0xD418, a2)
                m[self._a(0x171C)] = m[self.b_filt_ctrl + 1 + yf]
                m[self._a(0x171D)] = m[self.b_filt_ctrl + 2 + yf]
                m[self._a(0x171E)] = m[self.b_filt_ctrl + 3 + yf]
        else:
            m[self._a(0x1034)] = m[self._a(0x1034)] & m[self._a(0x1713) + x]
        y = m[self._a(0x174D) + x]
        av = m[self.b_instr + 0x07 + y]
        m[self._a(0x1771) + x] = (av & 0xF0) >> 1
        m[self._a(0x1774) + x] = av & 0x0F
        m[self._a(0x1777) + x] = m[self.b_instr + 0x08 + y]
        m[self._a(0x177A) + x] = m[self.b_instr + 0x09 + y]
        m[self._a(0x177D) + x] = m[self.b_instr + 0x0A + y]
        yn = m[self._a(0x1012) + x]
        m[self._a(0x178C) + x] = m[self.b_freqhi + yn] >> 1
        m[self._a(0x1786) + x] = 2
        self._output_1591(x)

    # -- sustain tick ($1300) --------------------------------------------
    def _sustain_1300(self, x: int) -> None:
        m = self.m
        a1786 = self._a(0x1786) + x
        if (m[self._a(0x177D) + x] & 0x80) != 0 and m[a1786] == 2:
            yv = m[self._a(0x170D) + x]
            self.w(0xD400 + yv, 0xFF)
            self.w(0xD401 + yv, 0xFF)
            self.w(0xD404 + yv, 0x81)
            m[a1786] = (m[a1786] - 1) & 0xFF
            return
        if m[a1786] != 0:
            m[a1786] = (m[a1786] - 1) & 0xFF
            self._tick_134e(x)
            return
        if (m[self._a(0x177D) + x] & 0x10) != 0:
            if m[self._a(0x173B) + x] == 1:
                m[self._a(0x100F) + x] = 0xFE
            self._tick_134e(x)
            return
        if (m[self._a(0x177D) + x] & 0x08) == 0:
            m[self._a(0x100F) + x] = 0xFE
        self._tick_134e(x)

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
        m[self._a(0x1012) + x] = y
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
            m[self._a(0x1035)] = (m[self._a(0x1035)] + 1) & 0x01
            if m[self._a(0x1035)] == 0:
                self._output_1591(x)
                return
            yv = m[self._a(0x170D) + x]
            lo = m[self._a(0x172F) + x] + m[self._a(0x1735) + x]
            m[self._a(0x1724)] = lo & 0xFF
            hi = m[self._a(0x1732) + x] + (1 if lo > 0xFF else 0)
            m[self._a(0x1725)] = hi & 0xFF
            d = m[self._a(0x1724)] - m[self._a(0x1798) + x]
            self.w(0xD400 + yv, d & 0xFF)
            d2 = m[self._a(0x1725)] - m[self._a(0x179B) + x] - (1 if d < 0 else 0)
            self.w(0xD401 + yv, d2 & 0xFF)
            if (m[self._a(0x1777) + x] & 0x80) == 0:
                lo = m[self._a(0x1798) + x] + m[self._a(0x1777) + x]
                m[self._a(0x1798) + x] = lo & 0xFF
                hi = m[self._a(0x179B) + x] + (1 if lo > 0xFF else 0)
                m[self._a(0x179B) + x] = hi & 0xFF
                self._out_1619(x)
                return
            m[self._a(0x1036)] = m[self._a(0x1777) + x] & 0x7F
            lo = m[self._a(0x1798) + x] - m[self._a(0x1036)]
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
        bb = (m[self.b_arp_note + y] + m[self._a(0x1012) + x]) & 0xFF
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
        self.w(0xD400 + yv, lo & 0xFF)
        hi = m[self._a(0x1732) + x] + m[self._a(0x1738) + x] + (1 if lo > 0xFF else 0)
        self.w(0xD401 + yv, hi & 0xFF)
        self._out_1619(x)

    def _out_1619(self, x: int) -> None:
        m = self.m
        yv = m[self._a(0x170D) + x]
        self.w(0xD402 + yv, m[self._a(0x1750) + x])
        self.w(0xD403 + yv, m[self._a(0x1753) + x])
        self.w(0xD404 + yv, m[self._a(0x1780) + x] & m[self._a(0x100F) + x])


def iter_frames(
    song: Song, max_frames: int = 50 * 60, subtune: int = 0
) -> Iterator[List[Tuple[int, int]]]:
    """Yield each VBI play call's ``(reg, val)`` SID writes (reg = $D4xx 0..24).

    The first yielded burst is the INIT burst (the player's setup writes -- it
    forms the per-frame grid's frame-0 baseline, matching the cycle-exact
    emulator: init runs once, then the steady per-VBI play loop); each subsequent
    burst is one play call.
    """
    player = Player(song, subtune=subtune)
    yield [(reg - SID_BASE, val) for reg, val in player.init_writes]
    for _ in range(max_frames):
        yield [(reg - SID_BASE, val) for reg, val in player.play_frame()]
