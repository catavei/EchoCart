"""FastAPI app: JSON API for the voice UI, PayPal return pages, static frontend."""
from __future__ import annotations

import html
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .cart import Catalog
from .config import ROOT, get_settings
from .dialog import Conversation, Deps
from .llm import make_parser
from .paypal_client import PaymentLog, PayPalClient, PayPalError
from .store import Store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("echocart")

FRONTEND = ROOT / "frontend"
MAX_SESSIONS = 200
logging.getLogger("httpx").setLevel(logging.WARNING)


class SessionRef(BaseModel):
    session_id: str


class Utterance(SessionRef):
    text: str = Field(max_length=500)


def build_deps() -> Deps:
    settings = get_settings()  # raises LiveCredentialsRefused unless sandbox
    catalog = Catalog.load()
    paypal = None
    if settings.paypal_client_id and settings.paypal_client_secret:
        paypal = PayPalClient(settings.paypal_client_id, settings.paypal_client_secret,
                              PaymentLog(settings.data_dir / "payments.jsonl"), settings.paypal_api_base)
    else:
        log.warning("PAYPAL_CLIENT_ID/SECRET not set: payments disabled")
    log.info("LLM mode: %s, model: %s", settings.llm_mode, settings.anthropic_model)
    return Deps(catalog=catalog, parser=make_parser(settings, catalog), paypal=paypal,
                store=Store(settings.data_dir / "store.json"), eur_per_ron=settings.eur_per_ron,
                base_url=settings.app_base_url)


def create_app(deps: Deps | None = None) -> FastAPI:
    state: dict = {"deps": deps, "sessions": {}}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if state["deps"] is None:
            state["deps"] = build_deps()
        yield
        d = state["deps"]
        if d.paypal:
            await d.paypal.aclose()
        if hasattr(d.parser, "aclose"):
            await d.parser.aclose()

    app = FastAPI(title="EchoCart", lifespan=lifespan)

    def session(session_id: str) -> Conversation:
        conv = state["sessions"].get(session_id)
        if conv is None:
            raise HTTPException(404, "Unknown session. Reload the page.")
        return conv

    @app.get("/api/status")
    def status():
        d = state["deps"]
        return {"linked": d.store.linked, "paypal_configured": d.paypal is not None}

    @app.post("/api/session")
    def new_session():
        sessions = state["sessions"]
        if len(sessions) >= MAX_SESSIONS:
            sessions.pop(next(iter(sessions)))
        conv = Conversation(state["deps"])
        sessions[conv.session_id] = conv
        return {"session_id": conv.session_id, **conv.welcome().to_json()}

    @app.post("/api/utterance")
    async def utterance(body: Utterance):
        reply = await session(body.session_id).handle(body.text)
        return reply.to_json()

    @app.post("/api/link/start")
    async def link_start(body: SessionRef):
        conv = session(body.session_id)
        try:
            url = await conv.start_link()
        except PayPalError as e:
            # Vault not enabled for this sandbox app: fall back to approving each payment.
            log.warning("setup token failed, using approval popup per payment: %s %s", e.status, e.body)
            conv.vault_unavailable = True
            return {"url": None, **conv.vault_fallback_notice().to_json()}
        return {"url": url}

    @app.post("/api/events")
    def events(body: SessionRef):
        return {"events": [r.to_json() for r in session(body.session_id).drain_events()]}

    # ---- pages PayPal redirects the popup to ----------------------------
    @app.get("/paypal/linked", response_class=HTMLResponse)
    async def paypal_linked(session: str):
        conv = state["sessions"].get(session)
        reply = await conv.complete_link() if conv else None
        return _done_page(reply.speech if reply else "Session expired. Return to EchoCart and reload.")

    @app.get("/paypal/approved", response_class=HTMLResponse)
    async def paypal_approved(session: str, token: str):
        conv = state["sessions"].get(session)
        reply = await conv.complete_approval(token) if conv else None
        return _done_page(reply.speech if reply else "Session expired. Return to EchoCart and reload.")

    @app.get("/paypal/cancelled", response_class=HTMLResponse)
    def paypal_cancelled(session: str):
        conv = state["sessions"].get(session)
        reply = conv.cancelled_in_paypal() if conv else None
        return _done_page(reply.speech if reply else "Nothing was charged. You can close this window.")

    app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
    return app


def _done_page(message: str) -> str:
    """Accessible popup page: says what happened, tells the opener, closes itself."""
    msg = html.escape(message)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>EchoCart: back from PayPal</title>
<style>body{{font:1.25rem/1.5 system-ui,sans-serif;background:#fff;color:#111;margin:2rem}}
@media (prefers-color-scheme:dark){{body{{background:#111;color:#f5f5f5}}}}</style></head>
<body><main><h1>Back from PayPal</h1><p role="status">{msg}</p>
<p>This window will close. If it doesn't, close it to return to EchoCart.</p></main>
<script>
try {{ if (window.opener) window.opener.postMessage("echocart:paypal-done", location.origin); }} catch (e) {{}}
setTimeout(function () {{ window.close(); }}, 1500);
</script></body></html>"""


app = create_app()
