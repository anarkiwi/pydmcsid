"""The DMC song-stream grammar (:file:`dmc.lark`) and the streams a tune's player reads.

The play routine walks three kinds of stream: per-voice order lists, patterns and
stride-11 instrument records.  :data:`GRAMMAR` parses each as a
:class:`~pysidtracker.bytegrammar.ByteGrammar` start rule, so a malformed stream is
a :class:`~pydmcsid.errors.DmcParseError` rather than being mis-played.
:func:`song_streams` lists the streams one subtune's voices reach.
"""

from __future__ import annotations

from dataclasses import dataclass
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

    def row37(self, children):
        """A row: a note, rest, tie or slide with its operand bytes."""
        return _merge(_row_kind(children), children)

    row1d = row37

    def prefix37(self, children):
        """An instrument-select or duration byte."""
        return _merge(next(_flat(children)).kind, children)

    prefix1d = prefix37


def _row_kind(children) -> str:
    return next(_flat(children)).kind


GRAMMAR = ByteGrammar(
    "pydmcsid",
    "dmc.lark",
    start=("order", "order_jump", "pattern37", "pattern1d", "instrument"),
    transformer=_Build(),
    error=DmcParseError,
)

_ORDER_KINDS = ("ORDER_PATTERN", "ORDER_TRANSPOSE")
_PATTERN_RULES = {"v37": "pattern37", "v1d": "pattern1d", "nn": "pattern1d"}


def order_rule(build) -> str:
    """The order-list start rule for a build's ``order_loop`` facet."""
    return "order_jump" if build["order_loop"] == "jump" else "order"


def pattern_rule(generation: str) -> str:
    """The pattern start rule of a body ``generation`` (``v37``/``v1d``/``nn``)."""
    return _PATTERN_RULES[generation]


def modelled(generation) -> bool:
    """Whether :func:`song_streams` has a grammar for this body generation."""
    return generation in _PATTERN_RULES


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
    mem = bytes(player.m)
    build = player.song.facets()
    pattern_rule_ = pattern_rule(generation)
    patterns: Set[int] = set()
    instruments: Set[int] = set()
    for voice in range(VOICES):
        ptr = mem[player._a(constants.ORDER_PTR_LO) + voice] | (
            mem[player._a(constants.ORDER_PTR_HI) + voice] << 8
        )
        what = f"voice {voice} order list"
        order = _read(mem, ptr, order_rule(build), what)
        yield f"voice{voice}.order", order.model, order_rule(build)
        patterns.update(order.model.numbers(*_ORDER_KINDS))
    for number in sorted(patterns):
        addr = _word(mem, player.b_pat_lo, player.b_pat_hi, number)
        parsed = _read(mem, addr, pattern_rule_, f"pattern {number}")
        yield f"pattern{number}", parsed.model, pattern_rule_
        instruments.update(
            item.data[0] & 0x1F
            for item in parsed.model.items
            if item.kind in ("INST37", "INST1D")
        )
    for number in sorted(instruments):
        addr = (player.b_instr + number * INSTRUMENT_SIZE) & 0xFFFF
        yield (
            f"instrument{number}",
            _read(mem, addr, "instrument", f"instrument {number}").model,
            "instrument",
        )
