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

Recognition anchors on the resident player's *play body* (the opening
`DEC tempo_ctr`, confirmed by opcode + operand `base+$718`) reached via the
dispatch `JMP` table's play entry. This is load-independent (the JMP target and
the body's own operand both track the base, so relocated and short-stub-prepended
images are found), init-independent (the init entry varies, commonly `$1d` or
`$37`) and play-offset-independent: the play body is checked wherever the
dispatch points, so builds whose id-string/init region shifts the play entry
earlier (e.g. dispatch play → `base+$50`, the engine byte-identical downstream at
`base+$b0`) are found — the older anchor required exactly `base+$85`. It accepts
both the original 4-entry (init/play/stop/FUN) dispatch and the 2-entry
(init/play only) builds that carry the identical body, while rejecting
reorganised bodies (a different tempo work cell) that only share the DMC
data-table structure. Across HVSC (sidid `DMC` family) it recognises ~7960 of
10759 tunes.

Reorganised bodies that are **not** modeled (a different tempo/work-RAM layout,
rejected by `parse`): the `$40`/`$a1`, `$40`/`$95`/`$d3` and `$947`/`$94a`/`$937`
families, and the `DMC_V6.x` `$50`/`$7b` vector.

## Data model

The play routine models: tempo divider, per-voice orderlist walk (transpose /
loop / stop), pattern walk (note / instrument-select / duration / effect /
gate; the pattern markers are *rest / switch(tie) / end-of-pattern*, encoded
`$fe/$fd/$ff` in the init-`$37` body and `$7e/$7d/$7f` in the init-`$1d` body),
stride-11 instrument records driving PW init + a 16-bit PW sweep, the `$FF`-arp
wavetable, triangle vibrato, glide, the pitch-slide effect and a 6-step filter
sweep, the hard-restart window, and the final per-voice frequency compose.

The note-release gate-off ($133d) either leaves AD/SR static (the `$37` stock
inline `STA $100f,X`) or clears AD/SR=0 as an envelope-clearing hard-restart (the
`$1d` `$17ec` helper, or a scene-modified `$37` build patched to `JSR` a clearing
routine). Both generations ship both variants, so `release_clears_adsr` reads the
behaviour from the actual release code (an inline store, or a `JSR` helper
followed to its first `RTS`) rather than assuming it per generation.

## Player and playback notes

`iter_register_writes(song, max_frames)` yields one `RegWrite(clock, reg, val)`
per SID write, frames `cycles_per_frame` apart (the shared `py*` register-log
convention, matching `pygoattracker` / `pymusicassembler`). The DMC player emits
one tight write burst per VBI play call.

`song.byte_exact()` reports whether the recognised body is one the transcription
reproduces byte-for-byte: the init-`$37` and the modelled init-`$1d` generations
(both release variants), gated out only for a build whose play entry is wrapped,
whose AD/SR helper is relocated, or whose release is patched to an unrecognised
routine. `tests/test_corpus.py` and `tests/test_corpus_clusters.py` validate a
deterministic HVSC sample against a local `$HVSC` tree, and the committed
`.grid.txt` frozen py65-oracle references are checked frame-exact — covering both
generations, a 2-entry-dispatch build, relocated builds (load ≠ `$1000` and the
relocated `base+$50` play entry), and all four release AD/SR combinations
(`$37` stock/patched, `$1d` clear/no-clear).

## Export

`to_prg(song)` / `to_sid(song)` / `write(song, path)` serialize a parsed tune
back to the packed player+data image the DMC editor loads (`to_prg` a bare `.prg`
= load address + resident image; `to_sid` a PSID/RSID container). The resident
image is reproduced byte-for-byte, so `parse(to_prg(song))` yields the same tune
and a re-wrapped `.sid` reproduces it frame-for-frame on the py65 oracle.

## References

- DMC (Demo Music Creator) player by Brian/Graffity; transcribed from the DMC
  disassembly.
- HVSC `DMC` sidid family.
- [`pysidtracker`](https://github.com/anarkiwi/pysidtracker) — shared
  container/image/detection base.
