"""Exporter tests: a parsed Song serializes back to a loadable .prg / .sid.

The resident player+data image is reproduced byte-for-byte, so a re-parse yields
the same tune; the round-tripped container also reproduces the original tune
frame-for-frame on the py65 oracle (the packed form the DMC editor loads).
"""

import pytest

import pydmcsid
from pysidtracker.header import PSID_MAGIC, RSID_MAGIC
from pysidtracker.oracle import aligned_match, register_grid


def test_to_prg_image_byte_exact(tune_path):
    """``to_prg`` reproduces the resident image; the load word prefixes it."""
    song = pydmcsid.read(tune_path)
    prg = pydmcsid.to_prg(song)
    assert prg[:2] == bytes((song.load & 0xFF, song.load >> 8))
    assert prg[2:] == pydmcsid.image_bytes(song)
    assert pydmcsid.image_bytes(pydmcsid.parse(prg)) == pydmcsid.image_bytes(song)


def test_to_sid_preserves_metadata_and_image(tune_path):
    """``to_sid`` carries header metadata and the image across a re-parse."""
    song = pydmcsid.read(tune_path)
    round_tripped = pydmcsid.parse(pydmcsid.to_sid(song))
    assert round_tripped.songs == song.songs
    assert round_tripped.start_song == song.start_song
    assert round_tripped.name == song.name
    assert round_tripped.author == song.author
    assert round_tripped.init == song.init
    assert round_tripped.play == song.play
    assert pydmcsid.image_bytes(round_tripped) == pydmcsid.image_bytes(song)


def test_to_sid_roundtrip_frame_exact(tune_path):
    """The re-wrapped ``.sid`` reproduces the source tune frame-for-frame (py65)."""
    data = tune_path.read_bytes()
    song = pydmcsid.parse(data)
    rewrapped = pydmcsid.to_sid(song)
    assert aligned_match(register_grid(rewrapped, 120), register_grid(data, 120))


def test_to_sid_container_override(tune_path):
    """The container magic can be overridden and defaults to the source magic."""
    song = pydmcsid.read(tune_path)
    assert pydmcsid.to_sid(song)[:4] == (
        song.header.magic if song.header is not None else PSID_MAGIC
    )
    assert pydmcsid.to_sid(song, container=RSID_MAGIC)[:4] == RSID_MAGIC
    with pytest.raises(ValueError):
        pydmcsid.to_sid(song, container=b"XXXX")


def test_write_dispatch_by_suffix(tune_path, tmp_path):
    """``write`` emits a container for ``.sid`` and a bare ``.prg`` otherwise."""
    song = pydmcsid.read(tune_path)
    prg_path = tmp_path / "out.prg"
    sid_path = tmp_path / "out.sid"
    pydmcsid.write(song, prg_path)
    pydmcsid.write(song, sid_path)
    assert prg_path.read_bytes() == pydmcsid.to_prg(song)
    assert sid_path.read_bytes()[:4] in (PSID_MAGIC, RSID_MAGIC)
    assert pydmcsid.image_bytes(pydmcsid.parse(prg_path.read_bytes())) == (
        pydmcsid.image_bytes(song)
    )


def test_prg_roundtrip_from_bare_prg():
    """A Song parsed from a bare ``.prg`` (no header) still serializes back."""
    base = 0x1000
    tbl = bytearray()
    for rel in (0x1D, 0x85, 0x62F, 0x63E):
        tgt = base + rel
        tbl += bytes([0x4C, tgt & 0xFF, (tgt >> 8) & 0xFF])
    body = bytearray(tbl)
    body += b"\x00" * (0x300 - len(body))
    tempo = (base + 0x718) & 0xFFFF
    body[0x85:0x88] = bytes([0xCE, tempo & 0xFF, (tempo >> 8) & 0xFF])
    body[0x126] = 0xFE
    prg = bytes((base & 0xFF, base >> 8)) + bytes(body)
    song = pydmcsid.parse(prg)
    assert song.header is None
    assert song.init == base
    assert pydmcsid.to_prg(song) == prg
    assert pydmcsid.to_sid(song)[:4] == PSID_MAGIC
