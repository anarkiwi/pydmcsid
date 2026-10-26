"""``scripts/dmc_regress.py``: per-tune decode records and their diff."""

import json

import dmc_regress

from helpers import TUNES


def test_record_and_diff(hvsc, tmp_path, capsys):
    root = tmp_path / "hvsc"
    for rel in (TUNES["ode"], TUNES["katusha"]):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(hvsc.read(rel))
    (root / "DEMOS" / "junk.sid").write_bytes(b"\x00\x10" + bytes(64))
    before = tmp_path / "before.jsonl"
    dmc_regress.main(["dump", str(root), str(before), "--frames", "20", "--jobs", "2"])
    recs = [json.loads(line) for line in before.read_text().splitlines()]
    assert [r["path"] for r in recs] == sorted([TUNES["katusha"], TUNES["ode"]])
    assert recs[0]["subtunes"][0]["engine"] == "PlayerA1"
    assert dmc_regress.main(["diff", str(before), str(before)]) == 0
    after = tmp_path / "after.jsonl"
    recs[0]["byte_exact"] = not recs[0]["byte_exact"]
    after.write_text("".join(json.dumps(r) + "\n" for r in recs))
    assert dmc_regress.main(["diff", str(before), str(after)]) == 1
    assert "1 of 2 tunes differ" in capsys.readouterr().out
