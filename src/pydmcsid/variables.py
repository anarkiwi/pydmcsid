"""The player variables each body generation keeps in its work area.

The 6502 player keeps its variables at fixed offsets from the code base. A spec holds them as
``state``; this module names, for each generation, the byte(s) each state occupied so the binder
can read the values the tune's init left and say which state a table read past its end lands
on (``alias`` ids, 1-based in table order, stated by the spec's ``variable`` procedure).
"""

from dataclasses import dataclass
from typing import Dict, Iterator, List, Optional, Tuple


@dataclass(frozen=True)
class Var:
    """One player variable: its ``state`` name and the work-area offset of each byte lane
    (low first); ``voices`` for a per-voice list at consecutive addresses; ``lanes`` are the
    expressions the spec reads each byte as, with ``{v}`` the voice instance."""

    name: str
    at: Tuple[int, ...]
    voices: bool = True
    lanes: Optional[Tuple[str, ...]] = None

    def exprs(self) -> Tuple[str, ...]:
        """The spec expression of each byte lane."""
        if self.lanes:
            return self.lanes
        ref = f"voice[k].{self.name}" if self.voices else self.name
        if len(self.at) == 1:
            return (ref,)
        return (f"lo({ref})", f"hi({ref})")


def byte(name: str, at: int, voices: bool = True, lanes=None) -> Var:
    """A one-byte variable."""
    return Var(name, (at,), voices, lanes)


def word(name: str, lo: int, hi: int, voices: bool = True) -> Var:
    """A 16-bit variable kept as a low and a high byte."""
    return Var(name, (lo, hi), voices)


N95 = (
    byte("tempo_ctr", 0x016, False),
    byte("mode", 0x018, False),
    byte("cutoff", 0x019, False),
    word("vol", 0x01B, 0x01A, False),
    byte("filt_pos", 0x803, False),
    byte("filt_cnt", 0x84E, False),
    byte("cutoff_base", 0x853, False),
    byte("fade_up", 0x854, False),
    byte("fade_down", 0x855, False),
    byte("shift_tmp", 0x856, False),
    byte("filt_gate", 0x857, False),
    byte("active", 0x009),
    byte("key", 0x012),
    byte("seqpos", 0x7DF),
    byte("patpos", 0x7E2),
    byte("left", 0x7E5),
    byte("dur", 0x7E8),
    byte("instr", 0x7EB),
    byte("trans", 0x7EE, lanes=("voice[k].trans - voice[k].down",)),
    byte("sus_over", 0x7F1),
    byte("tie", 0x7F4),
    byte("slide_speed", 0x7F7),
    byte("slide_to", 0x7FA),
    byte("wave_pos", 0x7FD),
    byte("pw_pos", 0x800),
    byte("vib_delay", 0x806),
    byte("vib_period", 0x809),
    word("vib", 0x80C, 0x80F),
    byte("vib_ramp", 0x812),
    byte("fresh", 0x815),
    word("freq", 0x818, 0x81B),
    byte("ctrl", 0x81E),
    byte("gate", 0x821),
    byte("term", 0x827),
    word("pw", 0x82A, 0x82D),
    byte("pw_cnt", 0x830),
    byte("vib_grown", 0x833),
    byte("vib_dir", 0x836),
    byte("vib_pos", 0x839),
    word("slide", 0x83C, 0x83F),
    byte("detune", 0x842),
    byte("wave_speed", 0x845),
    byte("wave_cnt", 0x848),
    byte("inst_off", 0x84B),
)

A1 = (
    byte("tempo_ctr", 0x013, False),
    byte("mode", 0x015, False),
    word("cutoff", 0x017, 0x016, False),
    byte("fade_up", 0x018, False),
    byte("fade_down", 0x019, False),
    byte("vib_shift", 0x01A, False),
    word("vol", 0x01C, 0x01B, False),
    word("delta", 0x01E, 0x01F, False),
    byte("filt_pos", 0x7F9, False),
    word("fc", 0x83B, 0x83C, False),
    word("sl", 0x83D, 0x83E, False),
    byte("filt_start", 0x83F, False),
    byte("sr_tmp", 0x840, False),
    byte("pw_start", 0x841, False),
    byte("startup", 0x842, False),
    byte("cutoff_ovr", 0x843, False),
    byte("active", 0x006),
    byte("key", 0x00F),
    byte("seqpos", 0x7D5),
    byte("patpos", 0x7D8),
    byte("left", 0x7DB),
    byte("dur", 0x7DE),
    byte("instr", 0x7E1),
    byte("trans", 0x7E4, lanes=("voice[k].trans - voice[k].down",)),
    byte("sus_over", 0x7E7),
    byte("tie", 0x7EA),
    byte("slide_speed", 0x7ED),
    byte("slide_to", 0x7F0),
    byte("wave_pos", 0x7F3),
    byte("pw_pos", 0x7F6),
    byte("vib_delay", 0x7FC),
    byte("vib_period", 0x7FF),
    word("vib", 0x802, 0x805),
    byte("mark", 0x808),
    byte("fresh", 0x80B),
    word("freq", 0x80E, 0x811),
    byte("ctrl", 0x814),
    byte("gate", 0x817),
    byte("term", 0x81D),
    word("pw", 0x820, 0x823),
    word("pw_cnt", 0x826, 0x829),
    byte("vib_grown", 0x82C),
    byte("vib_dir", 0x82F),
    byte("vib_pos", 0x832),
    word("slide", 0x835, 0x838),
)

GENERATION_VARS: Dict[str, Tuple[Var, ...]] = {"n95": N95, "a1": A1}

# For each generation, the tables a read may run past the end of: the layout field of the
# list that names the variables there, the layout field of the table's address and its size.
WINDOWS: Dict[str, Tuple[Tuple[str, str, int], ...]] = {
    "n95": (("wild_freq_lo", "freqlo", 256), ("wild_freq_hi", "freqhi", 256)),
    "a1": (("wild_freq_lo", "freqlo", 256), ("wild_freq_hi", "freqhi", 256)),
}


def initial(var: Var, mem, base: int) -> object:
    """The value the init left in ``var``: an int, or a list over the voices."""

    def value(shift: int) -> int:
        return sum(
            mem[(base + at + shift) & 0xFFFF] << 8 * i for i, at in enumerate(var.at)
        )

    return [value(v) for v in range(3)] if var.voices else value(0)


def aliases(variables: Tuple[Var, ...]) -> Iterator[Tuple[int, str, int, int]]:
    """``(id, expression, offset, voice)`` of every byte lane of ``variables``, ids 1-based
    in table order with the voices of one lane sharing an id and ``voice`` from 0."""
    ident = 0
    for var in variables:
        for lane, expr in zip(var.at, var.exprs()):
            ident += 1
            for voice in range(3 if var.voices else 1):
                yield ident, expr, lane + voice, voice


def wild_codes(
    variables: Tuple[Var, ...], base: int, table: int, size: int
) -> List[int]:
    """For each byte of the ``size``-byte table at ``table``, the alias code (id * 4 + voice) of
    the player variable lying there, 0 where no variable does."""
    code = [0] * size
    for ident, _expr, offset, voice in aliases(variables):
        at = (base + offset - table) & 0xFFFF
        if at < size:
            code[at] = ident * 4 + voice
    return code


def state_layout(
    generation: str, mem, base: int, layout: Dict[str, int]
) -> Dict[str, object]:
    """The binding fields that carry the player's variables: ``init_<name>`` the values the
    init left in ``mem`` and, for each table a read may run past, the variable at each byte.
    """
    variables = GENERATION_VARS[generation]
    out = {f"init_{var.name}": initial(var, mem, base) for var in variables}
    for field, table, size in WINDOWS[generation]:
        out[field] = wild_codes(variables, base, layout[table], size)
    return out
