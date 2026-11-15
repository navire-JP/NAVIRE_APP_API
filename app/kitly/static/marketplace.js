/*
 * KITLY — marketplace (source unique).
 *
 * Servi par l'API : GET /kitly/static/marketplace.js
 * Utilisé par :
 *   - embeds/kitly/marketplace.html   page /marketplace, et mode « showcase » de la landing
 *   - embeds/kitly/home.html          accueil : « Crée ta vitrine » puis la marketplace
 *
 * API :
 *   KitlyMarket.mount(el, opts)
 *     opts = {
 *       apiBase, mode: "full" | "showcase",
 *       profileUrl: "https://kitly.fr/{slug}", marketplaceUrl, registerUrl,
 *       pageSize: 24, showcaseCount: 8,
 *       hero: true,       // en-tête « Trouve le bon freelance » (mode full)
 *       ctaBand: true,    // bandeau « Tu es freelance ? »
 *       syncUrl: true,    // filtres recopiés dans l'URL de la page parente
 *       title, subtitle   // textes de l'en-tête (facultatif)
 *     }
 *
 * Tout texte vient des freelances : il est systématiquement échappé.
 */
(function (global) {
  "use strict";
  if (global.KitlyMarket && global.KitlyMarket.version >= 1) return;

  var CSS = ".km {\n    box-sizing: border-box;\n    --ink: #0a0a0a; --paper: #ffffff; --grey: #f5f5f4; --line: #e4e4e2; --mut: #6f6f6c;\n    --serif: 'Instrument Serif', Georgia, serif;\n    --sans: 'Inter', -apple-system, 'Segoe UI', Arial, sans-serif;\n    font-family: var(--sans); color: var(--ink); -webkit-font-smoothing: antialiased;\n    max-width: 1160px; margin: 0 auto; padding: 8px 20px 48px;\n  }\n  .km button { font-family: inherit; }\n  .km .hidden { display: none !important; }\n  /* en-tête */\n  .km .hero { text-align: center; padding: 36px 0 24px; }\n  .km .kicker { font-size: 12px; font-weight: 800; letter-spacing: .14em; text-transform: uppercase; color: var(--mut); margin: 0 0 12px; }\n  .km h1 { font-size: clamp(30px, 5vw, 48px); font-weight: 900; letter-spacing: -.04em; line-height: 1.08; margin: 0; }\n  .km h1 em, .km h2 em { font-family: var(--serif); font-style: italic; font-weight: 400; letter-spacing: -.01em; }\n  .km .hero p { color: var(--mut); font-size: 16px; margin: 12px auto 0; max-width: 520px; line-height: 1.5; }\n  /* recherche */\n  .km .search { display: flex; align-items: center; gap: 8px; max-width: 680px; margin: 26px auto 0; border: 1px solid var(--line); border-radius: 999px; padding: 6px 6px 6px 18px; background: var(--paper); box-shadow: 0 8px 30px -18px rgba(0,0,0,.25); transition: border-color .15s; }\n  .km .search:focus-within { border-color: var(--ink); }\n  .km .search svg { width: 18px; height: 18px; color: var(--mut); flex-shrink: 0; }\n  .km .search input { flex: 1; min-width: 0; border: 0; outline: none; font: 15.5px var(--sans); color: var(--ink); background: none; padding: 10px 0; }\n  .km .search .clear { border: 0; background: none; color: var(--mut); cursor: pointer; font-size: 14px; padding: 6px; }\n  .km .btn { display: inline-flex; align-items: center; justify-content: center; gap: 7px; font-weight: 700; font-size: 14px; padding: 12px 22px; border-radius: 999px; border: 1px solid var(--ink); background: var(--ink); color: var(--paper); cursor: pointer; text-decoration: none; white-space: nowrap; transition: .15s; }\n  .km .btn:hover { background: #2a2a2a; }\n  .km .btn.ghost { background: var(--paper); color: var(--ink); }\n  .km .btn.ghost:hover { background: var(--grey); }\n  /* filtres */\n  .km .cats { display: flex; gap: 8px; overflow-x: auto; scrollbar-width: none; padding: 22px 0 4px; justify-content: safe center; }\n  .km .cats::-webkit-scrollbar { display: none; }\n  .km .chip { flex-shrink: 0; border: 1px solid var(--line); background: var(--paper); border-radius: 999px; padding: 8px 14px; font-size: 13px; font-weight: 700; color: var(--ink); cursor: pointer; transition: .15s; white-space: nowrap; }\n  .km .chip:hover { border-color: var(--ink); }\n  .km .chip.on { background: var(--ink); color: var(--paper); border-color: var(--ink); }\n  .km .chip small { font-weight: 600; opacity: .55; margin-left: 4px; }\n  .km .filters { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; justify-content: center; margin-top: 12px; }\n  .km .f { display: flex; align-items: center; gap: 6px; border: 1px solid var(--line); border-radius: 999px; padding: 0 6px 0 14px; background: var(--paper); height: 40px; }\n  .km .f:focus-within { border-color: var(--ink); }\n  .km .f label { font-size: 12px; font-weight: 700; color: var(--mut); white-space: nowrap; }\n  .km .f input, .km .f select { border: 0; outline: none; background: none; font: 600 13.5px var(--sans); color: var(--ink); height: 38px; min-width: 0; }\n  .km .f input { width: 120px; }\n  .km .f select { padding-right: 4px; cursor: pointer; }\n  .km .reset { border: 0; background: none; font-size: 13px; font-weight: 700; color: var(--mut); cursor: pointer; text-decoration: underline; }\n  /* sections */\n  .km .sec { margin-top: 44px; }\n  .km .sec-h { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; margin-bottom: 16px; flex-wrap: wrap; }\n  .km h2 { font-size: clamp(22px, 3vw, 28px); font-weight: 900; letter-spacing: -.03em; margin: 0; }\n  .km .sec-h p { margin: 4px 0 0; color: var(--mut); font-size: 13.5px; }\n  .km .count { font-size: 13.5px; color: var(--mut); font-weight: 600; }\n  .km .link { border: 0; background: none; padding: 0; font: 700 13px var(--sans); color: var(--ink); cursor: pointer; border-bottom: 1px solid var(--ink); }\n  .km .muted-link { color: var(--mut); border-bottom-color: var(--line); font-weight: 600; }\n  /* cartes */\n  .km .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(250px, 1fr)); gap: 18px; }\n  .km .row { display: grid; grid-auto-flow: column; grid-auto-columns: minmax(250px, 1fr); gap: 18px; overflow-x: auto; scrollbar-width: none; scroll-snap-type: x mandatory; padding-bottom: 6px; }\n  .km .row::-webkit-scrollbar { display: none; }\n  .km .row > * { scroll-snap-align: start; }\n  @media (min-width: 1100px) { .km .row { grid-auto-columns: calc((100% - 54px) / 4); } }\n  .km .card { display: flex; flex-direction: column; border: 1px solid var(--line); border-radius: 18px; overflow: hidden; background: var(--paper); text-decoration: none; color: inherit; transition: transform .15s, box-shadow .15s, border-color .15s; position: relative; min-width: 0; }\n  .km .card:hover { transform: translateY(-3px); border-color: var(--ink); box-shadow: 0 14px 30px -18px rgba(0,0,0,.25); }\n  .km .card .ban { height: 76px; background: var(--grey) center/cover; }\n  .km .card .av { width: 58px; height: 58px; border-radius: 50%; background: var(--ink) center/cover; color: var(--paper); display: flex; align-items: center; justify-content: center; font-weight: 900; font-size: 22px; margin: -29px 0 0 16px; box-shadow: 0 0 0 3px var(--paper); position: relative; }\n  .km .card .bd { padding: 10px 16px 16px; display: flex; flex-direction: column; flex: 1; }\n  .km .card .nm { font-size: 16px; font-weight: 800; letter-spacing: -.01em; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }\n  .km .card .jb { font-size: 12.5px; color: var(--mut); font-weight: 600; margin-top: 2px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }\n  .km .card .bio { font-size: 13px; color: #444; line-height: 1.45; margin: 10px 0 0; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }\n  .km .card .offers { margin-top: 12px; display: flex; flex-direction: column; gap: 6px; }\n  .km .card .of { display: flex; justify-content: space-between; gap: 10px; font-size: 12.5px; border: 1px solid var(--line); border-radius: 10px; padding: 7px 10px; }\n  .km .card .of span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 600; }\n  .km .card .of b { white-space: nowrap; }\n  .km .card .ft { margin-top: auto; padding-top: 14px; display: flex; justify-content: space-between; align-items: center; font-size: 12.5px; font-weight: 700; }\n  .km .card .ft .pr { color: var(--mut); font-weight: 600; }\n  .km .tags { position: absolute; top: 10px; left: 10px; display: flex; gap: 6px; }\n  .km .tag { font-size: 10px; font-weight: 800; letter-spacing: .06em; text-transform: uppercase; background: var(--paper); color: var(--ink); padding: 4px 9px; border-radius: 99px; box-shadow: 0 1px 4px rgba(0,0,0,.12); }\n  .km .tag.dark { background: var(--ink); color: var(--paper); }\n  .km .sponsored { border: 1px dashed #cfcfcc; border-radius: 22px; padding: 16px; margin-bottom: 22px; }\n  .km .sponsored-h { display: flex; justify-content: space-between; align-items: center; font-size: 11.5px; font-weight: 800; letter-spacing: .1em; text-transform: uppercase; color: var(--mut); margin-bottom: 12px; }\n  .km .catgrid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 12px; }\n  .km .cat { border: 1px solid var(--line); border-radius: 16px; padding: 18px; text-align: left; background: var(--paper); cursor: pointer; transition: .15s; }\n  .km .cat:hover { border-color: var(--ink); transform: translateY(-2px); }\n  .km .cat b { display: block; font-size: 15px; }\n  .km .cat span { font-size: 12.5px; color: var(--mut); }\n  .km .more { display: flex; justify-content: center; margin-top: 26px; }\n  .km .empty { text-align: center; border: 1px solid var(--line); border-radius: 20px; padding: 40px 20px; color: var(--mut); font-size: 14.5px; }\n  .km .empty b { display: block; color: var(--ink); font-size: 18px; margin-bottom: 6px; }\n  .km .cta-band { margin-top: 56px; background: var(--ink); color: var(--paper); border-radius: 24px; padding: 32px; display: flex; align-items: center; justify-content: space-between; gap: 20px; flex-wrap: wrap; }\n  .km .cta-band h3 { font-size: 24px; font-weight: 900; letter-spacing: -.03em; margin: 0; }\n  .km .cta-band h3 em { font-family: var(--serif); font-style: italic; font-weight: 400; }\n  .km .cta-band p { margin: 6px 0 0; color: #b5b5b2; font-size: 14px; }\n  .km .cta-band .btn { background: var(--paper); color: var(--ink); border-color: var(--paper); }\n  .km .skel { border: 1px solid var(--line); border-radius: 18px; height: 260px; background: linear-gradient(90deg, #f5f5f4, #fbfbfa, #f5f5f4); background-size: 200% 100%; animation: km-sh 1.2s infinite; }\n  @keyframes km-sh { to { background-position: -200% 0; } }\n  .km .info-bg { position: fixed; inset: 0; background: rgba(10,10,10,.5); z-index: 99; display: flex; align-items: center; justify-content: center; padding: 16px; }\n  .km .info { background: var(--paper); border-radius: 20px; max-width: 520px; width: 100%; padding: 26px; position: relative; max-height: 92%; overflow-y: auto; }\n  .km .info h3 { margin: 0 30px 12px 0; font-size: 20px; font-weight: 900; letter-spacing: -.02em; }\n  .km .info p { font-size: 14px; line-height: 1.6; color: #333; margin: 0 0 12px; }\n  .km .info .x { position: absolute; top: 14px; right: 14px; width: 32px; height: 32px; border-radius: 50%; border: 1px solid var(--line); background: var(--paper); cursor: pointer; }\n  /* vitrine de landing */\n  .km.showcase .hero { padding-top: 8px; }\n  @media (max-width: 640px) {\n    .km { padding: 4px 16px 36px; }\n    .km .hero { padding: 20px 0 14px; }\n    .km .filters { justify-content: flex-start; }\n    .km .f { flex: 1 1 calc(50% - 10px); }\n    .km .f input, .km .f select { width: 100%; }\n    .km .search .btn { padding: 11px 16px; }\n    .km .cats { justify-content: flex-start; }\n    .km .cta-band { padding: 24px; }\n    .km .catgrid { grid-template-columns: 1fr 1fr; gap: 8px; }\n    .km .cat { padding: 13px 14px; }\n    .km .cat b { font-size: 13.5px; }\n  }\n.km *, .km *::before, .km *::after { box-sizing: border-box; }\n.km .hero.compact { padding: 8px 0 4px; }\n.km .hero.compact h2 { font-size: clamp(24px, 3.4vw, 34px); }\n.km .hero.compact .search { margin-top: 18px; }";

  function injectCss() {
    if (document.getElementById("kitly-market-css")) return;
    var st = document.createElement("style");
    st.id = "kitly-market-css";
    st.textContent = CSS;
    document.head.appendChild(st);
  }

  var KIND_LABEL = { service: "Prestations", appointment: "Rendez-vous", physical: "Produits physiques", digital: "Produits numériques" };
  var SORT_LABEL = { relevance: "Pertinence", "new": "Nouveaux profils", popular: "Les plus consultés", price_asc: "Prix croissant" };
  var SEARCH_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>';

  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }
  function cssUrl(u) { return u ? "url('" + String(u).replace(/['"\\()\s]/g, encodeURIComponent) + "')" : "none"; }
  function eur(cents) { return (cents / 100).toFixed(2).replace(".", ",").replace(",00", "") + " €"; }

  function mount(root, opts) {
    injectCss();
    opts = Object.assign({ mode: "full", pageSize: 24, showcaseCount: 8, hero: true, ctaBand: true, syncUrl: true,
                           profileUrl: "https://kitly.fr/{slug}", marketplaceUrl: "https://kitly.fr/marketplace",
                           registerUrl: "https://kitly.fr/connexion?mode=inscription" }, opts || {});
    var apiBase = (opts.apiBase || "").replace(/\/$/, "");
    root.classList.add("km");
    if (opts.mode === "showcase") root.classList.add("showcase");

    var HOST = window, frameEl = null;
    try { if (window.frameElement && window.parent.document) { HOST = window.parent; frameEl = window.frameElement; } } catch (e) {}

    var S = { q: "", cat: "", loc: "", kind: "", budget: "", sort: "relevance", page: 1, total: 0, items: [], sponsored: [], categories: [], timer: null, reqId: 0 };

    function $(sel) { return root.querySelector(sel); }
    function get(path) {
      return fetch(apiBase + "/kitly/marketplace" + path).then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      });
    }
    function profileUrl(slug) {
      var u = opts.profileUrl.replace("{slug}", encodeURIComponent(slug));
      return u + (u.indexOf("?") >= 0 ? "&" : "?") + "src=marketplace";
    }
    function catLabel(key) {
      for (var i = 0; i < S.categories.length; i++) if (S.categories[i].key === key) return S.categories[i].label;
      return "";
    }
    function filtering() { return !!(S.q || S.cat || S.loc || S.kind || S.budget || S.sort !== "relevance"); }

    // ── URL de la page parente (lien partageable) ─────────────
    function readUrl() {
      if (!opts.syncUrl) return;
      try {
        var p = new URLSearchParams(HOST.location.search);
        S.q = p.get("q") || ""; S.cat = p.get("categorie") || ""; S.loc = p.get("lieu") || "";
        S.kind = p.get("type") || ""; S.budget = p.get("budget") || ""; S.sort = SORT_LABEL[p.get("tri")] ? p.get("tri") : "relevance";
      } catch (e) {}
    }
    function writeUrl() {
      if (!opts.syncUrl || opts.mode !== "full") return;
      try {
        var p = new URLSearchParams(HOST.location.search);
        var set = function (k, v) { if (v) p.set(k, v); else p.delete(k); };
        set("q", S.q); set("categorie", S.cat); set("lieu", S.loc); set("type", S.kind); set("budget", S.budget);
        set("tri", S.sort === "relevance" ? "" : S.sort);
        var qs = p.toString();
        HOST.history.replaceState(HOST.history.state, "", HOST.location.pathname + (qs ? "?" + qs : "") + HOST.location.hash);
      } catch (e) {}
    }

    // ── Cartes ───────────────────────────────────────────────
    function card(c) {
      var tags = [];
      if (c.sponsored) tags.push('<span class="tag dark">Sponsorisé</span>');
      if (c.is_new) tags.push('<span class="tag">Nouveau</span>');
      var meta = [c.headline, c.location].filter(Boolean).join(" · ");
      var offers = (c.top_offers || []).slice(0, 2).map(function (o) {
        return '<div class="of"><span>' + esc(o.title) + '</span><b>' + esc(o.price_label) + '</b></div>';
      }).join("");
      var price = c.starting_price_cents != null ? (c.starting_price_cents === 0 ? "Offres gratuites" : "À partir de " + eur(c.starting_price_cents)) : esc(catLabel(c.category));
      return '<a class="card" href="' + esc(profileUrl(c.slug)) + '" target="_top" aria-label="' + esc(c.display_name) + '">' +
        '<div class="ban" style="background-image:' + cssUrl(c.banner_url) + '"></div>' +
        (tags.length ? '<div class="tags">' + tags.join("") + '</div>' : '') +
        '<div class="av" style="background-image:' + cssUrl(c.avatar_url) + '">' + (c.avatar_url ? "" : esc((c.display_name || "?").charAt(0).toUpperCase())) + '</div>' +
        '<div class="bd"><div class="nm">' + esc(c.display_name) + '</div>' +
        (meta ? '<div class="jb">' + esc(meta) + '</div>' : '') +
        (c.bio ? '<p class="bio">' + esc(c.bio) + '</p>' : '') +
        (offers ? '<div class="offers">' + offers + '</div>' : '') +
        '<div class="ft"><span class="pr">' + price + '</span><span>Voir le profil →</span></div></div></a>';
    }
    function skeletons(n) { var h = ""; for (var i = 0; i < n; i++) h += '<div class="skel"></div>'; return h; }
    function fillRow(sel, items, emptyText) {
      var el = $(sel);
      if (!el) return;
      if (!items.length) { el.outerHTML = '<div class="empty">' + esc(emptyText) + '</div>'; return; }
      el.innerHTML = items.map(card).join("");
    }
    function rankingLink() {
      return '<p style="text-align:center;margin-top:28px"><button type="button" class="link muted-link" data-ranking>Comment sont classés les résultats ?</button></p>';
    }

    // ── Squelette de page ─────────────────────────────────────
    function shell() {
      if (opts.mode === "showcase") {
        root.innerHTML = '<div class="sec" style="margin-top:0"><div class="sec-h"><div><h2>' + (opts.title || "Ils sont sur <em>Kitly</em>") + '</h2><p>' +
          esc(opts.subtitle || "Des freelances qui présentent leurs offres et trouvent de nouveaux clients.") + '</p></div>' +
          '<a class="btn ghost" href="' + esc(opts.marketplaceUrl) + '" target="_top">Découvrir la marketplace →</a></div><div class="row" id="km-show">' + skeletons(4) + '</div></div>';
        return;
      }
      var cats = '<button type="button" class="chip' + (S.cat ? "" : " on") + '" data-cat="">Tous</button>' + S.categories.map(function (c) {
        return '<button type="button" class="chip' + (S.cat === c.key ? " on" : "") + '" data-cat="' + esc(c.key) + '">' + esc(c.label) + (c.count ? '<small>' + c.count + '</small>' : '') + '</button>';
      }).join("");
      var searchForm = '<form class="search" id="km-form" role="search">' + SEARCH_ICON +
        '<input type="search" id="km-q" placeholder="Quel professionnel recherchez-vous ?" value="' + esc(S.q) + '" autocomplete="off" enterkeyhint="search">' +
        '<button type="button" class="clear' + (S.q ? "" : " hidden") + '" id="km-clear" aria-label="Effacer">✕</button>' +
        '<button type="submit" class="btn">Rechercher</button></form>';
      root.innerHTML =
        (opts.hero
          ? '<div class="hero"><p class="kicker">Marketplace Kitly</p><h1>' + (opts.title || "Trouve le bon <em>freelance</em>") + '</h1>' +
            '<p>' + esc(opts.subtitle || "Graphistes, développeurs, photographes, coachs… Compare leurs offres et contacte-les directement.") + '</p>' + searchForm + '</div>'
          : '<div class="hero compact">' + (opts.title ? '<h2>' + opts.title + '</h2>' : '') + (opts.subtitle ? '<p>' + esc(opts.subtitle) + '</p>' : '') + searchForm + '</div>') +
        '<div class="cats" id="km-cats">' + cats + '</div>' +
        '<div class="filters">' +
        '<div class="f"><label for="km-loc">Lieu</label><input id="km-loc" type="text" placeholder="Ville, région…" value="' + esc(S.loc) + '"></div>' +
        '<div class="f"><label for="km-kind">Type</label><select id="km-kind"><option value="">Tout</option>' +
        Object.keys(KIND_LABEL).map(function (k) { return '<option value="' + k + '"' + (S.kind === k ? " selected" : "") + '>' + KIND_LABEL[k] + '</option>'; }).join("") + '</select></div>' +
        '<div class="f"><label for="km-budget">Budget max</label><select id="km-budget"><option value="">Tous</option>' +
        [50, 100, 250, 500, 1000, 2500].map(function (b) { return '<option value="' + b + '"' + (String(S.budget) === String(b) ? " selected" : "") + '>' + b + ' €</option>'; }).join("") + '</select></div>' +
        '<div class="f"><label for="km-sort">Tri</label><select id="km-sort">' +
        Object.keys(SORT_LABEL).map(function (k) { return '<option value="' + k + '"' + (S.sort === k ? " selected" : "") + '>' + SORT_LABEL[k] + '</option>'; }).join("") + '</select></div>' +
        '<button type="button" class="reset' + (filtering() ? "" : " hidden") + '" id="km-reset">Réinitialiser</button></div>' +
        '<div id="km-body"></div>' +
        (opts.ctaBand
          ? '<div class="cta-band"><div><h3>Tu es <em>freelance</em> ?</h3><p>Crée ta vitrine gratuitement et apparais dans la marketplace avec Premium+.</p></div>' +
            '<a class="btn" href="' + esc(opts.registerUrl) + '" target="_top">Créer ma vitrine</a></div>'
          : '');
      bindShell();
    }

    function renderHome() {
      var body = $("#km-body");
      body.innerHTML =
        '<div class="sec"><div class="sec-h"><div><h2>Les nouveaux sur <em>Kitly</em></h2><p>Ils viennent de publier leur vitrine.</p></div></div><div class="row" id="km-new">' + skeletons(4) + '</div></div>' +
        '<div class="sec"><div class="sec-h"><div><h2>Les profils <em>populaires</em></h2><p>Les vitrines les plus consultées ces dernières semaines.</p></div>' +
        '<button type="button" class="link" data-sort="popular">Tout voir</button></div><div class="row" id="km-pop">' + skeletons(4) + '</div></div>' +
        '<div class="sec"><div class="sec-h"><div><h2>Parcourir par <em>catégorie</em></h2></div></div><div class="catgrid">' +
        S.categories.map(function (c) {
          return '<button type="button" class="cat" data-cat="' + esc(c.key) + '"><b>' + esc(c.label) + '</b><span>' + (c.count ? c.count + " profil" + (c.count > 1 ? "s" : "") : "Bientôt des profils") + '</span></button>';
        }).join("") + '</div></div>' + rankingLink();
      var rid = ++S.reqId;
      Promise.all([get("/new?limit=12"), get("/popular?limit=12")]).then(function (r) {
        if (rid !== S.reqId) return;
        fillRow("#km-new", r[0].items, "Aucun nouveau profil cette semaine.");
        fillRow("#km-pop", r[1].items, "Le classement se construit avec les premières visites.");
      }).catch(function () {
        if (rid !== S.reqId) return;
        fillRow("#km-new", [], "Chargement impossible."); fillRow("#km-pop", [], "Chargement impossible.");
      });
    }

    function resultsTitle() {
      if (S.q) return "Résultats pour « <em>" + esc(S.q) + "</em> »";
      if (S.cat) return esc(catLabel(S.cat)) || "Résultats";
      if (S.sort === "popular") return "Les profils <em>populaires</em>";
      if (S.sort === "new") return "Les <em>nouveaux</em> profils";
      return "Tous les <em>freelances</em>";
    }

    function search(append) {
      var body = $("#km-body");
      var rid = ++S.reqId;
      if (!append) {
        S.page = 1;
        body.innerHTML = '<div class="sec"><div class="sec-h"><div><h2>' + resultsTitle() + '</h2></div><span class="count">Recherche…</span></div><div class="grid">' + skeletons(6) + '</div></div>';
      }
      var p = new URLSearchParams();
      if (S.q) p.set("q", S.q);
      if (S.cat) p.set("category", S.cat);
      if (S.loc) p.set("location", S.loc);
      if (S.kind) p.set("kind", S.kind);
      if (S.budget) p.set("budget_max_cents", String(parseInt(S.budget, 10) * 100));
      p.set("sort", S.sort); p.set("page", String(S.page)); p.set("page_size", String(opts.pageSize));
      get("/search?" + p.toString()).then(function (r) {
        if (rid !== S.reqId) return;
        S.total = r.total;
        S.items = append ? S.items.concat(r.items) : r.items;
        if (!append) S.sponsored = r.sponsored || [];
        renderResults();
      }).catch(function () {
        if (rid !== S.reqId) return;
        body.innerHTML = '<div class="empty"><b>Oups</b>La recherche n\'a pas abouti. <button type="button" class="link" id="km-retry">Réessayer</button></div>';
        $("#km-retry").onclick = function () { search(false); };
      });
    }

    function renderResults() {
      var spons = S.sponsored.length
        ? '<div class="sponsored"><div class="sponsored-h"><span>Sponsorisé</span><button type="button" class="link muted-link" data-ranking>Pourquoi ?</button></div><div class="grid">' + S.sponsored.map(card).join("") + '</div></div>'
        : "";
      var list = S.items.length
        ? '<div class="grid">' + S.items.map(card).join("") + '</div>'
        : '<div class="empty"><b>Aucun freelance ne correspond</b>Essaie un autre mot-clé ou retire un filtre.<br><br><button type="button" class="btn ghost" id="km-reset2">Réinitialiser les filtres</button></div>';
      var more = S.items.length < S.total ? '<div class="more"><button type="button" class="btn ghost" id="km-more">Voir plus de profils</button></div>' : "";
      $("#km-body").innerHTML = '<div class="sec"><div class="sec-h"><div><h2>' + resultsTitle() + '</h2></div><span class="count">' +
        S.total + " profil" + (S.total > 1 ? "s" : "") + '</span></div>' + spons + list + more + rankingLink() + '</div>';
      var m = $("#km-more");
      if (m) m.onclick = function () { m.disabled = true; m.textContent = "Chargement…"; S.page += 1; search(true); };
      var r2 = $("#km-reset2"); if (r2) r2.onclick = resetAll;
    }

    function refresh() {
      writeUrl();
      var reset = $("#km-reset"); if (reset) reset.classList.toggle("hidden", !filtering());
      root.querySelectorAll("#km-cats .chip").forEach(function (c) { c.classList.toggle("on", c.getAttribute("data-cat") === S.cat); });
      if (filtering()) search(false); else renderHome();
    }

    function resetAll() {
      S.q = S.cat = S.loc = S.kind = S.budget = ""; S.sort = "relevance";
      shell(); renderHome(); writeUrl();
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
        S.timer = setTimeout(function () { var v = q.value.trim(); if (v !== S.q) { S.q = v; refresh(); } }, 400);
      };
      $("#km-clear").onclick = function () { q.value = ""; S.q = ""; this.classList.add("hidden"); refresh(); q.focus(); };
      var loc = $("#km-loc");
      loc.oninput = function () { clearTimeout(S.timer); S.timer = setTimeout(function () { S.loc = loc.value.trim(); refresh(); }, 450); };
      $("#km-kind").onchange = function () { S.kind = this.value; refresh(); };
      $("#km-budget").onchange = function () { S.budget = this.value; refresh(); };
      $("#km-sort").onchange = function () { S.sort = this.value; refresh(); };
      $("#km-reset").onclick = resetAll;
    }

    root.addEventListener("click", function (e) {
      var t = e.target.closest("[data-cat],[data-sort],[data-ranking]");
      if (!t || !root.contains(t)) return;
      if (t.hasAttribute("data-ranking")) return openRanking();
      if (t.hasAttribute("data-cat")) {
        S.cat = t.getAttribute("data-cat");
        if (t.classList.contains("cat")) { var top = $("#km-cats"); if (top && top.scrollIntoView) top.scrollIntoView({ behavior: "smooth", block: "start" }); }
      }
      if (t.hasAttribute("data-sort")) { S.sort = t.getAttribute("data-sort"); var s = $("#km-sort"); if (s) s.value = S.sort; }
      refresh();
    });

    // ── Démarrage ────────────────────────────────────────────
    if (opts.mode === "showcase") {
      shell();
      Promise.all([get("/popular?limit=" + opts.showcaseCount), get("/new?limit=" + opts.showcaseCount)]).then(function (r) {
        var seen = {}, items = [];
        r[0].items.concat(r[1].items).forEach(function (c) { if (!seen[c.slug] && items.length < opts.showcaseCount) { seen[c.slug] = 1; items.push(c); } });
        if (!items.length) return get("/search?sort=new&page_size=" + opts.showcaseCount).then(function (s) { fillRow("#km-show", s.items, "Les premières vitrines arrivent bientôt."); });
        fillRow("#km-show", items, "");
      }).catch(function () { fillRow("#km-show", [], "Chargement impossible."); });
      return;
    }
    readUrl();
    get("/categories").then(function (r) { S.categories = r.items; }).catch(function () { S.categories = []; }).then(function () {
      shell();
      if (filtering()) search(false); else renderHome();
    });
  }

  global.KitlyMarket = { version: 1, mount: mount };
})(window);
