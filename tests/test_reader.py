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
    assert song.load == 0x1000
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


def test_errors_subclass_pysidtracker():
    """The pydmcsid error hierarchy re-parents onto ``pysidtracker.SidError``."""
    assert issubclass(DmcError, pysidtracker.SidError)
    assert issubclass(SidParseError, pysidtracker.SidError)


def test_parser_detect_direct(tune_path):
    """``DmcSidParser().detect`` classifies a real DMC tune as direct-load."""
    detection = DmcSidParser().detect(tune_path)
    assert detection.kind is PlayroutineKind.DIRECT
