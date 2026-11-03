"""Optional CIK -> company name lookup from the SEC's public ticker list.

EDGAR-CORPUS carries no company name, only the CIK (the SEC's numeric filer id).
The SEC publishes a JSON of currently listed companies, which covers most but not
all filers from 2018-2020; unmatched CIKs keep a null name. The SEC's fair-access
policy rejects requests without a User-Agent naming a contact, so this is skipped
unless ELUSION_SEC_USER_AGENT is set.
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

URL = "https://www.sec.gov/files/company_tickers.json"


def load_company_names(user_agent: str | None, cache_path: Path) -> dict[str, str]:
    """Return {cik: name}. Empty when `user_agent` is None. Cached on disk after first fetch."""
    if user_agent is None:
        return {}
    if not cache_path.exists():
        req = urllib.request.Request(URL, headers={"User-Agent": user_agent})
        with urllib.request.urlopen(req, timeout=30) as resp:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_bytes(resp.read())
    raw = json.loads(cache_path.read_text())
    return {str(v["cik_str"]): v["title"] for v in raw.values()}
