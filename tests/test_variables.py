"""The player variables each spec keeps as state, and the table reads that land on them."""

import re
from importlib import resources

import pytest

from pydmcsid.errors import DmcFormatError
from pydmcsid.variables import (
    A1,
    N95,
    PLAIN,
    VOICE_OFFSETS,
    aliases,
    byte,
    dmc,
    initial,
    state_layout,
    wild_codes,
    word,
)

TABLES = {
    "dmc_95.play": N95,
    "dmc_a1.play": A1,
    "dmc.play": dmc(0x12, 0x15, 0x34, 0x35, 0x36),
}


def _arms(text):
    """``{id: expression}`` of the ``variable`` procedure's arms."""
    return {
        int(ident): expr
        for ident, expr in re.findall(r"^\s+(\d+) => return (.+);$", text, re.M)
    }


@pytest.mark.parametrize("name", sorted(TABLES))
def test_the_spec_states_every_variable_the_table_names(name):
    """Each id the binder reports for a byte of the work area is an arm of the spec's
    ``variable`` procedure returning the state that byte is."""
    text = resources.files("pydmcsid").joinpath("specs", name).read_text()
    expected = {}
    for ident, expr, _offset, _voice in aliases(TABLES[name]):
        expected.setdefault(ident, expr)
    assert _arms(text) == expected


@pytest.mark.parametrize("name", sorted(TABLES))
def test_every_state_has_its_initial_value_declared(name):
    """The spec declares each variable's ``init_`` field."""
    text = resources.files("pydmcsid").joinpath("specs", name).read_text()
    for var in TABLES[name]:
        assert f"init_{var.name}" in text


def test_wild_codes_name_the_variable_at_each_byte():
    """A table byte lying on a variable carries the variable's id and voice."""
    shared, per_voice = byte("a", 0x14, False), byte("b", 0x20)
    codes = wild_codes((shared, per_voice), 0x1000, 0x1010, 0x20)
    assert codes[0x14 - 0x10] == 1 * 4
    assert codes[0x20 - 0x10 : 0x23 - 0x10] == [2 * 4, 2 * 4 + 1, 2 * 4 + 2]
    assert sum(1 for c in codes if c) == 4


def test_wild_codes_ignore_variables_outside_the_table():
    """Bytes before and after the table, however far, name nothing."""
    codes = wild_codes((byte("b", 0x20),), 0x1000, 0x1040, 0x10)
    assert not any(codes)


def test_a_word_variable_has_a_code_per_byte_lane():
    """Both bytes of a 16-bit variable are aliased, low first."""
    codes = wild_codes((word("w", 0x20, 0x23),), 0x1000, 0x1020, 8)
    assert codes[0] == 1 * 4 and codes[3] == 2 * 4 and codes[1] == 1 * 4 + 1


def test_initial_values_are_what_the_init_left():
    """A word's value combines its bytes; a per-voice variable is a list over the voices."""
    mem = bytearray(0x2000)
    mem[0x1020:0x1023] = bytes((1, 2, 3))
    mem[0x1030:0x1033] = bytes((0x10, 0x20, 0x30))
    mem[0x1040:0x1043] = bytes((0xA, 0xB, 0xC))
    assert initial(byte("b", 0x20), mem, 0x1000) == [1, 2, 3]
    assert initial(word("w", 0x30, 0x40), mem, 0x1000) == [0xA10, 0xB20, 0xC30]
    assert initial(byte("s", 0x21, False), mem, 0x1000) == 2


def _work_area(offsets=(0, 7, 14)):
    """A memory whose work area holds the voices' SID offsets."""
    mem = bytearray(0x4000)
    at = 0x1000 + VOICE_OFFSETS["dmc"]
    mem[at : at + 3] = bytes(offsets)
    return mem


def test_a_variable_inside_a_directly_read_table_is_refused():
    """A table the spec reads without aliasing must not lie over a variable."""
    layout = {name: 0x3000 for name, _ in PLAIN["dmc"]}
    layout["patlo"] = 0x1000 + 0x700
    with pytest.raises(DmcFormatError, match="patlo"):
        state_layout(
            "dmc", dmc(0x12, 0x15, 0x34, 0x35, 0x36), _work_area(), 0x1000, layout
        )


def test_voices_that_do_not_write_the_chip_in_order_are_refused():
    """The specs fix the voices' register offsets at 0, 7 and 14."""
    with pytest.raises(DmcFormatError, match="voice registers"):
        state_layout(
            "dmc", dmc(0x12, 0x15, 0x34, 0x35, 0x36), _work_area((7, 0, 14)), 0x1000, {}
        )
