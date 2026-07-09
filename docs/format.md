# DMC (Demo Music Creator) format

## Overview

DMC (Demo Music Creator) is a C64 music player by Brian/Graffity
(`-PLAYER (C) BRIAN/GRAFFITY!`). `pydmcsid` loads a DMC tune, locates the
per-tune tables, and runs a faithful integer transcription of the 6502 play
routine, exposing the per-frame SID register writes. The player is transcribed
from the DMC disassembly.

## Container and detection notes

A DMC `.sid` is the player binary plus the per-tune song data resident in the
same image; `.prg` images are read the same way. The image is loaded through the
shared [`pysidtracker`](https://github.com/anarkiwi/pysidtracker) base and the
per-tune table bases are read from the player-code operands — no baked
addresses, so a differently-sized tune relocates its tables and is still found.
Container headers are not trusted.

Recognition anchors on the resident player's *play body* at `base+$85` (the
opening `DEC tempo_ctr`, confirmed by opcode + operand) reached via the dispatch
`JMP` table's play entry. This is load-independent (the JMP target and the
body's own operand both track the base, so relocated and short-stub-prepended
images are found) and init-independent (the init entry varies, commonly `$1d` or
`$37`). It accepts both the original 4-entry (init/play/stop/FUN) dispatch and
the 2-entry (init/play only) builds that carry the identical body, while
rejecting reorganised bodies that only share the DMC data-table structure.
Across HVSC (sidid `DMC` family) it recognises 7251 of 10695 tunes.

Reorganised bodies that are **not** modeled (different play offset / work-RAM
layout, rejected by `parse`): `DMC_V6.x` (a 2-entry `$50`/`$7b` vector) and the
`$40`/`$a1`, `$40`/`$95`/`$d3`, `$0718`/`$50` and `$947`/`$94a`/`$937` families.

## Data model

The play routine models: tempo divider, per-voice orderlist walk (transpose /
loop / stop), pattern walk (note / instrument-select / duration / effect /
gate), stride-11 instrument records driving PW init + a 16-bit PW sweep, the
`$FF`-arp wavetable, triangle vibrato, glide, the pitch-slide effect and a
6-step filter sweep, the hard-restart window, and the final per-voice frequency
compose.

## Player and playback notes

`iter_register_writes(song, max_frames)` yields one `RegWrite(clock, reg, val)`
per SID write, frames `cycles_per_frame` apart (the shared `py*` register-log
convention, matching `pygoattracker` / `pymusicassembler`). The DMC player emits
one tight write burst per VBI play call.

`song.byte_exact()` reports whether the recognised body is the generation the
transcription reproduces byte-for-byte — the init-`$37` generation (pattern
markers `$fe/$fd/$ff`), ~2878 tunes. The later init-`$1d` generation shares the
anchor and data-table layout but re-encodes the markers as `$7e/$7d/$7f` and
restructures note setup, so it is recognised and parsed but not yet played
byte-exact. `tests/test_corpus.py` and `tests/test_corpus_clusters.py` validate
a deterministic HVSC sample against a local `$HVSC` tree; four `.grid.txt`
byte-exact references (plus a 2-entry-dispatch and a relocated build) are checked
frame-exact.

## References

- DMC (Demo Music Creator) player by Brian/Graffity; transcribed from the DMC
  disassembly.
- HVSC `DMC` sidid family.
- [`pysidtracker`](https://github.com/anarkiwi/pysidtracker) — shared
  container/image/detection base.
