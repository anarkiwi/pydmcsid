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
from typing import Any, Dict, Iterator, Tuple

from pysidtracker.playspec import parse as parse_spec

from pydmcsid import constants
from pydmcsid.errors import DmcFormatError
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
NOTE_CELLS = (0x1012, 0x1015, 0x1034, 0x1035, 0x1036)
SID_BASE = 0xD400


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


def _note_cells(song: Song, v1d: bool) -> Dict[str, int]:
    if v1d:
        note = song.facets()["note_cells"]
        return {
            "c_note": note,
            "c_inst": note + 3,
            "g_filt": note + 6,
            "g_vib": note + 7,
            "g_bend": note + 8,
        }
    rel = song.base - 0x1000
    names = ("c_note", "c_inst", "g_filt", "g_vib", "g_bend")
    return {name: cell + rel for name, cell in zip(names, NOTE_CELLS)}


def _binding85(song: Song, v1d: bool) -> Dict[str, Any]:
    build = song.facets()

    def operand(off: int) -> int:
        return _operand(song.mem, song.base, off)

    tail = build["tail_d418"]
    onset = build["onset"] != "nop" if v1d else True
    layout = {
        "base": song.base,
        "sid": SID_BASE,
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
        "zp": operand(constants.ORDER_READ_ZP_OP) & 0xFF,
        "sr_tmp": operand(constants.V1D_SR_TMP_OP) if v1d else 0,
        "vibseed": operand(constants.VIB_SEED_OP) if v1d else 0,
        "release_clears": int(build["release"] == "clear"),
        "order_jump": int(build["order_loop"] == "jump"),
        "has_tail": int(tail is not None),
        "tail_val": tail or 0,
        "onset_on": int(onset),
        "onset_ctrl": (build["onset_ctrl"] or 0) if onset else 0,
        "onset_adsr": constants.V1D_ONSET_ADSR_IMM if v1d and onset else 0,
        "pw_shift": build["pw_min_shift"],
        "v1d": int(v1d),
        "rest_1322": int(v1d and build["rest_tail"] == constants.REST_TAIL_1322),
        "burst_imm": (build["burst_imm"] or 0) if v1d else 0,
    }
    layout.update(_note_cells(song, v1d))
    return layout


def _binding_a1(song: Song) -> Dict[str, Any]:
    def operand(off: int) -> int:
        return _operand(song.mem, song.base, off)

    return {
        "base": song.base,
        "sid": SID_BASE,
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
        "rel_clears_sr": int(song.facets()["release_sr"] == "clear"),
    }


def _binding95(song: Song) -> Dict[str, Any]:
    def operand(off: int) -> int:
        return _operand(song.mem, song.base, off)

    return {
        "base": song.base,
        "sid": SID_BASE,
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


def body_entries(song: Song) -> Iterator[int]:
    """The addresses a play call can enter the resident play body at."""
    mem, base = song.mem, song.base
    jump = (mem[base + 4] | (mem[base + 5] << 8)) & 0xFFFF
    reorganised = {"a1": constants.DMC_PLAY_A1_REL, "n95": constants.DMC_PLAY_95_REL}
    if song.variant() in reorganised:
        if jump == (base + reorganised[song.variant()]) & 0xFFFF:
            yield jump
        return
    for entry in ((base + constants.DMC_PLAY_BODY_REL) & 0xFFFF, jump):
        if _play_body_ok(mem, entry, base):
            yield entry
