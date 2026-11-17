/*
 * KITLY — marketplace (source unique).
 *
 * Servi par l'API : GET /kitly/static/marketplace.js
 * Utilisé par :
 *   - embeds/kitly/marketplace.html   page /marketplace, et mode « showcase » de la landing
 *   - embeds/kitly/home.html          accueil : « Crée ta vitrine » puis la marketplace
 *
 * Page volontairement simple : une recherche, une ligne de catégories, puis
 * deux sections — « Offres » (chaque carte ouvre la fiche de l'offre sur la
 * vitrine) et « Profils » (cartes compactes vers la vitrine).
 *
 * API :
 *   KitlyMarket.mount(el, opts)
 *     opts = {
 *       apiBase, mode: "full" | "showcase",
 *       profileUrl: "/profile#{slug}", marketplaceUrl, registerUrl,
 *       pageSize: 12, showcaseCount: 8,
 *       hero: true,       // en-tête « Trouve le bon freelance » (mode full)
 *       ctaBand: false,   // bandeau « Tu es freelance ? »
 *       syncUrl: true,    // recherche et catégorie recopiées dans l'URL de la page parente
 *       title, subtitle   // textes de l'en-tête (facultatif)
 *     }
 *
 * Tout texte vient des freelances : il est systématiquement échappé.
 */
(function (global) {
  "use strict";
  if (global.KitlyMarket && global.KitlyMarket.version >= 2) return;

  var CSS = [
    ".km{box-sizing:border-box;--ink:#0a0a0a;--paper:#fff;--grey:#f5f5f4;--line:#e4e4e2;--mut:#6f6f6c;--serif:'Instrument Serif',Georgia,serif;--sans:'Inter',-apple-system,'Segoe UI',Arial,sans-serif;font-family:var(--sans);color:var(--ink);-webkit-font-smoothing:antialiased;max-width:1160px;margin:0 auto;padding:8px 20px 48px}",
    ".km *,.km *::before,.km *::after{box-sizing:border-box}",
    ".km button{font-family:inherit}.km .hidden{display:none!important}",
    /* en-tête */
    ".km .hero{text-align:center;padding:32px 0 8px}",
    ".km .hero.compact{padding:8px 0 4px}",
    ".km .logo{width:64px;height:64px;object-fit:contain;display:block;margin:0 auto 10px}",
    ".km h1{font-size:clamp(30px,5vw,48px);font-weight:900;letter-spacing:-.04em;line-height:1.08;margin:0}",
    ".km h1 em,.km h2 em{font-family:var(--serif);font-style:italic;font-weight:400;letter-spacing:-.01em}",
    ".km .hero p{color:var(--mut);font-size:16px;margin:12px auto 0;max-width:520px;line-height:1.5}",
    ".km .hero.compact h2{font-size:clamp(24px,3.4vw,34px);font-weight:900;letter-spacing:-.03em;margin:0}",
    /* recherche */
    ".km .search{display:flex;align-items:center;gap:10px;max-width:640px;margin:24px auto 0;border:1px solid var(--line);border-radius:999px;padding:0 8px 0 18px;height:54px;background:var(--paper);box-shadow:0 8px 30px -18px rgba(0,0,0,.25);transition:border-color .15s}",
    ".km .search:focus-within{border-color:var(--ink)}",
    ".km .search svg{width:18px;height:18px;color:var(--mut);flex-shrink:0}",
    ".km .search input{flex:1;min-width:0;border:0;outline:none;font:16px var(--sans);color:var(--ink);background:none;height:100%}",
    ".km .search input::-webkit-search-cancel-button{display:none}",
    ".km .search .clear{border:0;background:var(--grey);color:var(--mut);cursor:pointer;font-size:12px;width:30px;height:30px;border-radius:50%;flex-shrink:0}",
    /* catégories */
    ".km .cats{display:flex;gap:8px;overflow-x:auto;scrollbar-width:none;padding:18px 0 4px;justify-content:safe center}",
    ".km .cats::-webkit-scrollbar{display:none}",
    ".km .chip{flex-shrink:0;border:1px solid var(--line);background:var(--paper);border-radius:999px;padding:8px 15px;font-size:13px;font-weight:700;color:var(--ink);cursor:pointer;transition:.15s;white-space:nowrap}",
    ".km .chip:hover{border-color:var(--ink)}",
    ".km .chip.on{background:var(--ink);color:var(--paper);border-color:var(--ink)}",
    /* sections */
    ".km .sec{margin-top:40px}",
    ".km .sec-h{display:flex;align-items:baseline;justify-content:space-between;gap:12px;margin-bottom:16px}",
    ".km h2{font-size:clamp(22px,3vw,28px);font-weight:900;letter-spacing:-.03em;margin:0}",
    ".km .count{font-size:13.5px;color:var(--mut);font-weight:600;white-space:nowrap}",
    ".km .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:16px}",
    ".km .row{display:grid;grid-auto-flow:column;grid-auto-columns:minmax(150px,170px);gap:14px;overflow-x:auto;scrollbar-width:none;scroll-snap-type:x mandatory;padding-bottom:6px}",
    ".km .row::-webkit-scrollbar{display:none}.km .row>*{scroll-snap-align:start}",
    "@media(min-width:1100px){.km .row{grid-auto-columns:calc((100% - 70px)/6)}}",
    /* carte offre */
    ".km .of{display:flex;flex-direction:column;border:1px solid var(--line);border-radius:18px;overflow:hidden;background:var(--paper);text-decoration:none;color:inherit;transition:transform .15s,box-shadow .15s,border-color .15s;min-width:0}",
    ".km .of:hover,.km .pf:hover{transform:translateY(-3px);border-color:var(--ink);box-shadow:0 14px 30px -18px rgba(0,0,0,.25)}",
    ".km .of .im{aspect-ratio:4/3;background:var(--grey) center/cover;display:flex;align-items:center;justify-content:center;color:var(--mut);position:relative}",
    ".km .of .im svg{width:30px;height:30px;opacity:.5}",
    ".km .of .bg{position:absolute;top:10px;left:10px;font-size:10px;font-weight:800;letter-spacing:.06em;text-transform:uppercase;background:var(--paper);padding:4px 9px;border-radius:99px;box-shadow:0 1px 4px rgba(0,0,0,.12)}",
    ".km .of .bd{padding:12px 14px 14px;display:flex;flex-direction:column;gap:4px;flex:1}",
    ".km .of .tt{font-size:15px;font-weight:800;letter-spacing:-.01em;line-height:1.3;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}",
    ".km .of .pr{font-size:14px;font-weight:800}",
    ".km .of .by{display:flex;align-items:center;gap:8px;margin-top:auto;padding-top:10px;font-size:12.5px;color:var(--mut);font-weight:600;min-width:0}",
    ".km .of .by span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}",
    ".km .av{border-radius:50%;background:var(--ink) center/cover;color:var(--paper);display:flex;align-items:center;justify-content:center;font-weight:900;flex-shrink:0}",
    ".km .of .av{width:24px;height:24px;font-size:11px}",
    /* carte profil (compacte) */
    ".km .pf{display:flex;flex-direction:column;align-items:center;text-align:center;border:1px solid var(--line);border-radius:18px;padding:20px 14px 16px;background:var(--paper);text-decoration:none;color:inherit;transition:transform .15s,box-shadow .15s,border-color .15s;min-width:0;position:relative}",
    ".km .pf .av{width:64px;height:64px;font-size:24px;margin-bottom:10px}",
    ".km .pf .nm{font-size:15px;font-weight:800;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}",
    ".km .pf .jb{font-size:12.5px;color:var(--mut);font-weight:600;margin-top:3px;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}",
    ".km .pf .tag{position:absolute;top:10px;left:10px;font-size:9.5px;font-weight:800;letter-spacing:.06em;text-transform:uppercase;background:var(--ink);color:var(--paper);padding:3px 8px;border-radius:99px}",
    ".km .pf .tag.lt{background:var(--grey);color:var(--ink)}",
    ".km .spons{border:1px dashed #cfcfcc;border-radius:22px;padding:14px;margin-bottom:16px}",
    ".km .spons-h{font-size:11px;font-weight:800;letter-spacing:.1em;text-transform:uppercase;color:var(--mut);margin:0 0 10px 4px}",
    /* divers */
    ".km .btn{display:inline-flex;align-items:center;justify-content:center;font-weight:700;font-size:14px;padding:12px 22px;border-radius:999px;border:1px solid var(--ink);background:var(--paper);color:var(--ink);cursor:pointer;text-decoration:none;white-space:nowrap;transition:.15s}",
    ".km .btn:hover{background:var(--grey)}",
    ".km .btn.dark{background:var(--ink);color:var(--paper)}",
    ".km .more{display:flex;justify-content:center;margin-top:22px}",
    ".km .empty{text-align:center;border:1px solid var(--line);border-radius:18px;padding:30px 20px;color:var(--mut);font-size:14px}",
    ".km .link{border:0;background:none;padding:0;font:600 12.5px var(--sans);color:var(--mut);cursor:pointer;text-decoration:underline}",
    ".km .rank{text-align:center;margin-top:32px}",
    ".km .skel{border:1px solid var(--line);border-radius:18px;height:250px;background:linear-gradient(90deg,#f5f5f4,#fbfbfa,#f5f5f4);background-size:200% 100%;animation:km-sh 1.2s infinite}",
    ".km .row .skel{height:150px}",
    "@keyframes km-sh{to{background-position:-200% 0}}",
    ".km .cta-band{margin-top:52px;background:var(--ink);color:var(--paper);border-radius:24px;padding:30px;display:flex;align-items:center;justify-content:space-between;gap:20px;flex-wrap:wrap}",
    ".km .cta-band h3{font-size:24px;font-weight:900;letter-spacing:-.03em;margin:0}",
    ".km .cta-band h3 em{font-family:var(--serif);font-style:italic;font-weight:400}",
    ".km .cta-band p{margin:6px 0 0;color:#b5b5b2;font-size:14px}",
    ".km .cta-band .btn{background:var(--paper);color:var(--ink);border-color:var(--paper)}",
    ".km .info-bg{position:fixed;inset:0;background:rgba(10,10,10,.5);z-index:99;display:flex;align-items:center;justify-content:center;padding:16px}",
    ".km .info{background:var(--paper);border-radius:20px;max-width:520px;width:100%;padding:26px;position:relative;max-height:92%;overflow-y:auto}",
    ".km .info h3{margin:0 30px 12px 0;font-size:20px;font-weight:900;letter-spacing:-.02em}",
    ".km .info p{font-size:14px;line-height:1.6;color:#333;margin:0 0 12px}",
    ".km .info .x{position:absolute;top:14px;right:14px;width:32px;height:32px;border-radius:50%;border:1px solid var(--line);background:var(--paper);cursor:pointer}",
    "@media(max-width:640px){.km{padding:4px 16px 36px}.km .hero{padding:18px 0 4px}.km .cats{justify-content:flex-start}.km .grid{grid-template-columns:1fr 1fr;gap:10px}.km .of .bd{padding:10px 11px 12px}.km .of .tt{font-size:13.5px}.km .cta-band{padding:22px}}"
  ].join("\n");

  function injectCss() {
    if (document.getElementById("kitly-market-css")) return;
    var st = document.createElement("style");
    st.id = "kitly-market-css";
    st.textContent = CSS;
    document.head.appendChild(st);
  }

  // Logo Kitly : fichier servi par l'API (app/kitly/static/logo.png), à côté de ce script.
  var LOGO_SRC = (function () {
    try { var src = document.currentScript && document.currentScript.src; if (src) return new URL("logo.png", src).href; } catch (e) {}
    return "/kitly/static/logo.png";
  })();
  var LOGO = '<img class="logo" src="' + LOGO_SRC + '" alt="Kitly" onerror="this.remove()">';
  var SEARCH_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>';
  var KIND_ICON = {
    physical: '<path d="m3 7 9-4 9 4v10l-9 4-9-4z"/><path d="m3 7 9 4 9-4M12 11v10"/>',
    digital: '<path d="M14 3H6v18h12V7z"/><path d="M14 3v4h4M9 13h6M9 17h6"/>',
    service: '<rect x="3" y="7" width="18" height="13" rx="2"/><path d="M8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M3 13h18"/>',
    appointment: '<rect x="3" y="5" width="18" height="16" rx="3"/><path d="M8 3v4M16 3v4M3 10h18"/>'
  };

  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }
  function cssUrl(u) { return u ? "url('" + String(u).replace(/['"\\()\s]/g, encodeURIComponent) + "')" : "none"; }
  function initial(name) { return esc((name || "?").charAt(0).toUpperCase()); }

  function mount(root, opts) {
    injectCss();
    opts = Object.assign({ mode: "full", pageSize: 12, showcaseCount: 8, hero: true, ctaBand: false, syncUrl: true,
                           profileUrl: "/profile#{slug}", marketplaceUrl: "/marketplace",
                           registerUrl: "/connexion?mode=inscription" }, opts || {});
    var apiBase = (opts.apiBase || "").replace(/\/$/, "");
    root.classList.add("km");

    var HOST = window, frameEl = null;
    try { if (window.frameElement && window.parent.document) { HOST = window.parent; frameEl = window.frameElement; } } catch (e) {}

    var S = { q: "", cat: "", categories: [], offers: [], offersTotal: 0, offersPage: 1, timer: null, reqId: 0 };

    function $(sel) { return root.querySelector(sel); }
    function get(path) {
      return fetch(apiBase + "/kitly/marketplace" + path).then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      });
    }
    // Lien d'une vitrine, avec ?src=marketplace avant le hash : /profile?src=marketplace#lea-martin
    function profileUrl(slug, offerId) {
      var u = opts.profileUrl.replace("{slug}", encodeURIComponent(slug));
      var i = u.indexOf("#"), hash = i >= 0 ? u.slice(i) : "";
      if (i >= 0) u = u.slice(0, i);
      if (offerId) hash = hash ? hash + "/offre-" + offerId : "#offre-" + offerId;
      return u + (u.indexOf("?") >= 0 ? "&" : "?") + "src=marketplace" + hash;
    }
    function filtering() { return !!(S.q || S.cat); }

    // ── URL de la page parente (lien partageable) ─────────────
    function readUrl() {
      if (!opts.syncUrl) return;
      try {
        var p = new URLSearchParams(HOST.location.search);
        S.q = p.get("q") || ""; S.cat = p.get("categorie") || "";
      } catch (e) {}
    }
    function writeUrl() {
      if (!opts.syncUrl || opts.mode !== "full") return;
      try {
        var p = new URLSearchParams(HOST.location.search);
        if (S.q) p.set("q", S.q); else p.delete("q");
        if (S.cat) p.set("categorie", S.cat); else p.delete("categorie");
        ["lieu", "type", "budget", "tri"].forEach(function (k) { p.delete(k); });   // anciens filtres
        var qs = p.toString();
        HOST.history.replaceState(HOST.history.state, "", HOST.location.pathname + (qs ? "?" + qs : "") + HOST.location.hash);
      } catch (e) {}
    }

    // ── Cartes ───────────────────────────────────────────────
    function offerCard(o) {
      var p = o.profile || {};
      return '<a class="of" href="' + esc(profileUrl(p.slug, o.id)) + '" target="_top" aria-label="' + esc(o.title + " — " + p.display_name) + '">' +
        '<div class="im" style="background-image:' + cssUrl(o.image) + '">' +
          (o.image ? "" : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' + (KIND_ICON[o.kind] || KIND_ICON.service) + '</svg>') +
          (o.badge ? '<span class="bg">' + esc(o.badge) + '</span>' : '') + '</div>' +
        '<div class="bd"><div class="tt">' + esc(o.title) + '</div>' +
          (o.price_label ? '<div class="pr">' + esc(o.price_label) + '</div>' : '') +
          '<div class="by"><span class="av" style="background-image:' + cssUrl(p.avatar_url) + '">' + (p.avatar_url ? "" : initial(p.display_name)) + '</span><span>' + esc(p.display_name) + '</span></div>' +
        '</div></a>';
    }
    function profileCard(c) {
      // « Sponsorisé » : porté par l'encart qui regroupe ces profils, à part du classement.
      var tag = !c.sponsored && c.is_new ? '<span class="tag lt">Nouveau</span>' : "";
      return '<a class="pf" href="' + esc(profileUrl(c.slug)) + '" target="_top" aria-label="' + esc(c.display_name) + '">' + tag +
        '<span class="av" style="background-image:' + cssUrl(c.avatar_url) + '">' + (c.avatar_url ? "" : initial(c.display_name)) + '</span>' +
        '<span class="nm">' + esc(c.display_name) + '</span>' +
        ((c.headline || c.location) ? '<span class="jb">' + esc(c.headline || c.location) + '</span>' : '') + '</a>';
    }
    function skeletons(n) { var h = ""; for (var i = 0; i < n; i++) h += '<div class="skel"></div>'; return h; }

    // ── Page ─────────────────────────────────────────────────
    function shell() {
      if (opts.mode === "showcase") {
        root.innerHTML = '<div class="sec" style="margin-top:0"><div class="sec-h"><h2>' + (opts.title || "Ils sont sur <em>Kitly</em>") + '</h2>' +
          '<a class="btn" href="' + esc(opts.marketplaceUrl) + '" target="_top">Tout voir</a></div><div class="row" id="km-show">' + skeletons(6) + '</div></div>';
        return;
      }
      var cats = '<button type="button" class="chip' + (S.cat ? "" : " on") + '" data-cat="">Tout</button>' + S.categories.filter(function (c) { return c.count; }).map(function (c) {
        return '<button type="button" class="chip' + (S.cat === c.key ? " on" : "") + '" data-cat="' + esc(c.key) + '">' + esc(c.label) + '</button>';
      }).join("");
      var search = '<form class="search" id="km-form" role="search">' + SEARCH_ICON +
        '<input type="search" id="km-q" placeholder="Une offre, un métier, un nom…" value="' + esc(S.q) + '" autocomplete="off" enterkeyhint="search" aria-label="Rechercher">' +
        '<button type="button" class="clear' + (S.q ? "" : " hidden") + '" id="km-clear" aria-label="Effacer">✕</button></form>';
      root.innerHTML =
        (opts.hero
          ? '<div class="hero">' + LOGO + '<h1>' + (opts.title || "Trouve le bon <em>freelance</em>") + '</h1>' +
            '<p>' + esc(opts.subtitle || "Leurs offres, leurs prix, et un contact direct.") + '</p>' + search + '</div>'
          : '<div class="hero compact">' + (opts.title ? '<h2>' + opts.title + '</h2>' : '') + (opts.subtitle ? '<p>' + esc(opts.subtitle) + '</p>' : '') + search + '</div>') +
        '<div class="cats" id="km-cats">' + cats + '</div>' +
        '<div id="km-body"></div>' +
        '<p class="rank"><button type="button" class="link" data-ranking>Comment sont classés les résultats ?</button></p>' +
        (opts.ctaBand
          ? '<div class="cta-band"><div><h3>Tu es <em>freelance</em> ?</h3><p>Crée ta vitrine gratuitement et apparais dans la marketplace.</p></div>' +
            '<a class="btn" href="' + esc(opts.registerUrl) + '" target="_top">Créer ma vitrine</a></div>'
          : '');
      bindShell();
    }

    function load() {
      var body = $("#km-body"), rid = ++S.reqId;
      S.offersPage = 1;
      body.innerHTML =
        '<section class="sec" id="km-offers"><div class="sec-h"><h2>Offres</h2><span class="count"></span></div><div class="grid">' + skeletons(4) + '</div></section>' +
        '<section class="sec" id="km-profiles"><div class="sec-h"><h2>Profils</h2><span class="count"></span></div><div class="row">' + skeletons(6) + '</div></section>';
      var p = new URLSearchParams();
      if (S.q) p.set("q", S.q);
      if (S.cat) p.set("category", S.cat);
      var qs = p.toString();
      var offersReq = get("/offers?" + qs + (qs ? "&" : "") + "page_size=" + opts.pageSize);
      // Sans recherche : profils populaires puis nouveaux ; avec recherche : résultats + encart sponsorisé à part.
      var profilesReq = filtering()
        ? get("/search?" + qs + "&page_size=18")
        : Promise.all([get("/popular?limit=12"), get("/new?limit=12")]).then(function (r) {
            var seen = {}, items = [];
            r[0].items.concat(r[1].items).forEach(function (c) { if (!seen[c.slug]) { seen[c.slug] = 1; items.push(c); } });
            if (items.length) return { items: items, total: items.length, sponsored: [] };
            return get("/search?sort=new&page_size=18");
          });
      offersReq.then(function (r) {
        if (rid !== S.reqId) return;
        S.offers = r.items; S.offersTotal = r.total;
        renderOffers();
      }).catch(function () { if (rid === S.reqId) failSection("#km-offers"); });
      profilesReq.then(function (r) {
        if (rid !== S.reqId) return;
        renderProfiles(r.items || [], r.total || 0, r.sponsored || []);
      }).catch(function () { if (rid === S.reqId) failSection("#km-profiles"); });
    }

    function failSection(sel) {
      var sec = $(sel); if (!sec) return;
      sec.querySelector(".sec-h + *").outerHTML = '<div class="empty">Chargement impossible. <button type="button" class="link" data-retry>Réessayer</button></div>';
    }

    function renderOffers() {
      var sec = $("#km-offers"); if (!sec) return;
      sec.querySelector(".count").textContent = filtering() && S.offersTotal ? S.offersTotal + " offre" + (S.offersTotal > 1 ? "s" : "") : "";
      var list = S.offers.length
        ? '<div class="grid">' + S.offers.map(offerCard).join("") + '</div>'
        : '<div class="empty">' + (filtering() ? "Aucune offre ne correspond à ta recherche." : "Les premières offres arrivent bientôt.") + '</div>';
      var more = S.offers.length < S.offersTotal ? '<div class="more"><button type="button" class="btn" id="km-more">Voir plus d\'offres</button></div>' : "";
      sec.innerHTML = sec.querySelector(".sec-h").outerHTML + list + more;
      var m = $("#km-more");
      if (m) m.onclick = function () {
        m.disabled = true; m.textContent = "Chargement…";
        var rid = S.reqId, p = new URLSearchParams();
        if (S.q) p.set("q", S.q);
        if (S.cat) p.set("category", S.cat);
        p.set("page", String(S.offersPage + 1)); p.set("page_size", String(opts.pageSize));
        get("/offers?" + p.toString()).then(function (r) {
          if (rid !== S.reqId) return;
          S.offersPage += 1; S.offers = S.offers.concat(r.items); S.offersTotal = r.total;
          renderOffers();
        }).catch(function () { m.disabled = false; m.textContent = "Réessayer"; });
      };
    }

    function renderProfiles(items, total, sponsored) {
      var sec = $("#km-profiles"); if (!sec) return;
      sec.querySelector(".count").textContent = filtering() && total ? total + " profil" + (total > 1 ? "s" : "") : "";
      var spons = sponsored.length
        ? '<div class="spons"><p class="spons-h">Sponsorisé</p><div class="row">' + sponsored.map(profileCard).join("") + '</div></div>' : "";
      var list = items.length
        ? '<div class="row">' + items.map(profileCard).join("") + '</div>'
        : '<div class="empty">' + (filtering() ? "Aucun profil ne correspond à ta recherche." : "Les premières vitrines arrivent bientôt.") + '</div>';
      sec.innerHTML = sec.querySelector(".sec-h").outerHTML + spons + list;
    }

    function refresh() {
      writeUrl();
      root.querySelectorAll("#km-cats .chip").forEach(function (c) { c.classList.toggle("on", c.getAttribute("data-cat") === S.cat); });
      load();
    }

    function viewport() {
      if (!frameEl) return null;
      var r = frameEl.getBoundingClientRect(), vh = HOST.innerHeight;
      var top = Math.max(0, -r.top), bottom = Math.min(r.height, vh - r.top);
      return { top: top, height: Math.max(240, bottom - top) };
    }
    function openRanking() {
      get("/ranking-info").then(function (info) {
        var bg = document.createElement("div");
        bg.className = "info-bg";
        var vp = viewport();
        if (vp) { bg.style.position = "absolute"; bg.style.top = vp.top + "px"; bg.style.height = vp.height + "px"; bg.style.bottom = "auto"; }
        bg.innerHTML = '<div class="info" role="dialog" aria-modal="true"><button type="button" class="x" aria-label="Fermer">✕</button><h3>Comment sont classés les résultats ?</h3>' +
          '<p>' + esc(info.organic) + '</p><p>' + esc(info.sponsored) + '</p><p>' + esc(info.eligibility) + '</p></div>';
        root.appendChild(bg);
        var close = function () { bg.remove(); };
        bg.onclick = function (e) { if (e.target === bg) close(); };
        bg.querySelector(".x").onclick = close;
      }).catch(function () {});
    }

    function bindShell() {
      var q = $("#km-q");
      $("#km-form").onsubmit = function (e) { e.preventDefault(); clearTimeout(S.timer); S.q = q.value.trim(); q.blur(); refresh(); };
      q.oninput = function () {
        $("#km-clear").classList.toggle("hidden", !q.value);
        clearTimeout(S.timer);
        S.timer = setTimeout(function () { var v = q.value.trim(); if (v !== S.q) { S.q = v; refresh(); } }, 350);
      };
      $("#km-clear").onclick = function () { q.value = ""; S.q = ""; this.classList.add("hidden"); refresh(); q.focus(); };
    }

    root.addEventListener("click", function (e) {
      var t = e.target.closest("[data-cat],[data-ranking],[data-retry]");
      if (!t || !root.contains(t)) return;
      if (t.hasAttribute("data-ranking")) return openRanking();
      if (t.hasAttribute("data-retry")) return load();
      S.cat = S.cat === t.getAttribute("data-cat") ? "" : t.getAttribute("data-cat");
      refresh();
    });

    // ── Démarrage ────────────────────────────────────────────
    if (opts.mode === "showcase") {
      shell();
      Promise.all([get("/popular?limit=" + opts.showcaseCount), get("/new?limit=" + opts.showcaseCount)]).then(function (r) {
        var seen = {}, items = [];
        r[0].items.concat(r[1].items).forEach(function (c) { if (!seen[c.slug] && items.length < opts.showcaseCount) { seen[c.slug] = 1; items.push(c); } });
        if (!items.length) return get("/search?sort=new&page_size=" + opts.showcaseCount).then(function (s) { return s.items; });
        return items;
      }).then(function (items) {
        $("#km-show").outerHTML = items.length ? '<div class="row">' + items.map(profileCard).join("") + '</div>' : '<div class="empty">Les premières vitrines arrivent bientôt.</div>';
      }).catch(function () { $("#km-show").outerHTML = '<div class="empty">Chargement impossible.</div>'; });
      return;
    }
    readUrl();
    get("/categories").then(function (r) { S.categories = r.items; }).catch(function () { S.categories = []; }).then(function () {
      shell();
      load();
    });
  }

  global.KitlyMarket = { version: 2, mount: mount };
})(window);
