"""Repository entry point. Python 3.11+; no install needed for non-PDF formats."""
import sys

if sys.version_info < (3, 11):
    raise SystemExit('Synapse KB requires Python 3.11 or newer.')

from atlas_core.cli import main

if __name__ == '__main__':
    raise SystemExit(main())
