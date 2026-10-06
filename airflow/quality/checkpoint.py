
import sys

from ge_checks import run_all

if __name__ == "__main__":
    sys.exit(1 if run_all() else 0)
