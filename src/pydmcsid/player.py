"""The DMC per-frame player: a :class:`~pysidtracker.playspec.SpecPlayer` over the
playspec of the tune's body generation (see :mod:`pydmcsid.spec`).

The tune's own init runs on the 6502 emulator; every play call then runs the spec
on that memory, and a play wrapper's call into the resident play body is trapped
and run as the spec.
"""

from pysidtracker.playspec import SpecPlayer

from pydmcsid.reader import Song, parse
from pydmcsid.spec import binding, spec_binding


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
        bound = spec_binding(song, subtune)
        self.layout, self.song, _ = binding(song, subtune)
        self.base = self.song.base
        super().__init__(
            bound.spec,
            bound.image,
            song.load,
            song.init,
            song.play,
            subtune,
            bind=bound.bind,
            entries=bound.entries,
            validate=validate,
        )
