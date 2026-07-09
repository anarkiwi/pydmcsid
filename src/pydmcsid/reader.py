"""Read a DMC tune from a ``.sid`` (PSID/RSID) or raw ``.prg``/image.

The DMC ``.sid`` IS the player + the per-tune song data (the player binary is
resident, and the song tables are relocated into it).  :func:`read` loads the C64
memory image and its load address; the player walks the resident tables directly.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from pysidtracker import BaseSidParser, SidError, SidImage

from pydmcsid import constants
from pydmcsid.errors import SidParseError


def find_dmc_base(mem, load: int) -> Optional[int]:
    """Locate the DMC player's JMP-table base, or ``None`` if not a DMC image.

    Scans from ``load`` for the opening 4-entry JMP table whose play/stop/FUN
    targets sit at the canonical offsets from the table base (see
    :mod:`pydmcsid.constants`).  This anchor is load-independent (the JMP targets
    are absolute, so they track the base) and init-independent (the init entry
    varies across DMC sub-versions), so it recognises the relocated and
    stub-prepended builds of the same player body -- not just the ``$1000`` /
    init-``$1037`` original.  Returns the table base (the player origin).
    """
    want = (
        constants.DMC_JMP_PLAY_REL,
        constants.DMC_JMP_STOP_REL,
        constants.DMC_JMP_FUN_REL,
    )
    for off in range(constants.DMC_TABLE_SCAN):
        base = load + off
        if base + 12 > len(mem):
            break
        if not (
            mem[base] == 0x4C
            and mem[base + 3] == 0x4C
            and mem[base + 6] == 0x4C
            and mem[base + 9] == 0x4C
        ):
            continue
        rels = tuple(
            ((mem[base + i + 1] | (mem[base + i + 2] << 8)) - base) & 0xFFFF
            for i in (3, 6, 9)
        )
        if rels == want:
            return base
    return None


@dataclass
class Song:
    """A loaded DMC tune: the C64 RAM image + load/base address + subtune count."""

    mem: bytearray  # 64K C64 memory with the tune resident
    load: int  # container load address
    base: int  # DMC player JMP-table base (== load unless a stub is prepended)
    image_len: int  # bytes of the loaded image
    songs: int  # subtune count (PSID header)
    start_song: int  # default subtune (1-based in the header)
    name: str = ""
    author: str = ""

    def is_dmc(self) -> bool:
        """Whether the resident binary carries the DMC JMP-table signature."""
        return find_dmc_base(self.mem, self.load) is not None


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
    base = find_dmc_base(image.mem, image.load)
    if base is None:
        raise SidParseError("image does not carry the DMC player signature")
    return Song(
        mem=image.mem,
        load=image.load,
        base=base,
        image_len=image.end - image.load,
        songs=songs,
        start_song=start,
        name=name,
        author=author,
    )


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
        """Return the DMC player JMP-table base if the image carries it."""
        return find_dmc_base(image.mem, image.load)
