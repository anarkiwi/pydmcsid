"""The DMC song-stream grammar (:file:`dmc.lark`) and the streams a tune's player reads.

The play routine walks three kinds of stream: per-voice order lists, patterns and
stride-11 instrument records.  :data:`GRAMMAR` parses each as a
:class:`~pysidtracker.bytegrammar.ByteGrammar` start rule, so a malformed stream is
a :class:`~pydmcsid.errors.DmcParseError` rather than being mis-played.
:func:`song_streams` lists the streams one subtune's voices reach.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterator, List, Set, Tuple

from lark import Token, Transformer
from pysidtracker.bytegrammar import ByteGrammar, Parsed

from pydmcsid import constants
from pydmcsid.errors import DmcParseError

INSTRUMENT_SIZE = 11
VOICES = 3
ORDER_STRIDE = 8


@dataclass(frozen=True)
class Item:
    """One grammar item: its kind and the bytes it spans."""

    kind: str
    data: bytes


@dataclass(frozen=True)
class Stream:
    """A parsed stream: its ``kind`` and items; ``to_bytes`` is the stream's bytes."""

    kind: str
    items: Tuple[Item, ...]

    def to_bytes(self) -> bytes:
        """The stream's bytes, terminator included."""
        return b"".join(item.data for item in self.items)

    def numbers(self, *kinds: str) -> List[int]:
        """The last byte of every item of the given ``kinds``, in stream order."""
        return [item.data[-1] for item in self.items if item.kind in kinds]


def _flat(children) -> Iterator[Item]:
    for child in children:
        if isinstance(child, Item):
            yield child
        elif isinstance(child, tuple):
            yield from _flat(child)
        elif isinstance(child, Token):
            yield Item(child.type, bytes([ord(child)]))


def _merge(kind: str, items) -> Item:
    items = tuple(_flat(items))
    return Item(kind, b"".join(item.data for item in items))


class _Build(Transformer):
    """Builds each start rule's :class:`~pysidtracker.bytegrammar.Parsed`."""

    def _stream(self, kind: str, children) -> Parsed:
        items = tuple(_flat(children))
        stream = Stream(kind, items)
        return Parsed(stream, len(stream.to_bytes()))

    def order(self, children):
        """An order list."""
        return self._stream("order", children)

    def order_jump(self, children):
        """An order list whose loop entry carries a target index."""
        return self._stream("order_jump", children)

    def pattern37(self, children):
        """An init-$37 pattern."""
        return self._stream("pattern37", children)

    def pattern1d(self, children):
        """An init-$1d pattern."""
        return self._stream("pattern1d", children)

    def instrument(self, children):
        """An instrument record."""
        return self._stream("instrument", children)

    def order_jump_loop(self, children):
        """A loop entry with its target index."""
        return _merge("ORDER_LOOP", children)

    def order_item(self, children):
        """A pattern number, or a transpose byte and its pattern number."""
        items = tuple(_flat(children))
        if len(items) == 1:
            return items[0]
        return Item("ORDER_TRANSPOSE", b"".join(item.data for item in items))

    def order_x(self, children):
        """An $a1/$95 order list."""
        return self._stream("order_x", children)

    def pattern_a1(self, children):
        """An $a1 pattern."""
        return self._stream("pattern_a1", children)

    def pattern95(self, children):
        """A $95 pattern."""
        return self._stream("pattern95", children)

    def instrument8(self, children):
        """A stride-8 instrument record."""
        return self._stream("instrument8", children)

    order_item_x = order_item

    def row37(self, children):
        """A row: a note, rest, tie or slide with its operand bytes."""
        return _merge(_row_kind(children), children)

    row1d = row_a1 = row_95 = row37

    def prefix37(self, children):
        """An instrument-select or duration byte."""
        return _merge(next(_flat(children)).kind, children)

    prefix1d = prefix_a1 = prefix_95 = prefix37


def _row_kind(children) -> str:
    return next(_flat(children)).kind


GRAMMAR = ByteGrammar(
    "pydmcsid",
    "dmc.lark",
    start=(
        "order",
        "order_jump",
        "pattern37",
        "pattern1d",
        "instrument",
        "order_x",
        "pattern_a1",
        "pattern95",
        "instrument8",
    ),
    transformer=_Build(),
    error=DmcParseError,
)

_ORDER_KINDS = ("ORDER_PATTERN", "ORDER_TRANSPOSE")


@dataclass(frozen=True)
class Engine:
    """Where one body generation keeps the streams its voices walk.

    ``order_ptr`` are the authored addresses of the per-voice order-list pointer
    low/high cells, ``order_index`` and ``pattern_index`` those of the per-voice
    order and pattern cursors; ``pattern_lo``/``pattern_hi``/``instrument`` name
    the player's table-base attributes.
    """

    pattern: str
    instrument: str
    instrument_size: int
    order_ptr: Tuple[int, int]
    order_index: int
    pattern_index: int
    pattern_lo: str
    pattern_hi: str
    instrument_base: str
    order: str = ""


_V37 = Engine(
    "pattern37",
    "instrument",
    INSTRUMENT_SIZE,
    (constants.ORDER_PTR_LO, constants.ORDER_PTR_HI),
    0x1726,
    0x1729,
    "b_pat_lo",
    "b_pat_hi",
    "b_instr",
)
ENGINES = {
    "v37": _V37,
    "v1d": replace(_V37, pattern="pattern1d"),
    "nn": replace(_V37, pattern="pattern1d"),
    "a1": Engine(
        "pattern_a1",
        "instrument8",
        8,
        (0x17CF, 0x17D2),
        0x17D5,
        0x17D8,
        "b_patlo",
        "b_pathi",
        "b_inst",
        order="order_x",
    ),
    "n95": Engine(
        "pattern95",
        "instrument8",
        8,
        (0x17D9, 0x17DC),
        0x17DF,
        0x17E2,
        "b_patlo",
        "b_pathi",
        "b_inst",
        order="order_x",
    ),
}
_INSTRUMENT_PICKS = {
    "INST37": lambda data: data[0] & 0x1F,
    "INST1D": lambda data: data[0] & 0x1F,
    "PARAM_A1": lambda data: data[1] if data[0] == 0xFC else None,
    "PARAM_95": lambda data: data[1] if data[0] == 0xFC else None,
}


def order_rule(build, generation: str = "v37") -> str:
    """The order-list start rule for a body ``generation`` and its build facets."""
    if ENGINES[generation].order:
        return ENGINES[generation].order
    return "order_jump" if build["order_loop"] == "jump" else "order"


def pattern_rule(generation: str) -> str:
    """The pattern start rule of a body ``generation``."""
    return ENGINES[generation].pattern


def modelled(generation) -> bool:
    """Whether :func:`song_streams` has a grammar for this body generation."""
    return generation in ENGINES


def instrument_numbers(stream: Stream) -> Set[int]:
    """The instrument numbers a pattern ``stream`` selects."""
    found = (
        _INSTRUMENT_PICKS[item.kind](item.data)
        for item in stream.items
        if item.kind in _INSTRUMENT_PICKS
    )
    return {number for number in found if number is not None}


def _word(mem, lo: int, hi: int, index: int) -> int:
    return mem[(lo + index) & 0xFFFF] | (mem[(hi + index) & 0xFFFF] << 8)


def _read(mem, addr: int, rule: str, what: str) -> Parsed:
    window = bytes(mem[addr : addr + 0x100])
    try:
        return GRAMMAR.parse_prefix(window, rule)
    except DmcParseError as exc:
        raise DmcParseError(f"{what} at {addr:#06x}: {exc}") from exc


def song_streams(song, subtune: int = 0) -> Iterator[Tuple[str, Stream, str]]:
    """``(name, stream, rule)`` for every stream ``subtune``'s voices reach.

    Walks each voice's order list from the post-init order-list pointer cells, then
    the patterns it names and the instruments those select, in the memory the
    play routine reads.  Yields nothing for a body generation without a grammar
    (see :func:`modelled`); a stream that does not parse raises
    :class:`~pydmcsid.errors.DmcParseError`.
    """
    # pylint: disable=import-outside-toplevel
    from pydmcsid.player import DmcPlayer

    player = DmcPlayer(song, subtune)
    generation = player.song.variant()
    if not modelled(generation):
        return
    engine = ENGINES[generation]
    mem = bytes(player.m)
    build = player.song.facets()
    order_name = order_rule(build, generation)
    patterns: Set[int] = set()
    instruments: Set[int] = set()
    for voice in range(VOICES):
        lo, hi = engine.order_ptr
        ptr = mem[player._a(lo) + voice] | (mem[player._a(hi) + voice] << 8)
        order = _read(mem, ptr, order_name, f"voice {voice} order list")
        yield f"voice{voice}.order", order.model, order_name
        patterns.update(order.model.numbers(*_ORDER_KINDS))
    for number in sorted(patterns):
        addr = _word(
            mem,
            getattr(player, engine.pattern_lo),
            getattr(player, engine.pattern_hi),
            number,
        )
        parsed = _read(mem, addr, engine.pattern, f"pattern {number}")
        yield f"pattern{number}", parsed.model, engine.pattern
        instruments.update(instrument_numbers(parsed.model))
    for number in sorted(instruments):
        addr = (
            getattr(player, engine.instrument_base) + number * engine.instrument_size
        ) & 0xFFFF
        record = _read(mem, addr, engine.instrument, f"instrument {number}")
        yield f"instrument{number}", record.model, engine.instrument
