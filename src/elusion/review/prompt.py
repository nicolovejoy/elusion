"""Versioned system prompts, stored as plain Markdown files next to the code.

A system prompt is the fixed instruction text sent to the LLM ahead of every document. Its
wording changes the scores, so a score is only meaningful alongside the exact prompt that
produced it. We therefore treat prompts like schema migrations: once published, a version
file such as prompts/cloud-concentration/v1.md is never edited, a change becomes v2.md.
The prompt version string is "<criterion>/<version>" (for example "cloud-concentration/v1")
and is stored with every result.

The prompts/ directory has no __init__.py: it is data, not an importable package. That is
also why this module is named prompt.py and not prompts.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

PROMPTS_DIR = Path(__file__).parent / "prompts"


@dataclass(frozen=True)
class Prompt:
    text: str
    version: str  # "<criterion>/<version>", recorded with every score


def load_prompt(criterion: str, version: str = "v1") -> Prompt:
    """Load prompts/<criterion>/<version>.md. Raises FileNotFoundError naming the path."""
    path = PROMPTS_DIR / criterion / f"{version}.md"
    if not path.is_file():
        raise FileNotFoundError(f"no prompt file at {path}")
    return Prompt(text=path.read_text(encoding="utf-8"), version=f"{criterion}/{version}")
