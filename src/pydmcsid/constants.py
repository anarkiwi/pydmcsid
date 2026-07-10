"""Player-constant values for the DMC (Demo Music Creator) player.

These are the FIXED code OFFSETS-from-load of each per-tune table base operand
in the DMC player binary (the 16-bit operand of the ``LDA <table>,Y`` that reads
the table).  The player binary is identical across tunes, so the offsets are
constant; the VALUE stored at each offset is the per-tune table base (a tune with
a differently sized song relocates its tables, so the bases are read from the code
operands, not hardcoded).  Transcribed from the DMC disassembly
(``disasm.asm`` $1000..$1830 / decompile.c).
"""

# Hardware constants live in pysidtracker; re-export them for back-compat so
# existing ``pydmcsid.constants.<NAME>`` callers keep working.
from pysidtracker.registers import (  # pylint: disable=unused-import
    NTSC_CLOCK_HZ,
    NTSC_CYCLES_PER_FRAME,
    PAL_CLOCK_HZ,
    PAL_CYCLES_PER_FRAME,
    PW_HI_REGS,
    SID_BASE,
    SID_REG_COUNT,
    SID_VOICE_OFFSET,
)

# Back-compat alias: the DMC reader/tests call the 25-register file ``SID_REGISTERS``.
SID_REGISTERS = SID_REG_COUNT

# DMC JMP-table signature at load ($1000): JMP init / play / stop / FUN_163E.
# The canonical exact 12 bytes for the $1000 / init-$1037 build.  Recognition is
# NOT done against these literal bytes: the player is relocatable (the JMP targets
# are absolute, so they track the load/base address) and the init entry varies
# across DMC sub-versions.  See ``DMC_JMP_*_REL`` for the load-independent anchor.
DMC_SIGNATURE = bytes.fromhex("4c37104c85104c2f164c3e16")

# The DMC resident player opens with a 4-entry JMP table (JMP init / play / stop /
# FUN).  The play/stop/FUN targets sit at these FIXED offsets from the table base
# regardless of load address -- they identify the exact player body this reader
# models.  The init target is NOT part of the anchor: it varies across DMC
# sub-versions (commonly $101d or $1037) without changing the body layout.
DMC_JMP_PLAY_REL = 0x85
DMC_JMP_STOP_REL = 0x62F
DMC_JMP_FUN_REL = 0x63E

# The JMP table is normally AT the load address, but some tunes carry a short
# relocator stub (commonly 7 bytes) ahead of the resident player.  Scan this many
# bytes from the load address for the table base.
DMC_TABLE_SCAN = 24

# Play-body anchor.  The play routine opens with ``DEC tempo_ctr`` (``CE lo hi``)
# where the tempo counter is the fixed work cell ``base+$718`` (the authored
# ``$1718``).  This two-fact check (opcode + operand==base+$718) is load-
# invariant and identifies the exact play body pydmcsid transcribes, so it
# recognises the same body whether the opening JMP table has 4 entries
# (init/play/stop/FUN) or only 2 (init/play) -- the 2-entry builds carry the
# identical body but a shorter dispatch table, which the old 4-JMP anchor
# rejected.
DMC_PLAY_BODY_REL = 0x85  # play routine offset from base
DMC_DEC_OPCODE = 0xCE  # DEC abs -- first opcode of the play body
DMC_TEMPO_WORK_REL = 0x718  # tempo counter work cell, relative to base

# Body-generation marker: the first pattern-command compare in the pattern walk,
# ``CMP #$xx`` at ``base+$126``.  The body pydmcsid plays byte-exact (the
# init-``$37`` generation) uses the pattern-end/tie/loop markers $FE/$FD/$FF, so
# this byte is $FE.  A later generation (init-``$1d``) re-encodes them as
# $7E/$7D/$7F (and adds restructured note-setup subroutines): same play-body
# anchor and data-table layout, but a DIFFERENT body pydmcsid does not yet
# reproduce byte-exact.  Recognition accepts both (both are DMC); this marker
# tells callers which generation, i.e. whether playback is byte-exact.
DMC_MARKER_OP_REL = 0x126  # CMP #$fe operand offset from base
DMC_MARKER_V37 = 0xFE  # byte-exact generation (init-$37 markers $fe/$fd/$ff)
DMC_MARKER_V1D = 0x7E  # later generation (init-$1d markers $7e/$7d/$7f)

# code OFFSET-from-load of each table-base operand (the LDA abs,Y operand byte).
FREQ_LO_OP = 0x1AB  # LDA $1647,Y @ $11AA  (note->freq lo)
FREQ_HI_OP = 0x1B1  # LDA $16A7,Y @ $11B0  (note->freq hi)
INSTR_OP = 0x227  # LDA $17B0,Y @ $1226  (stride-11 instrument records)
PATTERN_LO_OP = 0x103  # LDA $1829,Y @ $1102  (pattern ptr lo)
PATTERN_HI_OP = 0x108  # LDA $182D,Y @ $1107  (pattern ptr hi)
ORDER_TABLE_OP = 0x3E  # LDA $17F0,Y @ $103D  (per-voice orderlist base table)
ORDER_TABLE_OP_V1D = 0x51  # LDA <ordertable>,Y @ $1050 (init-$1d relocated the read)
VIB_SEED_OP = 0x2F2  # LDA <vibscale>,Y @ $12F1 (init-$1d vib-delta seed, note-indexed)

# init-$1d relocates a 9-cell work block (per-voice note[3]/inst[3] + the three
# filter/vibrato globals) as a unit.  Its base varies across $1d sub-layouts
# (the id-string length shifts it), so it is read from the note-store operand at
# $11A6; the block is contiguous (inst=note+3, filt=note+6, toggle=+7, bend=+8).
NOTE_CELL_OP = 0x1A7  # STA <note>,X @ $11A6
INST_CELL_OP = 0x11A  # STA <inst>,X @ $1119 (== note+3 in the modelled layout)
FILT_CELL_OP = 0xA7  # LDA <filt> @ $10A6 (== note+6 in the modelled layout)

# init-$1d rest/tie/legato tail: the JMP at $1180 (target of the shared $117D
# handler) sends a rest/tie/legato row to either the full steady tick ($1322) or
# a plain waveform re-output ($1591).  Both encodings occur across $1d tunes; the
# target rel-to-base is read from the JMP operand.
REST_TAIL_OP = 0x181  # JMP <tail> @ $1180
REST_TAIL_1322 = 0x322  # steady tick (PW/filter/glide/vibrato + output)
REST_TAIL_1591 = 0x591  # waveform re-output only

# init-$1d hard-restart burst FREQ immediate: ``LDA #imm`` at $130A ($ff for
# nearly all tunes, but a per-tune code constant a few builds hand-edited).
BURST_IMM_REL = 0x30B

# Byte-exactness gates for the init-$1d body: a few hand-customized $1d builds
# share the marker+layout but wrap the play entry (a relocator/extra-code stub)
# or relocate the AD/SR write out of the modelled $184B helper.  These are
# recognised as the $1d generation but NOT reproduced byte-exact, so they are
# gated out of the byte-exact claim.
STD_PLAY_REL = 0x03  # the standard DMC play entry ($1003 = base+3, unwrapped)
INST_ADSR_SUB_OP = 0x231  # JSR <adsr-helper> operand @ $1230
INST_ADSR_SUB_REL = 0x84B  # the modelled AD/SR write helper ($184B)
PW_TABLE_OP = 0x358  # LDA $17B3,Y @ $1357  (PW-sweep nibble table)
ARP_CTRL_OP = 0x59C  # LDA $17C6,Y @ $159B  (wavetable ctrl/arp)
ARP_NOTE_OP = 0x5B9  # LDA $17CA,Y @ $15B8  (wavetable note)
FILT_CTRL_OP = 0x296  # LDA $17CE,Y @ $1295  (filter presets)
FILT_STEP_LO_OP = 0x3E7  # LDA $17D2,Y @ $13E6  (filter sweep step lo)
FILT_STEP_HI_OP = 0x3ED  # LDA $17D8,Y @ $13EC  (filter sweep step hi)
