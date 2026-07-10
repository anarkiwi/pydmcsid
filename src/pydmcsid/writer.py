"""Export a loaded :class:`~pydmcsid.reader.Song` back to a loadable file.

The DMC editor's *packed* on-disk form is the resident player with the per-tune
tables packed in behind it (``init`` at the JMP-table base, ``play`` at
``base+3``) -- exactly the image :func:`pydmcsid.read` parses.  Both the DMC4
editor and the modern cross-platform DMC editor import a bare ``.prg`` (or
``.sid``) and depack it on load, so re-emitting that image round-trips a tune
back into an editor.

:func:`to_prg` emits the bare ``.prg`` (2-byte little-endian load address + the
resident image); :func:`to_sid` wraps it in a PSID/RSID container; :func:`write`
dispatches on the path suffix.  The resident image is reproduced byte-for-byte,
so ``parse(to_prg(song))`` reproduces the same tune (verified frame-exact against
the py65 oracle in the tests).
"""

from pathlib import Path
from typing import Optional

from pysidtracker.header import PSID_MAGIC, RSID_MAGIC, write_psid

from pydmcsid.reader import Song

__all__ = ["image_bytes", "to_prg", "to_sid", "write"]


def image_bytes(song: Song) -> bytes:
    """Return the resident player+data image (``mem[load:load+image_len]``)."""
    return bytes(song.mem[song.load : song.load + song.image_len])


def to_prg(song: Song) -> bytes:
    """Serialize ``song`` to a bare ``.prg`` (LE load address + resident image).

    This is the packed player+data the DMC editor loads: ``load`` addresses the
    JMP-table base (or a relocation stub ahead of it), ``init`` runs at that base
    with the accumulator set to the subtune and ``play`` at ``base+3``.
    """
    load = song.load & 0xFFFF
    return bytes((load & 0xFF, load >> 8)) + image_bytes(song)


def to_sid(song: Song, container: Optional[bytes] = None) -> bytes:
    """Serialize ``song`` to a PSID/RSID ``.sid`` container.

    The container magic/version/release/flags are carried over from the source
    header when the song was read from a ``.sid`` (overridable via ``container``,
    ``b"PSID"`` or ``b"RSID"``); a song read from a bare ``.prg`` defaults to a
    PSID v2 PAL container.  ``init``/``play``/``songs``/``startSong``/name/author
    come from the :class:`Song`.  The container is packed by the shared
    :func:`pysidtracker.header.write_psid`; the header ``loadAddress`` field is
    ``0`` so the load address is carried in the first two bytes of the image
    (the :func:`to_prg` form :func:`pydmcsid.read` reads back).
    """
    src = song.header
    magic = (
        container
        if container is not None
        else (src.magic if src is not None else PSID_MAGIC)
    )
    if magic not in (PSID_MAGIC, RSID_MAGIC):
        raise ValueError(f"container magic must be PSID or RSID, got {magic!r}")
    version = max(2, src.version) if src is not None else 2
    released = src.released if src is not None else ""
    flags = src.flags if src is not None else 0
    return write_psid(
        load=0,  # first data word carries the load address (see to_prg)
        init=song.init & 0xFFFF,
        play=song.play & 0xFFFF,
        image=to_prg(song),
        name=song.name,
        author=song.author,
        released=released,
        songs=song.songs,
        start_song=song.start_song,
        flags=flags,
        version=version,
        kind=magic,
    )


def write(song: Song, path) -> None:
    """Write ``song`` to ``path``; ``.sid`` emits a container, else a bare ``.prg``."""
    path = Path(path)
    data = to_sid(song) if path.suffix.lower() == ".sid" else to_prg(song)
    path.write_bytes(data)
