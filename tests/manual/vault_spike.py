"""Manual sandbox check of the vault path. Run BEFORE building on it.

    .venv/Scripts/python -m tests.manual.vault_spike        (Windows)
    .venv/bin/python -m tests.manual.vault_spike            (macOS/Linux)

Needs PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET for a sandbox app with "Vault"
enabled, and a sandbox *personal* account to log in with as the buyer.

Steps:
 1. create a setup token and print the approve URL
 2. you open it, log in as the sandbox buyer and approve, then press Enter
 3. exchange it for a payment token (vault_id)
 4. create an order with that vault_id and NO buyer interaction; capture if needed
 5. refund the capture
It prints each response status so you can see whether PayPal asks for payer
action at step 4 (the thing the docs do not guarantee).
"""
from __future__ import annotations

import asyncio
import json
import uuid
from decimal import Decimal

from backend.config import get_settings
from backend.paypal_client import PaymentLog, PayPalClient, PayPalError, first_capture_id


async def main() -> None:
    s = get_settings()
    if not (s.paypal_client_id and s.paypal_client_secret):
        raise SystemExit("Set PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET in .env first.")
    pp = PayPalClient(s.paypal_client_id, s.paypal_client_secret,
                      PaymentLog(s.data_dir / "payments-spike.jsonl"))
    try:
        st = await pp.create_setup_token(f"{s.app_base_url}/linked", f"{s.app_base_url}/link-cancelled")
        print("1. setup token:", st["id"], st["status"])
        print("   Open this, log in as the sandbox BUYER and approve:\n  ", st["approve_url"])
        input("2. Press Enter after approving (the return page may 404; that's fine)... ")

        tok = await pp.create_payment_token(st["id"])
        print("3. vault_id:", tok["vault_id"], " customer_id:", tok["customer_id"])

        order = await pp.create_order_with_vault(tok["vault_id"], Decimal("1.00"),
                                                 "EchoCart spike", f"spike-{uuid.uuid4()}")
        print("4. order:", order.get("id"), "status:", order.get("status"))
        if order.get("status") == "PAYER_ACTION_REQUIRED":
            print("   !! PayPal wants payer action -> vault path NOT voice-only in this account.")
            print(json.dumps(order.get("links"), indent=2))
            return
        if order.get("status") != "COMPLETED":
            order = await pp.capture_order(order["id"])
            print("   captured:", order.get("status"))
        cap = first_capture_id(order)
        print("   capture_id:", cap)

        refund = await pp.refund_capture(cap, order["id"])
        print("5. refund:", refund.get("id"), refund.get("status"))
        print("\nVault path works in this sandbox account.")
    except PayPalError as e:
        print("PayPal error", e.status, json.dumps(e.body, indent=2))
    finally:
        await pp.aclose()


if __name__ == "__main__":
    asyncio.run(main())
