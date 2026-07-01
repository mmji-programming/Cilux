from pathlib import Path
import sys


def base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent  # after install
    return Path(sys.argv[0]).resolve().parent  # develop
