"""The DMC song-stream grammar: what it accepts, and agreement with the player."""

import pytest

import pydmcsid
from pydmcsid import DmcPlayer
from pydmcsid.errors import DmcParseError
from pysidtracker.playspec import elaborate, run

from pydmcsid.grammar import ENGINES, GRAMMAR, modelled, order_rule, pattern_rule
from pydmcsid.spec import binding, spec

MODELLED = ("v37", "v1d", "nn")


def _kinds(rule, data):
    return [item.kind for item in GRAMMAR.parse_exact(data, rule).items]


def test_order_transpose_takes_any_pattern_byte():
    """A transpose byte is followed by a pattern number that is never range-checked."""
    items = _kinds("order", b"\x00\x85\xf0\xff")
    assert items == ["ORDER_PATTERN", "ORDER_TRANSPOSE", "ORDER_LOOP"]


def test_order_loop_target_only_in_jump_builds():
    """Only a ``jump`` build's ``$ff`` carries a target index."""
    assert GRAMMAR.parse_prefix(b"\x01\xff\x00", "order").size == 2
    assert GRAMMAR.parse_prefix(b"\x01\xff\x00", "order_jump").size == 3


def test_stream_size_excludes_the_tail():
    """Bytes after the terminator are never part of the stream."""
    assert GRAMMAR.parse_prefix(b"\x01\xfe" + bytes(range(40)), "order").size == 2


@pytest.mark.parametrize(
    "rule,data",
    [
        ("order", b"\x00\x01"),
        ("order", b""),
        ("pattern37", b"\x30\x31"),
        ("pattern37", b"\xff"),
        ("pattern37", b"\x61\xff"),
        ("pattern1d", b"\x7f"),
        ("pattern1d", b"\x30\xc0\x30\x7f"),
        ("instrument", bytes(10)),
    ],
)
def test_malformed_streams_are_parse_errors(rule, data):
    """A stream that is unterminated, empty or truncated does not parse."""
    with pytest.raises(DmcParseError):
        GRAMMAR.parse_prefix(data, rule)


def test_slide_operands_follow_the_command_bit():
    """Slide ``$c0..$cf`` carries a start and a target note, ``$d0..$df`` a target."""
    assert _kinds("pattern37", b"\xc0\x30\x35\xd0\x35\xff") == [
        "SLIDE_NOTE37",
        "SLIDE_REST37",
        "END37",
    ]


def test_pattern_markers_differ_by_generation():
    """``$fe``/``$fd``/``$ff`` in the init-$37 body are ``$7e``/``$7d``/``$7f`` in $1d."""
    assert _kinds("pattern37", b"\xfe\xfd\xff") == ["REST37", "TIE37", "END37"]
    assert _kinds("pattern1d", b"\x7e\x7d\x7f") == ["REST1D", "TIE1D", "END1D"]
    with pytest.raises(DmcParseError):
        GRAMMAR.parse_exact(b"\x7e\x7d\x7f", "pattern37")


def test_pattern1d_prefixes():
    """The init-$1d body adds legato ($7c) and volume ($f0..$ff) prefixes."""
    assert _kinds("pattern1d", b"\x7c\xf3\x30\x7f") == [
        "LEGATO1D",
        "VOLUME1D",
        "NOTE1D",
        "END1D",
    ]


def test_a1_and_95_patterns_differ_in_their_commands():
    """``$f0``/``$ef`` take an operand only in the $95 engine."""
    assert _kinds("pattern95", b"\xf0\x12\x30\xff") == ["PARAM_95", "NOTE_X", "END_X"]
    with pytest.raises(DmcParseError):
        GRAMMAR.parse_exact(b"\xf0\x12\x30\xff", "pattern_a1")


def test_x_order_transpose_carries_a_value_then_the_pattern():
    """The $a1/$95 transpose commands ($fd, $fc) carry a value, then a pattern."""
    assert _kinds("order_x", b"\xfd\x05\x01\xfc\x02\x03\xfe") == [
        "ORDER_TRANSPOSE",
        "ORDER_TRANSPOSE",
        "ORDER_STOP",
    ]
    assert GRAMMAR.parse_prefix(b"\x01\xff\x00\x09", "order_x").size == 3


def test_every_generation_is_modelled():
    """All five body generations have a grammar."""
    assert all(modelled(g) for g in ("v37", "v1d", "a1", "n95", "nn"))
    assert not modelled(None)


def test_streams_of_a_real_tune_round_trip(tune_path):
    """Every reachable stream parses and re-serialises to the same stream."""
    song = pydmcsid.read(tune_path)
    streams = song.streams()
    if not modelled(song.variant()):
        assert not streams
        return
    kinds = {rule for _, _, rule in streams}
    assert order_rule(song.facets(), song.variant()) in kinds
    assert pattern_rule(song.variant()) in kinds
    assert ENGINES[song.variant()].instrument in kinds
    GRAMMAR.check(streams, DmcParseError)


def _boundaries(stream):
    """Byte offsets at which each item of ``stream`` starts."""
    offset, starts = 0, []
    for item in stream.items:
        starts.append(offset)
        offset += len(item.data)
    return starts


def _entry_start(order, index):
    """The item of ``order`` holding byte ``index`` as ``(start, item)``."""
    for start, item in zip(_boundaries(order), order.items):
        if start <= index < start + len(item.data):
            return start, item
    return None, None


def test_player_cursors_stay_on_stream_items(tune_path):
    """The player's order and pattern cursors only ever sit where the grammar
    says an item starts (or, for a transposed order entry, at its pattern byte)."""
    song = pydmcsid.read(tune_path)
    generation = song.variant()
    player = DmcPlayer(song)
    engine = ENGINES[generation]
    layout = player.layout
    rel = player.base - 0x1000
    order_name = order_rule(player.song.facets(), generation)
    seen = 0
    for _ in range(600):
        player.play_frame()
        mem = player.memory
        for voice in range(3):
            lo, hi = (cell + rel + voice for cell in engine.order_ptr)
            order_ptr = mem[lo] | (mem[hi] << 8)
            order = GRAMMAR.parse_prefix(
                bytes(mem[order_ptr : order_ptr + 0x100]), order_name
            ).model
            index = mem[engine.order_index + rel + voice]
            start, item = _entry_start(order, index)
            assert item is not None
            assert index in (start, start + len(item.data) - 1)
            if item.kind in ("ORDER_LOOP", "ORDER_STOP"):
                continue
            number = item.data[-1]
            ptr = mem[layout[engine.pattern_lo] + number] | (
                mem[layout[engine.pattern_hi] + number] << 8
            )
            pattern = GRAMMAR.parse_prefix(
                bytes(mem[ptr : ptr + 0x100]), engine.pattern
            ).model
            assert mem[engine.pattern_index + rel + voice] in _boundaries(pattern)
            seen += 1
    assert seen


def _run_routine(song, routine, memory, patch):
    """``routine`` of the song's spec run once on ``memory`` after ``patch``."""
    layout, body, _ = binding(song)
    program = elaborate(spec(body.variant()), layout, routine=routine)
    mem = bytearray(memory)
    patch(mem, layout, body.base - 0x1000)
    run(program, mem)
    return mem, layout


ORDER_AT = 0x0800


def _order_list(data):
    def patch(mem, _layout, rel):
        mem[ORDER_AT : ORDER_AT + len(data)] = bytes(data)
        mem[0x1707 + rel], mem[0x170A + rel] = ORDER_AT & 0xFF, ORDER_AT >> 8
        mem[0x1726 + rel] = len(data) - 1 if data[-1] in (0xFF, 0xFE) else 0

    return patch


@pytest.mark.parametrize(
    "byte,transpose",
    [(0xA5, 5), (0xFD, 93), (0x80, 0), (0x81, 0xFF), (0x9F, 0xE1)],
)
def test_order_transpose_byte_sets_note_transpose(tune_path, byte, transpose):
    """An order-list transpose byte sets the voice transpose the docs describe."""
    song = pydmcsid.read(tune_path)
    if song.variant() not in ("v37", "v1d"):
        pytest.skip("covered on the v37/v1d bodies")
    player = DmcPlayer(song)
    patch = _order_list([byte, 0x00, 0xFE])
    mem, _ = _run_routine(song, "voice[0].orderwalk", player.memory, patch)
    assert mem[0x172C + player.base - 0x1000] == transpose


def test_wavetable_control_steps_position_back(tune_path):
    """A wavetable control byte of ``$90+n`` steps the position back by ``n``."""
    song = pydmcsid.read(tune_path)
    if song.variant() not in ("v37", "v1d"):
        pytest.skip("covered on the v37/v1d bodies")
    player = DmcPlayer(song)

    def patch(mem, layout, rel):
        mem[layout["arp_ctrl"] + 3], mem[layout["arp_ctrl"] + 5] = 0x20, 0x92
        mem[0x177A + rel] = 5

    mem, _ = _run_routine(song, "voice[0].compose", player.memory, patch)
    rel = player.base - 0x1000
    assert mem[0x177A + rel] == 4
    assert mem[0x1780 + rel] == 0x20


def test_order_loop_target_follows_the_build(tune_path):
    """A ``jump`` build's loop entry carries a target the player jumps to; a
    ``reset`` build's does not and restarts at index 0."""
    song = pydmcsid.read(tune_path)
    if song.variant() not in ("v37", "v1d"):
        pytest.skip("covered on the v37/v1d bodies")
    player = DmcPlayer(song)
    jump = order_rule(player.song.facets(), song.variant()) == "order_jump"
    ordered = [0x00, 0x01, 0xFF, 0x01] if jump else [0x00, 0x01, 0xFF]
    mem, layout = _run_routine(
        song, "voice[0].orderwalk", player.memory, _order_list(ordered)
    )
    number = 1 if jump else 0
    pointer = mem[layout["patlo"] + number] | (mem[layout["pathi"] + number] << 8)
    assert mem[layout["zp"]] | (mem[layout["zp"] + 1] << 8) == pointer
