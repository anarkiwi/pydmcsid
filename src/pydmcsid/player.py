"""The DMC per-frame player: a :class:`~pysidtracker.playspec.SpecPlayer` over the
playspec of the tune's body generation (see :mod:`pydmcsid.spec`).

The tune's own init runs on the 6502 emulator; every play call then runs the spec
on that memory, and a play wrapper's call into the resident play body is trapped
and run as the spec.
"""

from pysidtracker.playspec import SpecPlayer

from pydmcsid.reader import Song, parse
from pydmcsid.spec import binding, body_entries, spec


class DmcPlayer(SpecPlayer):
    """The DMC player of ``source`` (a :class:`~pydmcsid.reader.Song` or
    ``.sid``/``.prg`` bytes) at 0-based ``subtune``.

    ``render_grid(n)`` is the per-frame ``$D400..$D418`` register grid (25 registers,
    forward-filled, pulse-width-high nibble-masked); ``validate`` checks every
    stream the spec opens against the song grammar (``self.validator.failures()``);
    ``strict`` raises :class:`~pydmcsid.errors.DmcParseError` up front when a stream
    ``subtune``'s voices reach does not parse.
    """

    def __init__(
        self, source, subtune: int = 0, validate: bool = False, strict: bool = False
    ):
        song = source if isinstance(source, Song) else parse(bytes(source))
        if strict:
            song.streams(subtune)
        layout, body, _ = binding(song, subtune)
        entries = frozenset(body_entries(body))
        self.song = body
        self.layout = layout
        self.base = body.base
        super().__init__(
            spec(body.variant()),
            bytes(song.mem[song.load : song.load + song.image_len]),
            song.load,
            song.init,
            song.play,
            subtune,
            bind=lambda view, entry: layout,
            entries=lambda view: entries,
            validate=validate,
        )
