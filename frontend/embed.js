/* EchoCart for shop websites.

   1. Add EchoCart to the page, from wherever EchoCart runs:
        <script src="https://echocart.example/embed.js"></script>
   2. Open it in any element, for example when the shopper presses "Shop by voice":
        const voice = EchoCart.mount(document.getElementById("voice"), {
          onCart(cart) { ... },         // after every reply: the cart, or null when it is empty
          onOrder(receipt) { ... },     // after a payment: order id, total, delivery date
          onRefund(refund) { ... },     // after a refund: order id, this refund, refunded so far
        });
   3. Optional: your Add to basket buttons can fill the same cart, by the variant id in your feed:
        voice.add("4400101", "Ibuprofen 200 mg, 16 tablets");
      EchoCart reads the cart back, so paying still needs the shopper's spoken yes.

   EchoCart opens only on the websites in its SHOP_ORIGINS setting. Prices, the cart and the
   payment stay with EchoCart; the shop page only shows them. */
(() => {
  "use strict";
  if (window.EchoCart) return;
  const ECHOCART = new URL(document.currentScript.src).origin;

  function mount(container, { onCart = () => {}, onOrder = () => {}, onRefund = () => {} } = {}) {
    const frame = document.createElement("iframe");
    frame.title = "EchoCart voice checkout";
    frame.allow = "microphone";
    frame.className = "echocart-frame";
    frame.src = `${ECHOCART}/?embed=1&shop=${encodeURIComponent(location.origin)}`;

    let ready = false;
    const waiting = [];
    const flush = () => {
      while (ready && waiting.length) frame.contentWindow.postMessage(waiting.shift(), ECHOCART);
    };
    window.addEventListener("message", (event) => {
      if (event.origin !== ECHOCART || event.source !== frame.contentWindow) return;
      const m = event.data;
      if (!m || m.type !== "echocart:update") return;
      if (m.receipt) onOrder(m.receipt);
      if (m.refund) onRefund(m.refund);
      onCart(m.cart || null);
      if (!ready) { ready = true; flush(); }   // EchoCart has started its conversation
    });
    // Once EchoCart has loaded, the keyboard focus goes into it, onto its Talk button.
    frame.addEventListener("load", () => frame.focus());
    container.append(frame);

    return {
      frame,
      focus: () => frame.focus(),
      add(variantId, name) {
        waiting.push({ type: "echocart:add", product_id: String(variantId), name: String(name || "") });
        flush();
      },
    };
  }

  window.EchoCart = Object.freeze({ mount });
})();
