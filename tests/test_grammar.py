"""The DMC song-stream grammar: what it accepts, and agreement with the player."""

import pytest

import pydmcsid
from pydmcsid import DmcPlayer
from pydmcsid.errors import DmcParseError
from pydmcsid.grammar import GRAMMAR, modelled, order_rule, pattern_rule

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


def test_modelled_generations():
    """The grammar covers the init-$37/$1d bodies and the $94a family."""
    assert [g for g in ("v37", "v1d", "a1", "n95", "nn") if modelled(g)] == [
        "v37",
        "v1d",
        "nn",
    ]


def test_streams_of_a_real_tune_round_trip(tune_path):
    """Every reachable stream parses and re-serialises to the same stream."""
    song = pydmcsid.read(tune_path)
    streams = song.streams()
    if not modelled(song.variant()):
        assert not streams
        return
    kinds = {rule for _, _, rule in streams}
    assert order_rule(song.facets()) in kinds
    assert pattern_rule(song.variant()) in kinds
    assert "instrument" in kinds
    GRAMMAR.check(streams, DmcParseError)


def _boundaries(stream):
    """Byte offsets at which each item of ``stream`` starts."""
    offset, starts = 0, []
    for item in stream.items:
        starts.append(offset)
        offset += len(item.data)
    return starts


def test_player_cursors_stay_on_stream_items(tune_path):
    """The player's order and pattern cursors only ever sit where the grammar
    says an item starts (or, for a transposed order entry, at its pattern byte)."""
    song = pydmcsid.read(tune_path)
    if not modelled(song.variant()):
        pytest.skip("no grammar for this generation")
    player = DmcPlayer(song)
    mem = player.m
    build = player.song.facets()
    pat_rule = pattern_rule(player.song.variant())
    seen = 0
    for _ in range(600):
        player.play_frame()
        for voice in range(3):
            f8, f9 = player._curpat[voice]  # pylint: disable=protected-access
            ptr = f8 | f9 << 8
            if not ptr:
                continue
            pattern = GRAMMAR.parse_prefix(bytes(mem[ptr : ptr + 0x100]), pat_rule)
            cursor = mem[player._a(0x1729) + voice]  # pylint: disable=protected-access
            assert cursor in _boundaries(pattern.model)
            order_ptr = mem[player._a(0x1707) + voice] | (
                mem[player._a(0x170A) + voice] << 8
            )
            order = GRAMMAR.parse_prefix(
                bytes(mem[order_ptr : order_ptr + 0x100]), order_rule(build)
            )
            ok = set(_boundaries(order.model))
            ok |= {
                start + len(item.data) - 1
                for start, item in zip(_boundaries(order.model), order.model.items)
            }
            assert mem[player._a(0x1726) + voice] in ok
            seen += 1
    assert seen


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
    mem = player.m
    assert not any(mem[0x0800:0x0810])
    mem[0x0800:0x0803] = bytes((byte, 0x00, 0xFE))
    mem[player._a(0x1726)] = 0  # pylint: disable=protected-access
    player._orderwalk(0, 0x00, 0x08)  # pylint: disable=protected-access
    assert mem[player._a(0x172C)] == transpose  # pylint: disable=protected-access


def test_wavetable_control_steps_position_back(tune_path):
    """A wavetable control byte of ``$90+n`` steps the position back by ``n``."""
    song = pydmcsid.read(tune_path)
    if song.variant() not in ("v37", "v1d"):
        pytest.skip("covered on the v37/v1d bodies")
    player = DmcPlayer(song)
    mem = player.m
    ctrl = player.b_arp_ctrl
    mem[ctrl + 3], mem[ctrl + 5] = 0x20, 0x92
    cell = player._a(0x177A)  # pylint: disable=protected-access
    mem[cell] = 5
    player._compose_1598(0)  # pylint: disable=protected-access
    assert mem[cell] == 4
    assert mem[player._a(0x1780)] == 0x20  # pylint: disable=protected-access


def test_order_loop_target_follows_the_build(tune_path):
    """A ``jump`` build's loop entry carries a target the player jumps to; a
    ``reset`` build's does not and restarts at index 0."""
    song = pydmcsid.read(tune_path)
    if song.variant() not in ("v37", "v1d"):
        pytest.skip("covered on the v37/v1d bodies")
    player = DmcPlayer(song)
    jump = order_rule(player.song.facets()) == "order_jump"
    mem = player.m
    mem[0x0800:0x0803] = bytes((0xFF, 0x07, 0x00))
    result = player._order_loop(0x00, 0x08, 0)  # pylint: disable=protected-access
    assert result == (0x07 if jump else 0)
