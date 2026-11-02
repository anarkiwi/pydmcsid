"""The playspec of the DMC bodies: static checks and agreement with the player."""

from pathlib import Path

import pytest

from pysidtracker import song_frames
from pysidtracker.oracle import EmuPlayer
from pysidtracker.testing import resolve_tune

import pydmcsid
from pydmcsid import DmcPlayer
from pydmcsid.errors import DmcFormatError
from pydmcsid.format import dmc_format
from pydmcsid.spec import GENERATIONS, spec, supported, variables_of


def _song(tune_path):
    song = pydmcsid.read(tune_path)
    if not supported(song):
        pytest.skip("generation not stated by the spec yet")
    return song


def _emu(song, subtune):
    image = bytes(song.mem[song.load : song.load + song.image_len])
    return EmuPlayer(image, song.load, song.init, song.play, subtune)


def test_spec_elaborates_for_every_layout(tune_path):
    """The spec passes its static checks against each tune's own binding."""
    song = _song(tune_path)
    player = DmcPlayer(song)
    player.play_frame()
    assert player.program().name.startswith("dmc")


def test_player_grid_matches_the_6502(tune_path):
    """The register grid equals the tune's own 6502 run, and every stream the spec
    opens parses."""
    song = _song(tune_path)
    subtune = song.start_song - 1
    got = DmcPlayer(song, subtune, validate=True)
    assert got.render_grid(1000) == _emu(song, subtune).render_grid(1000)
    assert not list(got.validator.failures())


def _agree_with_the_6502(song, frames):
    """The first ``(frame, variable)`` whose state differs from the byte(s) the tune's
    own code keeps, or None."""
    subtune = song.start_song - 1
    got, want = DmcPlayer(song, subtune), _emu(song, subtune)
    variables = variables_of(got.song)
    for frame in range(frames):
        got.play_frame()
        want.play_frame()
        for var in variables:
            for voice in range(3 if var.voices else 1):
                key = f"voice[{voice}].{var.name}" if var.voices else var.name
                seen = sum(
                    want._mem[(got.base + at + voice) & 0xFFFF] << 8 * lane
                    for lane, at in enumerate(var.at)
                )
                have = got.state(key)
                if var.name == "trans" and var.voices and "down" in var.exprs()[0]:
                    have = (have - got.state(f"voice[{voice}].down")) & 0xFF
                if have != seen:
                    return frame, key, have, seen
    return None


def test_state_equals_the_6502_variables(tune_path):
    """Every call leaves each player variable the spec keeps as the byte(s) the tune's own
    code keeps in its work area."""
    assert _agree_with_the_6502(_song(tune_path), 300) is None


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
def test_full_length_state_equals_the_6502(tune_path):
    """Over the whole song every call leaves the variables as the tune's own code does."""
    song = _song(tune_path)
    assert _agree_with_the_6502(song, song_frames(tune_path, None)) is None


def test_supported_generations():
    """The specs state the init-$37, init-$1d, $94a, $a1 and $95 bodies."""
    assert set(GENERATIONS) == {"v37", "v1d", "nn", "a1", "n95"}


def test_format_exports_the_spec(tune_path):
    """The format's ``spec`` hook binds the packaged .play and plays as DmcPlayer."""
    song = _song(tune_path)
    bound = dmc_format().spec(song)
    assert bound.spec is spec(song.variant())
    assert dmc_format().spec_player(song).render_grid(300) == (
        DmcPlayer(song).render_grid(300)
    )


REFUSED = Path(__file__).parent / "fixtures" / "dmc_refused.txt"


@pytest.mark.parametrize(
    "relpath",
    [
        line.strip()
        for line in REFUSED.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ],
)
def test_a_play_call_running_the_tunes_own_voice_code_is_refused(relpath):
    """The listed tunes call code of their own for a voice, which the spec cannot state."""
    path = resolve_tune(relpath)
    assert path is not None
    with pytest.raises(DmcFormatError, match="code of the tune's own"):
        DmcPlayer(pydmcsid.read(path))
