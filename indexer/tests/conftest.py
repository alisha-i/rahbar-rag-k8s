"""Make the repository root importable, so tests can reach eval/ and api/ as packages,
and let the offline tests use deterministic hash vectors instead of a live Ollama."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("ALLOW_FAKE_EMBEDDINGS", "1")
