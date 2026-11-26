"""Keep test runs from appending trace lines to the committed traces/ folder."""

from __future__ import annotations

import os
import tempfile

os.environ.setdefault("TRACE_FILE", os.path.join(tempfile.gettempdir(), "rahbar-test-traces.jsonl"))
