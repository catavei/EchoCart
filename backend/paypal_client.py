"""Thin async client for the PayPal REST APIs EchoCart uses (sandbox only).

- Vault v3:    setup token -> (buyer approves once) -> payment token (vault_id)
- Orders v2:   create an order with payment_source.paypal.vault_id; capture if needed
- Payments v2: refund a captured payment

Every money-moving call is appended to a JSONL audit log with a timestamp and
the order / capture id.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

from .config import SANDBOX_API_BASE, LiveCredentialsRefused

log = logging.getLogger("echocart.paypal")


class PayPalError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, body: Any = None):
        super().__init__(message)
        self.status = status
        self.body = body


class PaymentLog:
    """Append-only audit trail of create / capture / refund actions."""

    def __init__(self, path: Path):
        self.path = path

    def write(self, action: str, **fields: Any) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        entry = {"ts": datetime.now(timezone.utc).isoformat(), "action": action, **fields}
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, default=str) + "\n")
        log.info("payment %s %s", action, fields)


def _link(body: dict, rel: str) -> str | None:
    for link in body.get("links", []):
        if link.get("rel") == rel:
            return link.get("href")
    return None


class PayPalClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        payment_log: PaymentLog,
        api_base: str = SANDBOX_API_BASE,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        # Second line of defence after Settings.assert_sandbox().
        if api_base.rstrip("/") != SANDBOX_API_BASE:
            raise LiveCredentialsRefused(f"Refusing non-sandbox PayPal base URL {api_base!r}")
        self._auth = (client_id, client_secret)
        self._log = payment_log
        self._http = httpx.AsyncClient(base_url=SANDBOX_API_BASE, transport=transport, timeout=30)
        self._token: str | None = None

    async def aclose(self) -> None:
        await self._http.aclose()

    # ---- plumbing -------------------------------------------------------
    async def _access_token(self) -> str:
        if self._token is None:
            resp = await self._http.post(
                "/v1/oauth2/token", data={"grant_type": "client_credentials"}, auth=self._auth
            )
            if resp.status_code != 200:
                raise PayPalError("PayPal authentication failed", resp.status_code, resp.text)
            self._token = resp.json()["access_token"]
        return self._token

    async def _request(self, method: str, path: str, json_body: dict | None = None,
                       idempotency_key: str | None = None) -> dict:
        headers = {
            "Authorization": f"Bearer {await self._access_token()}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
            "PayPal-Request-Id": idempotency_key or str(uuid.uuid4()),
        }
        resp = await self._http.request(method, path, json=json_body, headers=headers)
        if resp.status_code == 401:  # token expired: refresh once
            self._token = None
            headers["Authorization"] = f"Bearer {await self._access_token()}"
            resp = await self._http.request(method, path, json=json_body, headers=headers)
        body = resp.json() if resp.content else {}
        if resp.status_code >= 400:
            raise PayPalError(f"PayPal {method} {path} failed", resp.status_code, body)
        return body

    # ---- Vault v3: one-time account link --------------------------------
    async def create_setup_token(self, return_url: str, cancel_url: str,
                                 customer_id: str | None = None) -> dict:
        """Returns {"id", "approve_url"}. The buyer must open approve_url once."""
        payload: dict[str, Any] = {
            "payment_source": {
                "paypal": {
                    "description": "EchoCart voice checkout",
                    "usage_type": "MERCHANT",
                    "usage_pattern": "IMMEDIATE",
                    "customer_type": "CONSUMER",
                    "experience_context": {
                        "brand_name": "EchoCart",
                        "shipping_preference": "NO_SHIPPING",
                        "return_url": return_url,
                        "cancel_url": cancel_url,
                    },
                }
            }
        }
        if customer_id:
            payload["customer"] = {"id": customer_id}
        body = await self._request("POST", "/v3/vault/setup-tokens", payload)
        return {"id": body["id"], "status": body.get("status"), "approve_url": _link(body, "approve")}

    async def create_payment_token(self, setup_token_id: str) -> dict:
        """Exchange an approved setup token for a long-lived vault_id."""
        body = await self._request(
            "POST", "/v3/vault/payment-tokens",
            {"payment_source": {"token": {"id": setup_token_id, "type": "SETUP_TOKEN"}}},
        )
        return {"vault_id": body["id"], "customer_id": body.get("customer", {}).get("id"), "raw": body}

    # ---- Orders v2 -------------------------------------------------------
    async def create_order_with_vault(self, vault_id: str, amount_eur: Decimal,
                                      description: str, reference_id: str) -> dict:
        """Create an order paid by the saved wallet.

        PayPal's docs show this returning COMPLETED (already captured). If it
        comes back APPROVED instead, the caller captures it explicitly.
        """
        payload = {
            "intent": "CAPTURE",
            "purchase_units": [{
                "reference_id": reference_id,
                "description": description[:127],
                "amount": {"currency_code": "EUR", "value": f"{amount_eur:.2f}"},
            }],
            "payment_source": {"paypal": {"vault_id": vault_id}},
        }
        body = await self._request("POST", "/v2/checkout/orders", payload, idempotency_key=reference_id)
        self._log.write("create", order_id=body.get("id"), status=body.get("status"),
                        amount_eur=f"{amount_eur:.2f}", reference_id=reference_id)
        return body

    async def capture_order(self, order_id: str) -> dict:
        body = await self._request("POST", f"/v2/checkout/orders/{order_id}/capture", {},
                                   idempotency_key=f"capture-{order_id}")
        self._log.write("capture", order_id=order_id, status=body.get("status"),
                        capture_id=first_capture_id(body))
        return body

    # ---- Payments v2 -----------------------------------------------------
    async def refund_capture(self, capture_id: str, order_id: str) -> dict:
        body = await self._request("POST", f"/v2/payments/captures/{capture_id}/refund", {},
                                   idempotency_key=f"refund-{capture_id}")
        self._log.write("refund", order_id=order_id, capture_id=capture_id,
                        refund_id=body.get("id"), status=body.get("status"))
        return body


def first_capture_id(order: dict) -> str | None:
    for unit in order.get("purchase_units", []):
        for cap in unit.get("payments", {}).get("captures", []):
            return cap.get("id")
    return None
