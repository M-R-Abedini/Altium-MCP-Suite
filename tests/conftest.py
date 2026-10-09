"""Make repository modules available to pytest entry points and IDE runners."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
