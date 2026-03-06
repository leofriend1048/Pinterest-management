"""
Entry point for `python -m pinterest_buyer`.

Adds the pinterest_buyer/ directory to sys.path so that all internal
imports (import config, from state import db, etc.) resolve correctly.
"""
import sys
import os

# Ensure pinterest_buyer/ is on sys.path so bare `import config` works
_pkg_dir = os.path.dirname(__file__)
if _pkg_dir not in sys.path:
    sys.path.insert(0, _pkg_dir)

from pinterest_buyer.main import main  # noqa: E402

if __name__ == "__main__":
    main()
