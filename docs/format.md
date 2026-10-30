# DMC (Demo Music Creator) format

## Overview

DMC (Demo Music Creator) is a C64 music player by Brian/Graffity
(`-PLAYER (C) BRIAN/GRAFFITY!`). `pydmcsid` loads a DMC tune, locates the
per-tune tables, and plays the tune: the tune's own init runs on the 6502
emulator and every play call runs a playspec of the DMC play routine on that
memory, exposing the per-frame SID register writes.

## Container and detection notes

A DMC `.sid` is the player binary plus the per-tune song data resident in the
same image; `.prg` images are read the same way. The image is loaded through the
shared [`pysidtracker`](https://github.com/anarkiwi/pysidtracker) base and the
per-tune table bases are read from the player-code operands — no baked
addresses, so a differently-sized tune relocates its tables and is still found.
Container headers are only partly trusted: the header init/play vectors are followed to detect play-wrappers, but the player body is located from the code.

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
their own specs, each gated by a normalised-body signature disjoint from
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
  rather than a separate spec; the `$937` sub-family adds a resident
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
stride-11 instrument records driving PW init + a 16-bit PW sweep, the
wavetable (a control byte of `$90` or more steps the position back by that value
minus `$90`), triangle vibrato, glide, the pitch-slide effect and a 6-step filter
sweep, the hard-restart window, and the final per-voice frequency compose.

The note-release gate-off ($133d) either leaves AD/SR static (the `$37` stock
inline `STA $100f,X`) or clears AD/SR=0 as an envelope-clearing hard-restart (the
`$1d` `$17ec` helper, or a scene-modified `$37` build patched to `JSR` a clearing
routine). Both generations ship both variants, so the `release` facet reads the
behaviour from the actual release code (an inline store, or a `JSR` helper
followed to its first `RTS`) rather than assuming it per generation.

## Song streams

`src/pydmcsid/dmc.lark` is the grammar of the streams the play routine reads, a
`pysidtracker.ByteGrammar` (`pysidtracker grammar TUNE` lists a tune's streams).
`Song.streams(subtune)` parses every stream a subtune's voices reach: each voice's
order list, the patterns it names and the instruments those patterns select. A
stream that does not parse raises `DmcParseError`. All five body generations are
covered; the wavetable and filter tables (parallel byte tables indexed from the
instrument) are not streams.

| Stream | Rule | Bytes |
|---|---|---|
| order list | `order`, `order_jump` | pattern number `$00-$7f`; transpose `$80-$fd` followed by a pattern number (any byte, not range-checked); stop `$fe`; loop `$ff`, followed by a target order index in a build whose `order_loop` facet is `jump` |
| pattern, init-`$37` | `pattern37` | note `$00-$5f`; instrument `$60-$7f` (low 5 bits); duration `$80-$bf` (low 6 bits); slide `$c0-$cf`/`$e0-$ef` + start note + target note, slide `$d0-$df`/`$f0-$fc` + target note; tie `$fd`; rest `$fe`; end `$ff` |
| pattern, init-`$1d` | `pattern1d` | as above with tie `$7d`, rest `$7e`, end `$7f`, legato toggle `$7c`, volume `$f0-$ff` (a prefix), slides `$c0-$cf`/`$e0-$ef` and `$d0-$df` |
| order list, `$a1`/`$95` | `order_x` | pattern number `$00-$7f`; transpose `$fd` (positive) or `$fc` (negated) + value + pattern number; stop `$fe`; loop `$ff` + target order index |
| pattern, `$a1` | `pattern_a1` | note `$00-$7f`; row end `$fe`, gate-toggle row end `$f4`; portamento `$fa` + speed + target, `$fb` + speed + start + target; tie toggle `$f5`; one-operand commands `$fd` `$fc` `$f3` `$f9` `$f8` `$f2` `$f1` `$f7` `$f6`; end `$ff` |
| pattern, `$95` | `pattern95` | as `$a1`, plus one-operand commands `$f0` and `$ef` |
| instrument | `instrument`, `instrument8` | 11 bytes (init-`$37`/`$1d`, `$94a`), 8 bytes (`$a1`, `$95`) |

A pattern is a run of rows, each preceded by any number of prefixes (instrument,
duration, and in `init-$1d` legato and volume). A row is a note, rest, tie or
slide. The end marker is only recognised straight after a row. A byte the player does
not define as a command (it skips it) is a parse error rather than being accepted. An order-list
transpose byte `$a0-$ff` adds `byte - $a0` to each note of the pattern that follows;
`$80-$9f` subtracts `byte - $80` (`$80` is no transposition).

## Player and playspec

The play routine is stated as [`pysidtracker.playspec`](https://github.com/anarkiwi/pysidtracker/blob/main/docs/playspec.md)
specs in `src/pydmcsid/specs/`: `dmc.play` (init-`$37`, init-`$1d` and `$94a`
bodies, selected by layout flags), `dmc_a1.play` (`$a1`) and `dmc_95.play` (`$95`).
`pydmcsid.spec.binding` binds a spec's `layout` from the song's facets and
operands.

`DmcPlayer(source, subtune)` — a `Song` or `.sid`/`.prg` bytes — is a
`pysidtracker.playspec.SpecPlayer`, hence a `MemPlayer`: the tune's own init runs on
the 6502 emulator, then every play call runs the spec on that memory, trapping the
resident play body when a wrapper calls it (a benign thunk, the `$937` multispeed
divider, a subtune selector). `DmcPlayer(..., validate=True)` checks every stream
the spec opens against the song grammar and reports malformed ones in
`player.validator.failures()`.

`DmcPlayer(source).render_grid(nframes)` is the per-frame `$D400..$D418` register
grid (25 registers, forward-filled, pulse-width-high nibble-masked) — the shape
the sidtrace oracle produces. `iter_register_writes(song, max_frames)` yields one
`RegWrite(clock, reg, val)` per SID write, frames `cycles_per_frame` apart (the
shared `py*` register-log convention, via
`pysidtracker.register_writes_from_player`).

The oracle-marked tests check the players over whole songs: the register grid equals
the tune's own 6502 run and the sidtrace oracle, and all 64 KiB outside the stack
page equal the tune's own 6502 run after every call. `tv.validate` proves the
init-`$37` note-row reader (`voice[x].row_note`, the 6502 routine at `base+$1a2`)
equal to the code for every initial memory.

`song.byte_exact()` reports whether the recognised body is one the specs reproduce
byte-for-byte against the sidtrace oracle: the init-`$37` and the modelled init-`$1d`
generations (both release variants) and the reorganised `$a1`/`$95`/`$94a` engines,
gated out only for a build whose play entry is wrapped by something other than a
benign pass-through (a thunk or transparent multispeed divider that re-enters the
standard play body is followed), whose AD/SR helper is relocated, whose release is
patched to an unrecognised routine, or (for the reorganised engines) whose header
vectors resolve outside the resident player (a subtune-selector / multispeed
wrapper). `tests/test_corpus.py` validates a deterministic HVSC sample against a
local `$HVSC` tree, and `tests/test_oracle_hvsc.py` (the `oracle` marker, a
dedicated CI job) confirms `DmcPlayer.render_grid` frame-for-frame against the
deterministic `sidplayfp` [`sidtrace`](https://github.com/anarkiwi/sidtrace)
oracle over a real HVSC representative of every generation — init-`$37`/`$1d`
(with the code-read scene patches), the `$a1`/`$95` engines, the `$94a` 2-level
dispatch and its `$937` CIA-multispeed wrapper, and a benign play-wrapper build.
A small residual of per-tune scene micro-patches and CIA-multispeed wrappers the
specs do not state (e.g. a 2× CIA-reprogrammed play rate) is recognised but
diverges from the single-speed render, so it is excluded from the byte-exact oracle
set.

## HVSC oracle survey

[`scripts/oracle_hvsc.py`](../scripts/oracle_hvsc.py) plugs `DmcPlayer` into the
[`pysidtracker.survey`](https://github.com/anarkiwi/pysidtracker/blob/main/docs/survey.md)
whole-song comparison against the `sidtrace` oracle (Docker), over every subtune
of every HVSC tune SIDId names `DMC` (`PLAYER`). Its `info` is `engine` (the
name of the spec the player runs), the song's build variant (`Variant.as_info()`, below) and
`pysidtracker.cadence.cadence_info`'s `rsid`, play `trigger`, `speed` and
`dynamic`. A tune without the DMC player signature raises `DmcParseError` (a
`pysidtracker.SidParseError`), which the survey records as `not_decompiled`.

```bash
pip install "pysidtracker[survey]>=0.43.0"
python -m pysidtracker.survey find $HVSC --player '^\(?dmc(_v[45]\.x\))?$' --out .hvsc-survey/tunes.csv
PYTHONPATH=scripts python -m pysidtracker.survey run $HVSC .hvsc-survey/tunes.csv \
    --work .hvsc-survey --render oracle_hvsc:render --jobs 32
python -m pysidtracker.survey report .hvsc-survey/results.csv
python -m pysidtracker.survey triage .hvsc-survey
PYTHONPATH=scripts python -m pysidtracker.survey diff $HVSC .hvsc-survey/tunes.csv <md5>_<song> \
    --work .hvsc-survey --render oracle_hvsc:render
python -m pysidtracker.survey file scripts/oracle_hvsc.py $HVSC/DEMOS/A-F/FST.sid \
    --song all --frames 3000 --work .hvsc-survey
```

`survey file` compares one tune's subtunes (`--song N|N-M|list|all`) with the
oracle. `.hvsc-survey/` holds copyright-derived traces and is gitignored.

## Build variant

`song.facets()` is the tune's `pysidtracker.facets.Variant` over the
`pydmcsid.variant.FACETS` registry: every value the players and the byte-exact
gate read from the tune's own code, each with its source (`detected@$addr` where
its code site matched, `default` where the player falls back, or `inferred`):

| Kind | Facets |
|---|---|
| build | `generation` (`v37`/`v1d`/`a1`/`n95`/`nn`) |
| layout | `layout` (the `locate()` strategy: `dispatch` or `nn-dispatch`), `stub` (dispatch above the load address), `play_rel`, `nn_shift` (the `$94a` virtual base), `note_cells` (the `$1d` work-cell block), `rest_tail`, `order_table` (init store site, else the fixed operand as `default`) |
| release | `byte_exact`, `release`, `pw_min_shift`, `order_loop`, `tail_d418`, `onset_ctrl`, `onset`, `burst_imm`, `release_sr`, `release_gate` |
| init | `wrapper` (`none`/`benign`/`selector`/`multispeed`/`foreign`), `ms_reload`, `ms_seed` |

The registered `dmc` pysidtracker format exposes it as `variant` (so
`pysidtracker variant census` classes tunes by it) and `facet_sites` (the
`{addr: facet}` labels whydiff / pathdiff put on 6502 edges).
`pysidtracker snapshot build --format dmc` digests every DMC tune's decoded
model, whole-song render and the format's probes (`byte_exact`, the canonical
stream text) under a tune tree, and `pysidtracker snapshot diff` shows which tunes
a refactor changed; `pysidtracker grammar` lists a tune's streams and
`pysidtracker equiv` compares two tunes' canonical text.

## Export

`to_prg(song)` / `to_sid(song)` / `write(song, path)` serialize a parsed tune
back to its packed player+data image (`to_prg` a bare `.prg` = load address +
resident image; `to_sid` a PSID/RSID container). The resident image is reproduced
byte-for-byte, so `parse(to_prg(song))` yields the same tune and a re-wrapped
`.sid` reproduces it frame-for-frame on the jennings oracle.

The image is not a file the DMC editors load in general. A DMC V1-V4/V7.0 editor
save is the same image written from `$1000`, and `to_prg` returns such a save
unchanged. The C64 editors load with a plain KERNAL `LOAD` forced to `$1000` and
never read a `.sid` or depack anything, so an export is editor-loadable only for a
`v37`/`v1d` build with `load == base == $1000` and no stub. A build at another
base, or a stub-prefixed image, loads at the wrong address; the V5-era
`a1`/`n95`/`nn` engines have no editor form (V5 editors load only their `$4000`
work file).

## References

- DMC (Demo Music Creator) player by Brian/Graffity; stated as playspecs from the
  DMC disassembly.
- HVSC `DMC` sidid family.
- [`pysidtracker`](https://github.com/anarkiwi/pysidtracker) — shared
  container/image/detection base.
