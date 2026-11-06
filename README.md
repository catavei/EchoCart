# EchoCart

**Voice-first shopping and PayPal checkout for blind and low-vision shoppers.**

You press one big button (or Space) and say *"two packs of AA batteries under 50 lei"*. EchoCart reads the cart back with the exact total and delivery date. You say *"yes"*, and it pays with PayPal and reads you the receipt. Changes, repeats and refunds all work by voice, and you never have to find a button on a checkout page.

Built for the [PayPal AI Hackathon](https://paypalaihackathon.devpost.com/). It runs on the **PayPal sandbox only**, so no real money moves.

---

## Why it matters

Online checkout assumes you can see the page. Screen readers help, but checkouts are long and full of popups, changing totals and pages that redirect without warning. Most voice shopping assistants still hand you to a visual payment page at the most important moment.

EchoCart is designed around three things a blind shopper needs to trust a payment:

1. **Hearing exactly what you're paying for** just before you pay: the items, quantities, total and delivery date, in plain speech.
2. **A yes that means yes.** Money moves only after a clear "yes" given right after that read-back. Anything else counts as no.
3. **No sighted step at checkout.** You link your PayPal account once, in a screen-reader-friendly window. After that, checkout happens entirely by voice.

## How it works

```
 microphone / keyboard          FastAPI backend                         external APIs
┌──────────────────┐   text   ┌──────────────────────────────────┐
│ Talk button      │ ───────▶ │ dialog.py: one utterance in,     │
│ (Space / Esc)    │          │   one spoken reply out           │
│ Web Speech API   │ ◀─────── │ safety.py: confirmation gate,    │──▶ Claude: utterance → cart JSON
│ aria-live regions│  speech  │   LLM output validation          │    (JSON schema, ids only)
│ earcons, TTS     │          │ cart.py: prices from catalog.json│
└──────────────────┘          │ paypal_client.py                 │──▶ PayPal sandbox
                              └──────────────────────────────────┘    Vault v3, Orders v2, Payments v2
```

- **AI:** Claude turns what you said into structured JSON (`propose_cart`, `edit_cart`, `clarify`, `refund_request`, ...) that may only contain product ids and quantities. The backend rejects the reply if it doesn't match the schema, names a product that isn't in the catalogue, asks for more than is in stock, or goes over your stated budget. In that case EchoCart asks a short question instead of showing a cart. **Prices and totals are always computed by the backend**, never taken from the model.
- **PayPal:**
  - **Vault v3:** a one-time setup token. The shopper approves it in a PayPal popup, and it becomes a saved payment token (`vault_id`).
  - **Orders v2:** each order is created with `payment_source.paypal.vault_id`, so no approval page is needed. EchoCart captures the order explicitly if PayPal hasn't already.
  - **Payments v2:** refunds go to `POST /v2/payments/captures/{id}/refund`, either full or partial (with an amount). The last partial refund uses the exact remainder, so rounding never refunds more than was charged.
  - **Vault v3 delete:** "unlink my PayPal" calls `DELETE /v3/vault/payment-tokens/{id}`.
- **Currency:** PayPal REST doesn't support Romanian lei (RON). Carts are priced in lei and charged in **EUR at a fixed demo rate** (`EUR_PER_RON`). Both amounts are spoken: *"Total 45 lei 80 bani, charged as 9 euros 21 cents."*
- **Fallback:** if the sandbox app can't save PayPal accounts (Vault not enabled), EchoCart opens PayPal's standard approval page in a popup for each payment instead, and says what to do at each step.

### Safety rules, enforced in code

| Rule | Where |
|---|---|
| Money moves only after "yes", "yes, pay" or "confirm", said right after the backend spoke the exact total | `safety.ConfirmationGate`, `dialog.Conversation._say` |
| If the cart changes, or anything else is said, the yes no longer counts; a new read-back and a new yes are required | `ConfirmationGate.consume` checks the cart fingerprint; every non-read-back reply disarms it |
| Refunds, unlinking PayPal, and raising or removing the spending limit each need their own read-back and yes. A yes given for one can't approve another | `ConfirmationGate` kinds `payment` / `refund` / `unlink` / `limit` |
| An order over the spending limit is never offered for a yes, and the payment step checks the limit again | `dialog.Conversation._propose`, `_pay` |
| LLM output that fails the schema, names an unknown product id, or goes over budget is never shown | `safety.validate_llm_output` |
| Prices come from `catalog.json`, never from the LLM | `cart.price_cart` |
| Every create, capture and refund is logged with a timestamp and order id | `data/payments.jsonl` (`paypal_client.PaymentLog`) |
| The app refuses to start unless the PayPal URL is the sandbox one | `config.Settings.assert_sandbox`, plus a second check in `PayPalClient` |

---

## Setup (about 5 minutes)

### 1. PayPal sandbox app (2 min)

1. Sign in at <https://developer.paypal.com/> and open **Apps & Credentials**, making sure the **Sandbox** toggle is on.
2. Create an app (or use the default one) and copy its **Client ID** and **Secret**.
3. In the app's **Sandbox App Settings > App Feature Options**, make sure **Accept payments** is ticked. Then open **Advanced options** and tick **Vault**. Without Vault, EchoCart still works, but you approve each payment in a popup.
4. Open **Testing Tools > Sandbox Accounts** and note the email and password of a **Personal** sandbox account. You log in with it as the shopper.

### 2. Configure (1 min)

```bash
cp .env.example .env
```

Fill in `PAYPAL_CLIENT_ID` and `PAYPAL_CLIENT_SECRET`. For Claude, set `ANTHROPIC_API_KEY`. Without a key, EchoCart uses its built-in offline parser (see [Configuration](#configuration)).

### 3. Run (1 min)

```bash
docker compose up --build
```

Open **<http://localhost:8000> in Chrome** (or Edge). Speech recognition needs a Chromium browser; other browsers get the typing box and spoken replies.

### 4. First purchase

1. Press **Space** and say *"two packs of AA batteries"*.
2. Say *"yes"*. The first time, EchoCart asks you to link PayPal: press **Enter** to open the PayPal window, log in with the sandbox **Personal** account, and approve.
3. EchoCart says "PayPal is linked" and reads the cart again. Say *"yes"* to pay.
4. Say *"refund my last order"*, then *"yes"*.

### Without Docker

Python 3.12:

```bash
python -m venv .venv
```

```bash
.venv/bin/pip install -r requirements.txt
```

```bash
.venv/bin/uvicorn backend.app:app --port 8000
```

(On Windows, use `.venv\Scripts\` instead of `.venv/bin/`.)

### Sandbox test accounts for judges

> **To do before submitting:** add a sandbox *Personal* buyer login here, or tell judges to use one from their own sandbox. Sandbox credentials move no real money, but never put live credentials or your Anthropic key in this repository.

---

## Configuration

All settings come from environment variables or `.env`.

| Variable | Default | Meaning |
|---|---|---|
| `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET` | empty | Sandbox app credentials. Leave them empty and EchoCart runs with payments turned off. |
| `PAYPAL_API_BASE` | `https://api-m.sandbox.paypal.com` | Must be the sandbox URL, or the app won't start. |
| `EUR_PER_RON` | `0.201` | Fixed demo exchange rate. |
| `LLM_MODE` | `anthropic` | `anthropic` uses Claude. `offline` uses a deterministic keyword parser (no key needed). Without `ANTHROPIC_API_KEY`, EchoCart falls back to offline mode. |
| `ANTHROPIC_API_KEY` | empty | Claude API key. |
| `ANTHROPIC_MODEL` | `claude-opus-5-5` | Model name. |
| `ANTHROPIC_EFFORT` | `low` | Lower effort means faster replies, which matters in a spoken conversation. |
| `APP_BASE_URL` | `http://localhost:8000` | Where PayPal sends the popup back to. |
| `DATA_DIR` | `./data` | Holds `store.json` (linked account and orders) and `payments.jsonl` (audit log). |

## What you can say

| Say | What happens |
|---|---|
| "two packs of AA batteries under 50 lei" | Builds a cart within your budget and reads it back. |
| "double a batteries", "baterii AA", "hârtie igienică" | Misheard words and Romanian product names work too. |
| "remove the batteries", "make it three", "cheaper option", "add some sponges" | Changes the cart and reads it back. You need to say yes again. |
| "yes", "yes, pay", "confirm" | Pays, but only straight after a read-back. |
| "no" | Doesn't pay. The cart stays. |
| "repeat", "read my cart" | Says the last thing again, or reads the cart back. |
| "cancel" | Empties the cart. |
| "refund my last order", "refund just the batteries" | Reads back what will be refunded and the amount, then asks for a yes. Partial refunds go through PayPal with an amount. |
| "same as last time", "order it again" | Rebuilds your last order with today's prices and reads it back. |
| "where's my order?", "what did I order?" | Delivery date of your last order; your last three orders. |
| "how much is coffee?", "what tea do you have?" | Prices and options. The cart doesn't change. |
| "set my spending limit to 100 lei", "what's my limit?", "remove my spending limit" | EchoCart won't pay for an order above the limit. Lowering it is immediate; raising or removing it needs a yes. It can also be set in Settings. |
| "link PayPal", "unlink my PayPal" | Starts the one-time account link, or deletes the saved account after a yes. |
| "help" | Lists these commands. |

Keyboard: **Space** starts or stops listening, and **Esc** stops EchoCart talking. Every reply also appears in the page's live regions and in the conversation log.

---

## Accessibility

The target is **WCAG 2.2 AA**.

- **One primary control:** a large push-to-talk button labelled "Talk", also triggered by Space. Escape stops speech. No other control is needed to shop.
- **Live regions:** everything EchoCart says is also written to a `role="status"` (polite) region. Errors and payment results go to a `role="alert"` (assertive) region. The text is cleared and rewritten, so a repeated message is announced again.
- **"Let my screen reader speak":** turns off EchoCart's own voice so it doesn't talk over NVDA or VoiceOver. A speech-rate slider and a recognition language setting (English or Romanian) sit alongside it.
- **No timeouts:** nothing expires while you think. "Repeat" works at every step.
- **Earcons:** short tones for "listening started", "listening stopped", payment or refund success, and errors. They're made with Web Audio, so there are no audio files to load.
- **The PayPal window opens on a key press**, because browsers block popups that aren't triggered by the user. EchoCart moves focus to the button and says "press Enter". The page you return to from PayPal announces the result and closes itself.
- **Layout and contrast:** visible 4px focus outlines, colour tokens checked for at least 4.5:1 text contrast in light and dark mode, single-column layout with no fixed widths (works at 200% zoom and 375px wide), and reduced motion respected.
- **Typing is always available** for browsers without speech recognition, noisy rooms, or shoppers who prefer typing.

### Testing log

Be honest here: only list what was actually tested.

| Date | Setup | What was tested | Result |
|---|---|---|---|
| 2026-11-06 | Chromium (automated), offline parser, PayPal not configured | Page load, focus on Talk, live-region text, typed purchase / edit / yes, 375px width, light and dark mode, no horizontal scroll, no console errors | Pass |
| _to do_ | NVDA 2026.x + Chrome, Windows 11 | Full voice purchase with the screen off, edit, refund, PayPal link window, "Let my screen reader speak" | |
| _to do_ | VoiceOver + Safari, macOS | Typing fallback (Safari has no Web Speech recognition), live regions, PayPal window | |
| _to do_ | VoiceOver + Chrome, macOS | Full voice purchase, Space / Esc, earcons | |
| _to do_ | Chrome at 200% zoom | Reflow, focus visibility | |

---

## Tests

```bash
.venv/bin/python -m unittest discover -s tests -t .
```

The suite has 71 tests:

- **Cart pricing and speech:** `test_cart.py`
- **Safety rules** (`test_safety.py`): no capture without a fresh yes, a cart change voids the yes, refunds need their own yes, and the LLM output checks.
- **30 spoken requests** (`tests/fixtures/utterances.json`) run against the offline parser, covering budgets, "double a", Romanian names, an over-budget case, out of stock, unclear requests and edits. `test_llm.py` also covers the Claude client against a mocked API.
- **PayPal against a mocked API** (`test_paypal_mock.py`): the vault flow, the audit log, and the sandbox-only guard.
- **Whole conversations** (`test_dialog.py`): link → buy → receipt → refund, plus the popup fallback, run through the HTTP API.
- **The newer features** (`test_features.py`): spending limit (including that a limit yes can't pay), reorder, order status and history, partial refunds with exact remainders, unlink, and product questions.

**Manual end-to-end check against the real sandbox:** run this once with your `.env` filled in. It links a sandbox buyer, pays 1.00 EUR with no approval page, and refunds it.

```bash
.venv/bin/python -m tests.manual.vault_spike
```

## Project layout

```
backend/   app.py (API + PayPal return pages), dialog.py (conversation), safety.py, cart.py,
           llm.py (Claude + offline parser), paypal_client.py, store.py, config.py,
           catalog.json (41 products), prompts/cart_system.md
frontend/  index.html, app.js, styles.css, sounds/earcons.js
tests/     unit tests, fixtures/utterances.json, manual/vault_spike.py
```

## Known limitations

- **Single shopper per server.** Conversations are kept in memory, and the linked account and order history sit in one JSON file. That suits a demo, not production.
- **Fixed exchange rate**, because PayPal REST doesn't support RON.
- **No fraud-prevention data yet.** PayPal recommends sending fraud-prevention ("risk") data with payments that use a saved account; EchoCart doesn't include PayPal's FraudNet script yet.
- **Speech recognition depends on the browser.** Chrome sends audio to Google's speech service; Safari doesn't support recognition at all, so the typing box is used.
- **The product names and brands in the catalogue are invented.**

## Licence

[MIT](LICENSE)
