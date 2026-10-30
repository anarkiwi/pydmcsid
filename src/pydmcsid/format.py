"""pysidtracker format registration: lets the shared tooling census DMC tunes.

Registered on the ``pysidtracker.formats`` entry point, so ``pysidtracker variant
census`` and the survey renderer classify DMC tunes by their :mod:`pydmcsid.variant`
facets without a pydmcsid-specific sampler.
"""

from __future__ import annotations

from pysidtracker import SidFormat

from pydmcsid.grammar import GRAMMAR
from pydmcsid.player import DmcPlayer
from pydmcsid.reader import DmcSidParser, Song
from pydmcsid.variant import facet_sites


def _metadata(song: Song):
    released = song.header.released if song.header is not None else ""
    return (song.name, song.author, released, song.load, song.init, song.play)


def _streams(song: Song):
    return song.streams(max(song.start_song - 1, 0))


def _canonical(song: Song) -> str:
    return "\n".join(GRAMMAR.listing(_streams(song)))


def dmc_format() -> SidFormat:
    """The DMC :class:`SidFormat` for pysidtracker's format discovery."""
    return SidFormat(
        name="dmc",
        parser=DmcSidParser(),
        player=DmcPlayer,
        metadata=_metadata,
        variant=Song.facets,
        facet_sites=facet_sites,
        grammar=GRAMMAR,
        streams=_streams,
        canonical=_canonical,
        probes={"byte_exact": Song.byte_exact},
    )
