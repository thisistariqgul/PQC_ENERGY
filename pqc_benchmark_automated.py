#!/usr/bin/env python3
#
#Simple automation wrapper for pqc_benchmark.py
# Runs all methods and algorithms with a single user input: iteration count.
# Time and machine-power energy are recorded by pqc_benchmark.py.
# No external dependencies required.
# OG

import os
import subprocess
import sys
import argparse

HERE = os.path.dirname(os.path.abspath(__file__))
BENCHMARK = os.path.join(HERE, "pqc_benchmark.py")
POWER_MONITOR = os.path.join(HERE, "power_monitor.py")


def build_input_sequence(iterations):
    """Build the full sequence of inputs for all methods and algorithms."""
    lines = []
    
    # Method 0: Key generation (19 algorithms)
    for algo_idx in range(19):
        lines.append("y")               # Press y to start
        lines.append("0")               # Method: key generation
        lines.append(str(algo_idx))     # Algorithm index
        lines.append(str(iterations))   # Iterations
    
    # Method 1: Signatures (14 algorithms, signing only)
    for algo_idx in range(14):
        # Signing
        lines.append("y")
        lines.append("1")
        lines.append(str(algo_idx))
        lines.append("0")               # Submethod: signing
        lines.append(str(iterations))
    
    # Exit
    lines.append("x")
    
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description="Automate all pqc_benchmark.py benchmarks.")
    parser.add_argument("--iterations", type=int, default=None, help="Iterations per benchmark (required)")
    args = parser.parse_args()

    if not os.path.isfile(BENCHMARK):
        print(f"ERROR: {BENCHMARK} not found.")
        sys.exit(1)
    if not os.path.isfile(POWER_MONITOR):
        print("ERROR: power_monitor.py must be in the same folder as pqc_benchmark_automated.py.")
        print(f"Missing: {POWER_MONITOR}")
        sys.exit(1)
    
    # Ask for iterations if not provided
    if args.iterations is None:
        try:
            args.iterations = int(input("Enter number of iterations: "))
        except ValueError:
            print("Invalid input. Please enter an integer.")
            sys.exit(1)
    
    print(f"Running all benchmarks with {args.iterations} iterations...")
    print("Machine power and energy are recorded automatically.")
    print(f"Working folder: {HERE}")
    print("This will take a while. Be patient.\n")
    
    # Build and run from this script's folder so results_files/ is created here.
    seq = build_input_sequence(args.iterations)
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    proc = subprocess.run(
        [sys.executable, "-u", BENCHMARK],
        input=seq,
        text=True,
        cwd=HERE,
        env=env,
    )
    
    if proc.returncode == 0:
        print("\nAll benchmarks completed successfully.")
        print(f"Time and energy values are in: {os.path.join(HERE, 'results_files')}")
        print(f"Latest copy: {os.path.join(HERE, 'results_files', 'latest.csv')}")
    else:
        print(f"\nBenchmarks finished with return code {proc.returncode}")
        print(f"Check {os.path.join(HERE, 'results_files')} for any partial CSV.")
    
    sys.exit(proc.returncode)


if __name__ == "__main__":
    main()
