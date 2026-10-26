"""The DMC build variant: facet values, provenance and the format hooks."""

import json

import pytest
from pysidtracker.facets import DEFAULT, DETECTED

from helpers import TUNES

import pydmcsid
from pydmcsid.format import dmc_format
from pydmcsid.variant import FACETS, facet_sites


def test_facets_track_the_decode(tune_id, tune_path):
    song = pydmcsid.read(tune_path)
    found = song.facets()
    assert found is song.facets()  # decoded once per song
    assert list(found) == [facet.name for facet in FACETS]
    assert found["generation"] == song.variant()
    assert found["byte_exact"] is song.byte_exact()
    assert found["stub"] == song.dispatch.table - song.load
    assert found.source("order_table").kind == DETECTED
    info = found.as_info()
    assert json.loads(json.dumps(info)) == info, tune_id


@pytest.mark.parametrize(
    "rel, wrapper",
    [
        (TUNES["ode"], "none"),
        (TUNES["krupa_mix"], "benign"),
        ("MUSICIANS/B/Bayliss_Richard/Balloonacy.sid", "selector"),
        (TUNES["dude"], "multispeed"),
        ("DEMOS/M-R/Pimpin_Power_1994.sid", "foreign"),
    ],
)
def test_wrapper_kind(rel, wrapper, hvsc):
    song = pydmcsid.parse(hvsc.read(rel))
    found = song.facets()
    assert found["wrapper"] == wrapper
    assert found.source("wrapper").addr == song.play
    assert (wrapper in ("none", "benign", "multispeed")) is song.byte_exact()


def test_multispeed_divider_read_from_wrapper(hvsc):
    found = pydmcsid.parse(hvsc.read(TUNES["dude"])).facets()
    assert found["layout"] == "nn-dispatch"
    assert found["nn_shift"] > 0
    assert (found["ms_reload"], found["ms_seed"]) == (6, 1)
    assert found.source("ms_reload").kind == DETECTED


def test_inapplicable_facets_default(hvsc):
    found = pydmcsid.parse(hvsc.read(TUNES["katusha"])).facets()
    assert found["generation"] == "a1"
    for name in ("release", "pw_min_shift", "note_cells", "onset", "ms_reload"):
        assert found.source(name).kind == DEFAULT, name
    assert (found["release_sr"], found["release_gate"]) == ("clear", "mask")


def test_order_table_falls_back_to_operand_as_default():
    from test_reader import _dmc_body  # pylint: disable=import-outside-toplevel

    body = bytearray(_dmc_body(0x1000, init_rel=0x37))
    song = pydmcsid.parse(b"\x00\x10" + bytes(body))
    found = song.facets()
    assert found.source("order_table").kind == DEFAULT
    assert found["order_table"] == song.mem[0x103E] | song.mem[0x103F] << 8


def test_format_hooks(hvsc):
    data = hvsc.read(TUNES["ode"])
    fmt = dmc_format()
    song = fmt.parser.parse(data)
    sites = fmt.sites(song)
    assert sites == facet_sites(song)
    assert sites[0x1000 + 0x33D] == "release"
    assert fmt.variant_info(song) == song.facets().as_info()
