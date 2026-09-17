# The PQC Benchmark
# This is the implementation of my Post Quantum Cryptography benchmark, which measures the performance of 
# various post-quantum algorithms in key generation, signing, verification, and TLS handshakes. 
# The benchmark is designed to be run on a local machine and outputs results to a CSV file for analysis.
#
# Time is measured for every run. Power and energy are sampled
# automatically by power_monitor.py on every device: board sensors
# when present, otherwise CPU package RAPL or TDP x utilization.
#
# After benchmarking all the machines, it appears that TLS handshakes are not functioning correctly.
# I am leaving the code in place for now, to allow for future debugging.
#
# Key generation and signature operations are working as expected, and the results are being recorded in the CSV file.
#
# OG
import subprocess
import os
import csv
import datetime
import time
import sys
import traceback

import platform

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
os.chdir(SCRIPT_DIR)

try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

try:
    import power_monitor
except ImportError:
    print("ERROR: power_monitor.py was not found next to pqc_benchmark.py.")
    print("Copy BOTH files onto the device:")
    print("  pqc_benchmark.py")
    print("  power_monitor.py")
    print("  pqc_benchmark_automated.py   (optional)")
    print(f"Expected folder: {SCRIPT_DIR}")
    sys.exit(1)

#----------------Initialisation------------------
# Get machine name and create timestamp
machine_name = platform.node()
timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
# Initialise results CSV file with timestamped name and header
# Always write next to this script, not whatever folder you launched from.
results_dir = os.path.join(SCRIPT_DIR, "results_files")
os.makedirs(results_dir, exist_ok=True)
csv_file = os.path.join(results_dir, f"{machine_name}_{timestamp}.csv")
latest_csv = os.path.join(results_dir, "latest.csv")
csv_header = [
    "machine", "method", "submethod", "algorithm", "iterations", "start time", "stop time", "total time",
    "time per iteration", "baseline power", "on-load power", "total power used",
    "joules per iteration", "iterations per joule"
]
# Create the CSV file and write header
with open(csv_file, "w", newline="") as f:
    writer = csv.writer(f)
    writer.writerow(csv_header)
print(f"Working folder : {SCRIPT_DIR}")
print(f"Results CSV    : {csv_file}")
print(f"Latest copy    : {latest_csv}")
sys.stdout.flush()

# Helper function to append a row to the CSV
def append_result_row(row):
    with open(csv_file, "a", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(row)
    try:
        import shutil as _shutil
        _shutil.copyfile(csv_file, latest_csv)
    except Exception:
        pass

# Power is sampled by power_monitor.py on every device: board sensors
# when present, otherwise CPU package RAPL or TDP x utilization.
BASELINE_DURATION = float(os.environ.get("PQC_BASELINE_DURATION", "5"))
POWER_SAMPLE_INTERVAL = float(os.environ.get("PQC_POWER_INTERVAL", "0.1"))
BASELINE_POWER = None
POWER_SOURCE = "UNAVAILABLE"
TELEMETRY_OK = False


def init_machine_power():
    global BASELINE_POWER, POWER_SOURCE, TELEMETRY_OK

    probe = power_monitor.read_power()
    POWER_SOURCE = probe.get("source", "UNAVAILABLE")
    TELEMETRY_OK = probe.get("power_w") is not None
    platform_name, model = power_monitor.detect_platform()

    print()
    print("=" * 70)
    print("Machine power telemetry")
    print("=" * 70)
    print(f"Platform : {platform_name}")
    print(f"Model    : {model}")
    print(f"Source   : {POWER_SOURCE}")
    vcgencmd = power_monitor.find_command("vcgencmd")
    print(f"vcgencmd : {vcgencmd or 'not found'}")
    if power_monitor.PMIC_ERROR:
        print(f"PMIC     : {power_monitor.PMIC_ERROR}")
        if "VCHI" in power_monitor.PMIC_ERROR.upper():
            print("Hint: add this user to the video group, then log out and back in:")
            print("  sudo usermod -aG video $USER")
    sys.stdout.flush()

    if not TELEMETRY_OK:
        print("Retrying power probe ...")
        time.sleep(0.3)
        probe = power_monitor.read_power()
        POWER_SOURCE = probe.get("source", "UNAVAILABLE")
        TELEMETRY_OK = probe.get("power_w") is not None
        print(f"Source   : {POWER_SOURCE}")

    if not TELEMETRY_OK:
        print("Machine power telemetry is unavailable on this device.")
        print("Time will still be recorded. Energy columns will be left blank.")
        print("=" * 70)
        print()
        sys.stdout.flush()
        return

    if "estimate" in POWER_SOURCE.lower():
        print("No board power meter on this device; using CPU TDP x utilization.")

    print(f"Measuring idle baseline for {BASELINE_DURATION:.1f}s ...")
    BASELINE_POWER, POWER_SOURCE = power_monitor.measure_baseline_power(
        duration=BASELINE_DURATION,
        interval=max(POWER_SAMPLE_INTERVAL, 0.2),
        verbose=False
    )

    if BASELINE_POWER is None:
        print("Baseline machine power could not be measured.")
    else:
        print(f"Baseline machine power: {BASELINE_POWER:.6f} W")

    print("=" * 70)
    print()


def run_timed_workload(work_fn):
    sampler = power_monitor.PowerSampler(interval=POWER_SAMPLE_INTERVAL)

    if TELEMETRY_OK:
        sampler.start()

    time_start = time.time()
    work_fn()
    time_end = time.time()

    on_load_power = None
    source = POWER_SOURCE

    if TELEMETRY_OK:
        on_load_power = sampler.stop()
        source = sampler.source

    return time_start, time_end, time_end - time_start, on_load_power, source


def format_metric(value, digits=9):
    if value == "" or value is None:
        return "unavailable"
    return f"{float(value):.{digits}f}"


def record_benchmark(method, submethod, algorithm, iterations, time_start, time_end, total_time, on_load_power, power_source=None):
    time_per_iteration = total_time / iterations if iterations else 0
    baseline, onload, net, jpi, ipj = power_monitor.csv_energy_fields(
        BASELINE_POWER, on_load_power, total_time, iterations
    )
    source = power_source or POWER_SOURCE
    total_energy = float(net) * total_time if net != "" else ""

    print()
    print("-" * 70)
    print("Benchmark results")
    print("-" * 70)
    print(f"Method              : {method}")
    if submethod:
        print(f"Submethod           : {submethod}")
    print(f"Algorithm           : {algorithm}")
    print(f"Iterations          : {iterations}")
    print(f"Total time          : {total_time:.9f} s")
    print(f"Time / iteration    : {time_per_iteration:.9f} s")
    print(f"Power source        : {source}")
    print(f"Baseline power      : {format_metric(baseline)} W")
    print(f"On-load power       : {format_metric(onload)} W")
    print(f"Net machine power   : {format_metric(net)} W")
    print(f"Total energy        : {format_metric(total_energy)} J")
    print(f"Joules / iteration  : {format_metric(jpi, 12)} J")
    print(f"Iterations / joule  : {format_metric(ipj)}")
    print("-" * 70)
    print(f"Saved to : {csv_file}")
    print()
    sys.stdout.flush()

    append_result_row([
        machine_name, method, submethod, algorithm, iterations,
        time_start, time_end, total_time, time_per_iteration,
        baseline, onload, net, jpi, ipj
    ])

# Key generation function
def gen_key(command, args, priv, pub):
    # Replace {priv} in args with the actual priv path
    args = [a.replace("{priv}", priv) for a in args]
    # Generate private key
    subprocess.run(["openssl", command] + args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # Output public key
    subprocess.run(["openssl", "pkey", "-in", priv, "-pubout", "-out", pub], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# Signing function
def sign_file(private_key, message_to_sign, signature):
    # Sign a file using a private key.
    # Uses OpenSSL's pkeyutl utility to sign the input file with the given private key.
    # The signature is written to signature_output. All output is suppressed.
    return subprocess.run([
        "openssl", "pkeyutl",
        "-sign",
        "-inkey", private_key,
        "-in", message_to_sign,
        "-out", signature
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# Verification function
def verify_signature(public_key, message_to_sign, signature):
    # Verify a signature using a public key.
    # Uses OpenSSL's pkeyutl utility to verify the signature of the input file with the given public key.
    # Returns True if the signature is valid, False otherwise. All output is suppressed.
    result = subprocess.run([
        "openssl", "pkeyutl",
        "-verify",
        "-pubin",
        "-inkey", public_key,
        "-in", message_to_sign,
        "-sigfile", signature
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return result.returncode == 0

# TLS certificate generation function
def gen_tls_cert(command, args, priv, cert):
    # Generate private key
    args_with_priv = [a.replace("{priv}", priv) for a in args]
    subprocess.run(["openssl", command] + args_with_priv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # Generate self-signed certificate valid for 365 days
    subprocess.run([
        "openssl", "req", "-new", "-x509", "-key", priv, "-out", cert,
        "-days", "365", "-subj", "/CN=localhost"
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# TLS handshake benchmarking function
def benchmark_tls_handshake(cert, priv, iterations, port=4433):
    # Start s_server in background
    server_proc = subprocess.Popen([
        "openssl", "s_server",
        "-cert", cert,
        "-key", priv,
        "-accept", str(port),
        "-quiet"
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    # Give the server a moment to start
    time.sleep(0.5)
    
    # Run client handshakes and measure time plus machine power
    def _work():
        for i in range(iterations):
            subprocess.run([
                "openssl", "s_client",
                "-connect", f"localhost:{port}",
                "-brief"
            ], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    time_start, time_end, total_time, on_load_power, source = run_timed_workload(_work)
    
    # Stop the server
    server_proc.terminate()
    server_proc.wait(timeout=5)
    
    return time_start, time_end, total_time, on_load_power, source

def run_menu():
    init_machine_power()

    while True:
        choice = input("Press y to start, x to exit: ")
        if choice.lower() == 'x':
            break
        if choice.lower() != 'y':
            print("Invalid choice. Please try again.")
            continue

        methods = ["key generation", "signatures", "TLS handshake"]
        try:
            chosenMethod = int(input("Select a method to benchmark:\n" + "\n".join(f"{i}. {m}" for i, m in enumerate(methods)) + "\nEnter number: "))
        except ValueError:
            print("Invalid method number. Please try again.")
            continue
        if chosenMethod not in (0, 1, 2):
            print("Invalid method number. Please try again.")
            continue
        if chosenMethod == 0:
    
            keygen_algorithms = [
                ("RSA-2048", "genrsa", ["-out", "{priv}", "2048"]),
                ("ECDSA P-256", "genpkey", ["-algorithm", "EC", "-pkeyopt", "ec_paramgen_curve:P-256", "-out", "{priv}"]),
                ("X25519", "genpkey", ["-algorithm", "X25519", "-out", "{priv}"]),
                ("ECDH P-256", "genpkey", ["-algorithm", "EC", "-pkeyopt", "ec_paramgen_curve:P-256", "-out", "{priv}"]),
                ("ML-KEM-512", "genpkey", ["-algorithm", "ML-KEM-512", "-out", "{priv}"]),
                ("ML-KEM-768", "genpkey", ["-algorithm", "ML-KEM-768", "-out", "{priv}"]),
                ("ML-KEM-1024", "genpkey", ["-algorithm", "ML-KEM-1024", "-out", "{priv}"]),
                ("ML-DSA-44", "genpkey", ["-algorithm", "ML-DSA-44", "-out", "{priv}"]),
                ("ML-DSA-65", "genpkey", ["-algorithm", "ML-DSA-65", "-out", "{priv}"]),
                ("ML-DSA-87", "genpkey", ["-algorithm", "ML-DSA-87", "-out", "{priv}"]),
                ("SLH-DSA-SHA2-128s", "genpkey", ["-algorithm", "SLH-DSA-SHA2-128s", "-out", "{priv}"]),
                ("SLH-DSA-SHA2-128f", "genpkey", ["-algorithm", "SLH-DSA-SHA2-128f", "-out", "{priv}"]),
                ("SLH-DSA-SHAKE-128s", "genpkey", ["-algorithm", "SLH-DSA-SHAKE-128s", "-out", "{priv}"]),
                ("SLH-DSA-SHA2-192s", "genpkey", ["-algorithm", "SLH-DSA-SHA2-192s", "-out", "{priv}"]),
                ("SLH-DSA-SHA2-192f", "genpkey", ["-algorithm", "SLH-DSA-SHA2-192f", "-out", "{priv}"]),
                ("SLH-DSA-SHAKE-192s", "genpkey", ["-algorithm", "SLH-DSA-SHAKE-192s", "-out", "{priv}"]),
                ("SLH-DSA-SHA2-256s", "genpkey", ["-algorithm", "SLH-DSA-SHA2-256s", "-out", "{priv}"]),
                ("SLH-DSA-SHA2-256f", "genpkey", ["-algorithm", "SLH-DSA-SHA2-256f", "-out", "{priv}"]),
                ("SLH-DSA-SHAKE-256s", "genpkey", ["-algorithm", "SLH-DSA-SHAKE-256s", "-out", "{priv}"]),
            ]
    
            # Ensure keys directory exists
            os.makedirs("keys", exist_ok=True)
    
            #1.-------------Key Generation --------------
    
            #1.a Key generation usage:
            priv = os.path.abspath("/keys/private.key")
            pub = os.path.abspath("/keys/public.key")
            selected = int(input("Select an algorithm to benchmark:\n" + "\n".join(f"{i}. {name}" for i, (name, _, _) in enumerate(keygen_algorithms)) + "\nEnter number: "))
            name, command, args = keygen_algorithms[selected]
            priv = os.path.join("keys", f"{name.replace(' ', '_').replace('-', '_')}_priv.pem")
            pub = os.path.join("keys", f"{name.replace(' ', '_').replace('-', '_')}_pub.pem")
    
            iterations = int(input("Enter the number of iterations for key generation: "))
    
            def _work():
                for i in range(iterations):
                    gen_key(command, args, priv, pub)
    
            time_start, time_end, total_time, on_load_power, power_source = run_timed_workload(_work)
            record_benchmark(
                methods[chosenMethod],
                "",
                keygen_algorithms[selected][0],
                iterations,
                time_start,
                time_end,
                total_time,
                on_load_power,
                power_source
            )
    
    
        # 2------------------Signing------------------
        elif chosenMethod == 1:
            signature_algorithms = [
                ("RSA-2048", "genrsa", ["-out", "{priv}", "2048"]),
                ("ECDSA P-256", "genpkey", ["-algorithm", "EC", "-pkeyopt", "ec_paramgen_curve:P-256", "-out", "{priv}"]),
                ("ML-DSA-44", "genpkey", ["-algorithm", "ML-DSA-44", "-out", "{priv}"]),
                ("ML-DSA-65", "genpkey", ["-algorithm", "ML-DSA-65", "-out", "{priv}"]),
                ("ML-DSA-87", "genpkey", ["-algorithm", "ML-DSA-87", "-out", "{priv}"]),
                ("SLH-DSA-SHA2-128s", "genpkey", ["-algorithm", "SLH-DSA-SHA2-128s", "-out", "{priv}"]),
                ("SLH-DSA-SHA2-128f", "genpkey", ["-algorithm", "SLH-DSA-SHA2-128f", "-out", "{priv}"]),
                ("SLH-DSA-SHAKE-128s", "genpkey", ["-algorithm", "SLH-DSA-SHAKE-128s", "-out", "{priv}"]),
                ("SLH-DSA-SHA2-192s", "genpkey", ["-algorithm", "SLH-DSA-SHA2-192s", "-out", "{priv}"]),
                ("SLH-DSA-SHA2-192f", "genpkey", ["-algorithm", "SLH-DSA-SHA2-192f", "-out", "{priv}"]),
                ("SLH-DSA-SHAKE-192s", "genpkey", ["-algorithm", "SLH-DSA-SHAKE-192s", "-out", "{priv}"]),
                ("SLH-DSA-SHA2-256s", "genpkey", ["-algorithm", "SLH-DSA-SHA2-256s", "-out", "{priv}"]),
                ("SLH-DSA-SHA2-256f", "genpkey", ["-algorithm", "SLH-DSA-SHA2-256f", "-out", "{priv}"]),
                ("SLH-DSA-SHAKE-256s", "genpkey", ["-algorithm", "SLH-DSA-SHAKE-256s", "-out", "{priv}"]),
            ]
    
            # Generate the keys for the signature algorithms
    
            priv = os.path.abspath("/keys/private.key")
            pub = os.path.abspath("/keys/public.key")
            selected = int(input("Select an algorithm to benchmark:\n" + "\n".join(f"{i}. {name}" for i, (name, _, _) in enumerate(signature_algorithms)) + "\nEnter number: "))
            name, command, args = signature_algorithms[selected]
            priv = os.path.join("keys", f"{name.replace(' ', '_').replace('-', '_')}_priv.pem")
            pub = os.path.join("keys", f"{name.replace(' ', '_').replace('-', '_')}_pub.pem")
            gen_key(command, args, priv, pub)
    
            signature_methods = ["signing", "verification"]
            selected_method = int(input("Select a signature method to benchmark:\n" + "\n".join(f"{i}. {m}" for i, m in enumerate(signature_methods)) + "\nEnter number: "))
    
            # Sign a message 
            if not os.path.exists("README.md"):
                with open("README.md", "w") as f:
                    f.write("This is a sample file for signature benchmarking.\n")
            message_to_sign = "README.md"
            private_key = priv
            signature_output = os.path.join("signatures", f"{name.replace(' ', '_').replace('-', '_')}_signature.bin")
            os.makedirs("signatures", exist_ok=True)
    
            if selected_method == 0:
                iterations = int(input("Enter the number of iterations for signing: "))
    
                def _work():
                    for i in range(iterations):
                        sign_file(private_key, message_to_sign, signature_output)
    
                time_start, time_end, total_time, on_load_power, power_source = run_timed_workload(_work)
                record_benchmark(
                    methods[chosenMethod],
                    signature_methods[selected_method],
                    signature_algorithms[selected][0],
                    iterations,
                    time_start,
                    time_end,
                    total_time,
                    on_load_power,
                    power_source
                )
    
            elif selected_method == 1:
                # Create a signature first to verify
                sign_file(private_key, message_to_sign, signature_output)
    
                # Verify the signature
                public_key = pub
                iterations = int(input("Enter the number of iterations for signature verification: "))
    
                def _work():
                    for i in range(iterations):
                        verify_signature(public_key, message_to_sign, signature_output)
    
                time_start, time_end, total_time, on_load_power, power_source = run_timed_workload(_work)
                record_benchmark(
                    methods[chosenMethod],
                    signature_methods[selected_method],
                    signature_algorithms[selected][0],
                    iterations,
                    time_start,
                    time_end,
                    total_time,
                    on_load_power,
                    power_source
                )
    
            #3------------------TLS handshake------------------
        elif chosenMethod == 2:
            tls_algorithms = [
                ("RSA-2048", "genrsa", ["-out", "{priv}", "2048"]),
                ("ECDSA P-256", "genpkey", ["-algorithm", "EC", "-pkeyopt", "ec_paramgen_curve:P-256", "-out", "{priv}"]),
                ("ML-DSA-44", "genpkey", ["-algorithm", "ML-DSA-44", "-out", "{priv}"]),
                ("ML-DSA-65", "genpkey", ["-algorithm", "ML-DSA-65", "-out", "{priv}"]),
                ("ML-DSA-87", "genpkey", ["-algorithm", "ML-DSA-87", "-out", "{priv}"]),
            ]
    
            # Ensure certs directory exists
            os.makedirs("certs", exist_ok=True)
    
            # Select algorithm
            selected = int(input("Select an algorithm to benchmark:\n" + "\n".join(f"{i}. {name}" for i, (name, _, _) in enumerate(tls_algorithms)) + "\nEnter number: "))
            name, command, args = tls_algorithms[selected]
            priv = os.path.join("certs", f"{name.replace(' ', '_').replace('-', '_')}_priv.pem")
            cert = os.path.join("certs", f"{name.replace(' ', '_').replace('-', '_')}_cert.pem")
    
            # Generate certificate
            gen_tls_cert(command, args, priv, cert)
    
            # Get iterations
            iterations = int(input("Enter the number of iterations for TLS handshake: "))
    
            # Run handshakes and measure time plus machine power
            time_start, time_end, total_time, on_load_power, power_source = benchmark_tls_handshake(cert, priv, iterations)
            record_benchmark(
                methods[chosenMethod],
                "",
                tls_algorithms[selected][0],
                iterations,
                time_start,
                time_end,
                total_time,
                on_load_power,
                power_source
            )

        else:
            print('Invalid method number. Please try again.')

if __name__ == '__main__':
    try:
        run_menu()
        print('Finished. Results are in:')
        print(' ', csv_file)
        print(' ', latest_csv)
    except KeyboardInterrupt:
        print('\nStopped. Partial results (if any) are in:')
        print(' ', csv_file)
    except Exception:
        traceback.print_exc()
        print('\nThe run failed. Partial results (if any) are in:')
        print(' ', csv_file)
        sys.exit(1)
