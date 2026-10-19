"""Fiat Vox runs only on macOS with Apple Silicon."""
import platform
import sys

UNSUPPORTED = ("Fiat Vox runs only on Macs with Apple Silicon (M1 or later). "
               "This computer is {system} on {machine}.")


def check():
    """Exit with a clear message on anything but macOS on arm64."""
    system, machine = platform.system(), platform.machine()
    if system != "Darwin" or machine != "arm64":
        sys.exit(UNSUPPORTED.format(system=system or "unknown", machine=machine or "unknown"))
