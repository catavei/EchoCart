# EchoCart: demo video script

**Target length: 2:45** (Devpost: "less than three (3) minutes"; judges may stop watching at 3:00).
Upload to YouTube as **Public**. Use English only, and no copyrighted music. The earcons and speech are the soundtrack.

The video has to show three things: **one full purchase by voice with the screen off**, **one edit**, and **one refund**.

---

## Before you record

- [ ] Fill `.env` with the sandbox app credentials (Vault ticked) and `ANTHROPIC_API_KEY`. Record with Claude, not the offline parser.
- [ ] Run `docker compose up`, open Chrome at <http://localhost:8000>, and allow the microphone.
- [ ] **Link PayPal before recording** (say "link PayPal" and approve once) so the main take has no popup. Keep a 10-second clip of the link window to use in scene 4.
- [ ] Delete old orders: stop the app, remove `data/store.json` (this unlinks PayPal too, so link again), and start the app.
- [ ] Turn on **NVDA's Screen Curtain** (`NVDA+Ctrl+Esc`) for scene 2, or turn the monitor off and film the room. Either way the screen really is off.
- [ ] Record the screen and the system audio together, plus a phone camera on your hands and keyboard for scene 2.
- [ ] Burn in captions of everything spoken. Viewers watching on mute need them, and so do Deaf judges.
- [ ] Keep the PayPal sandbox dashboard open in another tab (Activity) to show the real transactions in scene 4.

---

## Scene 1 (0:00–0:15): cold open

*Black screen. Captions only.*

> **[Space pressed. Rising earcon.]**
> **You:** "Two packs of AA batteries under 50 lei."
> **EchoCart:** "Your cart: VoltMax AA alkaline batteries, 4 pack, quantity two. Total 45 lei 80 bani, charged as 9 euros 21 cents..."

Cut out mid-sentence.

**Title card (2 s):** *EchoCart: checkout you can hear.*

## Scene 2 (0:15–0:35): the problem

*Narration over a quick montage of a normal checkout page: tiny buttons, a popup, a redirect.*

> "Checkout pages assume you can see them. With a screen reader they're long, they change under you, and the payment step usually sends you to another site to find an Approve button. EchoCart is a shopping assistant for blind and low-vision shoppers where the whole checkout happens by voice, and you always hear exactly what you're paying for before you pay."

## Scene 3 (0:35–1:35): full purchase with the screen off, including an edit

*Screen Curtain on. Show the hands and keyboard on camera. Captions show every spoken line.*

> **[Space. Rising earcon.]**
> **You:** "I need double A batteries and some dish soap."
> **EchoCart:** "Your cart: VoltMax AA alkaline batteries, 4 pack, quantity one; Casa Clean Dishwashing liquid, lemon, 500 ml, quantity one. Total 31 lei 80 bani, charged as 6 euros 39 cents. Delivery tomorrow, Saturday… Say yes to pay, or tell me what to change."

**The edit:**

> **[Space.]** **You:** "Make the batteries two packs."
> **EchoCart:** "Updated. Your cart: … quantity two … Total 54 lei 70 bani, charged as 10 euros 99 cents. … Say yes to pay, or tell me what to change."

**Show the safety rule (keep this short):**

> **You:** "Yeah, go ahead."
> **EchoCart:** "I need a clear yes before paying. Your cart: … Total 54 lei 70 bani…"
> **You:** "Yes."
> **[Three-note success chime.]**
> **EchoCart:** "Payment done. Order number ending 7, 1, 5, T. Charged 10 euros 99 cents, that's 54 lei 70 bani. Delivery tomorrow, Saturday 7 November."

*Narration (5 s), screen still black:*

> "No screen, no PayPal page, no mouse. And a 'yeah' isn't a yes: money only moves on a clear yes, said right after it hears the exact total."

The actual totals depend on what Claude picks. Re-record until the edit reads naturally, and update the captions to match.

## Scene 4 (1:35–2:05): how it works

*Screen Curtain off. Split screen with quick cuts.*

1. (8 s) The one-time **Link PayPal** window: *"You link PayPal once, in a popup EchoCart talks you through. PayPal saves the account, and later payments need no approval page."*
2. (8 s) Claude's JSON next to the spoken cart: *"Claude turns speech into product ids and quantities, nothing else. The backend checks every id against the catalogue, enforces your budget, and works out every price itself."*
3. (8 s) The **PayPal sandbox dashboard** showing the captured payment: *"PayPal Vault, Orders v2 to create and capture the payment, Payments v2 for refunds. Every payment action is logged."*
4. (6 s) **Spending limit:** type 40 in Settings, then ask for something that costs more. *"Set a spending limit, and EchoCart won't pay above it. Raising it needs a yes."*

## Scene 5 (2:05–2:30): refund by voice

*Screen Curtain back on, or just the page.*

> **[Space.]** **You:** "Refund just the batteries."
> **EchoCart:** "Refund VoltMax AA alkaline batteries, 4 pack, quantity 2, from your last order, number ending 7, 1, 5, T. 9 euros 21 cents back to your PayPal, that's 45 lei 80 bani. The rest of the order stays. Say yes to refund, or no to keep it."
> **You:** "Yes."
> **[Success chime.]** **EchoCart:** "Refund done. 9 euros 21 cents will go back to your PayPal account."

Cut to the sandbox dashboard showing the partial refund (2 s). If there's time, add:

> **You:** "Same as last time." **EchoCart:** "Your last order again. Your cart: …"

*(Narration:* "Partial refunds, reorders, order status, all by voice.")

## Scene 6 (2:30–2:45): built for screen readers, and close

*Quick cuts, about 3 s each:*

- Tick **"Let my screen reader speak"**, and NVDA reads the same reply instead of EchoCart's voice.
- Press **Escape**, and the speech stops instantly.
- Type "cheaper option" into the text box for browsers without speech recognition.
- The page at 200% zoom.

> "EchoCart: one button, a spoken total, a real yes. Built on PayPal and Claude, for shoppers who've been sent to a page they can't see."

**End card:** repo URL · "PayPal sandbox demo. No real money moved."

---

## Shot list and timing check

| Scene | Ends at | Must show |
|---|---|---|
| 1. Cold open | 0:15 | Voice request + read-back |
| 2. Problem | 0:35 | Who it's for, why checkout fails them |
| 3. Screen-off purchase | 1:35 | **Full purchase by voice, screen off**, **one edit**, the clear-yes rule |
| 4. How it works | 2:05 | PayPal Vault + Orders + Payments, Claude JSON, backend pricing, spending limit |
| 5. Refund | 2:30 | **Partial refund by voice** + dashboard (+ "same as last time" if time allows) |
| 6. Accessibility + close | 2:45 | Screen-reader mode, Esc, typing fallback, zoom |
