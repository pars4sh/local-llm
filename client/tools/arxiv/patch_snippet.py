"""Build-time patch of arxiv-mcp-server 0.8.2 (D-023): search results carry the first two
sentences of each abstract instead of its first 280 characters, or "not available".

The replacement must match exactly once, or the build fails: a new upstream version needs this
file reviewed.
"""

import sys
from pathlib import Path

path = Path(sys.argv[1])
src = path.read_text()

OLD = """    if len(abstract) <= max_chars:
        return abstract

    # Fixed-length slice (no word-boundary heuristics) for stable output.
    return abstract[:max_chars].rstrip() + _ABSTRACT_TRUNCATION_MARK
"""
NEW = """    # local-llm patch (D-023): the first two sentences, at most about 450 characters.
    import re

    text = " ".join((abstract or "").split())
    if not text:
        return "not available"
    out = " ".join(re.split(r"(?<=[.!?])\\s+(?=[A-Z0-9(\\[])", text)[:2])
    if len(out) > 450:
        out = out[:450].rsplit(" ", 1)[0]
    return out + _ABSTRACT_TRUNCATION_MARK if len(out) < len(text) else out
"""

if src.count(OLD) != 1:
    sys.exit(f"{path}: expected the snippet code once, found {src.count(OLD)}")
path.write_text(src.replace(OLD, NEW))
print(f"patched {path}")
