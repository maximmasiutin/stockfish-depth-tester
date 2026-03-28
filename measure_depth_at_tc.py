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

  Run custom TC (one or more):
    python measure_depth_at_tc.py --tc 10+0.1 --threads 1
    python measure_depth_at_tc.py --tc 5+0.05 20+0.2 60+0.6 --threads 8
    python measure_depth_at_tc.py --tc 10+0.1  (threads default to min(CPU count, 8))

  Use opening book positions:
    python measure_depth_at_tc.py --tc 120+1 --threads 8 --book book.epd -n 30 --seed 1

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
import functools
import io
import json
import os
import pathlib
import platform
import random
import re
import shutil
import statistics
import subprocess
import sys
import time
from typing import Any

DEFAULT_EXE = "stockfish.exe" if sys.platform == "win32" else "./stockfish"


def _validate_executable(path: str) -> None:
    """Validate that path points to an existing file. Prevents command injection."""
    resolved = pathlib.Path(path).resolve()
    if not resolved.is_file():
        print(f"Error: executable not found: {resolved}", file=sys.stderr)
        sys.exit(1)


def _validate_output_path(path: str) -> None:
    """Validate that output path parent directory exists. Prevents path traversal."""
    resolved = pathlib.Path(path).resolve()
    if not resolved.parent.is_dir():
        print(f"Error: output directory does not exist: {resolved.parent}",
              file=sys.stderr)
        sys.exit(1)

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
MAX_DEFAULT_THREADS = 8


def _detect_cpu_name() -> str:
    """Detect CPU model name using OS-specific methods."""
    system = platform.system()
    if system == "Windows":
        # platform.processor() on Windows returns description like
        # "Intel64 Family 6 Model 85 Stepping 7, GenuineIntel".
        # Try WMI via wmic for a friendlier name.
        try:
            result = subprocess.run(
                ["wmic", "cpu", "get", "name"],
                capture_output=True, text=True, timeout=5, check=False,
            )
            for line in result.stdout.strip().splitlines():
                line = line.strip()
                if line and line.lower() != "name":
                    return line
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass
    elif system == "Linux":
        try:
            with open("/proc/cpuinfo", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("model name"):
                        return line.split(":", 1)[1].strip()
        except OSError:
            pass
    elif system == "Darwin":
        try:
            result = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True, text=True, timeout=5, check=False,
            )
            name = result.stdout.strip()
            if name:
                return name
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass
    return platform.processor() or "Unknown CPU"


@functools.lru_cache(maxsize=1)
def get_cpu_info() -> tuple[str, int]:
    """Detect CPU model name and available thread count.

    Returns (cpu_name, available_threads). Works on Windows, Linux, and macOS.
    Falls back to platform.processor() if OS-specific detection fails.
    """
    if hasattr(os, "sched_getaffinity"):
        available_threads = len(os.sched_getaffinity(0))
    else:
        available_threads = os.cpu_count() or 1
    return _detect_cpu_name(), available_threads


def load_book_positions(
    path: str, n: int, seed: int | None = None
) -> tuple[list[Position], list[int]]:
    """Load n random positions from an EPD file. Returns (positions, line_numbers)."""
    try:
        with open(path, encoding="utf-8") as f:
            all_lines = [(i + 1, line.strip()) for i, line in enumerate(f) if line.strip()]
    except FileNotFoundError:
        print(f"Error: book file not found: {path}", file=sys.stderr)
        sys.exit(1)
    rng = random.Random(seed)
    selected = rng.sample(all_lines, min(n, len(all_lines)))
    positions: list[Position] = []
    line_numbers: list[int] = []
    for line_num, epd_line in selected:
        # EPD has 4 FEN fields (board, side, castling, ep) then opcodes.
        # Full FEN has 6 fields (adding halfmove clock and fullmove number).
        fields = epd_line.split()
        if len(fields) >= 6 and fields[4].isdigit() and fields[5].isdigit():
            fen = " ".join(fields[:6])
        elif len(fields) >= 4:
            fen = " ".join(fields[:4]) + " 0 1"
        else:
            fen = epd_line
        positions.append((f"line {line_num}", fen))
        line_numbers.append(line_num)
    return positions, line_numbers



def _validate_engine(exe: str) -> None:
    """Check that the engine executable exists and responds to UCI."""
    if not shutil.which(exe):
        if os.path.isfile(exe):
            print(f"Error: engine is not executable: {exe}", file=sys.stderr)
        else:
            print(f"Error: engine not found: {exe}", file=sys.stderr)
        sys.exit(1)
    _validate_executable(exe)
    try:
        with subprocess.Popen(
            [exe], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True,
        ) as proc:
            if proc.stdin is None or proc.stdout is None:
                print(f"Error: cannot connect to engine stdio: {exe}",
                      file=sys.stderr)
                sys.exit(1)
            try:
                stdout, _ = proc.communicate(input="uci\nquit\n", timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()
                print(f"Error: engine timed out during UCI handshake: {exe}",
                      file=sys.stderr)
                sys.exit(1)
            if "uciok" not in stdout:
                print(f"Error: engine did not respond to UCI: {exe}",
                      file=sys.stderr)
                sys.exit(1)
    except FileNotFoundError:
        print(f"Error: engine not found: {exe}", file=sys.stderr)
        sys.exit(1)
    except OSError as e:
        print(f"Error: cannot start engine: {exe}: {e}", file=sys.stderr)
        sys.exit(1)


def _run_single_position(
    exe: str, fen: str, threads: int, movetime_ms: int
) -> tuple[int, int]:
    """Run one position in a fresh Stockfish process. Returns (depth, seldepth)."""
    timeout_s = max(30, movetime_ms // 1000 * 5)
    _validate_executable(exe)
    try:
        proc = subprocess.Popen(  # pylint: disable=consider-using-with
            [exe], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True)
    except OSError as e:
        print(f"Error: cannot start engine: {exe}: {e}", file=sys.stderr)
        sys.exit(1)
    if proc.stdin is None or proc.stdout is None:
        print(f"Error: cannot connect to engine stdio: {exe}", file=sys.stderr)
        proc.kill()
        proc.wait()
        sys.exit(1)
    proc.stdin.write("uci\n")
    proc.stdin.write(f"setoption name Threads value {threads}\n")
    proc.stdin.write("setoption name Hash value 256\n")
    proc.stdin.write("isready\n")
    proc.stdin.write(f"position fen {fen}\n")
    proc.stdin.write(f"go movetime {movetime_ms}\n")
    proc.stdin.flush()
    last_depth = 0
    last_seldepth = 0
    deadline = time.time() + timeout_s
    for line in proc.stdout:
        if time.time() > deadline:
            break
        line = line.strip()
        m = re.search(r"info depth (\d+) seldepth (\d+) .*score", line)
        if m:
            last_depth = int(m.group(1))
            last_seldepth = int(m.group(2))
        if line.startswith("bestmove"):
            break
    try:
        proc.stdin.write("quit\n")
        proc.stdin.flush()
    except (BrokenPipeError, OSError):
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    return last_depth, last_seldepth


def run_config(
    exe: str, cfg: Config, positions: list[Position]
) -> tuple[list[int], list[int], int]:
    """Run Stockfish on each position in a separate process."""
    total_time = cfg["base"] + AVG_MOVES * cfg["inc"]
    movetime_ms = int(total_time / AVG_MOVES * 1000)
    threads: int = cfg["threads"]

    depths: list[int] = []
    seldepths: list[int] = []
    for _label, fen in positions:
        d, sd = _run_single_position(exe, fen, threads, movetime_ms)
        depths.append(d)
        seldepths.append(sd)
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
    print("=== Summary: Mean Depth ===")
    print(f"  {'TC':<30} {'Depth':>8} {'SelDepth':>11}")
    print(f"  {'-' * 30} {'-' * 8} {'-' * 11}")
    for r in all_results:
        if r is None:
            continue
        print(f"  {r['label']:<30} {r['depth_mean']:>8.1f} {r['seldepth_mean']:>11.1f}")
    print()




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
    _validate_output_path(path)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


def save_json(all_results: list[ResultDict | None], path: str) -> None:
    """Save full results to a JSON file."""
    _validate_output_path(path)
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


def _default_threads() -> int:
    """Return default thread count: min(available CPUs, MAX_DEFAULT_THREADS)."""
    _, available = get_cpu_info()
    return min(available, MAX_DEFAULT_THREADS)


def _build_configs(
    parser: argparse.ArgumentParser, args: argparse.Namespace
) -> list[Config]:
    """Build config list from parsed arguments."""
    _, available = get_cpu_info()
    if args.threads is not None:
        if args.threads <= 0:
            parser.error("--threads must be a positive integer")
        if args.threads > available:
            parser.error(
                f"--threads {args.threads} exceeds available CPUs ({available})")
    if args.tc:
        threads: int = args.threads if args.threads is not None else _default_threads()
        configs: list[Config] = []
        for tc_str in args.tc:
            try:
                base, inc = parse_tc(tc_str)
            except ValueError as e:
                parser.error(str(e))
            configs.append({"label": f"TC {tc_str}, {threads}T",
                            "threads": threads, "base": base, "inc": inc})
        return configs
    # Standard configs: cap SMP thread counts at available CPUs
    default_t = args.threads if args.threads is not None else _default_threads()
    capped: list[Config] = []
    for cfg in STANDARD_CONFIGS:
        t = min(cfg["threads"], default_t)
        label = re.sub(r"\d+T\)", f"{t}T)", cfg["label"])
        capped.append({**cfg, "threads": t, "label": label})
    return capped


def _format_hardware_line(threads_used: int) -> str:
    """Format the hardware info line for display."""
    cpu_name, available_threads = get_cpu_info()
    return (f"Hardware: {cpu_name}. "
            f"The test used {threads_used} thread{'s' if threads_used != 1 else ''} "
            f"out of {available_threads} available on that CPU")


def _capture_full_output(
    args: argparse.Namespace, pos_desc: str,
    book_line_numbers: list[int], all_results: list[ResultDict | None],
    max_threads_used: int,
) -> str:
    """Reproduce all console output as a string for saving."""
    buf = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = buf
    try:
        print(f"Executable: {args.exe}")
        print(_format_hardware_line(max_threads_used))
        print(f"Positions: {pos_desc}")
        if args.seed is not None:
            print(f"Seed: {args.seed}")
        if book_line_numbers:
            print(f"Book lines: {', '.join(str(n) for n in book_line_numbers)}")
        print(f"Assumed average game length: {AVG_MOVES} moves")
        print()
        for r in all_results:
            if r is None:
                print("NO DATA")
            else:
                print(f"Running {r['label']} ...")
                print(f"  median depth={r['depth_median']:.1f}, "
                      f"seldepth={r['seldepth_median']:.1f}")
        print()
        print_results(all_results)
    finally:
        sys.stdout = old_stdout
    return buf.getvalue()


def _save_output(
    all_results: list[ResultDict | None], path: str, full_log: str
) -> None:
    """Save results to file, format auto-detected from extension."""
    _validate_output_path(path)
    ext = os.path.splitext(path)[1].lower()
    if ext == ".csv":
        save_csv(all_results, path)
    elif ext == ".json":
        save_json(all_results, path)
    else:
        with open(path, "w", newline="\n", encoding="utf-8") as f:
            f.write(full_log)
    print(f"Saved to {path}")


def main() -> None:
    """Entry point: parse arguments, run configs, display and save results."""
    parser = argparse.ArgumentParser(
        description="Measure typical search depth at given time controls",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    parser.add_argument("--exe", default=DEFAULT_EXE,
                        help=f"Path to stockfish executable (default: {DEFAULT_EXE})")
    parser.add_argument("--tc", type=str, nargs="+", default=None,
                        help="One or more TCs as base+inc, e.g. 5+0.05 20+0.2 60+0.6")
    parser.add_argument("--threads", type=int, default=None,
                        help="Thread count (default: min(CPU count, 8))")
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
    book_line_numbers: list[int] = []
    if args.book:
        positions, book_line_numbers = load_book_positions(
            args.book, args.num_positions, args.seed)
        pos_desc = f"{len(positions)} positions from {os.path.basename(args.book)}"
    else:
        positions = BUILTIN_POSITIONS
        pos_desc = f"{len(positions)} built-in positions"

    configs = _build_configs(parser, args)

    _validate_executable(args.exe)
    _validate_engine(args.exe)

    max_threads_used = max(cfg["threads"] for cfg in configs)

    print(f"Executable: {args.exe}")
    print(_format_hardware_line(max_threads_used))
    print(f"Positions: {pos_desc}")
    if args.seed is not None:
        print(f"Seed: {args.seed}")
    if book_line_numbers:
        print(f"Book lines: {', '.join(str(n) for n in book_line_numbers)}")
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
        _save_output(all_results, args.output, _capture_full_output(
            args, pos_desc, book_line_numbers, all_results, max_threads_used))


if __name__ == "__main__":
    main()
