"""Config loading. Secrets come only from the environment / .env, never from YAML."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
RUNS_DIR = PROJECT_ROOT / "runs"


@lru_cache(maxsize=None)
def load_yaml(name: str) -> dict:
    with open(CONFIG_DIR / f"{name}.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_env() -> None:
    """Load .env from the project root or any parent directory (does not override real env vars)."""
    # Nearest .env wins: project root first, then parents (e.g. a shared .env one level up).
    for parent in [PROJECT_ROOT, *PROJECT_ROOT.parents]:
        candidate = parent / ".env"
        if candidate.is_file():
            load_dotenv(candidate, override=False)
            return


def get_api_key() -> str | None:
    load_env()
    key = os.environ.get("PORTKEY_API_KEY", "").strip()
    return key or None
