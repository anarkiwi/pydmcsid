"""The DMC playroutine as :mod:`pysidtracker.playspec` specs, bound to a build.

``specs/dmc.play`` states the init-``$37``, init-``$1d`` and ``$94a`` bodies,
``specs/dmc_a1.play`` the ``$a1`` engine and ``specs/dmc_95.play`` the ``$95``
engine; this module binds each spec's ``layout`` from the song's
:mod:`~pydmcsid.variant` facets and the player-code operands, and
:class:`~pydmcsid.player.DmcPlayer` runs them.
"""

from __future__ import annotations

import dataclasses
from functools import lru_cache
from importlib import resources
from typing import Any, Dict, Iterator, List, Tuple

from pysidtracker.formats import SpecBinding
from pysidtracker.playspec import parse as parse_spec

from pydmcsid import constants
from pydmcsid.errors import DmcFormatError
from pydmcsid.variables import VARIABLES, Var, dmc, state_layout
from pydmcsid.reader import Song, _operand, _play_body_ok, wrapper_body

SPEC_FILES = {
    "v37": "dmc.play",
    "v1d": "dmc.play",
    "nn": "dmc.play",
    "a1": "dmc_a1.play",
    "n95": "dmc_95.play",
}
GENERATIONS = frozenset(SPEC_FILES)
V1D_BODIES = frozenset({"v1d", "nn"})
FAMILIES = {"v37": "dmc", "v1d": "dmc", "nn": "dmc"}
NOTE_CELLS = (0x1012, 0x1015, 0x1034, 0x1035, 0x1036)


@lru_cache(maxsize=None)
def _parsed(name: str):
    return parse_spec(resources.files("pydmcsid").joinpath("specs", name).read_text())


def spec(generation: str = "v37"):
    """The parsed spec stating a body ``generation``."""
    return _parsed(SPEC_FILES[generation])


def supported(song: Song) -> bool:
    """Whether a spec states this song's body generation."""
    return song.variant() in GENERATIONS


def _rebased(song: Song, subtune: int) -> Tuple[Song, int]:
    base, subtune = wrapper_body(song, subtune)
    if base != (song.base & 0xFFFF):
        song = dataclasses.replace(
            song,
            base=base,
            play=(base + constants.STD_PLAY_REL) & 0xFFFF,
            init=base,
            dispatch=None,
        )
    return song, subtune


def _note_cells(song: Song, v1d: bool) -> Tuple[int, ...]:
    """The work-area offsets of the note cells: the voices' notes and instruments, the filter
    routing mask, the vibrato phase and the bend rate."""
    if v1d:
        note = song.facets()["note_cells"] - song.base
        return note, note + 3, note + 6, note + 7, note + 8
    return tuple(cell - 0x1000 for cell in NOTE_CELLS)


def _voice_calls(song: Song) -> List[Tuple[int, int, int]]:
    """``(voice, opcode, target)`` of each call the body's unrolled loop makes, or none."""
    mem, base = song.mem, song.base
    sites = [base + constants.DMC_PLAY_LOOP_REL + 4 * voice for voice in range(3)]
    if mem[sites[0] + 3] != 0xE8 or mem[sites[1] + 3] != 0xE8:
        return []
    return [
        (voice, mem[site], mem[site + 1] | mem[site + 2] << 8)
        for voice, site in enumerate(sites)
    ]


def _play_voices(song: Song) -> List[int]:
    """The voices the play call runs: those whose call the body's unrolled loop keeps."""
    calls = _voice_calls(song)
    step = (song.base + constants.DMC_STEP_REL) & 0xFFFF
    return [v for v, op, target in calls if op in (0x20, 0x4C) and target == step] or (
        [] if calls else [0, 1, 2]
    )


def _runs_own_code(song: Song) -> bool:
    """Whether the unrolled loop calls, for a voice, code that is not a return."""
    return song.variant() in FAMILIES and any(
        op in (0x20, 0x4C)
        and target != (song.base + constants.DMC_STEP_REL) & 0xFFFF
        and song.mem[target] != 0x60
        for _voice, op, target in _voice_calls(song)
    )


def _binding85(song: Song, v1d: bool) -> Dict[str, Any]:
    build = song.facets()

    def operand(off: int) -> int:
        return _operand(song.mem, song.base, off)

    tail = build["tail_d418"]
    onset = build["onset"] != "nop" if v1d else True
    return {
        "instr": operand(constants.INSTR_OP),
        "patlo": operand(constants.PATTERN_LO_OP),
        "pathi": operand(constants.PATTERN_HI_OP),
        "freqlo": operand(constants.FREQ_LO_OP),
        "freqhi": operand(constants.FREQ_HI_OP),
        "pwtab": operand(constants.PW_TABLE_OP),
        "arp_ctrl": operand(constants.ARP_CTRL_OP),
        "arp_note": operand(constants.ARP_NOTE_OP),
        "filt_ctrl": operand(constants.FILT_CTRL_OP),
        "step_lo": operand(constants.FILT_STEP_LO_OP),
        "step_hi": operand(constants.FILT_STEP_HI_OP),
        "vibseed": operand(constants.VIB_SEED_OP) if v1d else 0,
        "order_lo": song.base + 0x707,
        "order_hi": song.base + 0x70A,
        "filt_bit": song.base + 0x710,
        "filt_mask": song.base + 0x713,
        "tempo": song.base + 0x716,
        "volume": song.base + 0x717,
        "release_clears": int(build["release"] == "clear"),
        "order_jump": int(build["order_loop"] == "jump"),
        "has_tail": int(tail is not None),
        "tail_val": tail or 0,
        "onset_on": int(onset),
        "onset_ctrl": (build["onset_ctrl"] or 0) if onset else 0,
        "onset_adsr": constants.V1D_ONSET_ADSR_IMM if v1d and onset else 0,
        "pw_shift": build["pw_min_shift"],
        "v1d": int(v1d),
        "v37": int(not v1d),
        "run": _play_voices(song),
        "filtered": int(song.mem[song.base + constants.DMC_PLAY_LOOP_REL + 8] != 0x4C),
        "rest_1322": int(v1d and build["rest_tail"] == constants.REST_TAIL_1322),
        "burst_imm": (build["burst_imm"] or 0) if v1d else 0,
    }


def _binding_a1(song: Song) -> Dict[str, Any]:
    def operand(off: int) -> int:
        return _operand(song.mem, song.base, off)

    return {
        "patlo": operand(constants.A1_PATTERN_LO_OP),
        "pathi": operand(constants.A1_PATTERN_HI_OP),
        "inst": operand(constants.A1_INST_OP),
        "wtctrl": operand(constants.A1_WT_CTRL_OP),
        "wtarg": operand(constants.A1_WT_ARG_OP),
        "pwa": operand(constants.A1_PW_A_OP),
        "pwb": operand(constants.A1_PW_B_OP),
        "filta": operand(constants.A1_FILT_A_OP),
        "filtb": operand(constants.A1_FILT_B_OP),
        "freqlo": (song.base + constants.A1_FREQ_LO_REL) & 0xFFFF,
        "freqhi": (song.base + constants.A1_FREQ_HI_REL) & 0xFFFF,
        "tempo": (song.base + constants.A1_TEMPO_REL) & 0xFFFF,
        "order_lo": (song.base + constants.A1_ORDER_STORE_REL - 0x1000) & 0xFFFF,
        "order_hi": (song.base + constants.A1_ORDER_STORE_REL - 0x1000 + 3) & 0xFFFF,
        "rel_clears_sr": int(song.facets()["release_sr"] == "clear"),
    }


def _binding95(song: Song) -> Dict[str, Any]:
    def operand(off: int) -> int:
        return _operand(song.mem, song.base, off)

    return {
        "patlo": operand(constants.N95_PATTERN_LO_OP),
        "pathi": operand(constants.N95_PATTERN_HI_OP),
        "inst": operand(constants.N95_INST_OP),
        "wtctrl": operand(constants.N95_WT_CTRL_OP),
        "wtarg": operand(constants.N95_WT_ARG_OP),
        "pwa": operand(constants.N95_PW_A_OP),
        "pwb": operand(constants.N95_PW_B_OP),
        "filta": operand(constants.N95_FILT_A_OP),
        "filtb": operand(constants.N95_FILT_B_OP),
        "freqlo": (song.base + constants.N95_FREQ_LO_REL) & 0xFFFF,
        "freqhi": (song.base + constants.N95_FREQ_HI_REL) & 0xFFFF,
        "tempo": (song.base + constants.N95_RELOAD_SEED_REL) & 0xFFFF,
        "order_lo": (song.base + constants.N95_ORDER_STORE_REL - 0x1000) & 0xFFFF,
        "order_hi": (song.base + constants.N95_ORDER_STORE_REL - 0x1000 + 3) & 0xFFFF,
    }


def binding(song: Song, subtune: int = 0) -> Tuple[Dict[str, Any], Song, int]:
    """``(layout binding, body song, subtune)`` for ``song``'s resident body.

    Raises:
      DmcFormatError: no spec states this body generation.
    """
    song, subtune = _rebased(song, subtune)
    generation = song.variant()
    if generation not in GENERATIONS:
        raise DmcFormatError(f"no playspec for the {generation!r} body")
    if generation == "a1":
        return _binding_a1(song), song, subtune
    if generation == "n95":
        return _binding95(song), song, subtune
    return _binding85(song, generation in V1D_BODIES), song, subtune


(
    PLAY_CALL,
    STEADY_CALL,
    FADE_CALL,
    WAVE_CALL,
    MODULATE_CALL,
    PULSE_CALL,
    TURN_CALL,
    STEP_CALL,
) = range(8)

# The player's routines a tune's own code may call for one voice (X): offset from the base and
# what the routine does.
DMC_CALLS = {
    constants.DMC_STEP_REL: STEP_CALL,
    constants.DMC_STEADY_REL: STEADY_CALL,
    constants.DMC_PULSE_REL: PULSE_CALL,
    constants.DMC_MODULATE_REL: MODULATE_CALL,
    constants.DMC_TURN_REL: TURN_CALL,
    constants.DMC_OUTPUT_REL: WAVE_CALL,
}
VOICE_CALLS = {
    "v37": DMC_CALLS,
    "v1d": DMC_CALLS,
    "nn": DMC_CALLS,
    "n95": {constants.N95_WAVE_REL: WAVE_CALL},
    "a1": {
        constants.A1_STEADY_REL: STEADY_CALL,
        constants.A1_FADE_REL: FADE_CALL,
        constants.A1_WAVE_REL: WAVE_CALL,
    },
}


def voice_calls(song: Song) -> Dict[int, int]:
    """``{address: call}`` of the routines of the song's body that run for the voice in X."""
    calls = VOICE_CALLS.get(song.variant(), {})
    mem, base = song.mem, song.base
    if calls is DMC_CALLS and not _voice_step_ok(mem, base):
        calls = {rel: call for rel, call in calls.items() if call != STEP_CALL}
    return {(base + rel) & 0xFFFF: call for rel, call in calls.items()}


def _voice_step_ok(mem, base: int) -> bool:
    """Whether the body's per-voice call opens ``LDA voice_on,X`` at its offset."""
    at = base + constants.DMC_STEP_REL
    return mem[at] == 0xBD and mem[at + 1] | mem[at + 2] << 8 == base + 0x00C


def body_entries(song: Song) -> Iterator[int]:
    """The addresses a play call can enter the resident play body at."""
    mem, base = song.mem, song.base
    yield from voice_calls(song)
    jump = (mem[base + 4] | (mem[base + 5] << 8)) & 0xFFFF
    reorganised = {"a1": constants.DMC_PLAY_A1_REL, "n95": constants.DMC_PLAY_95_REL}
    if song.variant() in reorganised:
        if jump == (base + reorganised[song.variant()]) & 0xFFFF:
            yield jump
        return
    for entry in ((base + constants.DMC_PLAY_BODY_REL) & 0xFFFF, jump):
        if _play_body_ok(mem, entry, base):
            yield entry


def variables_of(body: Song) -> Tuple[Var, ...]:
    """The player variables of the song's resident body."""
    generation = body.variant()
    if generation in V1D_BODIES or generation == "v37":
        return dmc(*_note_cells(body, generation in V1D_BODIES))
    return VARIABLES[generation]


def spec_binding(song: Song, subtune: int = 0) -> SpecBinding:
    """The :class:`~pysidtracker.formats.SpecBinding` of ``song``'s resident body at
    0-based ``subtune``: its generation's spec, layout and play-body entries.

    Raises:
      DmcFormatError: no spec states this body generation.
    """
    layout, body, _ = binding(song, subtune)
    if _runs_own_code(body):
        raise DmcFormatError("the play call runs code of the tune's own for a voice")
    entries = frozenset(body_entries(body))
    generation = body.variant()
    family = FAMILIES.get(generation, generation)
    variables = variables_of(body)
    calls = voice_calls(body)

    def bind(view, entry):
        states = state_layout(family, variables, view, body.base, layout)
        return {**layout, **states, "call": calls.get(entry, PLAY_CALL)}

    return SpecBinding(
        spec(generation),
        bind,
        bytes(song.mem[song.load : song.load + song.image_len]),
        entries=lambda view: entries,
        subtune=subtune,
    )
