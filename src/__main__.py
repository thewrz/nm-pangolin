#!/usr/bin/env python3
"""Entry point for the NM Pangolin VPN service daemon.

Configures logging to systemd journal when available, falling back to stderr.
Can be invoked as ``python -m src`` during development or directly when
installed to /usr/lib/nm-pangolin/.
"""

import logging
import os
import sys

# When installed flat to /usr/lib/nm-pangolin/, ensure sibling modules
# are importable without package structure.
_here = os.path.dirname(os.path.abspath(__file__))
if _here not in sys.path:
    sys.path.insert(0, _here)

try:
    from systemd.journal import JournalHandler
    handler = JournalHandler(SYSLOG_IDENTIFIER="nm-pangolin")
except ImportError:
    handler = logging.StreamHandler(sys.stderr)

logging.basicConfig(level=logging.INFO, handlers=[handler])

try:
    from .nm_pangolin_service import main
except ImportError:
    from nm_pangolin_service import main

main()
