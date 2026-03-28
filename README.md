# Stockfish Depth Tester

Measure typical Stockfish search depth at given time controls.

Version 1.0

Copyright 2026 Maxim Masiutin.

License: GPL-3.0 (see [LICENSE](LICENSE))

## Overview

Runs Stockfish on test positions using `go movetime`, where movetime
approximates average time per move: `(base + 60 * inc) / 60`.
Records final depth and seldepth for each position, computes min, max,
mean, and median.

## Usage

Run standard Fishtest TCs (STC, LTC, STC SMP, LTC SMP):

```
    python measure_depth_at_tc.py
    python measure_depth_at_tc.py --exe ./stockfish
```

Run custom TC:

```
    python measure_depth_at_tc.py --tc 10+0.1 --threads 1
    python measure_depth_at_tc.py --tc 120+1 --threads 8
```

Use opening book positions (EPD format):

```
    python measure_depth_at_tc.py --tc 120+1 --threads 8 --book UHO_Lichess_4852_v1.epd -n 20
    python measure_depth_at_tc.py --tc 120+1 --threads 8 --book UHO_Lichess_4852_v1.epd -n 20 --seed 42
```

Save output (.txt, .csv, or .json auto-detected from extension):

```
    python measure_depth_at_tc.py -o results.txt
    python measure_depth_at_tc.py -o results.csv
    python measure_depth_at_tc.py -o results.json
```

## Options

| Option | Description |
|--------|-------------|
| `--exe PATH` | Path to stockfish executable (default: stockfish.exe on Windows, ./stockfish on Linux) |
| `--tc BASE+INC` | Custom time control, e.g. `10+0.1` (overrides standard TCs) |
| `--threads N` | Thread count (required with --tc) |
| `--book FILE` | EPD file with opening positions (random sample) |
| `-n N` | Number of positions to sample from book (default: 20) |
| `--seed N` | Random seed for reproducible book sampling |
| `-o FILE` | Save output to file (.txt, .csv, or .json) |

## Example output

```
    === TC 120+1, 8T ===
      movetime: 3000ms per move
      Positions tested: 20

      Position                                                  Depth  SelDepth
      -------------------------------------------------------- ------ ---------
      rnb1kb1r/ppppqppp/8/3Q4/2B5/8/PPP1NPPP/R1B1K2R b KQkq        26        51
      r1bqk1nr/pp3ppp/1n1ppb2/2pP4/2P1P3/1PN2N2/P1Q2PPP/R1B1KB1R   23        48
      ...

      Depth:    min=  6  max= 27  mean= 16.6  median= 23.0
      SelDepth: min=  8  max= 78  mean= 33.5  median= 46.0
```

## Terminology

- **Depth**: The nominal search depth completed by the engine (number of full plies searched in iterative deepening).
- **SelDepth** (Selective Depth): The maximum ply reached in any single line during the search, including extensions (checks, singular moves) and quiescence search. SelDepth is always >= Depth because some lines are explored deeper than others. For example, `depth 20 seldepth 35` means the engine completed depth 20, but the deepest variation explored reached ply 35.

## Method

Uses `go movetime` with time calculated as `(base + 60 * inc) / 60 * 1000` ms,
assuming 60-move average game length. This approximates the average time per
move that Stockfish would use under real game conditions with the given TC.

Without `--book`, uses 12 built-in middlegame positions from the Stockfish bench
set (trivial endgames excluded). With `--book`, samples random positions from
the provided EPD file.

## Requirements

- Python 3.12+ (stdlib only, no external dependencies)
- Stockfish executable
- Cross-platform: Windows, Linux, macOS without modification

## Architecture

- Single-file script with typed helpers
- `Position = tuple[str, str]` (label, FEN)
- `Config = dict[str, Any]` (label, threads, base, inc)
- One Stockfish process per position (isolated, no state leakage)
- Output formats: text (console), CSV, JSON

## Code Quality

- Python 3.12+ syntax (use `X | Y` union types, not `Optional[X]`)
- Type annotations on all function signatures
- Run `mypy --strict measure_depth_at_tc.py` before committing
- Run `pylint measure_depth_at_tc.py` before committing
- Use `subprocess.run` for one-shot commands, `subprocess.Popen` for interactive I/O
- Graceful fallbacks when OS-specific detection fails (CPU info, paths)

