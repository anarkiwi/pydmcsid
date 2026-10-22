"""Native DMC editor saves: parse, round-trip, and render against the editors' own files.

Each DMC editor saved a song as a headerless bare PRG at $1000; these pin that path
on the V1.2/V2.0/V3.0 (v37) and V4.0 (v1d) release disks. The disks are copyright
works and are never committed: the manifest pins each by SHA-256 and a cold cache is a failure.
"""

import os
import re
from pathlib import Path

import pytest

import pydmcsid
import pysidtracker as ps
from pysidtracker.header import write_psid
from pysidtracker.oracle import aligned_match, register_grid
from pysidtracker.testing import resolve_tune

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
MANIFEST = HERE / "fixtures" / "dmc_native_manifest.txt"
CACHE = Path(
    os.environ.get("PYSID_NATIVECACHE", str(REPO / "tests" / ".tunecache" / "native"))
)
HVSC_CACHE = Path(os.environ.get("DMC_TUNECACHE", str(REPO / "tests" / ".tunecache")))

# Editor saves load at $1000; the play entry is the standard base + 3.
LOAD = 0x1000
PLAY = LOAD + 3

# Render frames: well past the first order loop, exercising pattern-boundary events.
FRAMES = 500
MAX_LEAD = 8

_SHA = {(e.release_id, e.name): e.sha256 for e in ps.load_manifest(MANIFEST)}

# case: id | release | disk | .DMC member | body variant | 6502-exact | HVSC relpath (V7.0's VICE save stays out; V4.0 covers its v1d body)
CASES = [
    dict(
        id="v12_rock_zak1",
        release="2598",
        disk="dmc_12.d64",
        save="B.ROCK ZAK1 .DMC",
        variant="v37",
        jennings=True,
        hvsc="MUSICIANS/B/Brian/Rock_Zak_1.sid",
    ),
    dict(
        id="v20_rock_zak1",
        release="10757",
        disk="dmc_v2.d64",
        save="B.ROCK ZAK1 .DMC",
        variant="v37",
        jennings=True,
        hvsc="MUSICIANS/B/Brian/Rock_Zak_1.sid",
    ),
    dict(
        id="v20_a_music",
        release="10757",
        disk="dmc_v2.d64",
        save="B.A MUSIC .DMC",
        variant="v37",
        # byte-identical image to the HVSC rip, but the 6502 and the player part ways mid-song
        jennings=False,
        hvsc="MUSICIANS/B/Brian/A_Music.sid",
    ),
    dict(
        id="v30_depechemix",
        release="98640",
        disk="dmc3.zip",
        save="G.DEPECHEMIX.DMC",
        variant="v37",
        jennings=True,
        hvsc=None,
    ),
    dict(
        id="v40_syndrommuzax",
        release="2596",
        disk="dmc_40.d64.gz",
        save="SYNDROMMUZAX.DMC",
        variant="v1d",
        jennings=True,
        hvsc=None,
    ),
]


def _disk_bytes(case):
    sha = _SHA.get((case["release"], case["disk"]))
    path = ps.fixture(sha, CACHE) if sha else None
    if path is None:
        raise ps.CsdbFetchError(
            f"native disk {case['disk']} not cached; `pysidtracker fixtures fetch` first"
        )
    return path.read_bytes()


def _save(case, disk=None):
    disk = _disk_bytes(case) if disk is None else disk
    for path, data in ps.iter_prgs(case["disk"], disk):
        if (
            data
            and re.sub(r"\s+", " ", path.rsplit("/", 1)[-1].strip()) == case["save"]
        ):
            return data
    raise AssertionError(f"{case['save']} not found on {case['disk']}")


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_editor_save_parses_and_roundtrips(case):
    save = _save(case)
    assert (save[0], save[1]) == (LOAD & 0xFF, LOAD >> 8)
    song = pydmcsid.parse(save)
    assert song.header is None
    assert song.songs == 1
    assert song.load == LOAD
    assert (song.init, song.play) == (LOAD, PLAY)
    assert song.variant() == case["variant"]
    assert song.byte_exact()
    assert pydmcsid.to_prg(song) == save


@pytest.mark.parametrize(
    "case",
    [c for c in CASES if c["jennings"]],
    ids=[c["id"] for c in CASES if c["jennings"]],
)
def test_render_matches_6502(case):
    save = _save(case)
    song = pydmcsid.parse(save)
    grid = pydmcsid.DmcPlayer(song).render_grid(FRAMES)
    psid = write_psid(load=LOAD, init=LOAD, play=PLAY, image=save[2:], songs=1)
    assert aligned_match(register_grid(psid, FRAMES), grid, max_lead=MAX_LEAD)


@pytest.mark.parametrize(
    "case", [c for c in CASES if c["hvsc"]], ids=[c["id"] for c in CASES if c["hvsc"]]
)
def test_render_matches_hvsc(case):
    save = _save(case)
    song = pydmcsid.parse(save)
    grid = pydmcsid.DmcPlayer(song).render_grid(FRAMES)
    tune = resolve_tune(case["hvsc"], cache_dir=HVSC_CACHE)
    if tune is None:
        raise ps.TuneFetchError(f"{case['hvsc']}: unreachable (offline, not cached)")
    hvsc = Path(tune).read_bytes()
    hgrid = pydmcsid.DmcPlayer(pydmcsid.parse(hvsc)).render_grid(FRAMES)
    assert aligned_match(hgrid, grid, max_lead=MAX_LEAD)
