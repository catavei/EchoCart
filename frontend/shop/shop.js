/* The demo shop: lists the products, opens EchoCart beside them, and keeps the basket and the
   order confirmation in step with the voice conversation (EchoCart posts each reply's cart and
   receipt to this page). */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const toggle = $("voice-toggle"), panel = $("voice-panel"), frame = $("voice-frame");
  const basket = $("basket"), order = $("order"), orderText = $("order-text"), orderLines = $("order-lines");
  const eur = (v) => `€${Number(v).toFixed(2)}`;
  const el = (tag, cls, text) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  };
  let lastCart = null;

  // ---- products, from the same catalogue EchoCart sells from --------------
  fetch("/api/catalog").then((r) => r.json()).then(({ shop_name, products }) => {
    $("shop-name").textContent = shop_name;
    document.title = `${shop_name} (demo shop)`;
    $("products").replaceChildren(...products.map((p) => {
      const li = el("li", "product");
      li.append(el("h3", "", p.name),
                el("p", "muted", [p.brand, p.pack_size].filter(Boolean).join(" · ")),
                el("p", "price", eur(p.price_eur)));
      if (!p.in_stock) li.append(el("p", "stock", "Out of stock"));
      return li;
    }));
  }).catch(() => { $("products").replaceChildren(el("li", "muted", "The products couldn't load. Reload the page.")); });

  // ---- the basket and the order, as the voice conversation changes them ----
  function renderBasket(cart) {
    const before = new Map(((lastCart && lastCart.lines) || []).map((l) => [l.product_id, l]));
    if (!cart || !cart.lines.length) {
      basket.replaceChildren(el("p", "muted", "Your basket is empty."));
      lastCart = cart;
      return;
    }
    const ul = el("ul", "basket-lines");
    for (const line of cart.lines) {
      const old = before.get(line.product_id);
      const status = !lastCart ? "" : !old ? "Added" : old.quantity !== line.quantity ? "Updated" : "";
      const li = el("li", status ? "changed" : "");
      const name = el("span", "line-name", line.name);
      if (status) name.append(el("span", "pill", status));
      li.append(name, el("span", "line-qty", `× ${line.quantity}`), el("span", "line-price", eur(line.subtotal_eur)));
      ul.append(li);
    }
    const totalChanged = lastCart && lastCart.total_eur !== cart.total_eur;
    const total = el("p", "basket-total" + (totalChanged ? " changed" : ""), `Total ${eur(cart.total_eur)}`);
    basket.replaceChildren(ul, total);
    lastCart = cart;
  }

  function showOrder(receipt) {
    const delivery = new Date(receipt.delivery_date + "T12:00:00")
      .toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" });
    orderText.textContent = `Order ${receipt.order_id}. Paid ${eur(receipt.total_eur)} with PayPal. Delivery ${delivery}.`;
    orderLines.replaceChildren(...((lastCart && lastCart.lines) || []).map(
      (l) => el("li", "", `${l.name} × ${l.quantity}`)));
    order.hidden = false;
  }

  window.addEventListener("message", (event) => {
    if (event.origin !== location.origin || event.source !== frame.contentWindow) return;
    const m = event.data;
    if (!m || m.type !== "echocart:update") return;
    if (m.receipt) showOrder(m.receipt);
    else if (m.cart) order.hidden = true;   // a new basket replaces the last confirmation
    renderBasket(m.cart);
  });

  // ---- the voice panel ------------------------------------------------------
  toggle.addEventListener("click", () => {
    const open = panel.hidden;
    panel.hidden = !open;
    toggle.setAttribute("aria-expanded", String(open));
    toggle.textContent = open ? "Close voice shopping" : "Shop by voice";
    if (!open) return;
    if (!frame.getAttribute("src")) frame.src = "/?embed=1";   // EchoCart starts when first opened
    else frame.focus();
  });
  // When EchoCart has loaded, the focus goes into it, onto its Talk button.
  frame.addEventListener("load", () => frame.focus());
})();
