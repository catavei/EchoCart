"""Settings loaded from the environment (and a local .env file, if present).

No python-dotenv: the loader below is deliberately tiny, and docker compose
passes .env through on its own.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

SANDBOX_API_BASE = "https://api-m.sandbox.paypal.com"
ROOT = Path(__file__).resolve().parent.parent


def load_dotenv(path: Path = ROOT / ".env") -> None:
    """Populate os.environ from KEY=VALUE lines. Existing variables win."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


class LiveCredentialsRefused(RuntimeError):
    """Raised when the PayPal base URL is anything but the sandbox."""


@dataclass(frozen=True)
class Settings:
    paypal_client_id: str
    paypal_client_secret: str
    paypal_api_base: str
    eur_per_ron: Decimal
    llm_mode: str
    anthropic_api_key: str
    anthropic_model: str
    anthropic_effort: str
    app_base_url: str
    data_dir: Path

    def assert_sandbox(self) -> None:
        if self.paypal_api_base.rstrip("/") != SANDBOX_API_BASE:
            raise LiveCredentialsRefused(
                f"PAYPAL_API_BASE must be {SANDBOX_API_BASE}; got {self.paypal_api_base!r}. "
                "EchoCart only ever talks to the PayPal sandbox."
            )


def get_settings() -> Settings:
    load_dotenv()
    env = os.environ.get
    settings = Settings(
        paypal_client_id=env("PAYPAL_CLIENT_ID", ""),
        paypal_client_secret=env("PAYPAL_CLIENT_SECRET", ""),
        paypal_api_base=env("PAYPAL_API_BASE", SANDBOX_API_BASE),
        eur_per_ron=Decimal(env("EUR_PER_RON", "0.201")),
        llm_mode=env("LLM_MODE", "anthropic").lower(),
        anthropic_api_key=env("ANTHROPIC_API_KEY", ""),
        anthropic_model=env("ANTHROPIC_MODEL", "claude-opus-5-5"),
        anthropic_effort=env("ANTHROPIC_EFFORT", "low"),
        app_base_url=env("APP_BASE_URL", "http://localhost:8000").rstrip("/"),
        data_dir=Path(env("DATA_DIR", str(ROOT / "data"))),
    )
    settings.assert_sandbox()
    return settings
