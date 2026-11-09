# EchoCart: Devpost write-up (draft)

> Draft for the PayPal AI Hackathon submission. Lines marked **[EDIT]** need your own words or facts before you submit.

**Tagline:** Checkout you can hear: voice-first shopping and PayPal payments for blind and low-vision shoppers.

**Elevator pitch:** Most shopping agents are built to act *for* you. EchoCart is built so you can *trust* what it does. It reads back exactly what you'll pay, and PayPal is only charged after a clear "yes" to that total. Blind shoppers can buy, change, reorder and refund by voice, with the screen off, from one button.

### What makes it different

| Typical voice shopping agent | EchoCart |
|---|---|
| Fills the cart, then sends you to a visual payment page | Links PayPal once (PayPal Vault); every later checkout is voice-only |
| Trusts the AI's numbers | The AI only picks products; every price, total and refund amount comes from the backend |
| Any "ok" can trigger a purchase | Money moves only on "yes", "yes, pay" or "confirm", straight after the exact total is spoken, and each yes works once |
| No guard against mistakes | A per-order spending limit the shopper sets by voice; raising it needs a yes |
| Anyone in the room can say "yes" | An optional PIN, typed and never spoken, after the yes; 3 wrong tries lock payments |
| Refunds mean calling support | "Refund just the batteries" gives a partial refund through PayPal Payments v2, spoken back first |
| Accessibility added at the end | Designed with screen readers first: live regions, earcons and vibration, a "let my screen reader speak" mode, no timeouts |

---

## Inspiration

**[EDIT: in your own voice]** I've built literacy resources for visually impaired teenagers, and I watched how much of the web assumes you can see it. Online checkout is one of the worst cases. It's long, full of popups and totals that change, and at the most important moment, paying, it usually sends you to another page to find an "Approve" button.

Voice assistants can fill a cart, but most of them still hand you to a visual payment page at the end, or they pay without telling you exactly what you're paying for. We wanted a checkout that a blind shopper could actually trust: you hear exactly what you're buying and what it costs, and money moves only when you clearly say yes.

## What it does

- **Press one button (or Space) and speak:** *"two packs of AA batteries under 10 euros."*
- **EchoCart reads the cart back:** items, quantities, the total in euros, delivery date. Then it asks: *"Say yes to pay, or tell me what to change."*
- **Change anything by voice:** "remove the batteries", "make it three", "cheaper option", "add some sponges". Each change is read back again and needs a new yes.
- **Say "yes" and it pays with PayPal**, then speaks the receipt: order number, amount charged, delivery date. A success chime confirms it.
- **"Refund my last order"**, or **"refund just the batteries"**, reads back what will be refunded and the amount, asks for a yes, refunds through PayPal (fully or partly) and confirms aloud.
- **"Same as last time"** rebuilds the last order with today's prices and stock, then reads it back. Ordering your usual essentials takes one sentence and one yes.
- **"Where's my order?"** and **"what did I order?"** read back delivery dates and recent orders.
- **"How much is coffee?"** and **"what tea do you have?"** answer without touching the cart.
- **"Set my spending limit to 20 euros"** stops EchoCart paying for any order above it. Lowering the limit takes effect at once; raising or removing it needs a yes.
- **"Unlink my PayPal"** deletes the saved PayPal account through PayPal's Vault API, after a yes.
- **An optional PIN** protects against someone else in the room saying "yes". After your yes, EchoCart asks you to *type* the PIN, because a spoken PIN can be overheard. You choose whether it's every payment or only those over an amount. It's also needed to raise the spending limit or unlink PayPal.
- **"Repeat", "help" and "cancel"** work at every step, with no timeouts.

You link your PayPal account **once**, in a popup that EchoCart talks you through. After that, checkout is entirely by voice.

**For merchants:** EchoCart is the voice checkout a shop adds for its blind and low-vision customers. Point it at the shop's product feed (the Shopify-style `products.json`) and the shop's own PayPal app, and the same voice checkout sells that shop's products. The repo includes a sample pharmacy feed.

## How we built it

**PayPal (sandbox):**
- **Vault v3 (payment method tokens).** A setup token is approved once by the shopper in a PayPal popup, then exchanged for a saved payment token. This is what removes the visual approval step from every later purchase.
- **Orders v2.** Each purchase is created with `payment_source.paypal.vault_id` and `intent: CAPTURE`. If PayPal returns the order as approved but not yet captured, EchoCart captures it explicitly.
- **Payments v2.** `POST /v2/payments/captures/{id}/refund` for voice refunds, both full and partial (with an amount). For partial refunds, EchoCart works out each line's amount and refunds the exact remainder last, so rounding never adds up to more than was charged.
- **Vault v3 delete.** `DELETE /v3/vault/payment-tokens/{id}` when the shopper says "unlink my PayPal".
- **Fallback.** If a sandbox app doesn't have Vault enabled, EchoCart switches to PayPal's standard approval page in a popup and narrates each step. Judges can run it either way.
- **Currency.** The shop prices in euros, the currency PayPal charges, so the spoken total is exactly what PayPal takes. There's no conversion step to explain or get wrong.
- **Audit log.** Every create, capture and refund is logged with a timestamp and order id.
- **Sandbox only.** The app refuses to start unless the API base URL is PayPal's sandbox.

**Merchant catalogues:**
- **`CATALOG_URL`** loads a merchant's product feed in the Shopify-style `products.json` shape, from a URL or a file. Each variant becomes a product; prices are read as euros, unavailable variants can't be bought, and a feed in another currency is refused.
- **The safety rules don't change.** Prices still come only from the feed, and payments go to the merchant's own PayPal account.

**AI (Claude):**
- **What Claude does.** It turns the shopper's words into structured JSON through a JSON-schema response: an action (`propose_cart`, `edit_cart`, `clarify`, `refund_request`, ...), product ids and quantities, the stated budget, and a clarifying question if the request is ambiguous.
- **Speech-friendly understanding.** It handles misheard words ("double a" → AA) and Romanian product names ("hârtie igienică").
- **What Claude never does: set a price.** The backend rejects any reply that breaks the schema, names a product not in the catalogue, exceeds stock, or goes over the budget the shopper stated. EchoCart then asks a short question instead.
- **Prices come from the backend.** It computes every price, total and delivery date from the catalogue.
- **Claude's own words are spoken only for clarifying questions.** Carts, totals, receipts and refunds are worded by the backend.
- **Offline mode.** A deterministic keyword parser lets anyone run the app without an API key.

**Stack:**
- Python 3.12, FastAPI, httpx, pydantic, uvicorn.
- Plain HTML, CSS and JavaScript, with the Web Speech API for recognition and speech.
- Web Audio for the earcons.
- Docker Compose to run it.

## Safety: "yes" has to mean yes

The safety rules are enforced in code, not left to the AI's instructions:

- **A yes counts only straight after the backend has spoken the exact total**, and only "yes", "yes, pay" or "confirm" are accepted. "Yeah", "yes please", "ok" and everything else count as no; EchoCart reads the cart again and asks for a clear yes.
- **Each yes is single-use and tied to a fingerprint of the cart that was read back.** If the cart changes, or anything else is said in between, the yes no longer counts.
- **Refunds, unlinking PayPal, and raising or removing the spending limit** each have their own read-back and yes. A yes given for one can't authorise another.
- **The spending limit is checked twice:** an over-limit cart is never offered for a yes, and the payment step checks again.
- **A second order can't be created** while one is waiting for approval in the fallback popup.
- **The optional PIN is a second step, never a shortcut.** It only completes a valid yes. Saying anything else cancels it, and three wrong tries lock payments and account changes for 10 minutes. It's stored as a salted scrypt hash and goes to its own endpoint, so it never reaches the conversation, the speech engine or the logs.
- **When PayPal doesn't answer**, EchoCart doesn't pretend nothing happened. It says it can't tell yet, and paying the same cart again reuses the same PayPal request id, so PayPal returns the first order instead of charging twice. We found this case in a real sandbox run, where one create took over 30 seconds.

These rules are covered by 115 automated tests, including 30 recorded-style utterances: budgets, misheard words, Romanian names, an over-budget case, out of stock, and edits.

## Accessibility choices

- **One primary control:** a large, labelled Talk button, also triggered by Space. Escape stops speech.
- **Live regions:** everything spoken is also written to an `aria-live` region, polite for updates and assertive for errors and payment results. Screen readers and braille displays get the same information as the voice.
- **"Let my screen reader speak":** turns off EchoCart's own voice so it doesn't talk over NVDA or VoiceOver. There's also an adjustable speech rate and a choice of recognition language (English or Romanian).
- **Earcons and vibration:** short tones for listening started, listening stopped, success and error, so you know what's happening without words. On phones they come with matching vibration patterns, for shoppers who are also hard of hearing.
- **The PayPal window opens on a key press.** Browsers block popups that aren't triggered by the user, so EchoCart says "press Enter" and moves focus to the right button. The page you return to announces the result and closes itself.
- **WCAG 2.2 AA as the floor:**
  - full keyboard operation
  - visible focus
  - at least 4.5:1 contrast in light and dark mode
  - works at 200% zoom and on a phone-width screen
  - no timeouts
- **A typing box is always there**, for browsers without speech recognition (like Safari) and for noisy rooms.
- **Screen-reader testing:** **[EDIT: fill in from the README testing log after you test with NVDA and VoiceOver. Don't claim anything you didn't test.]**

## Challenges we ran into

- **The approval page problem.** Standard PayPal checkout sends the buyer to a login and approval page, which breaks a voice-only flow. We checked PayPal's documentation for saving a PayPal account (vaulting) for returning buyers, and confirmed the one-time setup flow and the server-side order with `vault_id`. Then we built a fallback for when Vault isn't available.
- **Popups and accessibility.** Browsers block a popup opened by a voice command, because it isn't a user gesture. The fix: EchoCart says "press Enter" and moves focus to the button, so the key press opens the window.
- **Trust versus convenience.** It's tempting to accept "yeah" or "sure". We chose a strict rule, and made the error message re-read the total so a strict yes is never confusing.
- **One currency, said once.** We first priced in Romanian lei and charged in euros, because PayPal REST doesn't support lei. Hearing every total twice made each read-back longer and harder to follow, so the shop now prices in euros only.
- **Proving the vault path on the real sandbox before building on it.** A standalone script links a sandbox buyer, pays 1.00 EUR with the saved account and refunds it. The vaulted order came back `COMPLETED` directly from create, with no approval page and no separate capture call. **[EDIT: add anything else that came up.]**

## Accomplishments that we're proud of

- **A complete purchase, edit and refund with the screen off.**
- **Safety rules that are tested code, not prompt text.**
- **The AI can't invent a price**, because all money is computed by the backend from the catalogue.
- **It runs with one command**, and works without an AI key thanks to the offline parser.

## What we learned

**[EDIT]** For example:
- Designing for speech means designing the *sentences*: the order of information, money said the way people say it ("9 euros 18 cents"), and when to stay quiet.
- `aria-live` regions need care. A repeated message has to be cleared and rewritten, or screen readers stay silent.

## What's next

- **Testing with blind and low-vision shoppers**, and changing the wording and pacing based on what they tell us.
- **More shop platforms:** WooCommerce and Google Merchant Center feeds next to Shopify's, live stock updates instead of reading the feed once, and delivery slots.
- **Marketplaces without PayPal checkout, such as Amazon,** would need a browser extension that reads the page and only presses the real checkout button after a spoken yes. We left that out of this demo because it can't run on the sandbox.
- **Fraud-prevention data and production vetting** for saved PayPal accounts.
- **Real delivery tracking** through PayPal's shipment tracking API, replacing the delivery estimate.
- **Passkeys** (Windows Hello, Face ID, fingerprint) as an alternative to the PIN: nothing to remember, nothing to overhear.
- **A trusted-contact option:** an optional second confirmation by a family member above a set amount.
- **A phone-call channel**, so shoppers can use it without a browser at all.

## Built with

`python` · `fastapi` · `pydantic` · `httpx` · `paypal-orders-v2` · `paypal-payments-v2` · `paypal-vault` · `claude` · `anthropic-api` · `web-speech-api` · `web-audio-api` · `aria-live` · `docker`

## Try it

- **Repo:** https://github.com/catavei/EchoCart (MIT licence)
- **Video:** **[EDIT: YouTube URL]**
- **Setup:** see the README. A PayPal sandbox app plus `docker compose up` takes about 5 minutes. All payments are sandbox; no real money moves.
