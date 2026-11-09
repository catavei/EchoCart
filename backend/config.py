"""Settings loaded from the environment (and a local .env file, if present).

No python-dotenv: the loader below is deliberately tiny, and docker compose
passes .env through on its own.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
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
    llm_mode: str
    anthropic_api_key: str
    anthropic_model: str
    anthropic_effort: str
    app_base_url: str
    data_dir: Path
    catalog_url: str = ""   # merchant product feed (URL or file); empty = built-in catalogue
    shop_origins: tuple[str, ...] = ()   # shop websites allowed to show EchoCart (embed.js)
    # "single": one shopper per server (data/store.json). "per_browser": each browser gets its own
    # linked PayPal, orders, limit and PIN, so a hosted demo can be tried by several judges at once.
    shoppers: str = "single"

    def assert_sandbox(self) -> None:
        if self.paypal_api_base.rstrip("/") != SANDBOX_API_BASE:
            raise LiveCredentialsRefused(
                f"PAYPAL_API_BASE must be {SANDBOX_API_BASE}; got {self.paypal_api_base!r}. "
                "EchoCart only ever talks to the PayPal sandbox."
            )


_ORIGIN = re.compile(r"https?://[a-z0-9.-]+(:[0-9]{1,5})?")


def parse_origins(raw: str) -> tuple[str, ...]:
    """SHOP_ORIGINS: comma-separated shop addresses, like https://shop.example. Each is a scheme,
    a host and maybe a port, nothing else, because the browser compares them exactly."""
    origins = []
    for part in raw.split(","):
        origin = part.strip().rstrip("/").lower()
        if not origin:
            continue
        if not _ORIGIN.fullmatch(origin):
            raise ValueError(f"SHOP_ORIGINS: {part.strip()!r} isn't a website address like https://shop.example "
                             "(no path, no spaces).")
        origins.append(origin)
    return tuple(dict.fromkeys(origins))


def get_settings() -> Settings:
    load_dotenv()
    env = os.environ.get
    settings = Settings(
        paypal_client_id=env("PAYPAL_CLIENT_ID", ""),
        paypal_client_secret=env("PAYPAL_CLIENT_SECRET", ""),
        paypal_api_base=env("PAYPAL_API_BASE", SANDBOX_API_BASE),
        llm_mode=env("LLM_MODE", "anthropic").lower(),
        anthropic_api_key=env("ANTHROPIC_API_KEY", ""),
        anthropic_model=env("ANTHROPIC_MODEL", "claude-opus-5-5"),
        anthropic_effort=env("ANTHROPIC_EFFORT", "low"),
        # Render sets RENDER_EXTERNAL_URL to the service's public https address.
        app_base_url=(env("APP_BASE_URL") or env("RENDER_EXTERNAL_URL") or "http://localhost:8000").rstrip("/"),
        data_dir=Path(env("DATA_DIR", str(ROOT / "data"))),
        catalog_url=env("CATALOG_URL", "").strip(),
        shop_origins=parse_origins(env("SHOP_ORIGINS", "")),
        shoppers="per_browser" if env("SHOPPERS", "").strip().lower() == "per_browser" else "single",
    )
    settings.assert_sandbox()
    return settings
