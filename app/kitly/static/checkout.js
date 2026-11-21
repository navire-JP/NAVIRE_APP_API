/*
 * KITLY — page de paiement du panier (source unique).
 *
 * Servi par l'API : GET /kitly/static/checkout.js — chargé par embeds/kitly/checkout.html
 * (page Framer /checkout). Utilise le panier partagé (static/cart.js).
 *
 * Étapes :
 *   1. Panier : articles groupés par vendeur, option, quantité, livraison, suppression
 *      (prix et disponibilités recalculés par le serveur : POST /kitly/cart/quote).
 *   2. Coordonnées, adresse de livraison (si un article est expédié), CGV de chaque vendeur.
 *   3. « Continuer vers le paiement » : POST /kitly/checkout → formulaire de carte Stripe
 *      intégré (Payment Element : carte, Apple Pay, Google Pay…, 3D Secure).
 *   4. Confirmation (le paiement n'est validé qu'après le webhook Stripe : la page suit
 *      GET /kitly/checkout/{ref}). Retour de 3D Secure : /checkout?ref=P…
 *
 * API : KitlyCheckout.mount(el, { apiBase, marketplaceUrl, privacyUrl, cgvUrl })
 * Tout texte vient des vendeurs : il est systématiquement échappé.
 */
(function (global) {
  "use strict";
  if (global.KitlyCheckout) return;

  var CSS = [
    ".kc{box-sizing:border-box;--ink:#0a0a0a;--paper:#fff;--grey:#f5f5f4;--line:#e4e4e2;--mut:#6f6f6c;--bad:#c2410c;--good:#15803d;--serif:'Instrument Serif',Georgia,serif;--sans:'Inter',-apple-system,'Segoe UI',Arial,sans-serif;font-family:var(--sans);color:var(--ink);-webkit-font-smoothing:antialiased;width:100%;max-width:1120px;margin:0 auto;padding:24px 16px 56px}",
    ".kc *,.kc *::before,.kc *::after{box-sizing:border-box}",
    ".kc button,.kc input,.kc select{font-family:inherit}",
    ".kc h1{font-size:clamp(28px,5vw,42px);font-weight:900;letter-spacing:-.04em;margin:0 0 22px}",
    ".kc h1 em{font-family:var(--serif);font-style:italic;font-weight:400}",
    ".kc-grid{display:grid;gap:20px;align-items:start}",
    "@media(min-width:900px){.kc-grid{grid-template-columns:minmax(0,1fr) 360px;gap:32px}.kc-side{position:sticky;top:90px}}",
    ".kc-box{border:1px solid var(--line);border-radius:20px;padding:20px;background:var(--paper)}",
    ".kc-box+.kc-box{margin-top:16px}",
    ".kc-box h2{font-size:18px;font-weight:800;letter-spacing:-.02em;margin:0 0 14px;display:flex;align-items:center;gap:10px}",
    ".kc-box h2 .n{width:24px;height:24px;border-radius:50%;background:var(--ink);color:var(--paper);font-size:12px;display:inline-flex;align-items:center;justify-content:center}",
    ".kc-seller{border-top:1px solid var(--line);padding-top:14px;margin-top:14px}",
    ".kc-seller:first-of-type{border-top:0;padding-top:0;margin-top:0}",
    ".kc-sh{display:flex;align-items:center;gap:10px;margin-bottom:10px;font-weight:800;font-size:14.5px}",
    ".kc-sh a{color:inherit;text-decoration:none}.kc-sh a:hover{text-decoration:underline}",
    ".kc-av{width:28px;height:28px;border-radius:50%;background:var(--ink) center/cover;color:var(--paper);display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:900;flex-shrink:0}",
    ".kc-line{display:grid;grid-template-columns:64px minmax(0,1fr) auto;gap:12px;padding:10px 0;align-items:start}",
    ".kc-line+.kc-line{border-top:1px dashed var(--line)}",
    ".kc-im{width:64px;height:64px;border-radius:12px;background:var(--grey) center/cover}",
    ".kc-tt{font-weight:700;font-size:14.5px;line-height:1.3;overflow-wrap:anywhere}",
    ".kc-sub{font-size:12.5px;color:var(--mut);margin-top:2px}",
    ".kc-ctl{display:flex;flex-wrap:wrap;gap:8px;margin-top:8px;align-items:center}",
    ".kc-ctl select{border:1px solid var(--line);border-radius:10px;padding:7px 9px;font-size:13px;background:var(--paper);color:var(--ink);max-width:100%}",
    ".kc-qty{display:inline-flex;align-items:center;border:1px solid var(--line);border-radius:10px;overflow:hidden}",
    ".kc-qty button{border:0;background:none;width:30px;height:32px;font-size:16px;cursor:pointer;color:var(--ink)}",
    ".kc-qty button:disabled{opacity:.3;cursor:default}",
    ".kc-qty span{min-width:22px;text-align:center;font-weight:700;font-size:13.5px}",
    ".kc-rm{border:0;background:none;color:var(--mut);font-size:12.5px;text-decoration:underline;cursor:pointer;padding:0}",
    ".kc-pr{font-weight:800;font-size:14.5px;white-space:nowrap;text-align:right}",
    ".kc-pr small{display:block;font-weight:600;font-size:11.5px;color:var(--mut)}",
    ".kc-err{color:var(--bad);font-size:12.5px;font-weight:600;margin-top:6px}",
    ".kc-field{margin-top:12px}.kc-field:first-child{margin-top:0}",
    ".kc-field label{display:block;font-size:12px;font-weight:700;color:var(--mut);margin-bottom:5px}",
    ".kc-field input,.kc-field select{width:100%;border:1px solid var(--line);border-radius:11px;padding:12px 13px;font-size:16px;color:var(--ink);background:var(--paper);outline:none}",
    ".kc-field input:focus,.kc-field select:focus{border-color:var(--ink)}",
    ".kc-row{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:12px}.kc-row:first-child{margin-top:0}.kc-row>.kc-field{margin-top:0}",
    "@media(max-width:520px){.kc-row{grid-template-columns:1fr}}",
    ".kc-chk{display:flex;gap:10px;align-items:flex-start;font-size:13px;line-height:1.5;color:#333;margin-top:12px;cursor:pointer}",
    ".kc-chk input{width:18px;height:18px;margin:1px 0 0;accent-color:var(--ink);flex-shrink:0}",
    ".kc-lnk{border:0;background:none;padding:0;font:inherit;color:var(--ink);font-weight:700;text-decoration:underline;cursor:pointer}",
    ".kc-tot{display:flex;justify-content:space-between;align-items:baseline;font-size:14px;margin-top:8px}",
    ".kc-tot.big{border-top:1px solid var(--line);margin-top:14px;padding-top:14px;font-size:15px}",
    ".kc-tot.big b{font-size:26px;font-weight:900;letter-spacing:-.02em}",
    ".kc-btn{display:flex;width:100%;align-items:center;justify-content:center;gap:8px;margin-top:16px;border:0;border-radius:999px;padding:16px;font:800 15.5px var(--sans);background:var(--ink);color:var(--paper);cursor:pointer;text-decoration:none}",
    ".kc-btn:disabled{opacity:.45;cursor:default}",
    ".kc-btn.ghost{background:var(--paper);color:var(--ink);border:1px solid var(--ink)}",
    ".kc-note{font-size:12px;color:var(--mut);line-height:1.5;margin-top:12px;text-align:center}",
    ".kc-msg{color:var(--bad);font-size:13.5px;font-weight:600;margin-top:12px}.kc-msg:empty{display:none}",
    ".kc-pay{margin-top:4px}",
    ".kc-empty,.kc-done{text-align:center;padding:48px 20px}",
    ".kc-empty h2,.kc-done h2{font-size:26px;font-weight:900;letter-spacing:-.03em;margin:0 0 8px}",
    ".kc-empty p,.kc-done p{color:var(--mut);margin:0 0 20px;font-size:15px}",
    ".kc-done .kc-btn{display:inline-flex;width:auto;padding:14px 26px}",
    ".kc-done ul{list-style:none;padding:0;margin:0 auto 22px;max-width:460px;text-align:left}",
    ".kc-done li{border:1px solid var(--line);border-radius:12px;padding:10px 14px;margin-top:8px;font-size:14px}",
    ".kc-done li small{display:block;color:var(--mut)}",
    ".kc-ok{width:56px;height:56px;border-radius:50%;background:var(--good);color:#fff;display:flex;align-items:center;justify-content:center;margin:0 auto 16px;font-size:28px}",
    ".kc-skel{height:120px;border-radius:20px;background:linear-gradient(90deg,#f5f5f4,#fbfbfa,#f5f5f4);background-size:200% 100%;animation:kc-sh 1.2s infinite}",
    "@keyframes kc-sh{to{background-position:-200% 0}}",
    ".kc-mbg{position:fixed;inset:0;background:rgba(10,10,10,.5);z-index:99;display:flex;align-items:center;justify-content:center;padding:16px}",
    ".kc-m{background:var(--paper);border-radius:20px;max-width:620px;width:100%;padding:24px;position:relative;max-height:92%;overflow-y:auto;font-family:var(--sans)}",
    ".kc-m h3{margin:0 34px 6px 0;font-size:20px;font-weight:900}",
    ".kc-m h4{font-size:14.5px;margin:16px 0 4px}.kc-m p{font-size:13.5px;line-height:1.6;color:#333;white-space:pre-wrap;margin:0}",
    ".kc-m .x{position:absolute;top:14px;right:14px;width:32px;height:32px;border-radius:50%;border:1px solid var(--line);background:var(--paper);cursor:pointer}"
  ].join("\n");

  function injectCss() {
    if (document.getElementById("kitly-checkout-css")) return;
    var st = document.createElement("style");
    st.id = "kitly-checkout-css"; st.textContent = CSS;
    document.head.appendChild(st);
  }
  var SCRIPT_SRC = (function () { try { return document.currentScript && document.currentScript.src; } catch (e) { return ""; } })();

  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }
  function cssUrl(u) { return u ? "url('" + String(u).replace(/['"\\()\s]/g, encodeURIComponent) + "')" : "none"; }
  function money(c) { return ((c || 0) / 100).toFixed(2).replace(".", ",").replace(",00", "") + " €"; }
  function load(src) {
    return new Promise(function (resolve, reject) {
      var s = document.createElement("script"); s.src = src; s.onload = resolve; s.onerror = reject; document.head.appendChild(s);
    });
  }
  var regionName = (function () {
    try { var dn = new Intl.DisplayNames(["fr"], { type: "region" }); return function (c) { return dn.of(c) || c; }; }
    catch (e) { return function (c) { return c; }; }
  })();

  function mount(root, opts) {
    injectCss();
    opts = opts || {};
    var apiBase = (opts.apiBase || "").replace(/\/$/, "");
    root.classList.add("kc");
    var HOST = window, frameEl = null;
    try { if (window.frameElement && window.parent.document) { HOST = window.parent; frameEl = window.frameElement; } } catch (e) {}
    var loc = (function () { try { return HOST.location; } catch (e) { return window.location; } })();

    var S = { quote: null, timer: null, reqId: 0, ref: null, stripe: null, elements: null, form: {}, paying: false };
    var cart = null;

    function api(method, path, body) {
      return fetch(apiBase + "/kitly" + path, {
        method: method, headers: body ? { "Content-Type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined
      }).then(function (res) {
        return res.json().catch(function () { return null; }).then(function (d) {
          if (!res.ok) {
            var msg = d && d.detail;
            if (Array.isArray(msg)) msg = String((msg[0] && msg[0].msg) || "").replace(/^Value error, /, "");
            if (msg && typeof msg === "object") msg = msg.message;
            throw new Error(msg || "Une erreur est survenue, réessaie.");
          }
          return d;
        });
      }, function () { throw new Error("Connexion impossible, réessaie."); });
    }
    function $(sel) { return root.querySelector(sel); }
    function cartItems() {
      return cart.items().map(function (x) {
        return { offer_id: x.offer_id, variant_id: x.variant_id || null, quantity: x.quantity || 1, delivery: x.delivery || null, source: x.source || "direct" };
      });
    }
    function scrollTop() {
      try { if (frameEl) HOST.scrollTo({ top: HOST.scrollY + frameEl.getBoundingClientRect().top - 20, behavior: "smooth" }); else window.scrollTo({ top: 0, behavior: "smooth" }); } catch (e) {}
    }

    // ── Écrans ───────────────────────────────────────────────
    function empty() {
      root.innerHTML = '<div class="kc-empty"><h2>Ton panier est vide</h2><p>Ajoute des offres depuis les vitrines des freelances.</p>' +
        '<a class="kc-btn" style="display:inline-flex;width:auto;padding:14px 26px" href="' + esc(opts.marketplaceUrl || "/marketplace") + '" target="_top">Découvrir les offres</a></div>';
    }
    function done(st) {
      S.finished = true;   // le panier vidé ne doit pas ré-afficher « panier vide »
      try { cart.clear(); } catch (e) {}
      var items = (st.items || []).map(function (i) {
        return '<li>' + esc(i.title) + (i.variant ? " — " + esc(i.variant) : "") + (i.quantity > 1 ? " × " + i.quantity : "") +
          '<small>Vendu par ' + esc(i.seller) + ' · ' + esc(i.order_ref) + '</small></li>';
      }).join("");
      root.innerHTML = '<div class="kc-done"><div class="kc-ok">✓</div><h2>Merci pour ta commande</h2>' +
        '<p>Paiement confirmé · référence <b style="color:#0a0a0a">' + esc(st.ref) + '</b> · ' + esc(money(st.amount_cents)) + '<br>' +
        'Tu reçois le récapitulatif par email, et chaque vendeur est prévenu.</p><ul>' + items + '</ul>' +
        '<a class="kc-btn" href="' + esc(opts.marketplaceUrl || "/marketplace") + '" target="_top">Continuer mes achats</a></div>';
      scrollTop();
    }
    function waiting(ref, tries) {
      S.finished = true;
      root.innerHTML = '<div class="kc-done"><div class="kc-skel" style="width:56px;height:56px;border-radius:50%;margin:0 auto 16px"></div>' +
        '<h2>Confirmation du paiement…</h2><p>Encore un instant, ne ferme pas la page.</p></div>';
      poll(ref, tries || 0);
    }
    function poll(ref, n) {
      api("GET", "/checkout/" + encodeURIComponent(ref)).then(function (st) {
        if (st.status === "paid") return done(st);
        if (st.status === "canceled") return failedPay("Le paiement a été annulé.");
        if (n < 20) setTimeout(function () { poll(ref, n + 1); }, 1500);
        else root.innerHTML = '<div class="kc-done"><h2>Paiement en cours de validation</h2><p>Ta banque confirme le paiement. ' +
          'Tu recevras un email dès qu\'il est validé (référence <b>' + esc(ref) + '</b>).</p></div>';
      }).catch(function () { if (n < 20) setTimeout(function () { poll(ref, n + 1); }, 2000); });
    }
    function failedPay(msg) {
      S.finished = false;
      start();
      setTimeout(function () { var m = $("#kc-msg"); if (m) m.textContent = msg; }, 600);
    }

    // ── Panier + formulaire ──────────────────────────────────
    function start() {
      if (!cart.count()) return empty();
      root.innerHTML = '<h1>Ton <em>panier</em></h1><div class="kc-grid"><div class="kc-main"><div class="kc-skel"></div></div>' +
        '<aside class="kc-side"><div class="kc-skel"></div></aside></div>';
      refreshQuote();
    }
    function refreshQuote() {
      var rid = ++S.reqId;
      api("POST", "/cart/quote", { items: cartItems() }).then(function (q) {
        if (rid !== S.reqId || S.finished) return;
        S.quote = q;
        render();
      }).catch(function (e) {
        if (rid !== S.reqId) return;
        root.innerHTML = '<div class="kc-empty"><h2>Oups</h2><p>' + esc(e.message) + '</p><button type="button" class="kc-btn" style="display:inline-flex;width:auto;padding:14px 26px" id="kc-retry">Réessayer</button></div>';
        $("#kc-retry").onclick = start;
      });
    }
    function readForm() {
      root.querySelectorAll("[data-f]").forEach(function (el) { S.form[el.getAttribute("data-f")] = el.type === "checkbox" ? el.checked : el.value; });
    }
    function field(name, label, type, auto, extra) {
      return '<div class="kc-field"><label for="kc-' + name + '">' + label + '</label><input id="kc-' + name + '" data-f="' + name + '" type="' + type + '"' +
        (auto ? ' autocomplete="' + auto + '"' : '') + ' value="' + esc(S.form[name] || "") + '"' + (extra || "") + '></div>';
    }

    function lineHtml(l) {
      var ctl = [];
      if (l.variants && l.variants.length) ctl.push('<select data-act="variant" data-i="' + l.index + '" aria-label="Option">' + l.variants.map(function (v) {
        return '<option value="' + esc(v.id) + '"' + (v.id === l.variant_id ? " selected" : "") + (v.in_stock ? "" : " disabled") + '>' + esc(v.label) + (v.in_stock ? "" : " (épuisé)") + '</option>';
      }).join("") + '</select>');
      if (l.kind === "physical") ctl.push('<span class="kc-qty"><button type="button" data-act="minus" data-i="' + l.index + '"' + (l.quantity <= 1 ? " disabled" : "") + ' aria-label="Moins">−</button>' +
        '<span>' + l.quantity + '</span><button type="button" data-act="plus" data-i="' + l.index + '"' + (l.quantity >= l.max_quantity ? " disabled" : "") + ' aria-label="Plus">+</button></span>');
      if (l.delivery_options && l.delivery_options.length > 1) ctl.push('<select data-act="delivery" data-i="' + l.index + '" aria-label="Livraison">' + l.delivery_options.map(function (o) {
        return '<option value="' + esc(o.value) + '"' + ((l.delivery || "0") === o.value ? " selected" : "") + '>' + esc(o.label) + ' — ' + (o.price_cents ? esc(money(o.price_cents)) : "offert") + '</option>';
      }).join("") + '</select>');
      ctl.push('<button type="button" class="kc-rm" data-act="remove" data-i="' + l.index + '">Retirer</button>');
      var grouped = l.needs_address && !l.shipping_cents && l.delivery !== "pickup" && (l.delivery_options || []).some(function (o) { return o.price_cents > 0; });
      var sub = [l.kind === "digital" ? "Produit numérique" : l.kind === "service" ? "Prestation" : "",
                 l.shipping_cents ? "livraison " + money(l.shipping_cents) : grouped ? "livraison groupée avec le vendeur" : ""].filter(Boolean).join(" · ");
      return '<div class="kc-line"><div class="kc-im" style="background-image:' + cssUrl(l.image) + '"></div><div><div class="kc-tt">' + esc(l.title || "Offre indisponible") + '</div>' +
        (sub ? '<div class="kc-sub">' + esc(sub) + '</div>' : '') + '<div class="kc-ctl">' + ctl.join("") + '</div>' +
        (l.error ? '<div class="kc-err">' + esc(l.error) + '</div>' : '') + '</div>' +
        '<div class="kc-pr">' + (l.error ? "—" : esc(money(l.total_cents))) + (l.quantity > 1 && !l.error ? '<small>' + esc(money(l.unit_cents)) + ' / unité</small>' : '') + '</div></div>';
    }

    function render() {
      var q = S.quote;
      if (!cart.count()) return empty();
      readForm();
      var sellers = q.sellers.map(function (g) {
        var link = "";
        try { link = HOST.location.origin + "/profile#" + encodeURIComponent(g.slug); } catch (e) {}
        return '<div class="kc-seller"><div class="kc-sh"><span class="kc-av" style="background-image:' + cssUrl(g.avatar_url) + '">' + (g.avatar_url ? "" : esc((g.display_name || "?").charAt(0))) + '</span>' +
          '<a href="' + esc(link) + '" target="_top">' + esc(g.display_name) + '</a></div>' + g.lines.map(lineHtml).join("") + '</div>';
      }).join("") + (q.unavailable.length ? '<div class="kc-seller">' + q.unavailable.map(lineHtml).join("") + '</div>' : "");

      var cgv = q.sellers.map(function (g) { return '<button type="button" class="kc-lnk" data-cgv="' + esc(g.slug) + '" data-name="' + esc(g.display_name) + '">' + esc(g.display_name) + '</button>'; }).join(", ");
      var countries = q.countries.slice().sort(function (a, b) { return regionName(a).localeCompare(regionName(b), "fr"); });
      if (!S.form.country) S.form.country = countries.indexOf("FR") >= 0 ? "FR" : (countries[0] || "FR");
      var step = 1;
      root.innerHTML = '<h1>Ton <em>panier</em></h1><div class="kc-grid"><div class="kc-main">' +
        '<section class="kc-box"><h2><span class="n">' + (step++) + '</span>Articles</h2>' + sellers + '</section>' +
        '<section class="kc-box"><h2><span class="n">' + (step++) + '</span>Tes coordonnées</h2>' +
          '<div class="kc-row">' + field("email", "Email", "email", "email") + field("name", "Nom et prénom", "text", "name", ' maxlength="120"') + '</div>' +
          field("phone", "Téléphone (facultatif)", "tel", "tel", ' maxlength="40"') + '</section>' +
        (q.needs_address ? '<section class="kc-box"><h2><span class="n">' + (step++) + '</span>Livraison</h2>' +
          field("ship_name", "Nom du destinataire", "text", "shipping name", ' maxlength="120"') +
          field("line1", "Adresse", "text", "shipping address-line1", ' maxlength="200"') +
          field("line2", "Complément (facultatif)", "text", "shipping address-line2", ' maxlength="200"') +
          '<div class="kc-row">' + field("postal_code", "Code postal", "text", "shipping postal-code", ' maxlength="20"') + field("city", "Ville", "text", "shipping address-level2", ' maxlength="120"') + '</div>' +
          '<div class="kc-field"><label for="kc-country">Pays</label><select id="kc-country" data-f="country">' + countries.map(function (c) {
            return '<option value="' + c + '"' + (S.form.country === c ? " selected" : "") + '>' + esc(regionName(c)) + '</option>'; }).join("") + '</select></div></section>' : '') +
        '<section class="kc-box"><h2><span class="n">' + (step++) + '</span>Paiement</h2>' +
          '<label class="kc-chk"><input type="checkbox" data-f="terms"' + (S.form.terms ? " checked" : "") + '><span>J\'ai lu et j\'accepte les conditions générales de vente de ' + cgv + '.</span></label>' +
          (q.has_digital ? '<label class="kc-chk"><input type="checkbox" data-f="waive"' + (S.form.waive ? " checked" : "") + '><span>Pour les contenus numériques, je demande l\'accès immédiat et je reconnais perdre mon droit de rétractation dès leur fourniture.</span></label>' : '') +
          (q.has_service ? '<label class="kc-chk"><input type="checkbox" data-f="early"' + (S.form.early ? " checked" : "") + '><span>Je souhaite que les prestations commencent avant la fin du délai de rétractation de 14 jours (facultatif). Si je me rétracte ensuite, je paierai la part déjà réalisée.</span></label>' : '') +
          '<div id="kc-pay" class="kc-pay"></div><div class="kc-msg" id="kc-msg"></div>' +
          '<button type="button" class="kc-btn" id="kc-go"' + (q.has_errors || !q.payments_ready ? " disabled" : "") + '>Continuer vers le paiement</button>' +
          (!q.payments_ready ? '<p class="kc-note">Le paiement en ligne n\'est pas encore activé sur Kitly.</p>' : '') +
          (q.has_errors ? '<p class="kc-note">Retire les articles indisponibles pour continuer.</p>' : '') +
        '</section></div>' +
        '<aside class="kc-side"><div class="kc-box"><h2>Récapitulatif</h2>' +
          q.sellers.map(function (g) { return '<div class="kc-tot"><span>' + esc(g.display_name) + '</span><span>' + esc(money(g.subtotal_cents)) + '</span></div>'; }).join("") +
          '<div class="kc-tot big"><span>Total TTC</span><b>' + esc(money(q.total_cents)) + '</b></div>' +
          '<p class="kc-note">Paiement sécurisé par Stripe. Kitly encaisse le paiement pour le compte des vendeurs et reverse à chacun sa part. ' +
          'Chaque vente est conclue avec le vendeur concerné, selon ses conditions générales de vente.</p></div></aside></div>';
      bind();
    }

    function resetPayment() {
      // Panier ou coordonnées modifiés après la préparation du paiement : on repart du formulaire.
      if (!S.elements) return;
      S.elements = null;
      var pay = $("#kc-pay"); if (pay) pay.innerHTML = "";
      var go = $("#kc-go"); if (go) { go.textContent = "Continuer vers le paiement"; go.disabled = !!(S.quote.has_errors || !S.quote.payments_ready); }
    }

    function bind() {
      root.querySelectorAll("[data-act]").forEach(function (el) {
        var i = parseInt(el.getAttribute("data-i"), 10), act = el.getAttribute("data-act");
        var handler = function () {
          var item = cart.items()[i]; if (!item) return;
          if (act === "remove") cart.remove(i);
          else if (act === "plus") cart.update(i, { quantity: (item.quantity || 1) + 1 });
          else if (act === "minus") cart.update(i, { quantity: Math.max(1, (item.quantity || 1) - 1) });
          else if (act === "variant") cart.update(i, { variant_id: el.value });
          else if (act === "delivery") cart.update(i, { delivery: el.value });
          resetPayment();
          refreshQuote();
        };
        el.addEventListener(el.tagName === "SELECT" ? "change" : "click", handler);
      });
      root.querySelectorAll("[data-f]").forEach(function (el) {
        el.addEventListener("input", function () { readForm(); resetPayment(); });
        el.addEventListener("change", function () { readForm(); resetPayment(); });
      });
      root.querySelectorAll("[data-cgv]").forEach(function (el) {
        el.onclick = function (e) { e.preventDefault(); openTerms(el.getAttribute("data-cgv"), el.getAttribute("data-name")); };
      });
      $("#kc-go").onclick = function () { if (S.elements) pay(); else prepare(); };
    }

    function validate() {
      var f = S.form, q = S.quote;
      if (!/^[^\s@]+@[^\s@]+\.[a-zA-Z]{2,}$/.test(f.email || "")) return "Indique une adresse email valide.";
      if (!(f.name || "").trim()) return "Indique ton nom et ton prénom.";
      if (q.needs_address && (!(f.ship_name || f.name || "").trim() || !(f.line1 || "").trim() || !(f.postal_code || "").trim() || !(f.city || "").trim()))
        return "Complète ton adresse de livraison.";
      if (!f.terms) return "Merci d'accepter les conditions générales de vente des vendeurs.";
      if (q.has_digital && !f.waive) return "Coche la case d'accès immédiat pour les contenus numériques.";
      return "";
    }

    function prepare() {
      readForm();
      var msg = $("#kc-msg"), go = $("#kc-go"), f = S.form, q = S.quote;
      var problem = validate();
      if (problem) { msg.textContent = problem; return; }
      msg.textContent = ""; go.disabled = true; go.textContent = "Préparation du paiement…";
      var body = { items: cartItems(), email: f.email.trim(), name: f.name.trim(), phone: (f.phone || "").trim(),
                   accept_terms: true, waive_withdrawal: !!f.waive, early_start: !!f.early, replaces: S.ref };
      if (q.needs_address) body.address = { name: (f.ship_name || f.name).trim(), line1: f.line1.trim(), line2: (f.line2 || "").trim(),
                                            postal_code: f.postal_code.trim(), city: f.city.trim(), country: f.country || "FR" };
      Promise.all([api("POST", "/checkout", body), global.Stripe ? Promise.resolve() : load("https://js.stripe.com/v3/")]).then(function (r) {
        var co = r[0];
        S.ref = co.ref;
        S.stripe = S.stripe || global.Stripe(co.publishable_key, { locale: "fr" });
        S.elements = S.stripe.elements({ clientSecret: co.client_secret, appearance: { theme: "stripe",
          variables: { colorPrimary: "#0a0a0a", borderRadius: "11px", fontFamily: "Inter, -apple-system, Segoe UI, Arial, sans-serif" } } });
        var holder = $("#kc-pay");
        holder.innerHTML = '<div style="margin:14px 0 4px"></div>';
        S.elements.create("payment", { layout: "tabs", defaultValues: { billingDetails: { email: body.email, name: body.name } } }).mount(holder.firstChild);
        go.disabled = false; go.textContent = "Payer " + money(co.amount_cents);
      }).catch(function (e) { go.disabled = false; go.textContent = "Continuer vers le paiement"; msg.textContent = e.message || "Le paiement n'a pas pu être préparé."; });
    }

    function pay() {
      if (S.paying) return;
      var msg = $("#kc-msg"), go = $("#kc-go");
      S.paying = true; go.disabled = true; go.textContent = "Paiement en cours…"; msg.textContent = "";
      var back = "";
      try { back = HOST.location.origin + HOST.location.pathname + "?ref=" + encodeURIComponent(S.ref); } catch (e) {}
      S.stripe.confirmPayment({ elements: S.elements, confirmParams: { return_url: back }, redirect: "if_required" }).then(function (r) {
        S.paying = false;
        if (r.error) {
          go.disabled = false; go.textContent = "Réessayer le paiement";
          msg.textContent = r.error.message || "Le paiement a échoué.";
          return;
        }
        waiting(S.ref);
      });
    }

    // CGV d'un vendeur, dans une fenêtre sur la partie visible de la page.
    function openTerms(slug, name) {
      var bg = document.createElement("div");
      bg.className = "kc-mbg";
      if (frameEl) {
        var r = frameEl.getBoundingClientRect(), top = Math.max(0, -r.top), bottom = Math.min(r.height, HOST.innerHeight - r.top);
        bg.style.position = "absolute"; bg.style.top = top + "px"; bg.style.height = Math.max(240, bottom - top) + "px"; bg.style.bottom = "auto";
      }
      bg.innerHTML = '<div class="kc-m" role="dialog" aria-modal="true"><button type="button" class="x" aria-label="Fermer">✕</button>' +
        '<h3>Conditions générales de vente</h3><p style="color:#6f6f6c">' + esc(name) + '</p><div data-body><p>Chargement…</p></div></div>';
      root.appendChild(bg);
      var close = function () { bg.remove(); };
      bg.onclick = function (e) { if (e.target === bg) close(); };
      bg.querySelector(".x").onclick = close;
      api("GET", "/public/profiles/" + encodeURIComponent(slug) + "/terms").then(function (r) {
        bg.querySelector("[data-body]").innerHTML = (r.terms.articles || []).map(function (a) { return '<h4>' + esc(a.title) + '</h4><p>' + esc(a.body) + '</p>'; }).join("");
      }).catch(function (e) { bg.querySelector("[data-body]").innerHTML = '<p>' + esc(e.message) + '</p>'; });
    }

    // ── Démarrage ────────────────────────────────────────────
    var cartSrc = "";
    try { cartSrc = new URL("cart.js", SCRIPT_SRC || (apiBase + "/kitly/static/checkout.js")).href; } catch (e) { cartSrc = apiBase + "/kitly/static/cart.js"; }
    root.innerHTML = '<div class="kc-skel"></div>';
    (global.KitlyCart ? Promise.resolve() : load(cartSrc)).then(function () {
      cart = global.KitlyCart;
      var params = new URLSearchParams(loc.search || ""), ref = params.get("ref");
      if (ref) {
        // Retour de 3D Secure (ou lien de la confirmation) : on suit l'état du paiement.
        try { params.delete("payment_intent"); params.delete("payment_intent_client_secret"); params.delete("redirect_status"); } catch (e) {}
        if ((new URLSearchParams(loc.search)).get("redirect_status") === "failed") { S.ref = ref; return failedPay("Le paiement n'a pas abouti. Réessaie ou choisis un autre moyen de paiement."); }
        return waiting(ref);
      }
      start();
      // Panier modifié dans un autre onglet (ou une autre page du site) : récapitulatif mis à jour.
      cart.onChange(function () { if (!S.elements && S.quote && !S.finished) refreshQuote(); });
    }).catch(function () {
      root.innerHTML = '<div class="kc-empty"><h2>Oups</h2><p>Le panier n\'a pas pu être chargé. Recharge la page.</p></div>';
    });
  }

  global.KitlyCheckout = { version: 1, mount: mount };
})(window);
