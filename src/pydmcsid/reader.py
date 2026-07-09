"""Read a DMC tune from a ``.sid`` (PSID/RSID) or raw ``.prg``/image.

The DMC ``.sid`` IS the player + the per-tune song data (the player binary is
resident, and the song tables are relocated into it).  :func:`read` loads the C64
memory image and its load address; the player walks the resident tables directly.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pysidtracker import BaseSidParser, SidError, SidImage

from pydmcsid import constants
from pydmcsid.errors import SidParseError


@dataclass
class Song:
    """A loaded DMC tune: the C64 RAM image + load address + subtune count."""

    mem: bytearray  # 64K C64 memory with the tune resident
    load: int  # load address
    image_len: int  # bytes of the loaded image
    songs: int  # subtune count (PSID header)
    start_song: int  # default subtune (1-based in the header)
    name: str = ""
    author: str = ""

    def is_dmc(self) -> bool:
        """Whether the resident binary carries the DMC JMP-table signature."""
        sig = bytes(self.mem[self.load : self.load + len(constants.DMC_SIGNATURE)])
        return sig == constants.DMC_SIGNATURE


def parse(data: bytes) -> Song:
    """Parse SID/PRG ``data`` bytes into a :class:`Song`."""
    try:
        image = SidImage.from_bytes(data)
    except SidError as exc:
        raise SidParseError(str(exc)) from exc
    header = image.header
    if header is not None:
        songs = header.songs
        start = header.start_song
        name = header.name
        author = header.author
    else:  # bare .prg: single subtune, no metadata
        songs = 1
        start = 1
        name = author = ""
    song = Song(
        mem=image.mem,
        load=image.load,
        image_len=image.end - image.load,
        songs=songs,
        start_song=start,
        name=name,
        author=author,
    )
    if not song.is_dmc():
        raise SidParseError("image does not carry the DMC player signature")
    return song


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
        """Return the load address when the DMC signature is at the load base."""
        sig = image.slice(image.load, len(constants.DMC_SIGNATURE))
        if sig == constants.DMC_SIGNATURE:
            return image.load
        return None
