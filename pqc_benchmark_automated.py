#!/usr/bin/env python3
#
#Simple automation wrapper for pqc_benchmark.py
# Runs all methods and algorithms with a single user input: iteration count.
# Time and machine-power energy are recorded by pqc_benchmark.py.
# No external dependencies required.
# OG

import subprocess
import sys
import argparse


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
    
    # Ask for iterations if not provided
    if args.iterations is None:
        try:
            args.iterations = int(input("Enter number of iterations: "))
        except ValueError:
            print("Invalid input. Please enter an integer.")
            sys.exit(1)
    
    print(f"Running all benchmarks with {args.iterations} iterations...")
    print("Machine power and energy are recorded automatically when telemetry is available.")
    print("This will take a while. Be patient.\n")
    
    # Build and run
    seq = build_input_sequence(args.iterations)
    proc = subprocess.run([sys.executable, "pqc_benchmark.py"], input=seq, text=True)
    
    if proc.returncode == 0:
        print("\nAll benchmarks completed successfully.")
        print("Time and energy values are in the CSV under results_files/.")
    else:
        print(f"\nBenchmarks finished with return code {proc.returncode}")
    
    sys.exit(proc.returncode)


if __name__ == "__main__":
    main()
