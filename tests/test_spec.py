"""The playspec of the DMC bodies: static checks and agreement with the player."""

import pytest

from pysidtracker import song_frames
from pysidtracker.oracle import EmuPlayer
from pysidtracker.playspec import elaborate
from pysidtracker.playspec.tv import validate

import pydmcsid
from pydmcsid import DmcPlayer
from pydmcsid.spec import GENERATIONS, binding, spec, supported

STACK = slice(0x100, 0x200)


def _song(tune_path):
    song = pydmcsid.read(tune_path)
    if not supported(song):
        pytest.skip("generation not stated by the spec yet")
    return song


def _emu(song, subtune):
    image = bytes(song.mem[song.load : song.load + song.image_len])
    return EmuPlayer(image, song.load, song.init, song.play, subtune)


def _equal_outside_stack(left, right):
    return (
        left[: STACK.start] == right[: STACK.start]
        and left[STACK.stop :] == right[STACK.stop :]
    )


def test_spec_elaborates_for_every_layout(tune_path):
    """The spec passes its static checks against each tune's own layout."""
    song = _song(tune_path)
    layout, body, _ = binding(song)
    assert elaborate(spec(body.variant()), layout).name.startswith("dmc")


def test_player_grid_matches_the_6502(tune_path):
    """The register grid equals the tune's own 6502 run, and every stream the spec
    opens parses."""
    song = _song(tune_path)
    subtune = song.start_song - 1
    got = DmcPlayer(song, subtune, validate=True)
    assert got.render_grid(1000) == _emu(song, subtune).render_grid(1000)
    assert not list(got.validator.failures())


def test_spec_memory_equals_the_6502(tune_path):
    """Outside the stack page the spec leaves the 64 KiB the tune's own code does."""
    song = _song(tune_path)
    subtune = song.start_song - 1
    got, want = DmcPlayer(song, subtune), _emu(song, subtune)
    for frame in range(300):
        got.play_frame()
        want.play_frame()
        assert _equal_outside_stack(got.memory, bytes(want._mem)), frame


@pytest.mark.oracle
def test_full_length_grid_matches_the_6502(tune_path):
    """Over the whole song the grid equals the tune's own 6502 run, and every
    stream the spec opens parses."""
    song = _song(tune_path)
    subtune = song.start_song - 1
    frames = song_frames(tune_path, None)
    got = DmcPlayer(song, subtune, validate=True)
    assert got.render_grid(frames) == _emu(song, subtune).render_grid(frames)
    assert not list(got.validator.failures())


@pytest.mark.oracle
def test_full_length_memory_equals_the_6502(tune_path):
    """Every call of the whole song leaves the same memory as the tune's own code."""
    song = _song(tune_path)
    subtune = song.start_song - 1
    got, want = DmcPlayer(song, subtune), _emu(song, subtune)
    for frame in range(song_frames(tune_path, None)):
        got.play_frame()
        want.play_frame()
        assert _equal_outside_stack(got.memory, bytes(want._mem)), frame


@pytest.mark.oracle
@pytest.mark.parametrize("voice", [0, 1, 2])
def test_row_reader_equals_the_6502_for_all_memory(tune_path, voice):
    """The spec's note-row reader equals the tune's 6502 routine at ``base+$1a2``
    (note + transpose, frequency lookup, onset write, end-of-pattern peek) for every
    initial memory."""
    song = _song(tune_path)
    if song.variant() != "v37":
        pytest.skip("the row reader sits at base+$1a2 in the init-$37 body")
    layout, body, _ = binding(song)
    view = DmcPlayer(song, song.start_song - 1).view()
    result = validate(
        spec(body.variant()),
        layout,
        f"voice[{voice}].row_note",
        view,
        body.base + 0x1A2,
        registers={"X": voice},
    )
    assert result.proven, result.counterexample


def test_supported_generations():
    """The specs state the init-$37, init-$1d, $94a, $a1 and $95 bodies."""
    assert set(GENERATIONS) == {"v37", "v1d", "nn", "a1", "n95"}
