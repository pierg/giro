"""``python -m giro`` — the same front door as the ``giro`` script.

A dispatched Run re-enters the engine this way, so detaching never depends on
a console script being on the background process's PATH.
"""

from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
