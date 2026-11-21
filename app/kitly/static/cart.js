/*
 * KITLY — panier (source unique).
 *
 * Servi par l'API : GET /kitly/static/cart.js
 * Chargé par vitrine.js (vitrines), marketplace.js et checkout.js (page /checkout).
 *
 * Le panier vit dans le navigateur (localStorage du site Kitly : les embeds Framer sont
 * sur la même origine que la page, ils partagent donc le même panier). Il ne contient que
 * des identifiants et un aperçu pour l'affichage : prix, stock et disponibilité sont
 * toujours recalculés par le serveur (POST /kitly/cart/quote).
 *
 * API :
 *   KitlyCart.items()                 → [{offer_id, variant_id, quantity, delivery, source,
 *                                         slug, seller, title, image, price_cents, variant}]
 *   KitlyCart.add(item)               → ajoute (fusionne même offre + même option)
 *   KitlyCart.update(index, patch) · KitlyCart.remove(index) · KitlyCart.clear()
 *   KitlyCart.count()                 → nombre d'articles (quantités cumulées)
 *   KitlyCart.onChange(fn)            → appelé à chaque modification (aussi depuis un autre onglet)
 *   KitlyCart.mountButton({ checkoutUrl })  → bouton « Panier » flottant sur la page
 *   KitlyCart.checkoutUrl()           → adresse de la page de paiement (/checkout du site)
 *
 * Événement publié sur la page : « kitly:cart » (detail = { count, items }), pour un
 * éventuel icône de panier dans le header du site.
 */
(function (global) {
  "use strict";
  if (global.KitlyCart) return;

  var KEY = "kitly_cart_v1", MAX_LINES = 30, MAX_QTY = 10;
  var HOST = global;
  try { if (global.frameElement && global.parent.document) HOST = global.parent; } catch (e) {}
  var listeners = [];

  function read() {
    try {
      var v = JSON.parse(global.localStorage.getItem(KEY) || "[]");
      return Array.isArray(v) ? v.filter(function (x) { return x && x.offer_id; }) : [];
    } catch (e) { return []; }
  }
  function write(list) {
    try { global.localStorage.setItem(KEY, JSON.stringify(list.slice(0, MAX_LINES))); } catch (e) {}
    notify();
  }
  function count(list) { return (list || read()).reduce(function (n, x) { return n + (parseInt(x.quantity, 10) || 1); }, 0); }
  function notify() {
    var list = read(), detail = { count: count(list), items: list };
    listeners.forEach(function (fn) { try { fn(list); } catch (e) {} });
    try { HOST.dispatchEvent(new HOST.CustomEvent("kitly:cart", { detail: detail })); } catch (e) {}
    renderButton();
  }
  // Un autre onglet (ou un autre embed de la page) a modifié le panier.
  try { global.addEventListener("storage", function (e) { if (e.key === KEY) notify(); }); } catch (e) {}

  function sameLine(a, b) { return String(a.offer_id) === String(b.offer_id) && (a.variant_id || "") === (b.variant_id || ""); }

  function add(item) {
    var list = read(), qty = Math.max(1, Math.min(MAX_QTY, parseInt(item.quantity, 10) || 1));
    var line = list.filter(function (x) { return sameLine(x, item); })[0];
    if (line) {
      line.quantity = item.kind === "physical" || line.kind === "physical" ? Math.min(MAX_QTY, (parseInt(line.quantity, 10) || 1) + qty) : 1;
      if (item.source === "marketplace") line.source = "marketplace";
    } else {
      list.push({
        offer_id: item.offer_id, variant_id: item.variant_id || null, quantity: item.kind === "physical" ? qty : 1,
        delivery: item.delivery || null, source: item.source === "marketplace" ? "marketplace" : "direct",
        kind: item.kind || "", slug: item.slug || "", seller: item.seller || "", title: item.title || "",
        image: item.image || "", price_cents: item.price_cents || 0, variant: item.variant || "", added_at: Date.now()
      });
    }
    write(list);
    return list;
  }
  function update(index, patch) {
    var list = read();
    if (!list[index]) return list;
    Object.keys(patch || {}).forEach(function (k) { list[index][k] = patch[k]; });
    if (list[index].quantity != null) list[index].quantity = Math.max(1, Math.min(MAX_QTY, parseInt(list[index].quantity, 10) || 1));
    write(list);
    return list;
  }
  function remove(index) { var list = read(); list.splice(index, 1); write(list); return list; }
  function clear() { write([]); }

  function checkoutUrl() {
    try { return HOST.location.origin + "/checkout"; } catch (e) { return "/checkout"; }
  }

  // ── Bouton « Panier » flottant, posé sur la page elle-même (pas dans l'embed) ──
  var btnOpts = null;
  function renderButton() {
    if (!btnOpts) return;
    var doc;
    try { doc = HOST.document; } catch (e) { return; }
    var n = count(), el = doc.getElementById("kitly-cart-btn");
    var onCheckout = false;
    try { onCheckout = /\/checkout\/?$/.test(HOST.location.pathname); } catch (e) {}
    if (!n || onCheckout) { if (el) el.remove(); return; }
    if (!el) {
      el = doc.createElement("a");
      el.id = "kitly-cart-btn";
      el.setAttribute("aria-label", "Voir mon panier");
      el.style.cssText = "position:fixed;right:18px;bottom:18px;z-index:2147482000;display:flex;align-items:center;gap:10px;" +
        "background:#0a0a0a;color:#fff;text-decoration:none;border-radius:999px;padding:13px 20px 13px 16px;" +
        "font:700 15px Inter,-apple-system,'Segoe UI',Arial,sans-serif;box-shadow:0 14px 34px -12px rgba(0,0,0,.5);" +
        "transition:transform .15s";
      el.onmouseenter = function () { el.style.transform = "translateY(-2px)"; };
      el.onmouseleave = function () { el.style.transform = "none"; };
      doc.body.appendChild(el);
    }
    el.href = btnOpts.checkoutUrl || checkoutUrl();
    el.target = "_top";
    el.innerHTML = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 7h14l-1 13H6z"/><path d="M9 7a3 3 0 0 1 6 0"/></svg>' +
      '<span>Panier</span><span style="background:#fff;color:#0a0a0a;border-radius:99px;min-width:22px;height:22px;padding:0 6px;display:inline-flex;align-items:center;justify-content:center;font-size:12.5px">' + n + '</span>';
  }
  function mountButton(opts) { btnOpts = opts || {}; renderButton(); }

  global.KitlyCart = {
    items: read, add: add, update: update, remove: remove, clear: clear,
    count: function () { return count(); },
    onChange: function (fn) { listeners.push(fn); },
    mountButton: mountButton, checkoutUrl: checkoutUrl
  };
})(window);
