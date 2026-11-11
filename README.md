# EchoCart

**Voice-first shopping and PayPal checkout for blind and low-vision shoppers.**

You press one big button (or Space) and say *"two packs of AA batteries under 10 euros"*. EchoCart reads the cart back with the exact total and delivery date. You say *"yes"*, and it pays with PayPal and reads you the receipt. Changes, repeats and refunds all work by voice, and you never have to find a button on a checkout page.

Built for the [PayPal AI Hackathon](https://paypalaihackathon.devpost.com/). It runs on the **PayPal sandbox only**, so no real money moves.

---

## Why it matters

Online checkout assumes you can see the page. Screen readers help, but checkouts are long and full of popups, changing totals and pages that redirect without warning. Most voice shopping assistants still hand you to a visual payment page at the most important moment.

EchoCart is designed around three things a blind shopper needs to trust a payment:

1. **Hearing exactly what you're paying for** just before you pay: the items, quantities, total and delivery date, in plain speech.
2. **A yes that means yes.** Money moves only after a yes given right after that read-back. Say it any way you like ("yeah, go ahead", "sure, pay"), but "yes, but…" or "yeah, wait" isn't a yes.
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
- **Currency:** the shop prices everything in **euros**, the currency PayPal charges, so the total you hear is exactly the amount PayPal takes: *"Total 9 euros 18 cents."*
- **Fallback:** if the sandbox app can't save PayPal accounts (Vault not enabled), EchoCart opens PayPal's standard approval page in a popup for each payment instead, and says what to do at each step.

### Safety rules, enforced in code

| Rule | Where |
|---|---|
| Money moves only after a yes said right after the backend spoke the exact total. Any everyday yes works ("yeah, go ahead", "sure, pay now", "ok"), but the whole reply must be agreement: one other word ("but", "wait", "later", a product) and it isn't a yes, so EchoCart asks again. "Pay" only agrees to a payment and "refund" only to a refund | `safety.is_confirmation`, `safety.ConfirmationGate`, `dialog.Conversation._say` |
| If the cart changes, or anything else is said, the yes no longer counts; a new read-back and a new yes are required | `ConfirmationGate.consume` checks the cart fingerprint; every non-read-back reply disarms it |
| Refunds, unlinking PayPal, and raising or removing the spending limit each need their own read-back and yes. A yes given for one can't approve another | `ConfirmationGate` kinds `payment` / `refund` / `unlink` / `limit` |
| An order over the spending limit is never offered for a yes, and the payment step checks the limit again | `dialog.Conversation._propose`, `_pay` |
| Optional PIN: when set, a valid yes still needs a **typed** PIN (never spoken) for payments over a chosen amount, raising or removing the limit, and unlinking PayPal. Escape, Cancel or saying anything else cancels that step; the PIN alone never pays. Three wrong tries lock these for 10 minutes. Stored only as a salted scrypt hash | `pin.py`, `Store.check_pin`, `Conversation._confirmed` / `submit_pin`; the PIN has its own endpoint and never enters the conversation or logs |
| If PayPal doesn't answer, EchoCart says it can't tell yet (not "nothing was charged"), and paying the same cart again reuses the same `PayPal-Request-Id`, so it can't charge twice | `PayPalClient._send`, `Conversation.unanswered_payment` |
| LLM output that fails the schema, names an unknown product id, or goes over budget is never shown | `safety.validate_llm_output` |
| Prices come from `catalog.json`, never from the LLM | `cart.price_cart` |
| Every create, capture and refund is logged with a timestamp and order id | `data/payments.jsonl` (`paypal_client.PaymentLog`) |
| The app refuses to start unless the PayPal URL is the sandbox one | `config.Settings.assert_sandbox`, plus a second check in `PayPalClient` |

### Built with

- **PayPal developer platform, sandbox only:** Vault v3 (setup tokens, payment tokens, delete), Orders v2 (create and capture with the saved account), Payments v2 (full and partial refunds), standard PayPal checkout as the fallback, and OAuth 2.0 client credentials.
- **AI: Anthropic Claude** through the Messages API (Claude Opus 5.5 by default, set with `ANTHROPIC_MODEL`), with JSON-schema output: it turns what the shopper says into product ids and quantities, never prices. A built-in offline parser does the same job with no API key; the hosted demo and the demo video use it.
- **Render (sponsor):** hosts the [live demo](https://echocart-46wx.onrender.com) from [`render.yaml`](render.yaml). See [Hosting it on Render](#hosting-it-on-render).
- **Backend:** Python 3.12, FastAPI, httpx, pydantic, uvicorn. Docker Compose to run it.
- **Frontend:** plain HTML, CSS and JavaScript; the Web Speech API for recognition and speech; the Web Audio API for the earcons; ARIA live regions. `embed.js` for shop websites.
- **Demo video:** recorded automatically from the live app in Chromium (through the DevTools protocol), against the real PayPal sandbox. The voices are by **ElevenLabs**; the app itself doesn't use ElevenLabs.
- **Development:** built with help from **Claude Code** (Anthropic).

---

## Setup (about 5 minutes)

### 1. PayPal sandbox app (2 min)

1. Sign in at <https://developer.paypal.com/> and open **Apps & Credentials**, making sure the **Sandbox** toggle is on.
2. Create an app (or use the default one) and copy its **Client ID** and **Secret**.
3. On the app's page, scroll to **Features > Payment capabilities**, tick **Save payment methods** ("Use the Vault API to save your customer's payment methods"), and click **Save changes** at the bottom. Without it, EchoCart still works, but you approve each payment in a popup. (Older dashboards call this **App Feature Options > Advanced options > Vault**.)
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

### Try it without installing anything (for judges)

**Hosted demo: <https://echocart-46wx.onrender.com>** (PayPal sandbox; no real money moves). Open it in Chrome or Edge.

To pay, you link PayPal once with a PayPal sandbox **personal** (buyer) account. Any sandbox personal account works: at [developer.paypal.com](https://developer.paypal.com/), open **Testing Tools > Sandbox Accounts** and use the Personal account's email and password when the PayPal window asks you to log in. Everything up to the payment works without one.

Then follow [First purchase](#4-first-purchase) from step 1. If speech recognition isn't available (Safari, Firefox) or the room is noisy, use the **Type instead** box: it works exactly like speaking.

- **Your own shopper.** Each browser gets its own linked PayPal account, orders, spending limit and PIN, so several people can try the demo at the same time. Reloading the page keeps them; a private window starts afresh.
- **The first visit can take about a minute** while the free server wakes up. A restart of the server also forgets the linked accounts, so you may be asked to link PayPal again.
- **Running it yourself** works too, with the setup above and your own sandbox app.

#### Hosting it on Render

[`render.yaml`](render.yaml) describes the service. In the Render dashboard choose **New > Blueprint**, pick this repository, and fill in `PAYPAL_CLIENT_ID` and `PAYPAL_CLIENT_SECRET`. The hosted demo runs the offline parser, so it needs no Claude key. To use Claude there, add `ANTHROPIC_API_KEY` in the service's **Environment** tab and set `LLM_MODE` to `anthropic`; give the key a spend limit first, because anyone with the link can talk to the demo. Render sets the public address itself (`RENDER_EXTERNAL_URL`), so PayPal returns to the right page with no extra setting.

---

## Configuration

All settings come from environment variables or `.env`.

| Variable | Default | Meaning |
|---|---|---|
| `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET` | empty | Sandbox app credentials. Leave them empty and EchoCart runs with payments turned off. |
| `PAYPAL_API_BASE` | `https://api-m.sandbox.paypal.com` | Must be the sandbox URL, or the app won't start. |
| `LLM_MODE` | `anthropic` | `anthropic` uses Claude. `offline` uses a deterministic keyword parser (no key needed). Without `ANTHROPIC_API_KEY`, EchoCart falls back to offline mode. |
| `ANTHROPIC_API_KEY` | empty | Claude API key. |
| `ANTHROPIC_MODEL` | `claude-opus-5-5` | Model name. |
| `ANTHROPIC_EFFORT` | `low` | Lower effort means faster replies, which matters in a spoken conversation. |
| `APP_BASE_URL` | `http://localhost:8000` | Where PayPal sends the popup back to. |
| `CATALOG_URL` | empty | A merchant's product feed, as a URL or a file path. Empty means the demo catalogue. See [Use a merchant's own catalogue](#use-a-merchants-own-catalogue). |
| `SHOP_ORIGINS` | empty | The shop websites allowed to show EchoCart with `embed.js`, comma-separated, like `https://shop.example`. Empty: only EchoCart's own pages. See [Add EchoCart to a shop's website](#add-echocart-to-a-shops-website). |
| `DATA_DIR` | `./data` | Holds `store.json` (linked account and orders) and `payments.jsonl` (audit log). |
| `SHOPPERS` | `single` | `single`: one shopper per server, in `store.json`. `per_browser`: each browser gets its own shopper (linked PayPal, orders, limit, PIN) in `DATA_DIR/shoppers/`, for a hosted demo. |

## Use a merchant's own catalogue

EchoCart is meant to be the voice checkout a shop adds for its blind and low-vision customers. By default it sells from its demo catalogue (`backend/catalog.json`). To sell a merchant's products instead, point `CATALOG_URL` at the shop's product feed, and use that merchant's own PayPal sandbox app credentials:

```bash
CATALOG_URL=https://shop.example/products.json
# or a file, relative to the repository:
CATALOG_URL=examples/linden-street-pharmacy.json
```

The feed is the Shopify-style `products.json` shape (Shopify storefronts serve it at `/products.json`). [`examples/linden-street-pharmacy.json`](examples/linden-street-pharmacy.json) is a small sample pharmacy. With it, *"two packs of ibuprofen"* is read back as *"Linden Health Ibuprofen 200 mg tablets, 16 tablets, quantity two. Total 4 euros 98 cents."*

| Feed field | Becomes |
|---|---|
| each entry in `variants` | one product the shopper can buy |
| `title`, `vendor` | the spoken name and brand |
| variant `title` | the pack size ("16 tablets"); Shopify's "Default Title" is left out |
| variant `price` | the price, read as euros. A feed with a `currency` other than EUR is refused, and variants without a valid price are skipped |
| `inventory_quantity`, else `available` | stock (an unavailable variant can't be bought) |
| `product_type`, `tags` | the category and the words the shopper may use; a `delivery:2` tag sets the delivery time in days (default 1) |

Everything else in the feed (descriptions, images, dates) is ignored. The feed is read once at startup and holds up to 500 products, because the whole catalogue goes into Claude's prompt. The safety rules don't change: prices still come only from the feed, never from the model, and the voice checkout, PIN, limit and refunds work the same way. Payments go to the PayPal account whose credentials the app runs with, so each merchant runs EchoCart with their own PayPal app.

### Add EchoCart to a shop's website

A shop adds EchoCart to its own pages with a script tag, the way it would add PayPal's buttons or a chat widget. EchoCart keeps running on its own address:

```html
<script src="https://YOUR-ECHOCART/embed.js"></script>
<script>
  // for example when the shopper presses "Shop by voice"
  const voice = EchoCart.mount(document.getElementById("voice"), {
    onCart(cart) { /* show the basket: lines, total, delivery date; null when empty */ },
    onOrder(receipt) { /* show the order: PayPal order id, total, delivery date */ },
    onRefund(refund) { /* after a refund: order id, amount refunded, refunded so far */ },
  });
  // optional: Add to basket buttons fill the same cart, by the variant id in the shop's feed
  voice.add("4400101");
</script>
```

- **Only the shop's own sites.** List them in `SHOP_ORIGINS` (for example `https://shop.example`). EchoCart tells the browser, with a `frame-ancestors` header, that only those sites may show it in a frame, so no other site can wrap the checkout. Messages go only to that shop's page and are taken only from it.
- **EchoCart still runs the checkout.** It prices the cart, reads it back and waits for the spoken yes. Add to basket (`POST /api/cart/add`) ends in the same read-back, so it never pays. The shop's page only shows the basket and the order.
- **The shop's own products.** EchoCart reads the same feed the shop publishes (`CATALOG_URL`), so the variant ids on the shop's buttons are ones EchoCart knows.

#### The demo shop

[`examples/shop/`](examples/shop/) is a pharmacy website made for the demo, laid out like a real one: search, categories, brand and price filters, sorting, and an **Add to basket** button on each product. It builds its pages from its own feed, [`examples/linden-street-pharmacy.json`](examples/linden-street-pharmacy.json), and adds EchoCart with `embed.js`. The shop and EchoCart run on two different addresses, as they would for a real shop:

```bash
# the shop's website, on port 8012
python -m http.server 8012 --bind 127.0.0.1 --directory examples
# EchoCart, on port 8011, reading the shop's feed and allowing the shop's site
CATALOG_URL=http://localhost:8012/linden-street-pharmacy.json SHOP_ORIGINS=http://localhost:8012 APP_BASE_URL=http://localhost:8011 uvicorn backend.app:app --port 8011
# then open http://localhost:8012/shop/
```

**Shop by voice** opens EchoCart in a panel beside the products, with the keyboard focus on its Talk button. As you speak, the shop's own basket changes (new and changed lines and the total are highlighted), an order confirmation appears once PayPal has the money, and a refund shows on that order. The shop's page links to `http://localhost:8011/embed.js`; for another address, change that line.

## What you can say

| Say | What happens |
|---|---|
| "two packs of AA batteries under 10 euros" | Builds a cart within your budget and reads it back. |
| "double a batteries", "baterii AA", "hârtie igienică" | Misheard words and Romanian product names work too. |
| "remove the batteries", "make it three", "cheaper option", "add some sponges" | Changes the cart and reads it back. You need to say yes again. |
| "yes", "yeah, go ahead", "sure, pay" | Pays, but only straight after a read-back. "Yeah, wait" or "yes, I think so" gets the question again. |
| "no" | Doesn't pay. The cart stays. |
| "repeat", "read my cart" | Says the last thing again, or reads the cart back. |
| "cancel" | Empties the cart. |
| "refund my last order", "refund just the batteries" | Reads back what will be refunded and the amount, then asks for a yes. Partial refunds go through PayPal with an amount. |
| "same as last time", "order it again" | Rebuilds your last order with today's prices and reads it back. |
| "where's my order?", "what did I order?" | Delivery date of your last order; your last three orders. |
| "how much is coffee?", "what tea do you have?" | Prices and options. The cart doesn't change. |
| "set my spending limit to 20 euros", "what's my limit?", "remove my spending limit" | EchoCart won't pay for an order above the limit. Lowering it is immediate; raising or removing it needs a yes. It can also be set in Settings. |
| "link PayPal", "unlink my PayPal" | Starts the one-time account link, or deletes the saved account after a yes. |
| "set a PIN" | Moves you to the PIN settings. For privacy the PIN is typed (4 to 8 digits), never spoken. Choose every payment or only payments over an amount. |
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
- **A PIN you type, not say:** speaking a PIN aloud lets anyone nearby hear it. When the PIN is needed, a popup opens with the keyboard focus already in a masked numeric field (number keypad on phones). EchoCart says "Type your PIN and press Enter"; Enter confirms and Escape or Cancel closes the popup and cancels the step aloud. The popup blocks the rest of the page, so focus can't wander off while money waits. The digits never appear in the conversation log, and "One moment." is spoken if PayPal is slow.

### Testing log

Be honest here: only list what was actually tested.

| Date | Setup | What was tested | Result |
|---|---|---|---|
| 2026-11-06 | Chromium (automated), offline parser, PayPal not configured | Page load, focus on Talk, live-region text, typed purchase / edit / yes, 375px width, light and dark mode, no horizontal scroll, no console errors | Pass |
| 2026-11-09 | Real PayPal sandbox (Vault enabled), `tests/manual/vault_spike.py` | Buyer approves the setup token once → payment token → order with `vault_id` returned COMPLETED (no approval page, no separate capture) → full refund COMPLETED | Pass |
| 2026-11-09 | Full app against the real PayPal sandbox, offline parser, typed input | Link PayPal (in Chrome; a browser that blocks pop-ups gets a spoken "allow pop-ups" message), the demo script's purchase with the edit and the refused "yeah", payment with no PayPal page, order status, partial refund of one line (PayPal shows the capture as PARTIALLY_REFUNDED), order history. The audit log matches PayPal | Pass |
| 2026-11-09 | Chromium (built-in browser), real keyboard input | Set a PIN in Settings; raise the limit, then yes, then the PIN field gets focus; a wrong PIN shows "2 tries left" with focus kept; the right PIN applies the change and focus returns to Talk; no PIN digits in the conversation log; PIN settings at 375px with no horizontal scroll | Pass |
| 2026-11-09 | Chromium (built-in browser), real keyboard input | PIN popup: after the yes it opens with focus in the PIN field; a wrong PIN keeps it open with "2 tries left"; Escape and Cancel each close it, EchoCart says "Okay, I won't pay" and focus returns to Talk | Pass |
| _to do_ | NVDA 2026.x + Chrome, Windows 11 | Full voice purchase with the screen off, edit, refund, PayPal link window, "Let my screen reader speak" | |
| _to do_ | VoiceOver + Safari, macOS | Typing fallback (Safari has no Web Speech recognition), live regions, PayPal window | |
| _to do_ | VoiceOver + Chrome, macOS | Full voice purchase, Space / Esc, earcons | |
| _to do_ | Chrome at 200% zoom | Reflow, focus visibility | |

---

## Tests

```bash
.venv/bin/python -m unittest discover -s tests -t .
```

The suite has 130 tests:

- **Cart pricing and speech:** `test_cart.py`
- **Safety rules** (`test_safety.py`): the everyday ways to say yes, and the replies that only sound like one; no capture without a fresh yes, a cart change voids the yes, refunds need their own yes, and the LLM output checks.
- **30 spoken requests** (`tests/fixtures/utterances.json`) run against the offline parser, covering budgets, "double a", Romanian names, an over-budget case, out of stock, unclear requests and edits. `test_llm.py` also covers the Claude client against a mocked API.
- **PayPal against a mocked API** (`test_paypal_mock.py`): the vault flow, the audit log, and the sandbox-only guard.
- **Whole conversations** (`test_dialog.py`): link → buy → receipt → refund (and the refund the shop's page is told about), plus the popup fallback, run through the HTTP API.
- **The newer features** (`test_features.py`): spending limit (including that a limit yes can't pay), reorder, order status and history, partial refunds with exact remainders, unlink, and product questions.
- **PayPal not answering** (`test_paypal_mock.py`, `test_dialog.py`): retries reuse the same request id, the reply never claims nothing was charged, and a second yes can't create a second order.
- **Merchant product feeds** (`test_feed.py`): the Shopify-style feed becomes products with the right names, prices, stock and delivery; unusable feeds are refused; loading by URL or file; and a whole voice purchase from the sample pharmacy feed.
- **Shop websites** (`test_shop.py`): `embed.js` is served; only the sites in `SHOP_ORIGINS` may show EchoCart in a frame, and malformed addresses are refused at startup; the demo shop is its own site; Add to basket, by EchoCart's id or the shop's variant id, adds to the same cart and reads it back, waiting for a yes; out-of-stock and unknown products are refused.
- **The hosted demo** (`test_hosted.py`): each browser gets its own shopper and keeps it after a reload or restart, made-up ids get a fresh shopper, and the local single-shopper mode is unchanged.
- **The PIN** (`test_pin.py`): hashing, lockout and expiry, yes then PIN, the PIN alone never pays, anything said cancels the PIN step, the amount threshold, limit and unlink need it, refunds don't, changing it needs the current PIN, and the PIN never appears in replies or the store file.

**Manual end-to-end check against the real sandbox:** run this once with your `.env` filled in. It links a sandbox buyer, pays 1.00 EUR with no approval page, and refunds it.

```bash
.venv/bin/python -m tests.manual.vault_spike
```

Last run on 2026-11-09: the vaulted order came back `COMPLETED` straight from create, and the refund was `COMPLETED`.

## Project layout

```
backend/   app.py (API + PayPal return pages), dialog.py (conversation), safety.py, pin.py, cart.py,
           llm.py (Claude + offline parser), paypal_client.py, store.py, config.py, feed.py,
           catalog.json (41 products), prompts/cart_system.md
frontend/  index.html, app.js, styles.css, sounds/earcons.js; embed.js (for shop websites)
examples/  linden-street-pharmacy.json (a sample merchant product feed)
tests/     unit tests, fixtures/utterances.json, manual/vault_spike.py
```

## Known limitations

- **No accounts.** Locally there's one shopper per server. With `SHOPPERS=per_browser` each browser is its own shopper, identified by a random id it keeps; clearing the browser's data loses it. Shoppers are JSON files, which suits a demo, not production.
- **No fraud-prevention data yet.** PayPal recommends sending fraud-prevention ("risk") data with payments that use a saved account; EchoCart doesn't include PayPal's FraudNet script yet.
- **Speech recognition depends on the browser.** Chrome sends audio to Google's speech service; Safari doesn't support recognition at all, so the typing box is used.
- **The product names and brands in the catalogue are invented**, and so is the sample pharmacy feed.
- **One merchant per server.** EchoCart pays the PayPal account it runs with, so each shop runs its own instance. Shops that don't take PayPal, such as Amazon, aren't supported.
- **The first PIN can be set by anyone at the device**, as there's no account login in this single-shopper demo. Changing or removing it needs the current PIN. Passkeys (Windows Hello, Face ID) would be the stronger next step.

## Licence

[MIT](LICENSE)
