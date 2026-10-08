"""Run acceptance tests for the transport findings without live Altium."""
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[2]
raise SystemExit(subprocess.call(
    [sys.executable, '-m', 'pytest', 'tests/test_review_fixes.py', '-q'], cwd=root))
