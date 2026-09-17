#!/usr/bin/env python3

"""
power_monitor.py

Software-based power/energy measurement for any connected device:

    Raspberry Pi (1 / Zero / 2 / 3 / 4 / 5 and variants)
    NVIDIA Jetson (Nano, Xavier, Orin, ...)
    Linux desktops and laptops (Intel RAPL, AMD energy, battery)
    Windows PCs
    macOS (Intel and Apple Silicon)

Calculation follows the methodology used in the pqc_energy
repository:

    net_power = on_load_power - baseline_power

    total_energy = net_power * total_time

    energy_per_iteration = total_energy / iterations

    iterations_per_joule = iterations / total_energy

IMPORTANT:
    Board-level telemetry is preferred (Pi PMIC, Jetson VDD_IN, INA shunt).
    On PCs with no wall-power sensor, CPU package RAPL is used when the
    kernel exposes it. On Windows desktops without RAPL access, power is
    estimated from CPU TDP x utilization so energy can still be recorded.
    That estimate is labeled in the source field and is not a wall-meter.
"""

import argparse
import csv
import glob
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime


# ============================================================
# PLATFORM DETECTION
# ============================================================

def detect_platform():

    files = [
        "/proc/device-tree/model",
        "/sys/firmware/devicetree/base/model"
    ]

    for path in files:

        if not os.path.exists(path):
            continue

        try:

            with open(path, "rb") as f:
                model = f.read().decode(
                    errors="ignore"
                ).strip("\x00").strip()

            m = model.lower()

            if "raspberry pi 5" in m or "raspberry pi 500" in m:
                return "Raspberry Pi 5", model

            if "raspberry pi 4" in m or "raspberry pi 400" in m:
                return "Raspberry Pi 4", model

            if "raspberry pi 3" in m:
                return "Raspberry Pi 3", model

            if "raspberry pi zero 2" in m:
                return "Raspberry Pi Zero 2", model

            if "raspberry pi zero" in m:
                return "Raspberry Pi Zero", model

            if "raspberry pi 2" in m:
                return "Raspberry Pi 2", model

            if "raspberry pi" in m:
                return "Raspberry Pi", model

            if "jetson nano" in m:
                return "Jetson Nano", model

            if "orin" in m:
                return "Jetson Orin", model

            if "xavier" in m:
                return "Jetson Xavier", model

            if "jetson" in m:
                return "Jetson", model

            return model.split(",")[0].strip()[:48], model

        except Exception:
            pass

    if os.path.exists("/etc/nv_tegra_release"):
        return "Jetson", "NVIDIA Jetson"

    system = platform.system()

    if system == "Windows":
        return "Windows", platform.platform()

    if system == "Darwin":
        return "macOS", platform.platform()

    if system:
        return system, platform.platform()

    return "Unknown", platform.platform()


# ============================================================
# COMMAND
# ============================================================

LAST_CMD_ERROR = ""
PMIC_ERROR = ""


def command_exists(command):

    return find_command(command) is not None


def find_command(command):

    path = shutil.which(command)
    if path:
        return path

    extras = [
        os.path.join("/usr/bin", command),
        os.path.join("/usr/local/bin", command),
        os.path.join("/opt/vc/bin", command),
        os.path.join("/bin", command),
    ]

    for extra in extras:
        if os.path.isfile(extra) and os.access(extra, os.X_OK):
            return extra

    return None


def run_command(command):

    global LAST_CMD_ERROR
    LAST_CMD_ERROR = ""

    try:

        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5
        )

        LAST_CMD_ERROR = (result.stderr or "").strip()
        return (result.stdout or "").strip()

    except Exception as exc:
        LAST_CMD_ERROR = str(exc)
        return ""


# ============================================================
# RASPBERRY PI 5
# ============================================================

def read_pi5_power():

    global PMIC_ERROR

    vcgencmd = find_command("vcgencmd")

    if vcgencmd is None:
        PMIC_ERROR = "vcgencmd not found"
        return None

    output = run_command(
        [vcgencmd, "pmic_read_adc"]
    )

    if not output:
        PMIC_ERROR = LAST_CMD_ERROR or "empty pmic_read_adc output"
        return None

    currents = {}
    voltages = {}

    for line in output.splitlines():

        current_match = re.search(
            r"([A-Za-z0-9_]+)\s+current\(\d+\)=\s*([0-9.eE+-]+)\s*A",
            line
        )

        if current_match:

            name = current_match.group(1).strip()
            value = float(current_match.group(2))
            currents[name] = value

        voltage_match = re.search(
            r"([A-Za-z0-9_]+)\s+volt\(\d+\)=\s*([0-9.eE+-]+)\s*V",
            line
        )

        if voltage_match:

            name = voltage_match.group(1).strip()
            value = float(voltage_match.group(2))
            voltages[name] = value

    # --------------------------------------------------------
    # Calculate available PMIC rail power
    # --------------------------------------------------------

    total_power = 0.0
    measured_rails = 0

    for current_name, current in currents.items():

        rail = current_name[:-2] if current_name.endswith("_A") else current_name
        voltage = voltages.get(rail + "_V")
        if voltage is None:
            voltage = voltages.get(current_name.replace("_A", "_V"))

        if voltage is not None:

            total_power += current * voltage
            measured_rails += 1

    if measured_rails == 0:
        return None

    return {
        "power_w": total_power,
        "source": "Raspberry Pi 5 PMIC ADC",
        "raw": output
    }


# ============================================================
# JETSON NANO INA3221
# ============================================================

def find_ina3221():

    possible = [
        "/sys/bus/i2c/drivers/ina3221x/6-0040/iio:device0",
        "/sys/bus/i2c/drivers/ina3221x/7-0040/iio:device0",
        "/sys/bus/i2c/drivers/ina3221x/1-0040/iio:device0",
        "/sys/bus/i2c/drivers/ina3221x/0-0040/iio:device0"
    ]

    for path in possible:

        if os.path.isdir(path):
            return path

    matches = glob.glob(
        "/sys/bus/i2c/drivers/ina3221*/**/iio:device*",
        recursive=True
    )

    for path in matches:

        if os.path.isdir(path):
            return path

    return None


def read_file(path):

    try:

        with open(path, "r") as f:
            return f.read().strip()

    except Exception:
        return None


def _ina_power_to_watts(raw_value):

    # NVIDIA IIO ina3221x reports milliwatts.
    # Standard IIO/hwmon power is microwatts.
    if raw_value > 100_000:
        return raw_value / 1_000_000.0

    return raw_value / 1_000.0


def read_jetson_ina3221():

    base = find_ina3221()

    if base is None:
        return None

    rails = {}
    labels = {}

    try:
        filenames = os.listdir(base)
    except Exception:
        return None

    # Look for rail names and power values.
    for filename in filenames:

        if not filename.startswith("in_power"):
            continue

        if not filename.endswith("_input"):
            continue

        path = os.path.join(base, filename)

        value = read_file(path)

        if value is None:
            continue

        try:

            power_w = _ina_power_to_watts(float(value))

            channel = filename.replace(
                "in_power", ""
            ).replace("_input", "")

            rails[channel] = power_w

            label = (
                read_file(os.path.join(base, filename.replace("_input", "_label")))
                or read_file(os.path.join(base, f"rail_name_{channel}"))
                or read_file(os.path.join(base, f"in_voltage{channel}_label"))
            )

            if label:
                labels[channel] = label.strip()

        except ValueError:
            continue

    if not rails:
        return None

    # Machine power is the board input rail, not the sum of
    # VDD_IN + VDD_CPU + VDD_GPU (that would double-count).
    vdd_in = None

    for channel, power_w in rails.items():

        label = labels.get(channel, "").upper()

        if "VDD_IN" in label or label == "VIN":
            vdd_in = power_w
            break

    if vdd_in is None:

        for channel, label in labels.items():

            up = label.upper()

            if "IN" in up and "CPU" not in up and "GPU" not in up:
                vdd_in = rails[channel]
                break

    if vdd_in is None:
        # Channel 0 is VDD_IN on Jetson Nano.
        vdd_in = rails.get("0", next(iter(rails.values())))

    return {
        "power_w": vdd_in,
        "source": "Jetson INA3221 VDD_IN",
        "rails": rails
    }


# ============================================================
# JETSON TEGRASTATS FALLBACK
# ============================================================

def read_jetson_tegrastats():

    if not command_exists("tegrastats"):
        return None

    output = run_command(
        ["tegrastats", "--interval", "100", "--count", "1"]
    )

    if not output:
        return None

    # Look for:
    #
    # VDD_IN xxxmW/xxxmW
    #
    # or other VDD power rails.

    matches = re.findall(
        r"(VDD_[A-Za-z0-9_]+)\s+"
        r"([0-9]+)mW(?:/([0-9]+)mW)?",
        output
    )

    if not matches:
        return None

    rails = {}

    for rail, current, average in matches:

        rails[rail] = float(current) / 1000.0

    if "VDD_IN" in rails:

        power = rails["VDD_IN"]

    else:

        power = sum(rails.values())

    return {
        "power_w": power,
        "source": "Jetson tegrastats VDD_IN",
        "rails": rails
    }


# ============================================================
# GENERIC LINUX HWMON
# ============================================================

# CPU/GPU RAPL is not whole-machine power.
HWMON_SKIP_NAMES = (
    "rapl",
    "intel-rapl",
    "amdgpu",
    "nvidia",
    "coretemp",
    "nvme",
    "iwlwifi",
    "k10temp",
    "zenpower"
)

HWMON_MACHINE_NAMES = (
    "ina3221",
    "ina3221x",
    "ina219",
    "ina226",
    "ina238",
    "ina260",
    "pmic",
    "shunt",
    "power_meter",
    "acpi_power"
)


def read_hwmon_power():

    base = "/sys/class/hwmon"

    if not os.path.exists(base):
        return None

    preferred = []
    others = []

    for hwmon in os.listdir(base):

        directory = os.path.join(base, hwmon)

        try:
            files = os.listdir(directory)
        except Exception:
            continue

        chip_name = (read_file(os.path.join(directory, "name")) or "").lower()

        if any(skip in chip_name for skip in HWMON_SKIP_NAMES):
            continue

        is_machine = any(token in chip_name for token in HWMON_MACHINE_NAMES)

        for filename in files:

            if "power" not in filename:
                continue

            if not filename.endswith("_input"):
                continue

            path = os.path.join(directory, filename)
            label = (
                read_file(os.path.join(directory, filename.replace("_input", "_label")))
                or ""
            ).upper()

            try:

                value = float(read_file(path))

                # Standard hwmon power input: microwatts → watts
                power = value / 1_000_000.0

                if power <= 0:
                    continue

                sample = {
                    "power_w": power,
                    "source": f"Linux hwmon ({chip_name or hwmon})",
                    "label": label,
                    "chip": chip_name
                }

                if "VDD_IN" in label or label in ("VIN", "SYSTEM", "PSU"):
                    return sample

                if is_machine:
                    preferred.append(sample)
                else:
                    others.append(sample)

            except Exception:
                continue

    if preferred:
        return preferred[0]

    # Do not fall back to unknown hwmon sensors: those are often
    # CPU package RAPL, which is not machine power.
    return None


def read_power_supply_machine():

    base = "/sys/class/power_supply"

    if not os.path.isdir(base):
        return None

    try:
        supplies = os.listdir(base)
    except Exception:
        return None

    for name in supplies:

        directory = os.path.join(base, name)
        supply_type = (read_file(os.path.join(directory, "type")) or "").lower()
        status = (read_file(os.path.join(directory, "status")) or "").lower()

        # Battery power_now is machine draw only while discharging.
        if "battery" in supply_type and status != "discharging":
            continue

        power_now = read_file(os.path.join(directory, "power_now"))

        if power_now:

            try:
                power_w = abs(float(power_now)) / 1_000_000.0
            except ValueError:
                power_w = 0.0

            if power_w > 0:
                return {
                    "power_w": power_w,
                    "source": f"power_supply {name}"
                }

        current_now = read_file(os.path.join(directory, "current_now"))
        voltage_now = read_file(os.path.join(directory, "voltage_now"))

        if current_now and voltage_now:

            try:
                power_w = abs(float(current_now) * float(voltage_now)) / 1_000_000_000_000.0
            except ValueError:
                power_w = 0.0

            if power_w > 0:
                return {
                    "power_w": power_w,
                    "source": f"power_supply {name}"
                }

    return None


# ============================================================
# LINUX PACKAGE POWER (INTEL RAPL / AMD ENERGY)
# ============================================================

class _EnergyDelta:
    """Convert a cumulative energy counter into instantaneous watts."""

    def __init__(self):
        self._last_uj = None
        self._last_t = None

    def watts(self, energy_uj):
        now = time.monotonic()

        if self._last_uj is None or self._last_t is None:
            self._last_uj = energy_uj
            self._last_t = now
            return None

        dt = now - self._last_t
        du = energy_uj - self._last_uj
        self._last_uj = energy_uj
        self._last_t = now

        if dt <= 0:
            return None

        if du < 0:
            du += 2 ** 32

        return (du / 1_000_000.0) / dt


_rapl_delta = _EnergyDelta()
_amd_delta = _EnergyDelta()


def read_rapl_package_power():

    package_paths = []

    for pattern in (
        "/sys/class/powercap/intel-rapl/intel-rapl:[0-9]*",
        "/sys/class/powercap/amd-rapl/amd-rapl:[0-9]*",
        "/sys/class/powercap/*rapl*/*rapl:[0-9]",
    ):
        package_paths.extend(glob.glob(pattern))

    # Only top-level packages, not package:0:0 (cores) or DRAM subzones.
    package_paths = sorted({
        path for path in package_paths
        if os.path.isdir(path) and os.path.basename(path).count(":") == 1
    })

    if not package_paths:
        return None

    total_uj = 0.0
    names = []

    for path in package_paths:
        energy = read_file(os.path.join(path, "energy_uj"))
        name = read_file(os.path.join(path, "name")) or os.path.basename(path)

        if energy is None:
            continue

        try:
            total_uj += float(energy)
        except ValueError:
            continue

        names.append(name)

    if not names:
        return None

    power = _rapl_delta.watts(total_uj)

    if power is None or power <= 0:
        return None

    source = "AMD RAPL package" if any("amd" in n.lower() for n in names) else "Intel RAPL package"

    return {
        "power_w": power,
        "source": source
    }


def read_amd_energy_power():

    base = "/sys/class/hwmon"

    if not os.path.isdir(base):
        return None

    try:
        entries = os.listdir(base)
    except Exception:
        return None

    for hwmon in entries:
        directory = os.path.join(base, hwmon)
        name = (read_file(os.path.join(directory, "name")) or "").lower()

        if name != "amd_energy":
            continue

        # energy1_input is the socket; later channels are cores.
        energy = read_file(os.path.join(directory, "energy1_input"))

        if energy is None:
            continue

        try:
            energy_uj = float(energy)
        except ValueError:
            continue

        # hwmon energy is microjoules.
        if energy_uj > 10_000_000_000:
            energy_uj = energy_uj / 1.0

        power = _amd_delta.watts(energy_uj)

        if power is None or power <= 0:
            return None

        return {
            "power_w": power,
            "source": "AMD energy (socket)"
        }

    return None


# ============================================================
# GENERIC CPU POWER ESTIMATE (EVERY OS / DEVICE)
# ============================================================

# Typical package / board power (base W, max W)
_DEVICE_TDP = {
    "raspberry pi 5": (8.0, 12.0),
    "raspberry pi 500": (8.0, 12.0),
    "raspberry pi 4": (6.0, 8.0),
    "raspberry pi 400": (6.0, 8.0),
    "raspberry pi 3": (4.0, 6.5),
    "raspberry pi zero 2": (2.0, 3.0),
    "raspberry pi zero": (1.2, 1.8),
    "raspberry pi 2": (2.0, 3.5),
    "raspberry pi": (1.5, 2.5),
    "jetson orin nano": (7.0, 15.0),
    "jetson orin": (15.0, 60.0),
    "jetson xavier": (10.0, 30.0),
    "jetson nano": (5.0, 10.0),
    "jetson tx2": (7.5, 15.0),
    "jetson": (10.0, 20.0),
    "apple m4": (22.0, 40.0),
    "apple m3": (20.0, 40.0),
    "apple m2": (20.0, 35.0),
    "apple m1 pro": (30.0, 70.0),
    "apple m1": (20.0, 27.0),
    "i9-14900k": (125.0, 253.0),
    "i9-14900kf": (125.0, 253.0),
    "i9-14900": (65.0, 219.0),
    "i9-14900f": (65.0, 219.0),
    "i9-13900k": (125.0, 253.0),
    "i9-13900": (65.0, 219.0),
    "i7-14700k": (125.0, 253.0),
    "i7-14700": (65.0, 219.0),
    "i7-13700": (65.0, 219.0),
    "i7-8550u": (15.0, 40.0),
    "i5-14600k": (125.0, 181.0),
    "i5-14500": (65.0, 154.0),
    "8640hs": (35.0, 54.0),
}


def cpu_brand():

    system = platform.system()

    if system == "Windows":
        try:
            import winreg

            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
            )
            name, _ = winreg.QueryValueEx(key, "ProcessorNameString")
            winreg.CloseKey(key)
            return str(name).strip()
        except Exception:
            return platform.processor() or "Unknown CPU"

    if system == "Darwin":
        return run_command(["sysctl", "-n", "machdep.cpu.brand_string"]) or "Apple"

    try:
        with open("/proc/cpuinfo", encoding="utf-8", errors="ignore") as f:
            text = f.read()
        for key in ("model name", "Model", "Hardware", "cpu model"):
            for line in text.splitlines():
                if line.lower().startswith(key.lower()):
                    return line.split(":", 1)[-1].strip()
    except Exception:
        pass

    return platform.processor() or platform.machine() or "Unknown CPU"


def lookup_device_tdp():

    platform_name, model = detect_platform()
    cpu = cpu_brand()
    blob = f"{platform_name} {model} {cpu}".lower()
    blob = blob.replace("(r)", "").replace("(tm)", "")

    for key, (tdp, mtp) in sorted(_DEVICE_TDP.items(), key=lambda item: -len(item[0])):
        if key in blob:
            return tdp, mtp, key, cpu

    if re.search(r"\d{4,5}u\b", blob):
        return 15.0, 40.0, "mobile-U", cpu

    if re.search(r"\d{4,5}hx\b", blob):
        return 55.0, 157.0, "mobile-HX", cpu

    if re.search(r"\d{4,5}h\b", blob):
        return 45.0, 115.0, "mobile-H", cpu

    if re.search(r"\d{4,5}k\b", blob):
        return 125.0, 250.0, "desktop-K", cpu

    if "ryzen" in blob and "hs" in blob:
        return 35.0, 54.0, "ryzen-HS", cpu

    arch = platform.machine().lower()

    if arch in ("aarch64", "armv7l", "armv6l", "arm64", "arm"):
        return 5.0, 12.0, "generic-arm", cpu

    if arch in ("x86_64", "amd64", "x64"):
        return 65.0, 200.0, "generic-x86", cpu

    return 15.0, 45.0, "generic", cpu


class _EstimatedCpuPower:
    """TDP x utilization estimate. Works on Windows, Linux, and macOS."""

    def __init__(self):
        self.tdp, self.mtp, self.tdp_key, self.cpu_name = lookup_device_tdp()
        self.source = (
            f"CPU estimate ({self.cpu_name}, "
            f"{self.tdp:.0f}W TDP x utilization)"
        )
        self._idle = None
        self._kernel = None
        self._user = None
        self._linux_idle = None
        self._linux_total = None
        self._mac_idle = None
        self._mac_total = None
        self._pdh_ready = False
        if os.name == "nt":
            self._init_pdh()

    def _init_pdh(self):
        try:
            import ctypes
            from ctypes import wintypes

            pdh = ctypes.WinDLL("pdh")
            query = wintypes.HANDLE()
            status = pdh.PdhOpenQueryW(None, None, ctypes.byref(query))
            if status != 0:
                return

            counter = wintypes.HANDLE()
            path = r"\Processor Information(_Total)\% Processor Utility"
            add = getattr(pdh, "PdhAddEnglishCounterW", None)
            if add is None:
                add = pdh.PdhAddCounterW
            status = add(query, path, None, ctypes.byref(counter))
            if status != 0:
                pdh.PdhCloseQuery(query)
                return

            pdh.PdhCollectQueryData(query)
            self._pdh = pdh
            self._pdh_query = query
            self._pdh_counter = counter
            self._ctypes = ctypes
            self._wintypes = wintypes
            self._pdh_ready = True
        except Exception:
            self._pdh_ready = False

    def _read_pdh_utility(self):
        if not self._pdh_ready:
            return None

        try:
            status = self._pdh.PdhCollectQueryData(self._pdh_query)
            if status != 0:
                return None

            class PDH_FMT_COUNTERVALUE(self._ctypes.Structure):
                _fields_ = [
                    ("CStatus", self._wintypes.DWORD),
                    ("doubleValue", self._ctypes.c_double),
                ]

            value = PDH_FMT_COUNTERVALUE()
            PDH_FMT_DOUBLE = 0x00000200
            status = self._pdh.PdhGetFormattedCounterValue(
                self._pdh_counter,
                PDH_FMT_DOUBLE,
                None,
                self._ctypes.byref(value)
            )
            if status != 0:
                return None
            return max(0.0, float(value.doubleValue))
        except Exception:
            return None

    def _read_system_times_utility(self):
        import ctypes
        from ctypes import wintypes

        class FILETIME(ctypes.Structure):
            _fields_ = [
                ("dwLowDateTime", wintypes.DWORD),
                ("dwHighDateTime", wintypes.DWORD),
            ]

        idle = FILETIME()
        kernel = FILETIME()
        user = FILETIME()
        ok = ctypes.windll.kernel32.GetSystemTimes(
            ctypes.byref(idle),
            ctypes.byref(kernel),
            ctypes.byref(user)
        )
        if not ok:
            return None

        def to_int(ft):
            return (ft.dwHighDateTime << 32) | ft.dwLowDateTime

        idle_i = to_int(idle)
        kernel_i = to_int(kernel)
        user_i = to_int(user)

        if self._idle is None:
            self._idle = idle_i
            self._kernel = kernel_i
            self._user = user_i
            return None

        idle_d = idle_i - self._idle
        kernel_d = kernel_i - self._kernel
        user_d = user_i - self._user
        self._idle = idle_i
        self._kernel = kernel_i
        self._user = user_i

        total = kernel_d + user_d
        if total <= 0:
            return None

        busy = total - idle_d
        return max(0.0, min(100.0 * busy / total, 400.0))

    def _read_linux_utility(self):
        try:
            with open("/proc/stat", encoding="utf-8") as f:
                line = f.readline()
        except Exception:
            return None

        if not line.startswith("cpu"):
            return None

        parts = line.split()
        try:
            values = [float(x) for x in parts[1:8]]
        except ValueError:
            return None

        idle = values[3] + (values[4] if len(values) > 4 else 0.0)
        total = sum(values)

        if self._linux_total is None:
            self._linux_idle = idle
            self._linux_total = total
            return None

        idle_d = idle - self._linux_idle
        total_d = total - self._linux_total
        self._linux_idle = idle
        self._linux_total = total

        if total_d <= 0:
            return None

        return max(0.0, min(100.0 * (1.0 - idle_d / total_d), 100.0))

    def _read_macos_utility(self):
        raw = run_command(["sysctl", "-n", "kern.cp_time"])
        if not raw:
            return None

        try:
            ticks = [float(x) for x in raw.replace(",", " ").split()]
        except ValueError:
            return None

        if len(ticks) < 4:
            return None

        # user, nice, sys, idle, [intr]
        idle = ticks[3]
        total = sum(ticks)

        if self._mac_total is None:
            self._mac_idle = idle
            self._mac_total = total
            return None

        idle_d = idle - self._mac_idle
        total_d = total - self._mac_total
        self._mac_idle = idle
        self._mac_total = total

        if total_d <= 0:
            return None

        return max(0.0, min(100.0 * (1.0 - idle_d / total_d), 100.0))

    def _read_utility(self):
        system = platform.system()

        if system == "Windows":
            utility = self._read_pdh_utility()
            if utility is None:
                utility = self._read_system_times_utility()
            return utility

        if system == "Darwin":
            return self._read_macos_utility()

        return self._read_linux_utility()

    def read(self):
        utility = self._read_utility()
        if utility is None:
            return None

        power = self.tdp * (utility / 100.0)
        if self.mtp:
            power = min(power, self.mtp)
        if power < 0:
            return None
        return power


_estimated_cpu_power = None


def read_estimated_cpu_power():

    global _estimated_cpu_power

    if _estimated_cpu_power is None:
        _estimated_cpu_power = _EstimatedCpuPower()

    power = _estimated_cpu_power.read()

    if power is None:
        time.sleep(0.2)
        power = _estimated_cpu_power.read()

    if power is None:
        return None

    return {
        "power_w": power,
        "source": _estimated_cpu_power.source
    }


def read_macos_battery_power():

    if platform.system() != "Darwin":
        return None

    output = run_command(["ioreg", "-n", "AppleSmartBattery", "-r"])

    if not output:
        return None

    amps = None
    volts = None

    amp_match = re.search(r'"InstantAmperage"\s*=\s*(-?\d+)', output)
    volt_match = re.search(r'"Voltage"\s*=\s*(\d+)', output)

    if amp_match:
        raw_amps = int(amp_match.group(1))
        if raw_amps < 0:
            raw_amps += 2 ** 32
            if raw_amps > 2 ** 31:
                raw_amps -= 2 ** 32
        amps = abs(raw_amps) / 1000.0

    if volt_match:
        volts = float(volt_match.group(1)) / 1000.0

    if amps is None or volts is None or amps <= 0 or volts <= 0:
        return None

    return {
        "power_w": amps * volts,
        "source": "macOS battery"
    }


# ============================================================
# UNIVERSAL POWER READER
# ============================================================

def read_power():

    # 1. Board / SoC sensors when the hardware exposes them
    if command_exists("vcgencmd"):
        result = read_pi5_power()
        if result:
            return result

    if (
        detect_platform()[0].startswith("Jetson")
        or os.path.exists("/etc/nv_tegra_release")
        or command_exists("tegrastats")
    ):
        result = read_jetson_ina3221()
        if result:
            return result

        result = read_jetson_tegrastats()
        if result:
            return result

    result = read_hwmon_power()
    if result:
        return result

    result = read_power_supply_machine()
    if result:
        return result

    result = read_macos_battery_power()
    if result:
        return result

    # 2. CPU package energy counters (Linux Intel/AMD)
    result = read_rapl_package_power()
    if result:
        return result

    result = read_amd_energy_power()
    if result:
        return result

    # 3. Every remaining device: TDP x CPU utilization
    result = read_estimated_cpu_power()
    if result:
        return result

    return {
        "power_w": None,
        "source": "UNAVAILABLE"
    }


# ============================================================
# MEASURE POWER
# ============================================================

def measure_power(duration=30.0, interval=0.5, verbose=True):

    samples = []

    start = time.monotonic()

    while True:

        now = time.monotonic()

        elapsed = now - start

        if elapsed >= duration:
            break

        result = read_power()

        power = result["power_w"]

        samples.append({
            "time": elapsed,
            "power": power,
            "source": result["source"]
        })

        if verbose:

            if power is None:

                print(
                    f"{elapsed:8.3f}s | POWER UNAVAILABLE"
                )

            else:

                print(
                    f"{elapsed:8.3f}s | "
                    f"{power:10.6f} W"
                )

        time.sleep(interval)

    return samples


# ============================================================
# AVERAGE POWER
# ============================================================

def calculate_average_power(samples):

    values = [
        x["power"]
        for x in samples
        if x["power"] is not None
    ]

    if not values:
        return None

    return sum(values) / len(values)


# ============================================================
# SAVE RAW POWER DATA
# ============================================================

def save_samples(samples, filename):

    with open(
        filename,
        "w",
        newline=""
    ) as f:

        writer = csv.writer(f)

        writer.writerow([
            "time_s",
            "power_w",
            "source"
        ])

        for sample in samples:

            writer.writerow([
                sample["time"],
                sample["power"],
                sample["source"]
            ])


# ============================================================
# ENERGY CALCULATION
# ============================================================

def calculate_energy(
    baseline_power,
    onload_power,
    total_time,
    iterations
):

    if baseline_power is None:
        raise ValueError(
            "Baseline power unavailable."
        )

    if onload_power is None:
        raise ValueError(
            "On-load power unavailable."
        )

    if iterations <= 0:
        raise ValueError(
            "Iterations must be greater than zero."
        )

    # --------------------------------------------------------
    # EXACT REPOSITORY-STYLE NET POWER
    # --------------------------------------------------------

    net_power = (
        onload_power -
        baseline_power
    )

    # --------------------------------------------------------
    # TOTAL ENERGY
    # --------------------------------------------------------

    total_energy = (
        net_power *
        total_time
    )

    # --------------------------------------------------------
    # ENERGY PER ITERATION
    # --------------------------------------------------------

    energy_per_iteration = (
        total_energy /
        iterations
    )

    # --------------------------------------------------------
    # ITERATIONS PER JOULE
    # --------------------------------------------------------

    if total_energy > 0:

        iterations_per_joule = (
            iterations /
            total_energy
        )

    else:

        iterations_per_joule = None

    return {
        "baseline_power": baseline_power,
        "on_load_power": onload_power,
        "net_power": net_power,
        "total_time": total_time,
        "iterations": iterations,
        "total_energy_j": total_energy,
        "joules_per_iteration":
            energy_per_iteration,
        "iterations_per_joule":
            iterations_per_joule
    }


# ============================================================
# LIBRARY API FOR BENCHMARKS
# ============================================================

class PowerSampler:
    """Sample whole-machine power in a background thread."""

    def __init__(self, interval=0.1):

        self.interval = interval
        self.samples = []
        self.source = "UNAVAILABLE"
        self._stop = threading.Event()
        self._thread = None

    def start(self):

        self.samples = []
        self.source = "UNAVAILABLE"
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop,
            daemon=True
        )
        self._thread.start()

    def _take_sample(self):

        result = read_power()
        power = result.get("power_w")
        self.source = result.get("source", "UNAVAILABLE")
        self.samples.append({
            "time": time.monotonic(),
            "power": power,
            "source": self.source
        })

    def _loop(self):

        while not self._stop.is_set():
            self._take_sample()
            self._stop.wait(self.interval)

    def stop(self):

        self._stop.set()

        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

        if not self.samples:
            self._take_sample()

        return calculate_average_power(self.samples)


def power_available():

    result = read_power()
    return result.get("power_w") is not None


def measure_baseline_power(duration=5.0, interval=0.2, verbose=False):

    samples = measure_power(
        duration=duration,
        interval=interval,
        verbose=verbose
    )

    average = calculate_average_power(samples)
    source = samples[0]["source"] if samples else "UNAVAILABLE"

    return average, source


def csv_energy_fields(baseline_power, onload_power, total_time, iterations):

    empty = ("", "", "", "", "")

    if baseline_power is None or onload_power is None:
        return empty

    if iterations <= 0 or total_time < 0:
        return empty

    try:

        result = calculate_energy(
            baseline_power=baseline_power,
            onload_power=onload_power,
            total_time=total_time,
            iterations=iterations
        )

    except ValueError:
        return empty

    iterations_per_joule = result["iterations_per_joule"]

    if iterations_per_joule is None:
        iterations_per_joule = ""

    return (
        result["baseline_power"],
        result["on_load_power"],
        result["net_power"],
        result["joules_per_iteration"],
        iterations_per_joule
    )


# ============================================================
# BASELINE MODE
# ============================================================

def baseline_mode(args):

    platform_name, model = detect_platform()

    print()
    print("=" * 70)
    print("BASELINE POWER MEASUREMENT")
    print("=" * 70)

    print(f"Platform : {platform_name}")
    print(f"Model    : {model}")
    print(
        f"Duration : {args.duration} seconds"
    )
    print()

    samples = measure_power(
        duration=args.duration,
        interval=args.interval
    )

    average = calculate_average_power(
        samples
    )

    if average is None:

        print()
        print(
            "ERROR: No software power telemetry "
            "was available."
        )

        return 1

    print()
    print("=" * 70)
    print("BASELINE RESULT")
    print("=" * 70)

    print(
        f"Average baseline power: "
        f"{average:.9f} W"
    )

    save_samples(
        samples,
        args.output
    )

    print(
        f"Raw samples saved to: "
        f"{args.output}"
    )

    # Save a simple baseline file
    with open(
        "baseline_power.txt",
        "w"
    ) as f:

        f.write(
            f"{average:.12f}"
        )

    print(
        "Baseline value saved to "
        "baseline_power.txt"
    )

    return 0


# ============================================================
# ON-LOAD MODE
# ============================================================

def onload_mode(args):

    platform_name, model = detect_platform()

    print()
    print("=" * 70)
    print("ON-LOAD POWER MEASUREMENT")
    print("=" * 70)

    print(f"Platform : {platform_name}")
    print(f"Model    : {model}")
    print(
        f"Duration : {args.duration} seconds"
    )
    print()

    samples = measure_power(
        duration=args.duration,
        interval=args.interval
    )

    average = calculate_average_power(
        samples
    )

    if average is None:

        print()
        print(
            "ERROR: No software power telemetry "
            "was available."
        )

        return 1

    print()
    print("=" * 70)
    print("ON-LOAD RESULT")
    print("=" * 70)

    print(
        f"Average on-load power: "
        f"{average:.9f} W"
    )

    save_samples(
        samples,
        args.output
    )

    print(
        f"Raw samples saved to: "
        f"{args.output}"
    )

    with open(
        "onload_power.txt",
        "w"
    ) as f:

        f.write(
            f"{average:.12f}"
        )

    return 0


# ============================================================
# CALCULATE MODE
# ============================================================

def calculate_mode(args):

    if not os.path.exists(
        "baseline_power.txt"
    ):

        print(
            "ERROR: baseline_power.txt "
            "not found."
        )

        return 1

    if args.onload is None:

        if not os.path.exists(
            "onload_power.txt"
        ):

            print(
                "ERROR: onload_power.txt "
                "not found."
            )

            return 1

        with open(
            "onload_power.txt"
        ) as f:

            onload = float(
                f.read().strip()
            )

    else:

        onload = args.onload

    with open(
        "baseline_power.txt"
    ) as f:

        baseline = float(
            f.read().strip()
        )

    result = calculate_energy(
        baseline_power=baseline,
        onload_power=onload,
        total_time=args.time,
        iterations=args.iterations
    )

    print()
    print("=" * 70)
    print("ENERGY RESULT")
    print("=" * 70)

    print(
        f"Baseline power       : "
        f"{result['baseline_power']:.9f} W"
    )

    print(
        f"On-load power        : "
        f"{result['on_load_power']:.9f} W"
    )

    print(
        f"Net power            : "
        f"{result['net_power']:.9f} W"
    )

    print(
        f"Total execution time : "
        f"{result['total_time']:.9f} s"
    )

    print(
        f"Iterations           : "
        f"{result['iterations']}"
    )

    print(
        f"Total energy         : "
        f"{result['total_energy_j']:.9f} J"
    )

    print(
        f"Joules / iteration   : "
        f"{result['joules_per_iteration']:.12f} J"
    )

    if result["iterations_per_joule"]:

        print(
            f"Iterations / joule   : "
            f"{result['iterations_per_joule']:.9f}"
        )

    print("=" * 70)

    # --------------------------------------------------------
    # CSV compatible with repository terminology
    # --------------------------------------------------------

    file_exists = os.path.exists(
        args.result
    )

    with open(
        args.result,
        "a",
        newline=""
    ) as f:

        writer = csv.writer(f)

        if not file_exists:

            writer.writerow([
                "machine",
                "iterations",
                "total_time",
                "time_per_iteration",
                "baseline_power",
                "on_load_power",
                "total_power_used",
                "joules_per_iteration",
                "iterations_per_joule"
            ])

        platform_name, _ = detect_platform()

        writer.writerow([
            platform_name,
            result["iterations"],
            result["total_time"],
            (
                result["total_time"] /
                result["iterations"]
            ),
            result["baseline_power"],
            result["on_load_power"],
            result["net_power"],
            result["joules_per_iteration"],
            result["iterations_per_joule"]
        ])

    print()
    print(
        f"Result appended to: {args.result}"
    )

    return 0


# ============================================================
# MAIN
# ============================================================

def main():

    parser = argparse.ArgumentParser(
        description=
        "Software power and energy monitor "
        "for PQC experiments."
    )

    parser.add_argument(
        "mode",
        choices=[
            "baseline",
            "onload",
            "calculate"
        ]
    )

    parser.add_argument(
        "--duration",
        type=float,
        default=30,
        help="Measurement duration in seconds"
    )

    parser.add_argument(
        "--interval",
        type=float,
        default=0.5,
        help="Sampling interval in seconds"
    )

    parser.add_argument(
        "--time",
        type=float,
        default=0,
        help="PQC execution time in seconds"
    )

    parser.add_argument(
        "--iterations",
        type=int,
        default=100,
        help="Number of PQC iterations"
    )

    parser.add_argument(
        "--onload",
        type=float,
        default=None,
        help="Manually specify on-load power"
    )

    parser.add_argument(
        "--output",
        default="power_samples.csv"
    )

    parser.add_argument(
        "--result",
        default="energy_results.csv"
    )

    args = parser.parse_args()

    if args.mode == "baseline":

        return baseline_mode(args)

    if args.mode == "onload":

        return onload_mode(args)

    if args.mode == "calculate":

        if args.time <= 0:

            print(
                "ERROR: --time must be > 0"
            )

            return 1

        return calculate_mode(args)


if __name__ == "__main__":

    sys.exit(main())