"""pysidtracker format registration: lets the shared tooling census DMC tunes.

Registered on the ``pysidtracker.formats`` entry point, so ``pysidtracker variant
census`` (and the survey renderer) classify DMC tunes by body generation, byte-exact
modelling and layout without a pydmcsid-specific sampler.
"""

from __future__ import annotations

from pysidtracker import Facet, FacetRegistry, Finding, SidFormat
from pysidtracker.facets import inferred

from pydmcsid.player import DmcPlayer
from pydmcsid.reader import DmcSidParser, Song


def _generation(song: Song):
    return Finding(song.variant(), inferred("dmc_variant"))


def _byte_exact(song: Song):
    return Finding(song.byte_exact(), inferred("dmc_byte_exact"))


def _layout(song: Song):
    return Finding("stub" if song.base != song.load else "load", inferred("base-load"))


FACETS = FacetRegistry(
    [
        Facet("generation", "build", detector=_generation),
        Facet("byte_exact", "release", detector=_byte_exact),
        Facet("layout", "layout", detector=_layout),
    ]
)


def _metadata(song: Song):
    released = song.header.released if song.header is not None else ""
    return (song.name, song.author, released, song.load, song.init, song.play)


def dmc_format() -> SidFormat:
    """The DMC :class:`SidFormat` for pysidtracker's format discovery."""
    return SidFormat(
        name="dmc",
        parser=DmcSidParser(),
        player=DmcPlayer,
        metadata=_metadata,
        variant=FACETS.detect,
    )
