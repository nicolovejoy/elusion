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

import re
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


# The judge prompt a criterion uses when it is not its newest. `auditable-decisions` has a
# judge-v3 that did no better than judge-v2 on the owner's 30 labels; judge-v2 was chosen
# (docs/DECISIONS.md, 2026-11-21) and judge-v3 stays on disk because it wrote labels.
CHOSEN_JUDGE = {"auditable-decisions": "judge-v2"}


def judge_version(criterion: str) -> str:
    """The judge prompt of a criterion: the one in CHOSEN_JUDGE, else the newest on disk
    ("judge-vN" for the highest N).

    A judge prompt is never edited once it has labeled anything; a change of rule is a
    new file, and the judge, the agreement count and the report all work from one
    version. Labels written under another version stay in the gold CSV and are not read.
    "judge-v1" when the criterion has no judge prompt file.
    """
    if criterion in CHOSEN_JUDGE:
        return CHOSEN_JUDGE[criterion]
    found = (re.fullmatch(r"judge-v(\d+)", p.stem) for p in (PROMPTS_DIR / criterion).glob("*.md"))
    return f"judge-v{max((int(m.group(1)) for m in found if m), default=1)}"
