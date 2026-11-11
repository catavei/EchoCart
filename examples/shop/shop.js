/* The demo shop: a category page built from the shop's own product feed. "Shop by voice" opens
   EchoCart (added with embed.js) beside the products; EchoCart sends each cart and receipt back, so
   the basket and the order confirmation follow the conversation. Add to basket asks EchoCart to
   add the product, by its variant id in the feed, to the same cart. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const toggle = $("voice-toggle"), toggleLabel = $("voice-toggle-label"), panel = $("voice-panel"), slot = $("voice-slot");
  const basketBox = $("basket-box"), basket = $("basket"), basketSummary = $("basket-summary"), basketCount = $("basket-count");
  const order = $("order"), orderText = $("order-text"), orderLines = $("order-lines"), orderRefund = $("order-refund");
  const productList = $("products"), pageTitle = $("page-title"), crumb = $("crumb-current"), productsCount = $("products-count");
  const search = $("search"), categoryList = $("categories"), brandFilters = $("brand-filters"), sort = $("sort");
  const eur = (v) => `€${Number(v).toFixed(2)}`;
  const el = (tag, cls, text) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  };
  let products = [], category = "all", lastCart = null;

  // ---- packshots: a drawn pack for each kind of product ------------------
  const COLOURS = ["#1e5a43", "#2c5f9e", "#8a2a5b", "#1f6f78", "#5b3f8a", "#b9771b"];
  function colourFor(p) {
    if (/honey|lemon/.test(p.key)) return "#c99a12";
    if (/blackcurrant/.test(p.key)) return "#6b2350";
    let h = 0;
    for (const c of p.key) h = (h * 31 + c.charCodeAt(0)) >>> 0;
    return COLOURS[h % COLOURS.length];
  }
  function shapeFor(p) {
    const t = `${p.key} ${p.name}`.toLowerCase();
    if (/spray/.test(t)) return "spray";
    if (/thermometer/.test(t)) return "thermometer";
    if (/sanitiser|gel/.test(t)) return "pump";
    if (/cream|spf/.test(t)) return "tube";
    if (/toothbrush/.test(t)) return "brush";
    if (/vitamin/.test(t)) return "tub";
    return "box";
  }
  const safe = (s) => String(s || "").replace(/[<&>"]/g, "");
  function label(p, x, y, w) {
    return `<text x="${x}" y="${y}" fill="#fff" font-size="9" font-weight="700" text-anchor="middle" font-family="Atkinson Hyperlegible, sans-serif">${safe(p.brand)}</text>` +
           `<text x="${x}" y="${y + 13}" fill="#fff" font-size="11" text-anchor="middle" font-family="Atkinson Hyperlegible, sans-serif" textLength="${w}" lengthAdjust="spacingAndGlyphs">${safe(p.name.split(" ").slice(0, 2).join(" "))}</text>`;
  }
  function packshot(p) {
    const c = colourFor(p), s = shapeFor(p);
    const shapes = {
      box: `<path d="M38 34h84l10 10v84H48l-10-10z" fill="#000" opacity=".1"/>
            <rect x="34" y="30" width="84" height="88" rx="4" fill="#fff" stroke="${c}" stroke-width="2"/>
            <rect x="34" y="62" width="84" height="34" fill="${c}"/>${label(p, 76, 76, 64)}
            <rect x="44" y="40" width="22" height="6" rx="3" fill="${c}" opacity=".5"/>
            ${p.pack_size ? `<text x="108" y="112" font-size="8" text-anchor="end" fill="${c}" font-weight="700" font-family="Atkinson Hyperlegible, sans-serif">${safe(p.pack_size)}</text>` : ""}`,
      tub: `<rect x="46" y="34" width="60" height="16" rx="4" fill="${c}"/>
            <rect x="42" y="48" width="68" height="74" rx="10" fill="#fff" stroke="${c}" stroke-width="2"/>
            <rect x="42" y="70" width="68" height="32" fill="${c}"/>${label(p, 76, 82, 54)}`,
      spray: `<rect x="68" y="22" width="16" height="22" rx="4" fill="#d8dee2"/><rect x="62" y="40" width="28" height="10" rx="3" fill="${c}"/>
              <rect x="54" y="48" width="44" height="76" rx="14" fill="#fff" stroke="${c}" stroke-width="2"/>
              <rect x="54" y="72" width="44" height="32" fill="${c}"/>${label(p, 76, 84, 36)}`,
      pump: `<path d="M76 24h18v8H82v12h-12V32" fill="${c}"/><rect x="64" y="44" width="24" height="10" rx="2" fill="${c}"/>
             <rect x="52" y="52" width="48" height="72" rx="12" fill="#e9f4f8" stroke="${c}" stroke-width="2"/>
             <rect x="52" y="76" width="48" height="30" fill="${c}"/>${label(p, 76, 87, 40)}`,
      tube: `<path d="M48 30h56l-8 78H56z" fill="#fff" stroke="${c}" stroke-width="2"/><rect x="62" y="108" width="28" height="16" rx="3" fill="${c}"/>
             <path d="M51 56h50l-3 30H54z" fill="${c}"/>${label(p, 76, 68, 40)}`,
      thermometer: `<rect x="70" y="20" width="12" height="70" rx="6" fill="#e3e8ea" stroke="#9aa6ab"/>
                    <rect x="58" y="80" width="36" height="46" rx="12" fill="#fff" stroke="${c}" stroke-width="2"/>
                    <rect x="64" y="88" width="24" height="12" rx="2" fill="#cfe6d8"/><text x="76" y="97.5" font-size="8" text-anchor="middle" fill="#1e3a2c" font-family="monospace">36.8</text>
                    <circle cx="76" cy="113" r="5" fill="${c}"/>`,
      brush: `<rect x="72" y="44" width="10" height="84" rx="5" fill="${c}"/><rect x="70" y="20" width="14" height="28" rx="4" fill="#fff" stroke="${c}" stroke-width="2"/>
              <path d="M73 24v20M77 24v20M81 24v20" stroke="#9ec3ee" stroke-width="2"/>
              <rect x="94" y="44" width="10" height="84" rx="5" fill="#9ec3ee"/><rect x="92" y="20" width="14" height="28" rx="4" fill="#fff" stroke="#9ec3ee" stroke-width="2"/>`,
    };
    return `<svg class="packshot" viewBox="22 14 112 118" aria-hidden="true" focusable="false">${shapes[s]}</svg>`;
  }

  // ---- the shop's product feed: one card per variant (pack size) -----------
  const slug = (s) => String(s || "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
  function fromFeed(feed) {
    return (feed.products || []).flatMap((item) => {
      const variants = item.variants || [];
      return variants.map((v) => {
        const pack = /^default title$/i.test(v.title || "") ? "" : String(v.title || "");
        const handle = slug(item.handle || item.title);
        return {
          id: String(v.id), key: variants.length === 1 ? handle : `${handle}-${slug(pack)}`,
          name: item.title, brand: item.vendor || "", pack_size: pack, price_eur: v.price,
          category: item.product_type || "Other", in_stock: v.available !== false,
        };
      });
    });
  }

  // ---- category page: categories, filters, sort ----------------------------
  const categoryName = (c) => c === "all" ? "All products" : c;
  const fullName = (p) => p.pack_size ? `${p.name}, ${p.pack_size}` : p.name;

  function selectedBrands() {
    return [...brandFilters.querySelectorAll("input:checked")].map((i) => i.value);
  }
  function priceRange() {
    const v = document.querySelector('input[name="price"]:checked').value;
    return v === "any" ? [0, Infinity] : v.split("-").map(Number);
  }

  function renderProducts() {
    const q = search.value.trim().toLowerCase(), brands = selectedBrands(), [lo, hi] = priceRange();
    let shown = products.filter((p) => (category === "all" || p.category === category) &&
      (!brands.length || brands.includes(p.brand)) &&
      Number(p.price_eur) >= lo && Number(p.price_eur) < hi &&
      (!q || `${p.name} ${p.brand} ${p.pack_size} ${p.category}`.toLowerCase().includes(q)));
    const by = sort.value;
    if (by === "price-asc") shown = [...shown].sort((a, b) => a.price_eur - b.price_eur);
    if (by === "price-desc") shown = [...shown].sort((a, b) => b.price_eur - a.price_eur);
    if (by === "name") shown = [...shown].sort((a, b) => a.name.localeCompare(b.name));
    pageTitle.textContent = crumb.textContent = q ? `Results for “${search.value.trim()}”` : categoryName(category);
    productsCount.textContent = `${shown.length} ${shown.length === 1 ? "product" : "products"}`;
    if (!shown.length) {
      productList.replaceChildren(el("li", "empty-results", "No products match these filters. Clear a filter, or ask EchoCart."));
      return;
    }
    productList.replaceChildren(...shown.map((p) => {
      const li = el("li", "product");
      const pic = el("div", "product-pic");
      pic.innerHTML = packshot(p);
      li.append(pic, el("p", "product-brand", p.brand), el("h3", "", fullName(p)), el("p", "price", eur(p.price_eur)));
      if (p.in_stock) {
        const add = el("button", "add", "Add to basket");
        add.type = "button";
        add.setAttribute("aria-label", `Add ${fullName(p)} to basket`);
        add.addEventListener("click", () => addToBasket(p));
        li.append(add);
      } else {
        li.append(el("p", "sold-out", "Out of stock"));
      }
      return li;
    }));
  }

  function renderCategories() {
    const cats = ["all", ...new Set(products.map((p) => p.category))];
    categoryList.replaceChildren(...cats.map((c) => {
      const li = el("li");
      const b = el("button", "", categoryName(c));
      b.type = "button";
      b.setAttribute("aria-pressed", String(c === category));
      b.addEventListener("click", () => {
        category = c;
        search.value = "";
        for (const other of categoryList.querySelectorAll("button")) other.setAttribute("aria-pressed", String(other === b));
        renderProducts();
      });
      li.append(b);
      return li;
    }));
    const counts = new Map();
    for (const p of products) counts.set(p.brand, (counts.get(p.brand) || 0) + 1);
    brandFilters.replaceChildren(...[...counts].sort().map(([brand, n]) => {
      const label = el("label");
      const box = el("input");
      box.type = "checkbox";
      box.value = brand;
      box.addEventListener("change", renderProducts);
      label.append(box, ` ${brand} (${n})`);
      return label;
    }));
  }

  fetch("../linden-street-pharmacy.json").then((r) => r.json()).then((feed) => {
    products = fromFeed(feed);
    renderCategories();
    renderProducts();
  }).catch(() => { productList.replaceChildren(el("li", "empty-results", "The products didn't load. Reload the page.")); });

  search.addEventListener("input", renderProducts);
  sort.addEventListener("change", renderProducts);
  for (const r of document.querySelectorAll('input[name="price"]')) r.addEventListener("change", renderProducts);
  $("search-form").addEventListener("submit", (e) => { e.preventDefault(); renderProducts(); $("results").focus(); });

  // ---- the basket and the order, as the conversation changes them ----------
  function renderBasket(cart) {
    const before = new Map(((lastCart && lastCart.lines) || []).map((l) => [l.product_id, l]));
    if (!cart || !cart.lines.length) {
      basketBox.hidden = true;
      basketCount.hidden = true;
      basketSummary.textContent = ", empty";
      lastCart = cart;
      return;
    }
    const count = cart.lines.reduce((n, l) => n + l.quantity, 0);
    basketCount.textContent = String(count);
    basketCount.hidden = false;
    basketSummary.textContent = `, ${count} ${count === 1 ? "item" : "items"}, ${eur(cart.total_eur)}`;
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
    const total = el("p", "basket-total" + (totalChanged ? " changed" : ""));
    total.append(el("span", "", "Total"), el("span", "", eur(cart.total_eur)));
    const delivery = new Date(cart.delivery_date + "T12:00:00")
      .toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" });
    basket.replaceChildren(ul, total, el("p", "muted basket-note", `Delivery ${delivery}. To pay, say yes to EchoCart.`));
    basketBox.hidden = false;
    lastCart = cart;
  }

  function showOrder(receipt) {
    const delivery = new Date(receipt.delivery_date + "T12:00:00")
      .toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" });
    orderText.textContent = `Order ${receipt.order_id}. Paid ${eur(receipt.total_eur)} with PayPal. Arrives ${delivery}.`;
    orderLines.replaceChildren(...((lastCart && lastCart.lines) || []).map(
      (l) => el("li", "", `${l.name} × ${l.quantity}`)));
    orderRefund.hidden = true;
    shownOrder = receipt.order_id;
    order.hidden = false;
  }

  // A refund of the order on show: say how much went back.
  let shownOrder = null;
  function showRefund(refund) {
    if (refund.order_id !== shownOrder) return;
    orderRefund.textContent = `Refunded ${eur(refund.amount_eur)} to PayPal` +
      (refund.refunded_eur !== refund.amount_eur ? ` (${eur(refund.refunded_eur)} in all).` : ".");
    orderRefund.hidden = false;
  }

  // ---- EchoCart in the panel (embed.js) --------------------------------------
  let voice = null, gotOrder = false;
  function openVoice() {
    if (voice) return voice.focus();
    if (!window.EchoCart) {   // embed.js didn't load: EchoCart isn't running at that address
      slot.replaceChildren(el("p", "muted", "Voice shopping isn't available right now. Try again later."));
      return;
    }
    voice = window.EchoCart.mount(slot, {   // EchoCart starts when first opened
      onOrder(receipt) { showOrder(receipt); gotOrder = true; },
      onRefund: showRefund,
      onCart(cart) {
        if (cart && !gotOrder) order.hidden = true;   // a new basket replaces the last confirmation
        gotOrder = false;
        renderBasket(cart);
      },
    });
  }
  // Add to basket fills the same EchoCart cart: EchoCart reads it back, and paying still needs a yes.
  function addToBasket(p) {
    if (panel.hidden) setPanel(true);
    if (voice) voice.add(p.id, fullName(p));
  }

  function setPanel(open) {
    panel.hidden = !open;
    document.body.classList.toggle("voice-open", open);
    toggle.setAttribute("aria-expanded", String(open));
    toggleLabel.textContent = open ? "Close voice shopping" : "Shop by voice";
    if (open) openVoice();
  }
  toggle.addEventListener("click", () => setPanel(panel.hidden));
  $("welcome-voice").addEventListener("click", () => setPanel(true));
})();
