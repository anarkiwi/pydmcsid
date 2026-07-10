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
# Release gate-off site ($133d): inline ``STA $100f,X`` ($9D, $37 stock, AD/SR
# static) or ``JSR`` ($20) a helper that may also zero AD/SR (the $1d $17ec
# clear, or a $37 scene edit).  See ``reader.release_clears_adsr``.
V37_RELEASE_SITE_REL = 0x33D

# PW-sweep min-bound shift chain ($124b): the stock body forms
# ``pw_min = inst[2] >> 4`` with four ``LSR A`` (``4a 4a 4a 4a``) before the
# ``STA $1756,X`` store.  A hand-patched build overwrites the third ``LSR`` with
# an illegal 2-byte no-op (``$17``; py65 runs it as a 2-byte NOP that eats the
# following ``LSR``), leaving two ``LSR A`` -> ``pw_min = inst[2] >> 2``.  The
# shift is read from the code (count of ``LSR A`` before the store site opcode).
PW_MIN_SHIFT_REL = 0x24B  # first ``LSR A`` of the pw_min shift chain
PW_MIN_STORE_OP = 0x9D  # STA $1756,X -- terminates the shift chain
PW_MIN_SHIFT_STD = 4  # stock shift (four ``LSR A``)

# --- $a1 engine (V5-era reorganised player body) ---------------------------
# A genuinely different DMC generation: the play body sits at ``base+$a1`` (the
# dispatch play-JMP target) with a reorganised work-RAM map and a richer feature
# set (global filter cutoff sweep, volume fade, per-voice PW/vibrato/slide
# wavetables).  Recognised + reproduced byte-exact by ``PlayerA1``; gated wholly
# separately from the ``base+$85`` body so the v37/v1d engines are unchanged.
DMC_PLAY_A1_REL = 0xA1  # play-body offset from base for the $a1 engine

# Play-body operand offsets (rel to base): per-tune table bases, read from the
# ``LDA <table>,Y`` operands the same way the base engine reads its tables.  The
# instruction opcodes are identical across the family; only these operands (and
# the two patchable release stores below) vary per tune (the data tables
# relocate as the song size changes).
A1_PATTERN_LO_OP = 0x14F  # LDA <patptr_lo>,Y @ $114e
A1_PATTERN_HI_OP = 0x154  # LDA <patptr_hi>,Y @ $1153
A1_INST_OP = 0x2CC  # LDA <instruments>,Y @ $12cb (8-byte records)
A1_WT_CTRL_OP = 0x386  # LDA <wavetable ctrl>,Y @ $1385
A1_WT_ARG_OP = 0x390  # LDA <wavetable arg>,Y @ $138f
A1_PW_A_OP = 0x3C1  # LDA <pw hi/step>,Y @ $13c0
A1_PW_B_OP = 0x3C7  # LDA <pw lo/step>,Y @ $13c6
A1_FILT_A_OP = 0x3F0  # LDA <filter hi/step>,Y @ $13ef
A1_FILT_B_OP = 0x3F6  # LDA <filter lo/step>,Y @ $13f5
A1_FREQ_LO_REL = 0x70F  # note->freq lo table (fixed: right after the code)
A1_FREQ_HI_REL = 0x76F  # note->freq hi table (fixed)
A1_ORDER_STORE_REL = 0x17CF  # init copies orderlist ptr lo to $17cf,X

# Per-build patchable release stores: a scene edit may overwrite the store with
# an illegal 3-byte ``BIT`` no-op ($2c) to disable it (the analog of
# ``release_clears_adsr`` in the base engine), so the behaviour is read from the
# opcode rather than assumed.
A1_REL_SR_CLEAR_REL = 0x6C7  # STA $d406,Y ($99) / BIT ($2c): release SR clear
A1_REL_GATE_REL = 0x6E3  # STA $1817,X ($9d) / BIT ($2c): release gate-off mask

# Body signature: sha256 of the play body ($a1..$70e) with the per-tune table
# operands (any 3-byte instruction operand ``>= base+$846``, the per-tune data
# region) and the two patchable release opcodes zeroed.  All 1198 family tunes
# share this exact normalised body; it rejects both the unrelated engine that
# also dispatches play to ``base+$a1`` and the reorganised-wavetable sub-variant.
A1_BODY_LO = 0xA1
A1_BODY_HI = 0x70F
A1_DATA_REL = 0x846  # per-tune data starts here; operands >= this are wildcarded
A1_BODY_SHA256 = "fad9e7f9195f89dfce507681a9ee54fbec44507d5295e8e2e2d899834670914c"

# The header init/play vectors must resolve into the resident player (within this
# window of the base) for byte-exact playback; builds wrapped by a self-modifying
# subtune selector or a multispeed divider are recognised but gated out.
A1_DISPATCH_WINDOW = 0x20

STD_PLAY_REL = 0x03  # the standard DMC play entry ($1003 = base+3, unwrapped)
INST_ADSR_SUB_OP = 0x231  # JSR <adsr-helper> operand @ $1230
INST_ADSR_SUB_REL = 0x84B  # the modelled AD/SR write helper ($184B)
PW_TABLE_OP = 0x358  # LDA $17B3,Y @ $1357  (PW-sweep nibble table)
ARP_CTRL_OP = 0x59C  # LDA $17C6,Y @ $159B  (wavetable ctrl/arp)
ARP_NOTE_OP = 0x5B9  # LDA $17CA,Y @ $15B8  (wavetable note)
FILT_CTRL_OP = 0x296  # LDA $17CE,Y @ $1295  (filter presets)
FILT_STEP_LO_OP = 0x3E7  # LDA $17D2,Y @ $13E6  (filter sweep step lo)
FILT_STEP_HI_OP = 0x3ED  # LDA $17D8,Y @ $13EC  (filter sweep step hi)

# --- $95 engine (compact, self-modifying player body) ----------------------
# A distinct earlier-lineage DMC generation whose play body sits at ``base+$95``
# (the dispatch play-JMP target).  A single global tempo divider ($1016) chooses,
# per frame, between a row-advance pass ($10e1) and a steady tick ($1373) for all
# three voices; the SID voice stride is read from the preset table ``$100c,X`` =
# {0,7,14}.  The filter cutoff-hi is composed once per frame as a global
# accumulator plus a per-tune base offset (``$d416 = $1019 + $1853``).  Recognised
# + reproduced byte-exact by ``Player95``; gated wholly separately from the
# ``$85``/``$a1`` bodies so those engines are unchanged.
DMC_PLAY_95_REL = 0x95  # play-body offset from base for the $95 engine

# Play-body operand offsets (rel to base): per-tune table bases, read from the
# ``LDA <table>,Y`` operands.  The note-freq tables sit at a FIXED rel offset
# (right after the code, ahead of the work RAM), so they are base-relative
# constants, not read operands.
N95_ORDER_OP = 0x47  # LDA <ordertable>,Y @ $1046 (per-subtune 8-byte records)
N95_PATTERN_LO_OP = 0x147  # LDA <patptr_lo>,Y @ $1146
N95_PATTERN_HI_OP = 0x14C  # LDA <patptr_hi>,Y @ $114b
N95_INST_OP = 0x339  # LDA <instruments>,Y @ $1338 (8-byte records)
N95_WT_CTRL_OP = 0x658  # LDA <wavetable ctrl>,Y @ $1657
N95_WT_ARG_OP = 0x65F  # LDA <wavetable arg>,Y @ $165e
N95_PW_A_OP = 0x4D0  # LDA <pw hi/step>,Y @ $14cf
N95_PW_B_OP = 0x4C6  # LDA <pw lo/step>,Y @ $14c5
N95_FILT_A_OP = 0x496  # LDA <filter hi/step>,Y @ $1495
N95_FILT_B_OP = 0x4A7  # LDA <filter lo/step>,Y @ $14a6
N95_FREQ_LO_REL = 0x719  # note->freq lo table (fixed: right after the code)
N95_FREQ_HI_REL = 0x779  # note->freq hi table (fixed)
N95_ORDER_STORE_REL = 0x17D9  # init copies orderlist ptr lo to $17d9,X

# Body signature: sha256 of the play body ($95..$718) with the per-tune table
# operands (any 3-byte instruction operand ``>= base+$858``, the per-tune data
# region past the fixed freq tables + work RAM) zeroed, plus the self-modified
# tempo-reload seed byte at ``$10bf`` (init overwrites it from the subtune record,
# so its source value is irrelevant) wildcarded.  513 family tunes share this
# normalised body; it rejects the reorganised $95 sub-versions (groove counter,
# relocated filter cell, different zero-page pointers) which are not modelled.
N95_BODY_LO = 0x95
N95_BODY_HI = 0x719
N95_DATA_REL = 0x858  # per-tune data starts here; operands >= this are wildcarded
N95_RELOAD_SEED_REL = 0xBF  # self-modified $1016 tempo-reload immediate (wildcard)
N95_BODY_SHA256 = "43f32e5705a76585f41e1ad001db15a2c4b244a72a77d92a4900008fceb3de4c"

# The header init/play vectors must resolve into the resident player (within this
# window of the base) for byte-exact playback; builds wrapped by a self-modifying
# subtune selector or a multispeed divider (play/init far outside) are recognised
# but gated out.
N95_DISPATCH_WINDOW = 0x20

# --- $94a family (init-$1d body behind a 2-level PSID dispatch) -------------
# ~224 HVSC tunes whose PSID JMP table jumps into a SECOND JMP table (the sidid
# $947/$94a/$937 cluster): dispatch play -> base+$94a, which is itself a
# ``JMP real_play`` into the standard init-$1d ($85) DMC body -- authored at a
# VIRTUAL base (base+1..base+13, shifted by the family's longer id/dispatch stub).
# Following that second JMP and deriving the engine base (real_play-$85) recovers
# the resident body, which the reader already models (:func:`_play_body_ok`).  So
# the byte-exact-reproducible members are the init-$1d engine relocated; they are
# routed to :class:`~pydmcsid.player.PlayerNN` (a thin :class:`PlayerV1D`) at the
# derived base.  The reader gates them wholly separately (variant ``"nn"``) so the
# $85/$a1/$95 bodies are unaffected: the 2-level follow only fires when the
# dispatch play target is itself a ``JMP`` (the other generations' play targets are
# the body).  Deferred sub-variants (recognised, NOT byte-exact): the appended
# multispeed/second-engine wrappers that drive the reorganised base+$937 steady
# body instead of the $85 body, and the $85 sub-variant whose note onset writes
# CTRL inline (STA) rather than the modelled JMP/BIT form -- see
# :func:`_nn_byte_exact`.
DMC_PLAY_NN_REL = 0x94A  # dispatch play-JMP offset from the table base (family sig)

# How far below the derived engine base to scan for the family's 2-level dispatch
# table (its play entry is the JMP that reaches ``base+$85``).  The virtual-base
# shift is +1 for almost all builds, +13 for a few whose id/dispatch stub is
# longer; a $20 window covers both with margin.
NN_BASE_SCAN = 0x20
NN_DISPATCH_WINDOW = 0x20  # header play/init resident-window (each side of base)

# Byte-exact discriminators (read from the code, not a whole-body SHA: the $85
# body embeds the note-freq tables mid-range, so an a1/n95-style linear-walk
# normalisation desyncs).  The modelled init-$1d body reaches its note onset via a
# ``JMP`` at base+$318 and no-ops a vibrato-setup store with an illegal ``BIT`` at
# base+$58e; the unmodelled sub-variant re-encodes both as inline ``STA`` (writes
# CTRL/vibrato directly), so these two opcodes separate them cleanly.
NN_NOTE_ONSET_REL = 0x318
NN_NOTE_ONSET_OP = 0x4C  # JMP -- modelled note-onset dispatch
NN_VIBRATO_REL = 0x58E
NN_VIBRATO_OP = 0x2C  # BIT -- modelled (no-op'd) vibrato-setup store
