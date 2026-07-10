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
    """A build whose play offset is NOT $85 (a different body) is rejected."""
    # init/play JMP table but play targets base+$a1 (the sidid 40/a1 cluster).
    tbl = bytearray()
    for rel in (0x40, 0xA1):
        tgt = 0x1000 + rel
        tbl += bytes([0x4C, tgt & 0xFF, (tgt >> 8) & 0xFF])
    prg = b"\x00\x10" + bytes(tbl) + b"\x00" * 0x300
    with pytest.raises(SidParseError):
        pydmcsid.parse(prg)


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


def test_errors_subclass_pysidtracker():
    """The pydmcsid error hierarchy re-parents onto ``pysidtracker.SidError``."""
    assert issubclass(DmcError, pysidtracker.SidError)
    assert issubclass(SidParseError, pysidtracker.SidError)


def test_parser_detect_direct(tune_path):
    """``DmcSidParser().detect`` classifies a real DMC tune as direct-load."""
    detection = DmcSidParser().detect(tune_path)
    assert detection.kind is PlayroutineKind.DIRECT
