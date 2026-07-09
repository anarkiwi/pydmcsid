# pydmcsid

Pure-Python reader and player for **DMC (Demo Music Creator)** C64 SID tunes —
the player by Brian/Graffity (`-PLAYER (C) BRIAN/GRAFFITY!`).

Consumes `.sid` files (PSID/RSID containers) and bare `.prg` images through the
shared [`pysidtracker`](https://github.com/anarkiwi/pysidtracker) base: the
resident DMC player plus its per-tune data are loaded as a memory image, the
per-tune table bases are located from the player-code operands (relocated and
short-stub-prepended images are found), and container headers are not trusted.

## Install

```bash
pip install pydmcsid
```

## Usage

```python
import pydmcsid

song = pydmcsid.read("tune.sid")       # path, bytes, or binary file object
print(song.byte_exact())               # is this build reproduced frame-for-frame?

for w in pydmcsid.iter_register_writes(song, max_frames=50 * 60):
    print(w.clock, w.reg, w.val)       # absolute CPU cycle, $D4xx reg offset, value
```

`iter_register_writes` follows the shared `py*` register-log convention: one
`RegWrite(clock, reg, val)` per SID write, frames `cycles_per_frame` apart.

## Development

```bash
pip install -e ".[dev]"
pytest
```

See [docs/format.md](docs/format.md) for the DMC data model, recognition
anchors, supported builds, and byte-exact coverage.

## License

Apache 2.0 — see [`LICENSE`](LICENSE).
