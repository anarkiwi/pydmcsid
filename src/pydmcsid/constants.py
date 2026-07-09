"""Player-constant values for the DMC (Demo Music Creator) player.

These are the FIXED code OFFSETS-from-load of each per-tune table base operand
in the DMC player binary (the 16-bit operand of the ``LDA <table>,Y`` that reads
the table).  The player binary is identical across tunes, so the offsets are
constant; the VALUE stored at each offset is the per-tune table base (a tune with
a differently sized song relocates its tables, so the bases are read from the code
operands, not hardcoded).  Transcribed from the DMC disassembly
(``disasm.asm`` $1000..$1830 / decompile.c).
"""

SID_REGISTERS = 25
PAL_CYCLES_PER_FRAME = 19656  # 312 rasterlines * 63 cycles (PAL VBI period)

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

# code OFFSET-from-load of each table-base operand (the LDA abs,Y operand byte).
FREQ_LO_OP = 0x1AB  # LDA $1647,Y @ $11AA  (note->freq lo)
FREQ_HI_OP = 0x1B1  # LDA $16A7,Y @ $11B0  (note->freq hi)
INSTR_OP = 0x227  # LDA $17B0,Y @ $1226  (stride-11 instrument records)
PATTERN_LO_OP = 0x103  # LDA $1829,Y @ $1102  (pattern ptr lo)
PATTERN_HI_OP = 0x108  # LDA $182D,Y @ $1107  (pattern ptr hi)
ORDER_TABLE_OP = 0x3E  # LDA $17F0,Y @ $103D  (per-voice orderlist base table)
PW_TABLE_OP = 0x358  # LDA $17B3,Y @ $1357  (PW-sweep nibble table)
ARP_CTRL_OP = 0x59C  # LDA $17C6,Y @ $159B  (wavetable ctrl/arp)
ARP_NOTE_OP = 0x5B9  # LDA $17CA,Y @ $15B8  (wavetable note)
FILT_CTRL_OP = 0x296  # LDA $17CE,Y @ $1295  (filter presets)
FILT_STEP_LO_OP = 0x3E7  # LDA $17D2,Y @ $13E6  (filter sweep step lo)
FILT_STEP_HI_OP = 0x3ED  # LDA $17D8,Y @ $13EC  (filter sweep step hi)
