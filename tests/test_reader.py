"""Reader tests: PSID parsing, PRG parsing, and the DMC signature gate."""

import pytest

import pysidtracker
from pysidtracker import PlayroutineKind

import pydmcsid
from pydmcsid import DmcSidParser
from pydmcsid.errors import DmcError, SidParseError


def test_read_sid(tune_path):
    """A DMC ``.sid`` parses into a resident-image Song."""
    song = pydmcsid.read(tune_path)
    assert song.load <= song.base < song.load + 0x30  # base at/just past load
    assert song.image_len > 0
    assert song.songs >= 1


def test_parse_rejects_non_dmc():
    """A non-DMC image (no JMP-table signature) is rejected."""
    # A minimal PSID header loading $1000 with non-DMC body bytes.
    body = bytes([0x00] * 64)
    header = bytearray(0x7C)
    header[0:4] = b"PSID"
    header[6:8] = (0x7C).to_bytes(2, "big")  # data offset
    header[8:10] = (0x1000).to_bytes(2, "big")  # load
    header[0x0E:0x10] = (1).to_bytes(2, "big")  # songs
    with pytest.raises(SidParseError):
        pydmcsid.parse(bytes(header) + body)


def test_parse_prg_too_short():
    """A truncated PRG raises."""
    with pytest.raises(SidParseError):
        pydmcsid.parse(b"\x00")


def _dmc_jmp_table(base, init_rel=0x1D):
    """The 4-entry DMC JMP table (init/play/stop/FUN) for a player at ``base``."""
    tbl = bytearray()
    for rel in (init_rel, 0x85, 0x62F, 0x63E):
        tgt = base + rel
        tbl += bytes([0x4C, tgt & 0xFF, (tgt >> 8) & 0xFF])
    return bytes(tbl)


def _dmc_body(base, init_rel=0x1D, marker=0xFE):
    """A minimal recognisable DMC body: JMP table + the play-body signature.

    The recogniser confirms the play routine at ``base+$85`` opens with
    ``DEC base+$718`` (``CE`` + operand); ``marker`` seeds the generation byte at
    ``base+$126`` ($FE = byte-exact generation, $7E = later generation).
    """
    body = bytearray(_dmc_jmp_table(base, init_rel))
    body += b"\x00" * (0x400 - len(body))
    tempo = (base + 0x718) & 0xFFFF
    body[0x85:0x88] = bytes([0xCE, tempo & 0xFF, (tempo >> 8) & 0xFF])
    body[0x126] = marker
    # Seed the $1d note/inst/filter cell operands (note, note+3, note+6) so a
    # $7e-marker body satisfies the modelled-layout check in ``dmc_variant``.
    for off, cell in ((0x1A7, 0x12), (0x11A, 0x15), (0xA7, 0x18)):
        addr = (base + cell) & 0xFFFF
        body[off : off + 2] = bytes([addr & 0xFF, (addr >> 8) & 0xFF])
    # Rest-tail JMP ($1180 -> $1322) and AD/SR-helper JSR ($1230 -> $184b) so the
    # body also satisfies the byte-exactness gates.
    for off, tgt_rel in ((0x181, 0x322), (0x231, 0x84B)):
        tgt = (base + tgt_rel) & 0xFFFF
        body[off : off + 2] = bytes([tgt & 0xFF, (tgt >> 8) & 0xFF])
    body[0x180] = 0x4C
    body[0x230] = 0x20
    # Stock $37 release: the inline gate-off store ``STA $100f,X`` at $133d (a
    # patched ``JSR`` there marks a scene hard-restart edit; see v37_release_mode).
    gate = (base + 0x0F) & 0xFFFF
    body[0x33D:0x340] = bytes([0x9D, gate & 0xFF, (gate >> 8) & 0xFF])
    return bytes(body)


def test_parse_prg_stub_prepended_base_differs_from_load():
    """A stub-prepended DMC PRG is recognised at the true player base ($1000)."""
    load = 0x0FF9
    stub = b"\xea" * 7  # 7-byte relocator stub (NOPs), base lands at $1000
    body = stub + _dmc_body(0x1000)
    prg = bytes([load & 0xFF, (load >> 8) & 0xFF]) + body
    song = pydmcsid.parse(prg)
    assert song.load == load
    assert song.base == 0x1000  # located past the stub, init-variant tolerant
    assert song.songs == 1  # bare PRG: single subtune, no header
    assert song.is_dmc()


def test_parser_parse_method_returns_song():
    """``DmcSidParser().parse`` decodes bytes into a Song (the class API)."""
    prg = b"\x00\x10" + _dmc_body(0x1000, init_rel=0x37)
    song = DmcSidParser().parse(prg)
    assert song.base == 0x1000
    assert song.is_dmc()
    assert song.byte_exact()  # $37-generation marker


def test_parse_two_entry_dispatch_same_body():
    """A 2-entry (init/play only) dispatch with the identical body is recognised.

    These builds (the sidid ``$37/$85`` and ``$39/$85`` clusters) carry the exact
    play body but a shorter dispatch table; the old 4-JMP anchor rejected them.
    """
    body = bytearray(_dmc_body(0x1000, init_rel=0x37))
    body = bytearray(body)
    # Replace the stop/FUN JMP entries with non-JMP bytes: only init/play remain.
    body[6:12] = b"\xea" * 6
    prg = b"\x00\x10" + bytes(body)
    song = DmcSidParser().parse(prg)
    assert song.base == 0x1000
    assert song.is_dmc()
    assert song.byte_exact()


def test_later_generation_recognised_byte_exact():
    """The init-$1d body (marker $7e) is recognised and now played byte-exact."""
    prg = b"\x00\x10" + _dmc_body(0x1000, init_rel=0x1D, marker=0x7E)
    song = DmcSidParser().parse(prg)
    assert song.is_dmc()
    assert song.variant() == "v1d"
    assert song.byte_exact()


def test_unmodelled_generation_recognised_not_byte_exact():
    """A DMC body with an unknown marker is recognised but not byte-exact."""
    prg = b"\x00\x10" + _dmc_body(0x1000, init_rel=0x1D, marker=0x55)
    song = DmcSidParser().parse(prg)
    assert song.is_dmc()
    assert song.variant() is None
    assert not song.byte_exact()


def test_reorganised_body_rejected():
    """A play->base+$a1 dispatch whose body is NOT the $a1 signature is rejected."""
    # init/play JMP table but play targets base+$a1 (the sidid 40/a1 cluster) with
    # an all-zero body: the $a1 anchor requires the family body signature, so this
    # neither matches the $85 body nor the $a1 body and is not a DMC.
    tbl = bytearray()
    for rel in (0x40, 0xA1):
        tgt = 0x1000 + rel
        tbl += bytes([0x4C, tgt & 0xFF, (tgt >> 8) & 0xFF])
    prg = b"\x00\x10" + bytes(tbl) + b"\x00" * 0x300
    with pytest.raises(SidParseError):
        pydmcsid.parse(prg)


def _a1_body(base, sr_clear=0x99, gate=0x9D, order_tbl=0x1900):
    """A synthetic $a1 body: dispatch (play->base+$a1) + the family fixtures.

    Fills the play body ($a1..$70e) with NOPs plus one per-tune-data operand
    (wildcarded by the signature) and the two patchable release stores, and seeds
    the init order-table copy ``LDA <order>,Y : STA $17cf,X`` store site.  The
    caller monkeypatches ``constants.A1_BODY_SHA256`` to this body's normalised
    hash, so the recognition machinery (dispatch scan, $a1 play offset, body
    normalisation + hash, order-table read) is exercised without embedding the
    real engine bytes.
    """
    from pydmcsid import constants

    del base  # bodies are authored load-relative to $1000
    mem = bytearray(0x2000)
    mem[0:6] = bytes([0x4C, 0x40, 0x10, 0x4C, 0xA1, 0x10])  # play JMP -> base+$a1
    # init order-table copy store site: B9 <order,Y> : 9D CF 17 (STA $17cf,X)
    mem[0x46:0x4C] = bytes([0xB9, order_tbl & 0xFF, order_tbl >> 8, 0x9D, 0xCF, 0x17])
    for off in range(0xA1, 0x70F):  # NOP-fill the play body
        mem[off] = 0xEA
    mem[0xA1:0xA4] = bytes([0xAD, 0x00, 0x19])  # LDA $1900: a per-tune-data operand
    mem[constants.A1_REL_SR_CLEAR_REL] = sr_clear  # release SR-clear opcode
    mem[constants.A1_REL_GATE_REL] = gate  # release gate-mask opcode
    return bytes(mem)


def _a1_hash(body):
    import hashlib

    from pydmcsid.reader import _norm_a1_body

    img = bytearray(0x10000)
    img[0x1000 : 0x1000 + len(body)] = body  # body is load-relative to $1000
    return hashlib.sha256(_norm_a1_body(img, 0x1000)).hexdigest()


def test_a1_body_recognised_byte_exact(monkeypatch):
    """A synthetic $a1 body is recognised as variant "a1" and played byte-exact."""
    from pydmcsid import constants

    body = _a1_body(0x1000)
    monkeypatch.setattr(constants, "A1_BODY_SHA256", _a1_hash(body))
    song = pydmcsid.parse(b"\x00\x10" + body)
    assert song.is_dmc()
    assert song.base == 0x1000
    assert song.variant() == "a1"
    assert song.byte_exact()  # bare PRG: init/play at base, patches modelled


def test_a1_release_patch_and_order_gate(monkeypatch):
    """$a1 byte-exactness gates on the release patch opcodes + order-table anchor."""
    from pydmcsid import constants
    from pydmcsid.reader import a1_order_table_base, dmc_byte_exact

    body = _a1_body(0x1000, order_tbl=0x1888)
    monkeypatch.setattr(constants, "A1_BODY_SHA256", _a1_hash(body))
    song = pydmcsid.parse(b"\x00\x10" + body)
    assert a1_order_table_base(song.mem, 0x1000) == 0x1888  # read from store site
    assert song.byte_exact()

    # An unmodelled opcode at the release SR-clear site is recognised, not exact.
    body2 = _a1_body(0x1000, sr_clear=0xEA)
    monkeypatch.setattr(constants, "A1_BODY_SHA256", _a1_hash(body2))
    song2 = pydmcsid.parse(b"\x00\x10" + body2)
    assert song2.variant() == "a1"
    assert not song2.byte_exact()

    # A wrapped play vector (outside the player) is recognised, not byte-exact.
    assert not dmc_byte_exact(song.mem, 0x1000, play=0x2D93, init=0x1000)


def test_a1_does_not_misfire_on_base_body(monkeypatch):
    """The $a1 detector never claims the $85 (v37/v1d) synthetic bodies."""
    from pydmcsid import constants
    from pydmcsid.reader import _a1_body_ok

    # Even with the $a1 hash active, a $85 body is not an $a1 body.
    monkeypatch.setattr(constants, "A1_BODY_SHA256", _a1_hash(_a1_body(0x1000)))
    v37 = pydmcsid.parse(b"\x00\x10" + _dmc_body(0x1000, init_rel=0x37))
    assert v37.variant() == "v37"
    assert not _a1_body_ok(v37.mem, 0x1000)


def test_a1_norm_body_straddling_instruction_returns_none():
    """A body whose opcode walk straddles the body end is rejected, not crashed.

    When a 3-byte instruction begins in the last two bytes of the play-body window
    the ``_OP_LEN`` walk would index past the normalised buffer; the normaliser
    must return ``None`` (a non-family body) rather than raise.  Recognition then
    cleanly rejects the image instead of propagating an ``IndexError`` out of
    ``read``/``parse``.
    """
    from pydmcsid.reader import _norm_a1_body, find_dmc_base

    mem = bytearray(0x10000)
    base = 0x1000
    mem[base : base + 6] = bytes([0x4C, 0x40, 0x10, 0x4C, 0xA1, 0x10])  # play->$a1
    for off in range(base + 0xA1, base + 0x70F):  # JMP-abs (3-byte) fill: straddles
        mem[off] = 0x4C
    assert _norm_a1_body(mem, base) is None  # no IndexError
    assert find_dmc_base(mem, base) is None  # cleanly not recognised
    with pytest.raises(SidParseError):
        pydmcsid.parse(b"\x00\x10" + bytes(mem[base : base + 0x800]))


def _n95_body(base, order_tbl=0x1900, reload_seed=0x07):
    """A synthetic $95 body: dispatch (play->base+$95) + the family fixtures.

    NOP-fills the play body ($95..$718) with one per-tune-data operand (wildcarded
    by the signature) and the self-modified tempo-reload seed at $10bf, and seeds
    the init order-table copy ``LDA <order>,Y : STA $17d9,X`` store site.  The
    caller monkeypatches ``constants.N95_BODY_SHA256`` to this body's normalised
    hash, exercising the recognition machinery without embedding the real engine.
    """
    from pydmcsid import constants

    del base  # bodies are authored load-relative to $1000
    mem = bytearray(0x2000)
    mem[0:6] = bytes([0x4C, 0x40, 0x10, 0x4C, 0x95, 0x10])  # play JMP -> base+$95
    # init order-table copy store site: B9 <order,Y> : 9D D9 17 (STA $17d9,X)
    mem[0x46:0x4C] = bytes([0xB9, order_tbl & 0xFF, order_tbl >> 8, 0x9D, 0xD9, 0x17])
    for off in range(0x95, 0x719):  # NOP-fill the play body
        mem[off] = 0xEA
    mem[0x95:0x98] = bytes([0xAD, 0x00, 0x19])  # LDA $1900: a per-tune-data operand
    mem[constants.N95_RELOAD_SEED_REL] = reload_seed  # self-modified reload seed
    return bytes(mem)


def _n95_hash(body):
    import hashlib

    from pydmcsid.reader import _norm_n95_body

    img = bytearray(0x10000)
    img[0x1000 : 0x1000 + len(body)] = body  # body is load-relative to $1000
    return hashlib.sha256(_norm_n95_body(img, 0x1000)).hexdigest()


def test_n95_body_recognised_byte_exact(monkeypatch):
    """A synthetic $95 body is recognised as variant "n95" and played byte-exact."""
    from pydmcsid import constants

    body = _n95_body(0x1000)
    monkeypatch.setattr(constants, "N95_BODY_SHA256", _n95_hash(body))
    song = pydmcsid.parse(b"\x00\x10" + body)
    assert song.is_dmc()
    assert song.base == 0x1000
    assert song.variant() == "n95"
    assert song.byte_exact()  # bare PRG: play/init at base, order anchor present


def test_n95_reload_seed_wildcarded(monkeypatch):
    """The self-modified $10bf tempo-reload seed is wildcarded (init overwrites it).

    Two builds differing only in that seed byte share the normalised signature, so
    a single hash recognises both -- mirroring the real 415/88 sub-clusters.
    """
    from pydmcsid import constants
    from pydmcsid.reader import n95_order_table_base

    body1 = _n95_body(0x1000, order_tbl=0x1888, reload_seed=0x01)
    monkeypatch.setattr(constants, "N95_BODY_SHA256", _n95_hash(body1))
    song1 = pydmcsid.parse(b"\x00\x10" + body1)
    assert n95_order_table_base(song1.mem, 0x1000) == 0x1888  # read from store site
    assert song1.variant() == "n95"
    # A different reload seed normalises to the SAME body hash.
    body2 = _n95_body(0x1000, order_tbl=0x1888, reload_seed=0x00)
    assert _n95_hash(body2) == constants.N95_BODY_SHA256
    song2 = pydmcsid.parse(b"\x00\x10" + body2)
    assert song2.variant() == "n95"
    assert song2.byte_exact()


def test_n95_wrapped_vectors_not_byte_exact(monkeypatch):
    """A $95 build whose header play/init resolve outside the player is gated out."""
    from pydmcsid import constants
    from pydmcsid.reader import dmc_byte_exact

    body = _n95_body(0x1000)
    monkeypatch.setattr(constants, "N95_BODY_SHA256", _n95_hash(body))
    mem = bytearray(0x10000)
    mem[0x1000 : 0x1000 + len(body)] = body
    assert dmc_byte_exact(mem, 0x1000, play=0x1003, init=0x1000)  # unwrapped
    assert not dmc_byte_exact(mem, 0x1000, play=0x2D89, init=0x2D80)  # wrapper


def test_n95_does_not_misfire_on_base_body(monkeypatch):
    """The $95 detector never claims the $85 (v37/v1d) synthetic bodies."""
    from pydmcsid import constants
    from pydmcsid.reader import _n95_body_ok

    monkeypatch.setattr(constants, "N95_BODY_SHA256", _n95_hash(_n95_body(0x1000)))
    v37 = pydmcsid.parse(b"\x00\x10" + _dmc_body(0x1000, init_rel=0x37))
    assert v37.variant() == "v37"
    assert not _n95_body_ok(v37.mem, 0x1000)


def test_n95_norm_body_straddling_instruction_returns_none():
    """A $95 body whose opcode walk straddles the body end is rejected, not crashed."""
    from pydmcsid.reader import _norm_n95_body, find_dmc_base

    mem = bytearray(0x10000)
    base = 0x1000
    mem[base : base + 6] = bytes([0x4C, 0x40, 0x10, 0x4C, 0x95, 0x10])  # play->$95
    mem[base + 0x95] = 0xEA  # a 1-byte NOP shifts the JMP-abs run off alignment
    for off in range(base + 0x96, base + 0x719):  # JMP-abs (3-byte) fill: straddles
        mem[off] = 0x4C
    assert _norm_n95_body(mem, base) is None  # no IndexError
    assert find_dmc_base(mem, base) is None  # cleanly not recognised
    with pytest.raises(SidParseError):
        pydmcsid.parse(b"\x00\x10" + bytes(mem[base : base + 0x800]))


def _nn_body(note_onset=0x4C, vibrato=0x2C, second_play=0x1086):
    """A synthetic $94a-family image: a 2-level dispatch into the $85 body.

    The PSID table at $1000 jumps to a SECOND table (play -> $1000+$94a), which is
    itself a ``JMP second_play`` into the standard $85 DMC body authored at the
    virtual base $1001 (so ``base_eng = second_play - $85``).  Seeds the play-body
    tempo anchor (``DEC base_eng+$718``) plus the two byte-exact discriminator
    opcodes (note-onset $4C / vibrato $2C by default).  Exercises the recognition
    (dispatch follow, engine-base derivation, $94a byte-exact gate) without
    embedding the real engine.
    """
    base = 0x1001  # virtual engine base (load $1000 + 1)
    mem = bytearray(0x2000)
    mem[0x000:0x006] = bytes([0x4C, 0x47, 0x19, 0x4C, 0x4A, 0x19])  # JMP init/play
    mem[0x94A:0x94D] = bytes(  # second-level play JMP -> the $85 body
        [0x4C, second_play & 0xFF, (second_play >> 8) & 0xFF]
    )
    tempo = (base + 0x718) & 0xFFFF  # DEC base_eng+$718 -- the play-body anchor
    off = (second_play - 0x1000) & 0xFFFF
    mem[off : off + 3] = bytes([0xCE, tempo & 0xFF, (tempo >> 8) & 0xFF])
    mem[(base + 0x318 - 0x1000) & 0xFFFF] = note_onset  # note-onset discriminator
    mem[(base + 0x58E - 0x1000) & 0xFFFF] = vibrato  # vibrato-setup discriminator
    return bytes(mem)


def test_nn_body_recognised_byte_exact():
    """A synthetic $94a 2-level dispatch is recognised as "nn" and byte-exact."""
    song = pydmcsid.parse(b"\x00\x10" + _nn_body())
    assert song.is_dmc()
    assert song.base == 0x1001  # engine base derived from the second-level JMP
    assert song.variant() == "nn"
    assert song.byte_exact()  # bare PRG: play/init resident, discriminators modelled


def test_nn_wrapped_and_subvariant_not_byte_exact():
    """$94a byte-exactness gates on resident vectors + the modelled note encoding."""
    from pydmcsid.reader import dmc_byte_exact

    body = _nn_body()
    mem = bytearray(0x10000)
    mem[0x1000 : 0x1000 + len(body)] = body
    assert dmc_byte_exact(mem, 0x1001, play=0x1003, init=0x1000)  # resident dispatch
    # An appended multispeed/second-engine wrapper (play/init far outside) drives
    # the reorganised base+$937 body: recognised as "nn", not byte-exact.
    assert not dmc_byte_exact(mem, 0x1001, play=0x2411, init=0x23FE)

    # The $85 sub-variant writes CTRL inline (STA $99) at the note onset instead of
    # the modelled JMP: recognised as "nn", not byte-exact.
    sub = _nn_body(note_onset=0x99)
    song = pydmcsid.parse(b"\x00\x10" + sub)
    assert song.variant() == "nn"
    assert not song.byte_exact()


def test_nn_does_not_misfire_on_base_bodies():
    """The $94a detector never claims the $85/$a1/$95 synthetic bodies."""
    from pydmcsid.reader import _nn_table_base

    v37 = pydmcsid.parse(b"\x00\x10" + _dmc_body(0x1000, init_rel=0x37))
    assert v37.variant() == "v37"
    assert _nn_table_base(v37.mem, 0x1000) is None
    v1d = pydmcsid.parse(b"\x00\x10" + _dmc_body(0x1000, init_rel=0x1D, marker=0x7E))
    assert v1d.variant() == "v1d"
    assert _nn_table_base(v1d.mem, 0x1000) is None


def test_nn_dispatch_bounds_safe():
    """A $94a dispatch whose second-level JMP runs off memory is rejected, not crashed.

    The engine-base follow bounds-checks the second-level target, so a JMP into the
    top of memory (whose $85 body would straddle the image end) cleanly yields no
    recognition rather than an ``IndexError`` out of ``read``/``parse``.
    """
    from pydmcsid.reader import _nn_engine_base, find_dmc_base

    mem = bytearray(0x10000)
    base = 0x1000
    mem[base : base + 6] = bytes([0x4C, 0x47, 0x19, 0x4C, 0x4A, 0x19])
    mem[base + 0x94A : base + 0x94D] = bytes([0x4C, 0xFF, 0xFF])  # JMP $FFFF
    assert _nn_engine_base(mem, base + 0x94A, base) is None  # no IndexError
    assert find_dmc_base(mem, base) is None  # cleanly not recognised
    with pytest.raises(SidParseError):
        pydmcsid.parse(b"\x00\x10" + bytes(mem[base : base + 0x960]))


def _nn937_image(reload=6, seed=1):
    """A synthetic $937 CIA-multispeed appended-wrapper image (mem, play, init).

    Extends the $94a 2-level dispatch (:func:`_nn_body`, virtual base $1001) with
    the reorganised SECONDARY body at ``base+$936`` (``LDA flag / BEQ / JSR``
    refresh at ``base+$8f0``) and an appended $2400 wrapper: a divide-by-``reload``
    divider (``DEC counter``) reached by the PSID play vector, seeded by the init
    vector.  Returns the raw 64K image plus the wrapper play/init addresses.
    """
    mem = bytearray(0x10000)
    body = _nn_body()
    mem[0x1000 : 0x1000 + len(body)] = body
    mem[0x1937:0x193F] = bytes(  # secondary body: LDA $1927 / BEQ / JSR $18f1 / RTS
        [0xAD, 0x27, 0x19, 0xF0, 0x04, 0x20, 0xF1, 0x18]
    )
    mem[0x18F1:0x18F5] = bytes([0xAC, 0x26, 0x19, 0xB9])  # refresh: LDY $1926 / LDA ..
    mem[0x1927] = 0x01  # enable flag
    ctr = 0x24B7
    mem[0x2400:0x2408] = bytes(  # init: JSR $1000 / LDX #seed / STX ctr / RTS
        [0x20, 0x00, 0x10, 0xA2, seed, 0x8E, ctr & 0xFF, ctr >> 8]
    ) + bytes([0x60])
    mem[0x2411:0x2420] = bytes(  # play: DEC ctr / LDX#0 / BNE / LDX #reload / STX ctr
        [0xCE, ctr & 0xFF, ctr >> 8, 0xA2, 0x00, 0xD0, 0x08, 0xA2, reload]
    ) + bytes([0x8E, ctr & 0xFF, ctr >> 8, 0x4C, 0x03, 0x10])
    return mem, 0x2411, 0x2400


def test_nn937_wrapper_recognised_byte_exact():
    """A synthetic $937 wrapper is variant "nn", byte-exact, and routes to Player937."""
    from pydmcsid.player import Player937, _player_for
    from pydmcsid.reader import Song, _nn_wrapper_937, dmc_byte_exact

    mem, play, init = _nn937_image()
    assert _nn_wrapper_937(mem, 0x1001, play, init)
    assert dmc_byte_exact(mem, 0x1001, play=play, init=init)
    song = Song(
        mem=mem,
        load=0x1000,
        base=0x1001,
        image_len=0x1420,
        songs=1,
        start_song=1,
        play=play,
        init=init,
    )
    assert song.variant() == "nn"
    player = _player_for(song, 0)
    assert isinstance(player, Player937)
    assert player._reload == 6 and player._ms == 1  # read from the wrapper code


def test_nn937_wrapper_immediates_read_from_code():
    """Player937 reads the divider period + seed from the wrapper, not assumes them."""
    from pydmcsid.player import Player937
    from pydmcsid.reader import Song

    mem, play, init = _nn937_image(reload=3, seed=2)
    song = Song(
        mem=mem,
        load=0x1000,
        base=0x1001,
        image_len=0x1420,
        songs=1,
        start_song=1,
        play=play,
        init=init,
    )
    player = Player937(song, 0)
    assert player._reload == 3 and player._ms == 2
    for _ in range(12):  # a handful of wrapper calls: main + refresh, no crash
        for reg, val in player.play_frame():
            assert 0 <= reg - 0xD400 < 25 and 0 <= val <= 0xFF

    # Enable flag cleared: every intermediate wrapper call falls back to the full
    # resident play (the reorganised refresh body is a no-op for that build).
    mem[0x1927] = 0x00
    disabled = Player937(song, 0)
    for _ in range(4):
        disabled.play_frame()

    # No ``LDX #imm : STX counter`` in the wrapper -> the seed/reload fall back to 1.
    stripped = bytearray(mem)
    stripped[0x2403:0x2408] = b"\xea" * 5  # blank the init LDX/STX
    stripped[0x2414:0x2420] = b"\xea" * 12  # blank the play LDX/STX
    song2 = Song(
        mem=stripped,
        load=0x1000,
        base=0x1001,
        image_len=0x1420,
        songs=1,
        start_song=1,
        play=play,
        init=init,
    )
    fallback = Player937(song2, 0)
    assert fallback._reload == 1 and fallback._ms == 1


def test_nn937_detector_disjoint_from_resident_nn():
    """The $937 detector never fires for the plain resident-dispatch $94a body."""
    from pydmcsid.reader import _nn_wrapper_937

    mem = bytearray(0x10000)
    mem[0x1000 : 0x1000 + 0x960] = _nn_body()  # no secondary body / no wrapper
    assert not _nn_wrapper_937(mem, 0x1001, play=0x1003, init=0x1000)
    # resident vectors -> byte-exact via PlayerNN, not the wrapper path
    assert pydmcsid.reader.dmc_byte_exact(mem, 0x1001, play=0x1003, init=0x1000)


def test_nn937_detector_bounds_safe():
    """A truncated $937 image cleanly yields no wrapper match, never an IndexError."""
    from pydmcsid.reader import _nn_wrapper_937

    mem, play, init = _nn937_image()
    truncated = bytes(mem[:0x1938])  # body opcode present but its operand straddles
    assert not _nn_wrapper_937(truncated, 0x1001, play, init)  # no IndexError
    assert not _nn_wrapper_937(mem, 0x1001, play=None, init=init)  # missing vector


def test_reject_play_jmp_but_wrong_body():
    """Play JMP targets $85 but the body there is not the DMC play routine."""
    from pydmcsid.reader import find_dmc_base  # local import: internal helper

    body = bytearray(_dmc_body(0x1000, init_rel=0x37))
    body[0x85] = 0xAD  # LDA abs, not DEC -- a different body
    mem = bytearray(0x10000)
    mem[0x1000 : 0x1000 + len(body)] = body
    assert find_dmc_base(mem, 0x1000) is None


def test_dmc_byte_exact_out_of_range():
    """``dmc_byte_exact`` is False when the marker offset is past the image."""
    from pydmcsid.reader import dmc_byte_exact

    assert dmc_byte_exact(bytearray(16), 0x0) is False


def _put_release(body, base, jsr_target=None, adsr_clear=True):
    """Patch the $133d release site: stock store, or a JSR to a mod routine."""
    if jsr_target is None:  # stock inline gate-off store
        gate = (base + 0x0F) & 0xFFFF
        body[0x33D:0x340] = bytes([0x9D, gate & 0xFF, (gate >> 8) & 0xFF])
        return
    body[0x33D:0x340] = bytes([0x20, jsr_target & 0xFF, (jsr_target >> 8) & 0xFF])
    off = jsr_target - base
    clear = bytes([0xA9, 0x00, 0x99, 0x05, 0xD4, 0x99, 0x06, 0xD4])  # LDA #0;AD/SR=0
    if adsr_clear:  # STA mask; LDY stride; clear AD/SR; RTS
        routine = bytes([0x9D, 0x0F, 0x10, 0xBC, 0x0D, 0x17]) + clear + b"\x60"
    else:  # bare STA mask; RTS -- then DEAD AD/SR stores past the RTS (Coool shape)
        routine = bytes([0x9D, 0x0F, 0x10, 0x60, 0xBC, 0x0D, 0x17]) + clear + b"\x60"
    body[off : off + len(routine)] = routine


def test_release_clears_adsr_stock_clearing_and_unknown():
    """``release_clears_adsr`` reads the $133d release from the loaded image.

    A ``JSR`` whose helper stores ``$D405``/``$D406`` before ``RTS`` clears AD/SR
    (True); the bare ``STA $100f,X : RTS`` helper does not (False) even though its
    image is followed by unrelated ``STA $D405/$D406`` bytes past the ``RTS``; the
    inline stock store does not clear (False); a malformed release is ``None``.
    """
    from pydmcsid.reader import release_clears_adsr

    stock = pydmcsid.parse(b"\x00\x10" + _dmc_body(0x1000, init_rel=0x37))
    assert release_clears_adsr(stock.mem, stock.base) is False
    assert stock.byte_exact()

    m = bytearray(_dmc_body(0x1000, init_rel=0x37))
    _put_release(m, 0x1000, jsr_target=0x1018, adsr_clear=True)
    clearing = pydmcsid.parse(b"\x00\x10" + bytes(m))
    assert release_clears_adsr(clearing.mem, clearing.base) is True
    assert clearing.byte_exact()

    m2 = bytearray(_dmc_body(0x1000, init_rel=0x37))
    _put_release(m2, 0x1000, jsr_target=0x1018, adsr_clear=False)
    noclear = pydmcsid.parse(b"\x00\x10" + bytes(m2))
    assert release_clears_adsr(noclear.mem, noclear.base) is False
    assert noclear.byte_exact()

    m3 = bytearray(_dmc_body(0x1000, init_rel=0x37))
    m3[0x33D] = 0xEA  # a NOP -- neither the inline store nor a JSR helper
    unknown = pydmcsid.parse(b"\x00\x10" + bytes(m3))
    assert release_clears_adsr(unknown.mem, unknown.base) is None
    assert not unknown.byte_exact()


def test_pw_min_shift_stock_and_patched():
    """``pw_min_shift`` reads the pw_min shift-chain count from the loaded image.

    The stock four-``LSR A`` chain (``4a 4a 4a 4a``) before ``STA $1756,X`` yields
    ``inst[2]>>4`` (shift 4); a build whose third ``LSR`` is overwritten by the
    illegal 2-byte no-op ``$17`` (which py65 runs as a 2-byte NOP that eats the
    following ``LSR``) leaves two ``LSR A`` -> ``inst[2]>>2`` (shift 2).
    """
    from pydmcsid.reader import pw_min_shift

    store = bytes([0x9D, 0x56, 0x17])  # STA $1756,X -- terminates the chain
    m = bytearray(_dmc_body(0x1000, init_rel=0x1D, marker=0x7E))
    m[0x24B:0x24F] = bytes([0x4A, 0x4A, 0x4A, 0x4A])  # four LSR A
    m[0x24F:0x252] = store
    stock = pydmcsid.parse(b"\x00\x10" + bytes(m))
    assert pw_min_shift(stock.mem, stock.base) == 4

    m2 = bytearray(m)
    m2[0x24D] = 0x17  # third LSR -> illegal 2-byte no-op
    patched = pydmcsid.parse(b"\x00\x10" + bytes(m2))
    assert pw_min_shift(patched.mem, patched.base) == 2


def test_errors_subclass_pysidtracker():
    """The pydmcsid error hierarchy re-parents onto ``pysidtracker.SidError``."""
    assert issubclass(DmcError, pysidtracker.SidError)
    assert issubclass(SidParseError, pysidtracker.SidError)


def test_parser_detect_direct(tune_path):
    """``DmcSidParser().detect`` classifies a real DMC tune as direct-load."""
    detection = DmcSidParser().detect(tune_path)
    assert detection.kind is PlayroutineKind.DIRECT
