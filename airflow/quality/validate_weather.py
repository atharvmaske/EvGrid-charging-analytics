import sys

from ge_checks import run_check

if __name__ == "__main__":
    sys.exit(1 if run_check("weather") else 0)
