// EchoCart voice UI. Plain JS, no framework.
// One primary control (Talk / Space). Everything spoken is mirrored into live regions.
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const talkBtn = $("talk"), talkLabel = $("talk-label");
  const said = $("said"), saidUrgent = $("said-urgent");
  const linkBtn = $("link-paypal"), approveBtn = $("open-approval");
  const form = $("type-form"), typed = $("typed");
  const cartEl = $("cart"), logEl = $("log");
  const srMode = $("sr-mode"), rate = $("rate"), rateValue = $("rate-value"), lang = $("lang");

  let sessionId = null;
  let sessionReady = Promise.resolve();
  let listening = false;
  let busy = false;
  let approveUrl = null;
  let lastReply = null;

  // ---- settings (per-browser convenience only) ------------------------
  const prefs = { load(k, d) { try { const v = localStorage.getItem("echocart." + k); return v === null ? d : v; } catch (e) { return d; } },
                  save(k, v) { try { localStorage.setItem("echocart." + k, v); } catch (e) { /* private mode */ } } };
  srMode.checked = prefs.load("srMode", "false") === "true";
  rate.value = prefs.load("rate", "1");
  lang.value = prefs.load("lang", "en-US");
  const showRate = () => { rateValue.textContent = Number(rate.value).toFixed(1) + "×"; };
  showRate();
  srMode.addEventListener("change", () => { prefs.save("srMode", srMode.checked); if (srMode.checked) speechSynthesis.cancel(); });
  rate.addEventListener("input", () => { showRate(); prefs.save("rate", rate.value); });
  lang.addEventListener("change", () => { prefs.save("lang", lang.value); if (recognition) recognition.lang = lang.value; });

  // ---- output: live regions + optional TTS ------------------------------
  function announce(text, priority) {
    const region = priority === "assertive" ? saidUrgent : said;
    const other = priority === "assertive" ? said : saidUrgent;
    other.textContent = "";
    // Clear first so identical text (e.g. "repeat") is announced again.
    region.textContent = "";
    setTimeout(() => { region.textContent = text; }, 60);
  }

  function speak(text) {
    if (srMode.checked || !("speechSynthesis" in window)) return;
    speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text);
    u.rate = Number(rate.value);
    u.lang = "en-GB";
    speechSynthesis.speak(u);
  }

  function stopTalking() {
    if ("speechSynthesis" in window) speechSynthesis.cancel();
  }

  function addLog(who, text) {
    const li = document.createElement("li");
    const b = document.createElement("span");
    b.className = "who";
    b.textContent = who + ": ";
    li.append(b, document.createTextNode(text));
    logEl.append(li);
    while (logEl.children.length > 30) logEl.firstElementChild.remove();
  }

  function renderCart(cart) {
    cartEl.replaceChildren();
    if (!cart) {
      const p = document.createElement("p");
      p.textContent = "Your cart is empty.";
      cartEl.append(p);
      return;
    }
    const ul = document.createElement("ul");
    ul.className = "cart-lines";
    for (const line of cart.lines) {
      const li = document.createElement("li");
      li.textContent = `${line.name}. Quantity ${line.quantity}. ${line.subtotal_eur} euros`;
      ul.append(li);
    }
    const total = document.createElement("p");
    total.className = "cart-total";
    total.textContent = `Total ${cart.total_eur} euros.`;
    const delivery = document.createElement("p");
    delivery.textContent = "Delivery: " + new Date(cart.delivery_date + "T12:00:00")
      .toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" });
    cartEl.append(ul, total, delivery);
  }

  const limitInput = $("limit");
  function showLimit(limit) {
    if (document.activeElement !== limitInput) limitInput.value = limit ? String(Math.round(Number(limit))) : "";
  }

  // ---- PIN ---------------------------------------------------------------
  const pinDialog = $("pin-dialog"), pinForm = $("pin-form"), pinInput = $("pin"), pinSaid = $("pin-said");
  let pinClosedByReply = false;
  const pinSettings = $("pin-settings"), pinStatus = $("pin-status"), pinCurrentRow = $("pin-current-row");
  const pinCurrent = $("pin-current"), pinNew = $("pin-new"), pinNewLabel = $("pin-new-label");
  const pinOver = $("pin-over"), pinRemove = $("pin-remove");
  const PIN_RE = /^[0-9]{4,8}$/;

  function showPinSettings(reply) {
    if (!("pin_set" in reply)) return;
    const over = reply.pin_over_eur ? Math.round(Number(reply.pin_over_eur)) : 0;
    pinStatus.textContent = !reply.pin_set ? "No PIN set. Payments need only your spoken yes."
      : over === 0 ? "PIN on. EchoCart asks for it after your yes on every payment."
      : `PIN on. EchoCart asks for it after your yes on payments over ${over} euros.`;
    pinCurrentRow.hidden = !reply.pin_set;
    pinRemove.hidden = !reply.pin_set;
    pinNewLabel.textContent = reply.pin_set ? "New PIN, 4 to 8 digits (leave empty to keep your PIN)" : "New PIN, 4 to 8 digits";
    if (reply.pin_set && document.activeElement !== pinOver) pinOver.value = String(over);
  }

  function handleReply(reply) {
    lastReply = reply;
    renderCart(reply.cart);
    if ("spending_limit_eur" in reply) showLimit(reply.spending_limit_eur);
    showPinSettings(reply);
    addLog("EchoCart", reply.speech);
    announce(reply.speech, reply.priority);
    if (reply.earcon && window.Earcons) Earcons[reply.earcon]();
    speak(reply.speech);

    const action = reply.action && reply.action.type;
    const pinWasOpen = pinDialog.open;
    linkBtn.hidden = action !== "link";
    approveUrl = action === "approve" ? reply.action.url : null;
    approveBtn.hidden = !approveUrl;
    if (action === "pin") {
      // The PIN popup: what EchoCart just said, then the PIN field, with the keyboard focus in it.
      pinSaid.textContent = reply.speech;
      pinInput.value = "";
      if (!pinWasOpen) pinDialog.showModal();
      pinInput.focus();
    } else if (pinWasOpen) {
      pinClosedByReply = true;
      pinDialog.close();
    }
    if (action === "pin") return;
    if (!linkBtn.hidden) linkBtn.focus();
    else if (!approveBtn.hidden) approveBtn.focus();
    else if (action === "pin_settings") {
      const first = pinCurrentRow.hidden ? pinNew : pinCurrent;
      first.focus();
      first.scrollIntoView({ block: "center" });
    } else if (pinWasOpen) (talkBtn.disabled ? typed : talkBtn).focus();
  }

  // ---- talking to the backend -----------------------------------------
  async function post(path, body) {
    const res = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
    return res.json();
  }

  async function send(text) {
    text = text.trim();
    if (!text || busy) return;
    busy = true;
    addLog("You", text);
    // Silence while PayPal is slow is unsettling when you can't see a spinner.
    const slow = setTimeout(() => { announce("One moment.", "polite"); speak("One moment."); }, 1500);
    try {
      // Speaking right after the page loads must not race the session request.
      if (!sessionId) await sessionReady;
      handleReply(await post("/api/utterance", { session_id: sessionId, text }));
    } catch (e) {
      if (String(e.message).includes("Unknown session")) await startSession();
      handleReply({ speech: "Something went wrong talking to EchoCart. Please try again.", priority: "assertive", earcon: "error", cart: lastReply && lastReply.cart });
    } finally {
      clearTimeout(slow);
      busy = false;
    }
  }

  async function startSession() {
    const reply = await post("/api/session");
    sessionId = reply.session_id;
    // Browsers block speech before the first key press, so the welcome is shown and announced only.
    renderCart(reply.cart);
    showLimit(reply.spending_limit_eur);
    showPinSettings(reply);
    addLog("EchoCart", reply.speech);
    announce(reply.speech, "polite");
    lastReply = reply;
  }

  async function pullEvents() {
    if (!sessionId) return;
    try {
      const { events } = await post("/api/events", { session_id: sessionId });
      for (const reply of events) handleReply(reply);
      if (events.length && linkBtn.hidden && approveBtn.hidden) talkBtn.focus();
    } catch (e) { /* the next focus or message retries */ }
  }

  // ---- PayPal window ---------------------------------------------------
  const POPUP = "width=480,height=720";

  function popupBlocked() {
    handleReply({ speech: "Your browser blocked the PayPal window. Allow pop-ups for this site, then press the button again.", priority: "assertive", earcon: "error", cart: lastReply && lastReply.cart, action: lastReply && lastReply.action });
  }

  linkBtn.addEventListener("click", async () => {
    // Open the window during the click (a user gesture), then point it at PayPal.
    const w = window.open("", "echocart-paypal", POPUP);
    if (!w) return popupBlocked();
    try {
      const res = await post("/api/link/start", { session_id: sessionId });
      if (res.url) {
        w.location.href = res.url;
        linkBtn.hidden = true;
        handleReply({ speech: "PayPal window opened. Log in, then approve. I'll tell you when your account is linked.", priority: "polite", cart: lastReply && lastReply.cart });
      } else {
        w.close();
        handleReply(res); // vault not available: approve each payment instead
      }
    } catch (e) {
      w.close();
      handleReply({ speech: "PayPal couldn't start linking. Please try again in a moment.", priority: "assertive", earcon: "error", cart: lastReply && lastReply.cart, action: { type: "link" } });
    }
  });

  approveBtn.addEventListener("click", () => {
    const w = window.open(approveUrl, "echocart-paypal", POPUP);
    if (!w) return popupBlocked();
    approveBtn.hidden = true;
    handleReply({ speech: "PayPal window opened. Log in, then approve. I'll tell you when the payment is done.", priority: "polite", cart: lastReply && lastReply.cart });
  });

  window.addEventListener("message", (event) => {
    if (event.origin === location.origin && event.data === "echocart:paypal-done") pullEvents();
  });
  window.addEventListener("focus", pullEvents);

  // ---- speech recognition ----------------------------------------------
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  let recognition = null;

  function setListening(on) {
    listening = on;
    talkBtn.setAttribute("aria-pressed", String(on));
    talkLabel.textContent = on ? "Listening… press to stop" : "Talk";
  }

  if (Recognition) {
    recognition = new Recognition();
    recognition.lang = lang.value;
    recognition.interimResults = false;
    recognition.maxAlternatives = 1;
    recognition.continuous = false;
    recognition.onstart = () => { setListening(true); Earcons.listenStart(); };
    recognition.onend = () => { if (listening) Earcons.listenStop(); setListening(false); };
    recognition.onresult = (event) => send(event.results[0][0].transcript);
    recognition.onerror = (event) => {
      setListening(false);
      if (event.error === "no-speech") handleReply({ speech: "I didn't hear anything. Press Talk and try again.", priority: "polite", cart: lastReply && lastReply.cart });
      else if (event.error === "not-allowed" || event.error === "service-not-allowed")
        handleReply({ speech: "The microphone is blocked. Allow it in your browser, or type your request instead.", priority: "assertive", earcon: "error", cart: lastReply && lastReply.cart });
      else if (event.error !== "aborted")
        handleReply({ speech: "Voice input stopped working. You can type your request instead.", priority: "assertive", earcon: "error", cart: lastReply && lastReply.cart });
    };
  } else {
    talkBtn.disabled = true;
    talkLabel.textContent = "Voice input isn't available in this browser";
    $("talk-hint").textContent = "This browser can't recognise speech. Type your request below; EchoCart still reads its answers aloud.";
  }

  function toggleListening() {
    if (!recognition) { typed.focus(); return; }
    if (listening) { recognition.stop(); return; }
    stopTalking(); // barge in: never listen to ourselves
    try { recognition.start(); } catch (e) { /* already starting */ }
  }

  talkBtn.addEventListener("click", toggleListening);

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      stopTalking();
      if (listening && recognition) recognition.abort();
      return;
    }
    if (event.key !== " " || event.repeat) return;
    const t = event.target;
    // Space keeps its normal meaning in form fields and on other buttons.
    if (t === talkBtn) return; // the button's own click handles it
    if (t.closest && t.closest("input, textarea, select, button, a, [contenteditable]")) return;
    event.preventDefault();
    toggleListening();
  });

  // The limit form speaks to the same conversation, so raising/removing gets the same spoken yes.
  $("limit-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const value = Number(limitInput.value);
    if (!value || value < 1) {
      handleReply({ speech: "Enter a limit of at least 1 euro.", priority: "assertive", cart: lastReply && lastReply.cart });
      return;
    }
    send(`set my spending limit to ${value} euros`);
  });
  $("limit-remove").addEventListener("click", () => send("remove my spending limit"));

  async function postReply(path, body, logText) {
    if (busy) return;
    busy = true;
    if (logText) addLog("You", logText);
    try {
      handleReply(await post(path, { session_id: sessionId, ...body }));
    } catch (e) {
      handleReply({ speech: "Something went wrong talking to EchoCart. Please try again.", priority: "assertive", earcon: "error", cart: lastReply && lastReply.cart });
    } finally {
      busy = false;
    }
  }

  // The PIN goes to its own endpoint: never into speech recognition, the conversation or the log.
  pinForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const pin = pinInput.value;
    pinInput.value = "";
    if (!PIN_RE.test(pin)) {
      announce("A PIN is 4 to 8 digits. Type it again.", "assertive");
      speak("A PIN is 4 to 8 digits. Type it again.");
      pinInput.focus();
      return;
    }
    postReply("/api/pin", { pin }, "(PIN typed)");
  });

  // Escape or Cancel closes the popup, and the payment or change waiting for the PIN is cancelled aloud.
  $("pin-cancel").addEventListener("click", () => pinDialog.close());
  pinDialog.addEventListener("close", () => {
    pinInput.value = "";
    if (pinClosedByReply) { pinClosedByReply = false; return; }
    send("cancel");
  });

  pinSettings.addEventListener("submit", (event) => {
    event.preventDefault();
    const newPin = pinNew.value, current = pinCurrent.value;
    pinNew.value = pinCurrent.value = "";
    const over = Math.max(0, Math.round(Number(pinOver.value) || 0));
    if (newPin && !PIN_RE.test(newPin)) {
      handleReply({ speech: "A PIN must be 4 to 8 digits. Nothing was changed.", priority: "assertive", earcon: "error", cart: lastReply && lastReply.cart });
      pinNew.focus();
      return;
    }
    postReply("/api/pin/set", { new_pin: newPin || null, current_pin: current || null, over_eur: String(over) });
  });

  pinRemove.addEventListener("click", () => {
    const current = pinCurrent.value;
    pinCurrent.value = "";
    if (!current) {
      handleReply({ speech: "Type your current PIN first, then press Remove PIN.", priority: "assertive", cart: lastReply && lastReply.cart });
      pinCurrent.focus();
      return;
    }
    postReply("/api/pin/remove", { current_pin: current });
  });

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const text = typed.value;
    typed.value = "";
    send(text);
  });

  sessionReady = startSession();
  sessionReady.then(() => talkBtn.disabled || talkBtn.focus())
    .catch(() => announce("EchoCart can't reach its server. Check that it is running, then reload.", "assertive"));
})();
