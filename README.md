# pydmcsid

Pure-Python reader and player for **DMC (Demo Music Creator)** SID tunes — the
C64 player by Brian/Graffity ("`-PLAYER (C) BRIAN/GRAFFITY!`").

A DMC `.sid` is the player binary plus the per-tune song data resident in the same
image. `pydmcsid` loads the image, locates the per-tune table bases from the player
code operands (no baked addresses — a differently-sized tune relocates its tables),
and runs a faithful integer transcription of the 6502 play routine, exposing the
per-frame SID register writes.

## Usage

```python
import pydmcsid

song = pydmcsid.read("tune.sid")
for w in pydmcsid.iter_register_writes(song, max_frames=50 * 60):
    print(w.clock, w.reg, w.val)   # absolute CPU cycle, $D4xx reg offset, value
```

`iter_register_writes` follows the shared py* register-log convention
(`pygoattracker` / `pymusicassembler`): one `RegWrite(clock, reg, val)` per SID
write, frames `cycles_per_frame` apart. The DMC player emits one tight write burst
per VBI play call.

## What it models

Tempo divider, per-voice orderlist walk (transpose / loop / stop), pattern walk
(note / instrument-select / duration / effect / gate), stride-11 instrument records
driving PW init + a 16-bit PW sweep, the `$FF`-arp wavetable, triangle vibrato,
glide, the pitch-slide effect and a 6-step filter sweep, the hard-restart window,
and the final per-voice frequency compose. Transcribed from the DMC disassembly.

## Supported DMC builds

Recognition anchors on the resident player's opening 4-entry `JMP` table (init /
play / stop / FUN) whose play/stop/FUN targets sit at fixed offsets
(`$85`/`$62f`/`$63e`) from the table base — load-independent (relocated and
short-stub-prepended images are found) and init-independent (the init entry varies
across sub-versions, commonly `$1d` or `$37`). This is the exact player body the
transcription models. Across HVSC (sidid `DMC` family) it covers 7094 of 10695
tunes; `tests/test_corpus.py` validates a deterministic sample against a local
`$HVSC` tree (`scripts/gen_dmc_corpus.py` regenerates the list).

Other DMC variants carry a different player body (2- or 3-entry vector tables) and
are **not** modeled — rejected by `parse`: `DMC_V6.x` (a 2-entry `$50`/`$7b`
vector) and the `$40`/`$a1`, `$40`/`$95`/`$d3` and `$0718`/`$50` families.

## License

Apache-2.0.
