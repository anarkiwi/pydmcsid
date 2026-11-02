"""The player's entries other than the play call: a tune's own code calls the routines that
run one voice, and the spec plays them as the 6502 does."""

import dataclasses

import pytest

import pydmcsid
from pydmcsid import DmcPlayer, constants
from pydmcsid.spec import VOICE_CALLS
from pydmcsid.variables import A1, N95, aliases
from pysidtracker.oracle import EmuPlayer
from pysidtracker.testing import resolve_tune

from helpers import TUNES

STUB = 0x20
FRAMES = 60
ROWS = 16

CALLS = [
    (generation, rel, voice)
    for generation, calls in VOICE_CALLS.items()
    for rel in calls
    for voice in range(3)
]
TUNE = {
    "v37": "ode",
    "v1d": "glorious",
    "nn": "day_noter",
    "a1": "katusha",
    "n95": "happy_rave",
}
VARIABLES = {"a1": A1, "n95": N95}
TABLES = {
    "a1": (constants.A1_FREQ_HI_REL, "wtarg", "wtctrl"),
    "n95": (constants.N95_FREQ_HI_REL, "wtarg", "wtctrl"),
}


def _calling(song, rel, voice, plant=None):
    """``song`` whose play routine is a stub calling the routine at ``rel`` for ``voice``
    (``LDX #voice / JSR base+rel / RTS`` in the title area), after ``plant`` edits the image.
    """
    mem = bytearray(song.mem)
    at, target = song.base + STUB, song.base + rel
    mem[at : at + 6] = bytes((0xA2, voice, 0x20, target & 0xFF, target >> 8, 0x60))
    if plant:
        plant(mem)
    return dataclasses.replace(song, mem=mem, play=at)


def _grids(song, frames=FRAMES):
    image = bytes(song.mem[song.load : song.load + song.image_len])
    emu = EmuPlayer(image, song.load, song.init, song.play, 0)
    return DmcPlayer(song).render_grid(frames), emu.render_grid(frames)


def _song(generation):
    path = resolve_tune(TUNES[TUNE[generation]])
    assert path is not None, "tune unreachable"
    song = pydmcsid.read(path)
    assert song.variant() == generation
    return song


@pytest.mark.parametrize("generation,rel,voice", CALLS)
def test_a_voice_routine_called_by_the_tune_plays_as_the_6502(generation, rel, voice):
    """A tune whose play routine calls only a voice's routine plays the same registers as
    the 6502 running that call."""
    song = _song(generation)
    calling = _calling(song, rel, voice)
    player = DmcPlayer(calling)
    got = player.render_grid(FRAMES)
    assert player.entered == [calling.base + rel]
    assert got == _grids(calling)[1]


@pytest.mark.parametrize("generation", sorted(VARIABLES))
def test_a_table_read_past_its_end_reads_the_variable_it_lands_on(generation):
    """The wave program reads a pitch from beyond the frequency table, onto voice 0's wave
    position; the spec returns the position as the call has left it, as the 6502 does.
    """
    song = _song(generation)
    rel_hi, arg_table, ctrl_table = TABLES[generation]
    variables = VARIABLES[generation]
    player = DmcPlayer(song)
    layout = player.layout
    key = player.view()[song.base + next(v for v in variables if v.name == "key").at[0]]
    offset = next(
        off
        for _ident, expr, off, voice in aliases(variables)
        if voice == 0 and expr == "voice[k].wave_pos"
    )
    index = song.base + offset - (song.base + rel_hi)
    assert 0 < index < 256

    def plant(mem):
        for row in range(ROWS):
            mem[layout[ctrl_table] + row] = 0
            mem[layout[arg_table] + row] = (index - key) & 0xFF

    plant_song = _calling(song, constants_wave_rel(generation), 0, plant)
    got, want = _grids(plant_song, ROWS - 2)
    assert got == want
    assert len({row[1] for row in got}) > 2


def constants_wave_rel(generation):
    """The offset of the generation's wave-step routine."""
    return {"a1": constants.A1_WAVE_REL, "n95": constants.N95_WAVE_REL}[generation]
