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
data-table structure.

Three reorganised bodies dispatched to a non-`$85` play offset are modeled by
their own transcriptions, each gated by a normalised-body signature disjoint from
the `$85` anchor so the base engine is provably unaffected:

- `base+$a1` — the V5-era `$a1` engine (own `$17cf..` work-RAM map, two-frame
  startup gate, per-voice wavetable/16-bit PW+filter sweeps, global cutoff sweep +
  volume fade, two per-build patchable release stores). Signature: `A1_BODY_SHA256`.
- `base+$95` — the compact self-modifying `$95` engine (global tempo divider
  `$1016` selecting a row-advance vs steady-tick pass, preset voice stride
  `$100c,X`, global cutoff sweep on voice 2). Signature: `N95_BODY_SHA256`.
- the `$947`/`$94a`/`$937` dispatch — the standard init-`$1d` body behind a 2-level
  PSID dispatch (the play entry `JMP`s into the ordinary body at a virtual base
  `load+1` or `load+13`), so it reuses the init-`$1d` engine at the derived base
  rather than a separate transcription; the `$937` sub-family adds a resident
  CIA-multispeed wrapper.

Across HVSC (sidid `DMC` family) it recognises 9902 of 10759 tunes, 9702 of them
reproduced byte-exact (v37 2903, v1d 4925, `$a1` 1196, `$95` 472, `$94a` 206).
The byte-exact gate follows a benign play-wrapper (a thunk that JMPs into the
standard entry) rather than deferring on `play != base+3`, and reads hand-patched
onset / `$D418`-tail edits from the code so those builds reproduce too. Still
unmodeled: the `DMC_V6.x` `$50`/`$7b` vector; the `base+$937` inline-CTRL
note-onset sub-variant of the `$94a` cluster; and a long tail of distinct
single-tune scene micro-patches (a residual &lt;1% of the claimed v37/v1d set is
gated as byte-exact but diverges on unmodeled per-tune edits).

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

One class, `DmcPlayer`, derives from `pysidtracker.MemPlayer` and owns the shared
playback machinery (the 64 KiB memory mount, the post-init snapshot, the diffing
`play_frame`, `render_grid` / `iter_frames`); each DMC generation is a private
subclass implementing only its `_setup` (resolve the per-tune table bases from the
resident code), `_init` and `_frame`. `DmcPlayer(source)` — a `Song` or
`.sid`/`.prg` bytes — dispatches to the matching generation.

`DmcPlayer(source).render_grid(nframes)` is the per-frame `$D400..$D418` register
grid (25 registers, forward-filled, pulse-width-high nibble-masked) — the shape
the sidtrace oracle produces. `iter_register_writes(song, max_frames)` yields one
`RegWrite(clock, reg, val)` per SID write, frames `cycles_per_frame` apart (the
shared `py*` register-log convention, via
`pysidtracker.register_writes_from_player`).

`song.byte_exact()` reports whether the recognised body is one the transcription
reproduces byte-for-byte: the init-`$37` and the modelled init-`$1d` generations
(both release variants) and the reorganised `$a1`/`$95`/`$94a` engines, gated out
only for a build whose play entry is wrapped, whose AD/SR helper is relocated,
whose release is patched to an unrecognised routine, or (for the reorganised
engines) whose header vectors resolve outside the resident player (a
subtune-selector / multispeed wrapper). `tests/test_corpus.py` validates a
deterministic HVSC sample against a local `$HVSC` tree, and
`tests/test_oracle_hvsc.py` (the `oracle` marker, a dedicated CI job) confirms
`DmcPlayer.render_grid` frame-for-frame against the deterministic `sidplayfp`
[`sidtrace`](https://github.com/anarkiwi/sidtrace) oracle over a real HVSC
representative of every generation — init-`$37`/`$1d` (with the code-read scene
patches), the `$a1`/`$95` engines, the `$94a` 2-level dispatch and its `$937`
CIA-multispeed wrapper, and a benign play-wrapper build. A small residual of
per-tune scene micro-patches and CIA-multispeed wrappers pydmcsid does not model
(e.g. a 2× CIA-reprogrammed play rate) is recognised but diverges from the
single-speed render, so it is excluded from the byte-exact oracle set.

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
