"""Runtime settings, read from the environment after loading `.env` if present.

Only non-secret defaults live here. Secrets (API keys) have no default and are
required only by the code paths that use them.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()  # no-op when .env is absent; never overrides variables already set

# Prefect reads this itself at import time; set it before any `import prefect` so local
# flow runs report to the compose server instead of spinning up an ephemeral one.
os.environ.setdefault("PREFECT_API_URL", "http://localhost:4200/api")


def _env(name: str, default: str) -> str:
    return os.environ.get(name) or default


@dataclass(frozen=True)
class Settings:
    database_url: str = field(
        default_factory=lambda: _env(
            "DATABASE_URL", "postgresql://elusion:elusion@localhost:5433/elusion"
        )
    )
    kafka_bootstrap: str = field(default_factory=lambda: _env("KAFKA_BOOTSTRAP", "localhost:9092"))
    data_dir: Path = field(default_factory=lambda: Path(_env("ELUSION_DATA_DIR", "data")))
    # SEC EDGAR refuses requests whose User-Agent lacks a contact, e.g. "Name email@example.com".
    # Unset means company names are left null. Never a secret, but personal, so not defaulted.
    sec_user_agent: str | None = field(
        default_factory=lambda: os.environ.get("ELUSION_SEC_USER_AGENT") or None
    )
    # Pinned model snapshot id used for the bulk review pass (hard rule: always pinned).
    review_model: str = field(
        default_factory=lambda: _env("ELUSION_REVIEW_MODEL", "claude-haiku-5-5")
    )

    # Pinned OpenAI review model (phase 4). See docs/DECISIONS.md for the id and its prices.
    openai_model: str = field(default_factory=lambda: _env("ELUSION_OPENAI_MODEL", "gpt-6-luna"))

    # Local Ollama server and the model it serves (phase 4). Pinned by name:tag; the digest
    # is recorded in docs/DECISIONS.md.
    ollama_host: str = field(default_factory=lambda: _env("OLLAMA_HOST", "http://localhost:11434"))
    ollama_model: str = field(default_factory=lambda: _env("ELUSION_OLLAMA_MODEL", "qwen2.5:14b"))
    # sha256 digest of that tag (`ollama show`); `name:tag` can move, the digest cannot. Logged
    # as an MLflow param; docs/DECISIONS.md (2026-11-06) holds the same value.
    ollama_digest: str = field(
        default_factory=lambda: _env(
            "ELUSION_OLLAMA_DIGEST",
            "sha256:7cdf5a0187d5c58cc5d369b255592f7841d1c4696d45a8c8a9489440385b22f6",
        )
    )

    # Pinned model snapshot id used for the judge pass that labels the gold set.
    judge_model: str = field(default_factory=lambda: _env("ELUSION_JUDGE_MODEL", "claude-opus-5-5"))

    # Spend guard: the review flow refuses to continue a run projected to cost more.
    max_run_usd: float = field(default_factory=lambda: float(_env("ELUSION_MAX_RUN_USD", "10")))

    # MLflow tracking server (compose service, remapped off 5000, which macOS uses).
    mlflow_tracking_uri: str = field(
        default_factory=lambda: _env("MLFLOW_TRACKING_URI", "http://localhost:5001")
    )

    @property
    def corpus_dir(self) -> Path:
        return self.data_dir / "corpus"


settings = Settings()
