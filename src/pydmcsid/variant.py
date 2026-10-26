"""The DMC build variant: one :class:`~pysidtracker.Facet` per code-read axis.

Every value the players and the byte-exact gate read from a tune's own code is a
facet of :data:`FACETS`, recorded ``detected@$addr`` where its site matched and
``default`` where the player falls back; :meth:`pydmcsid.Song.facets` detects them once.
"""

from typing import Optional

from pysidtracker import Facet, FacetRegistry, Finding
from pysidtracker.facets import DEFAULT_SOURCE, DETECTED, detected, inferred

from pydmcsid import constants
from pydmcsid.reader import (
    Song,
    _nn_wrapper_937,
    _operand,
    _play_wrapper_benign,
    a1_release_gate,
    a1_release_sr,
    nn937_divider,
    onset_ctrl_site,
    order_table_site,
    pw_min_shift,
    release_site,
    tail_d418_site,
    v1d_onset_site,
    wrapper_body,
)

GENERATIONS = ("v37", "v1d", "a1", "n95", "nn")
# Generations sharing the init-$37/$1d (``$85``) engine and its code-read sites.
ENGINE85 = ("v37", "v1d", "nn")
V1D = ("v1d", "nn")
WRAPPERS = ("none", "benign", "selector", "multispeed", "foreign")
# The fixed-operand order-table read each generation falls back to.
_ORDER_FALLBACK_OP = {
    "v37": constants.ORDER_TABLE_OP,
    "v1d": constants.ORDER_TABLE_OP_V1D,
    "nn": constants.ORDER_TABLE_OP_V1D,
    "n95": constants.N95_ORDER_OP,
}


def _of(*generations):
    """Run the wrapped detector only for songs of ``generations``."""

    def wrap(detect):
        def gated(song: Song) -> Optional[Finding]:
            return detect(song) if song.variant() in generations else None

        return gated

    return wrap


def _at(song: Song, rel: int) -> int:
    return (song.base + rel) & 0xFFFF


def _generation(song: Song) -> Optional[Finding]:
    found = song.variant()
    return None if found is None else Finding(found, inferred("dmc_variant"))


def _byte_exact(song: Song) -> Finding:
    return Finding(song.byte_exact(), inferred("dmc_byte_exact"))


def _layout(song: Song) -> Optional[Finding]:
    if song.layout is None:
        return None
    return Finding(song.layout, inferred("locate"))


def _stub(song: Song) -> Optional[Finding]:
    if song.dispatch is None:
        return None
    return Finding(song.dispatch.table - song.load, detected(song.dispatch.table))


def _play_rel(song: Song) -> Optional[Finding]:
    if song.dispatch is None:
        return None
    rel = (song.dispatch.play - song.dispatch.table) & 0xFFFF
    return Finding(rel, detected(song.dispatch.table + 3))


def _nn_shift(song: Song) -> Optional[Finding]:
    if song.dispatch is None or song.layout != "nn-dispatch":
        return None
    shift = (song.dispatch.base - song.dispatch.table) & 0xFFFF
    return Finding(shift, detected(song.dispatch.play))


@_of(*V1D)
def _note_cells(song: Song) -> Finding:
    return Finding(
        _operand(song.mem, song.base, constants.NOTE_CELL_OP),
        detected(_at(song, constants.NOTE_CELL_OP - 1)),
    )


@_of(*V1D)
def _rest_tail(song: Song) -> Finding:
    tail = _operand(song.mem, song.base, constants.REST_TAIL_OP)
    return Finding(
        (tail - song.base) & 0xFFFF, detected(_at(song, constants.REST_TAIL_OP - 1))
    )


def _order_table(song: Song) -> Optional[Finding]:
    generation = song.variant()
    if generation is None:
        return None
    hit = order_table_site(song.mem, song.base, generation)
    if hit is not None:
        return Finding(hit.captures["table"], detected(hit.addr))
    op = _ORDER_FALLBACK_OP.get(generation)
    table = 0 if op is None else _operand(song.mem, song.base, op)
    return Finding(table, DEFAULT_SOURCE)


@_of(*ENGINE85)
def _release(song: Song) -> Optional[Finding]:
    return release_site(song.mem, song.base)


@_of(*ENGINE85)
def _pw_min_shift(song: Song) -> Finding:
    shift = pw_min_shift(song.mem, song.base)
    return Finding(shift, detected(_at(song, constants.PW_MIN_SHIFT_REL)))


@_of(*ENGINE85)
def _order_loop(song: Song) -> Finding:
    site = _at(song, constants.ORDER_LOOP_HANDLER_REL)
    jump = song.mem[site] == constants.ORDER_LOOP_JSR_OP
    return Finding("jump" if jump else "reset", detected(site))


@_of(*ENGINE85)
def _tail_d418(song: Song) -> Optional[Finding]:
    return tail_d418_site(song.mem, song.base)


def _onset_ctrl(song: Song) -> Optional[Finding]:
    generation = song.variant()
    if generation == "v37":
        return onset_ctrl_site(song.mem, song.base)
    site = _at(song, constants.V1D_ONSET_CTRL_REL)
    if generation in V1D and site + 1 < len(song.mem) and song.mem[site] == 0xA9:
        return Finding(song.mem[site + 1], detected(site))
    return None


@_of(*V1D)
def _onset(song: Song) -> Optional[Finding]:
    return v1d_onset_site(song.mem, song.base)


@_of(*V1D)
def _burst_imm(song: Song) -> Finding:
    site = _at(song, constants.BURST_IMM_REL)
    return Finding(song.mem[site], detected(site))


@_of("a1")
def _release_sr(song: Song) -> Optional[Finding]:
    return a1_release_sr(song.mem, song.base)


@_of("a1")
def _release_gate(song: Song) -> Optional[Finding]:
    return a1_release_gate(song.mem, song.base)


def _entry(song: Song) -> int:
    table = song.base if song.dispatch is None else song.dispatch.table
    return (table + constants.STD_PLAY_REL) & 0xFFFF


def _multispeed(song: Song) -> bool:
    return song.variant() == "nn" and _nn_wrapper_937(
        song.mem, song.base, song.play, song.init
    )


def _wrapper(song: Song) -> Finding:
    play = song.play & 0xFFFF
    if play == _entry(song):
        kind = "none"
    elif _multispeed(song):
        kind = "multispeed"
    elif wrapper_body(song, 0)[0] != song.base:
        kind = "selector"
    elif _play_wrapper_benign(song.mem, song.base, play, song.init):
        kind = "benign"
    else:
        kind = "foreign"
    return Finding(kind, detected(play))


def _divider(index: int):
    def detect(song: Song) -> Optional[Finding]:
        if not _multispeed(song):
            return None
        found = nn937_divider(song.mem, song.play, song.init)[index]
        return None if found is None else Finding(found.value, found.source)

    return detect


FACETS = FacetRegistry(
    [
        Facet("generation", "build", GENERATIONS, detector=_generation),
        Facet("byte_exact", "release", (True, False), False, detector=_byte_exact),
        Facet("layout", "layout", ("dispatch", "nn-dispatch"), detector=_layout),
        Facet("stub", "layout", default=0, detector=_stub),
        Facet("play_rel", "layout", detector=_play_rel),
        Facet("nn_shift", "layout", default=0, detector=_nn_shift),
        Facet("note_cells", "layout", detector=_note_cells),
        Facet("rest_tail", "layout", detector=_rest_tail),
        Facet("order_table", "layout", detector=_order_table),
        Facet("release", "release", ("inline", "clear", "keep"), detector=_release),
        Facet("pw_min_shift", "release", detector=_pw_min_shift),
        Facet("order_loop", "release", ("jump", "reset"), detector=_order_loop),
        Facet("tail_d418", "release", detector=_tail_d418),
        Facet(
            "onset_ctrl",
            "release",
            default=constants.V37_ONSET_CTRL,
            detector=_onset_ctrl,
        ),
        Facet("onset", "release", ("nop",), detector=_onset),
        Facet("burst_imm", "release", detector=_burst_imm),
        Facet("release_sr", "release", ("clear", "nop"), detector=_release_sr),
        Facet("release_gate", "release", ("mask", "nop"), detector=_release_gate),
        Facet("wrapper", "init", WRAPPERS, detector=_wrapper),
        Facet("ms_reload", "init", default=1, detector=_divider(0)),
        Facet("ms_seed", "init", default=1, detector=_divider(1)),
    ]
)


def facet_sites(song: Song) -> dict:
    """``{addr: facet}`` of every facet of ``song`` detected at a code address."""
    found = song.facets()
    sites = {}
    for name in found:
        source = found.source(name)
        if source.kind == DETECTED and source.addr is not None:
            sites[source.addr] = name
    return sites


__all__ = ["ENGINE85", "FACETS", "GENERATIONS", "V1D", "WRAPPERS", "facet_sites"]
