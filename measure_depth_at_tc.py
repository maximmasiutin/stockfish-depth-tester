#!/usr/bin/env python3
"""Measure typical search depth at given time controls.

Version 1.0
Copyright 2026 Maxim Masiutin.
License: GPL-3.0

Usage:
  python measure_depth_at_tc.py [options]

  Run standard Fishtest TCs (STC, LTC, STC SMP, LTC SMP):
    python measure_depth_at_tc.py
    python measure_depth_at_tc.py --exe ./stockfish

  Run custom TC:
    python measure_depth_at_tc.py --tc 10+0.1 --threads 1
    python measure_depth_at_tc.py --tc 20+0.2 --threads 8

  Use opening book positions:
    python measure_depth_at_tc.py --tc 120+1 --threads 8 --book book.epd -n 20

  Save output:
    python measure_depth_at_tc.py -o results.txt
    python measure_depth_at_tc.py -o results.csv
    python measure_depth_at_tc.py -o results.json

  Output format is auto-detected from file extension (.txt, .csv, .json).

Method:
  Runs Stockfish on test positions using "go movetime", where movetime
  approximates average time per move: (base + 60 * inc) / 60.
  Records final depth and seldepth for each position, computes min, max,
  mean, and median.
"""

import argparse
import csv
import io
import json
import os
import random
import re
import statistics
import subprocess
import sys
from typing import Any

DEFAULT_EXE = "stockfish.exe" if sys.platform == "win32" else "./stockfish"

Position = tuple[str, str]
Config = dict[str, Any]
ResultDict = dict[str, Any]

# Built-in middlegame positions from bench set.
# Excludes trivial endgames (KRK, KBK) that reach depth 60-80 instantly.
BUILTIN_POSITIONS: list[Position] = [
    ("startpos", "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"),
    ("kiwipete", "r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 10"),
    ("tactical", "r3k2r/Pppp1ppp/1b3nbN/nP6/BBP1P3/q4N2/Pp1P2PP/R2Q1RK1 w kq - 0 1"),
    ("open", "rnbq1k1r/pp1Pbppp/2p5/8/2B5/8/PPP1NnPP/RNBQK2R w KQ - 1 8"),
    ("closed", "r4rk1/1pp1qppp/p1np1n2/2b1p1B1/2B1P1b1/P1NP1N2/1PP1QPPP/R4RK1 w - - 0 10"),
    ("sicilian", "rnbqkb1r/pppppppp/5n2/8/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 1 2"),
    ("e4e5", "r1bqkbnr/pppppppp/2n5/8/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 1 2"),
    ("ruylopez", "r1bqkbnr/1ppp1ppp/p1n5/4p3/B3P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 0 4"),
    ("qgd", "rnbqkb1r/ppp2ppp/4pn2/3p4/2PP4/2N5/PP2PPPP/R1BQKBNR w KQkq - 2 4"),
    ("middlegame1", "r2qk2r/pppbbppp/2n1pn2/3p4/3P1B2/2NBPN2/PPP2PPP/R2QK2R w KQkq - 4 7"),
    ("middlegame2", "r1bq1rk1/pp2bppp/2n1pn2/2pp4/3P4/2NBPN2/PPP1BPPP/R2Q1RK1 w - - 0 9"),
    ("complex", "r2q1rk1/1b2bppp/ppnppn2/8/2PNP3/1PN1BP2/P5PP/R2QB1K1 w - - 0 13"),
]

# Standard Fishtest time controls
STANDARD_CONFIGS: list[Config] = [
    {"label": "STC (10+0.1, 1T)",      "threads": 1, "base": 10, "inc": 0.1},
    {"label": "LTC (60+0.6, 1T)",      "threads": 1, "base": 60, "inc": 0.6},
    {"label": "STC SMP (5+0.05, 8T)",  "threads": 8, "base": 5,  "inc": 0.05},
    {"label": "LTC SMP (20+0.2, 8T)",  "threads": 8, "base": 20, "inc": 0.2},
]

AVG_MOVES = 60


def load_book_positions(
    path: str, n: int, seed: int | None = None
) -> list[Position]:
    """Load n random positions from an EPD file."""
    try:
        with open(path, encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        print(f"Error: book file not found: {path}", file=sys.stderr)
        sys.exit(1)
    rng = random.Random(seed)
    selected = rng.sample(lines, min(n, len(lines)))
    positions: list[Position] = []
    for epd_line in selected:
        # EPD has 4 FEN fields (board, side, castling, ep) then opcodes.
        # Full FEN has 6 fields (adding halfmove clock and fullmove number).
        fields = epd_line.split()
        if len(fields) >= 6 and fields[4].isdigit() and fields[5].isdigit():
            fen = " ".join(fields[:6])
        elif len(fields) >= 4:
            fen = " ".join(fields[:4]) + " 0 1"
        else:
            fen = epd_line
        positions.append((fen, fen))
    return positions


def _parse_depths(output: str) -> tuple[list[int], list[int]]:
    """Extract final depth and seldepth per position from engine output."""
    depths: list[int] = []
    seldepths: list[int] = []
    last_depth = 0
    last_seldepth = 0
    found = False
    for line in output.splitlines():
        m = re.search(r"info depth (\d+) seldepth (\d+) .*score", line)
        if m:
            last_depth = int(m.group(1))
            last_seldepth = int(m.group(2))
            found = True
        elif line.startswith("bestmove") and found:
            depths.append(last_depth)
            seldepths.append(last_seldepth)
            found = False
    return depths, seldepths


def run_config(
    exe: str, cfg: Config, positions: list[Position]
) -> tuple[list[int], list[int], int]:
    """Run Stockfish on positions with given config, return depths and seldepths."""
    total_time = cfg["base"] + AVG_MOVES * cfg["inc"]
    movetime_ms = int(total_time / AVG_MOVES * 1000)
    threads: int = cfg["threads"]

    parts = [
        "uci",
        f"setoption name Threads value {threads}",
        "setoption name Hash value 256",
        "isready",
    ]
    for _label, fen in positions:
        parts.append(f"position fen {fen}")
        parts.append(f"go movetime {movetime_ms}")
    parts.append("quit")
    commands = "\n".join(parts) + "\n"

    timeout_s = max(600, len(positions) * movetime_ms // 1000 * 3)
    try:
        result = subprocess.run(
            [exe], input=commands, capture_output=True,
            text=True, timeout=timeout_s, check=False)
    except FileNotFoundError:
        print(f"Error: executable not found: {exe}", file=sys.stderr)
        return [], [], movetime_ms
    except subprocess.TimeoutExpired:
        print(f"Error: timeout after {timeout_s}s", file=sys.stderr)
        return [], [], movetime_ms

    depths, seldepths = _parse_depths(result.stdout + result.stderr)
    return depths, seldepths, movetime_ms


def format_results(
    cfg: Config,
    depths: list[int],
    seldepths: list[int],
    movetime_ms: int,
    positions: list[Position],
) -> ResultDict | None:
    """Return dict with all results for one config."""
    if not depths:
        return None
    per_position = []
    for i, (d, sd) in enumerate(zip(depths, seldepths, strict=True)):
        name = positions[i][0] if i < len(positions) else str(i + 1)
        per_position.append({"position": name, "depth": d, "seldepth": sd})
    return {
        "label": cfg["label"],
        "threads": cfg["threads"],
        "base": cfg["base"],
        "inc": cfg["inc"],
        "movetime_ms": movetime_ms,
        "positions_tested": len(depths),
        "depth_min": min(depths),
        "depth_max": max(depths),
        "depth_mean": round(statistics.mean(depths), 1),
        "depth_median": round(statistics.median(depths), 1),
        "seldepth_min": min(seldepths),
        "seldepth_max": max(seldepths),
        "seldepth_mean": round(statistics.mean(seldepths), 1),
        "seldepth_median": round(statistics.median(seldepths), 1),
        "per_position": per_position,
    }


def print_results(all_results: list[ResultDict | None]) -> None:
    """Print formatted results for all configs."""
    for r in all_results:
        if r is None:
            continue
        print(f"=== {r['label']} ===")
        print(f"  movetime: {r['movetime_ms']}ms per move")
        print(f"  Positions tested: {r['positions_tested']}")
        print()

        # Determine position column width from longest name
        max_name = max(14, *(len(p["position"]) for p in r["per_position"]))
        fmt = f"  %-{max_name}s %6s %9s"
        print(fmt % ("Position", "Depth", "SelDepth"))
        print(fmt % ("-" * max_name, "-" * 6, "-" * 9))
        for p in r["per_position"]:
            print(fmt % (p["position"], p["depth"], p["seldepth"]))
        print()
        print(f"  Depth:    min={r['depth_min']:>3}  max={r['depth_max']:>3}  "
              f"mean={r['depth_mean']:>5.1f}  median={r['depth_median']:>5.1f}")
        print(f"  SelDepth: min={r['seldepth_min']:>3}  max={r['seldepth_max']:>3}  "
              f"mean={r['seldepth_mean']:>5.1f}  median={r['seldepth_median']:>5.1f}")
        print()

    # Summary table
    print("=== Summary: Median Depth ===")
    print(f"  {'TC':<30} {'Depth':>8} {'SelDepth':>11}")
    print(f"  {'-' * 30} {'-' * 8} {'-' * 11}")
    for r in all_results:
        if r is None:
            continue
        print(f"  {r['label']:<30} {r['depth_median']:>8.1f} {r['seldepth_median']:>11.1f}")
    print()


def save_txt(all_results: list[ResultDict | None], path: str) -> None:
    """Save formatted results to a text file."""
    buf = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = buf
    try:
        print_results(all_results)
    finally:
        sys.stdout = old_stdout
    with open(path, "w", newline="\n", encoding="utf-8") as f:
        f.write(buf.getvalue())


def save_csv(all_results: list[ResultDict | None], path: str) -> None:
    """Save summary statistics to a CSV file."""
    fieldnames = [
        "label", "threads", "base", "inc", "movetime_ms",
        "depth_min", "depth_max", "depth_mean", "depth_median",
        "seldepth_min", "seldepth_max", "seldepth_mean", "seldepth_median",
    ]
    rows = []
    for r in all_results:
        if r is None:
            continue
        rows.append({k: r[k] for k in fieldnames})
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


def save_json(all_results: list[ResultDict | None], path: str) -> None:
    """Save full results to a JSON file."""
    data = [r for r in all_results if r is not None]
    with open(path, "w", newline="\n", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def parse_tc(tc_str: str) -> tuple[float, float]:
    """Parse 'base+inc' string, e.g. '10+0.1' -> (10.0, 0.1)."""
    parts = tc_str.split("+")
    if len(parts) != 2:
        raise ValueError("TC must be in format base+inc, e.g. 10+0.1")
    try:
        base, inc = float(parts[0]), float(parts[1])
    except ValueError as exc:
        raise ValueError(f"Invalid TC values: {tc_str}") from exc
    if base <= 0:
        raise ValueError(f"Base time must be positive, got {base}")
    if inc < 0:
        raise ValueError(f"Increment must be non-negative, got {inc}")
    return base, inc


def _save_output(all_results: list[ResultDict | None], path: str) -> None:
    """Save results to file, format auto-detected from extension."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".csv":
        save_csv(all_results, path)
    elif ext == ".json":
        save_json(all_results, path)
    else:
        save_txt(all_results, path)
    print(f"Saved to {path}")


def main() -> None:
    """Entry point: parse arguments, run configs, display and save results."""
    parser = argparse.ArgumentParser(
        description="Measure typical search depth at given time controls",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    parser.add_argument("--exe", default=DEFAULT_EXE,
                        help=f"Path to stockfish executable (default: {DEFAULT_EXE})")
    parser.add_argument("--tc", type=str, default=None,
                        help="Custom TC as base+inc, e.g. 10+0.1 (overrides standard TCs)")
    parser.add_argument("--threads", type=int, default=None,
                        help="Thread count for custom TC (required with --tc)")
    parser.add_argument("--book", type=str, default=None,
                        help="EPD file with opening positions (random sample)")
    parser.add_argument("-n", "--num-positions", type=int, default=20,
                        help="Number of positions to sample from book (default: 20)")
    parser.add_argument("--seed", type=int, default=None,
                        help="Random seed for book sampling (for reproducibility)")
    parser.add_argument("-o", "--output", type=str, default=None,
                        help="Save output to file (.txt, .csv, or .json)")
    args = parser.parse_args()

    if args.num_positions < 1:
        parser.error("--num-positions must be at least 1")

    positions: list[Position]
    if args.book:
        positions = load_book_positions(args.book, args.num_positions, args.seed)
        pos_desc = f"{len(positions)} positions from {os.path.basename(args.book)}"
    else:
        positions = BUILTIN_POSITIONS
        pos_desc = f"{len(positions)} built-in positions"

    configs: list[Config]
    if args.tc:
        if args.threads is None:
            parser.error("--threads is required when using --tc")
        if args.threads <= 0:
            parser.error("--threads must be a positive integer")
        try:
            base, inc = parse_tc(args.tc)
        except ValueError as e:
            parser.error(str(e))
        configs = [{"label": f"TC {args.tc}, {args.threads}T",
                     "threads": args.threads, "base": base, "inc": inc}]
    else:
        configs = STANDARD_CONFIGS

    print(f"Executable: {args.exe}")
    print(f"Positions: {pos_desc}")
    print(f"Assumed average game length: {AVG_MOVES} moves")
    print()

    all_results: list[ResultDict | None] = []
    for cfg in configs:
        print(f"Running {cfg['label']} ...", flush=True)
        depths, seldepths, movetime_ms = run_config(args.exe, cfg, positions)
        r = format_results(cfg, depths, seldepths, movetime_ms, positions)
        all_results.append(r)
        if r is None:
            print("  NO DATA")
        else:
            print(f"  median depth={r['depth_median']:.1f}, "
                  f"seldepth={r['seldepth_median']:.1f}")

    print()
    print_results(all_results)

    if args.output:
        _save_output(all_results, args.output)


if __name__ == "__main__":
    main()
