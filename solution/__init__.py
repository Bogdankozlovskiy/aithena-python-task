"""Source analysis and translation, supported on Python 3.13 only."""

import sys

if sys.version_info[:2] != (3, 13):
    raise RuntimeError("Python 3.13 is required to run aithena-solution.")
