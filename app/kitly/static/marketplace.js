/*
 * KITLY — marketplace (source unique).
 *
 * Servi par l'API : GET /kitly/static/marketplace.js
 * Utilisé par :
 *   - embeds/kitly/marketplace.html   page /marketplace, et mode « showcase » de la landing
 *   - embeds/kitly/home.html          accueil : « Crée ta vitrine » puis la marketplace
 *
 * Accueil (sans recherche), sur le modèle des grandes marketplaces :
 *   recherche + raccourcis populaires, grille de catégories, « Profils à la une » (cartes
 *   détaillées), « Dernières offres », « Nouveaux sur Kitly », bandeau de confiance.
 * Résultats (recherche, catégorie ou « Tout voir ») : catégories en filtre, tri, puis les
 * offres (chaque carte ouvre la fiche de l'offre sur la vitrine) et les profils.
 * Grilles qui passent à la ligne : rien ne dépasse du cadre sur téléphone.
 *
 * API :
 *   KitlyMarket.mount(el, opts)
 *     opts = {
 *       apiBase, mode: "full" | "showcase",
 *       profileUrl: "/profile#{slug}", marketplaceUrl,
 *       pageSize: 12,       // offres affichées avant « Voir plus »
 *       showcaseCount: 8,   // profils du mode showcase
 *       hero: true,         // logo + grand titre (false : titre compact, sans logo)
 *       syncUrl: true,      // recherche, catégorie et tri recopiés dans l'URL (?q=…&categorie=…&tri=…)
 *       floatingCart: false,// bouton « Panier » flottant (pages sans le header du site)
 *       title, subtitle     // textes de l'en-tête (facultatif)
 *     }
 *
 * Tout texte vient des freelances : il est systématiquement échappé.
 */
(function (global) {
  "use strict";
  if (global.KitlyMarket && global.KitlyMarket.version >= 4) return;

  var CSS = [
    ".km{box-sizing:border-box;--ink:#0a0a0a;--paper:#fff;--grey:#f5f5f4;--line:#e4e4e2;--mut:#6f6f6c;--serif:'Instrument Serif',Georgia,serif;--sans:'Inter',-apple-system,'Segoe UI',Arial,sans-serif;font-family:var(--sans);color:var(--ink);-webkit-font-smoothing:antialiased;width:100%;max-width:1160px;margin:0 auto;padding:8px 16px 48px;overflow-x:hidden;overflow-x:clip}",
    ".km *,.km *::before,.km *::after{box-sizing:border-box}",
    ".km button,.km select{font-family:inherit}.km .hidden{display:none!important}",
    /* en-tête */
    ".km .hero{text-align:center;padding:28px 0 6px}",
    ".km .logo{width:52px;height:52px;object-fit:contain;display:block;margin:0 auto 10px}",
    ".km h1{font-size:clamp(30px,6vw,52px);font-weight:900;letter-spacing:-.04em;line-height:1.05;margin:0}",
    ".km h1 em,.km h2 em{font-family:var(--serif);font-style:italic;font-weight:400;letter-spacing:-.01em}",
    ".km .sub{color:var(--mut);font-size:16px;line-height:1.5;margin:12px auto 0;max-width:540px}",
    ".km .search{display:flex;align-items:center;gap:10px;width:100%;max-width:640px;margin:22px auto 0;border:1px solid var(--line);border-radius:999px;padding:0 8px 0 18px;height:56px;background:var(--paper);box-shadow:0 10px 34px -20px rgba(0,0,0,.3);transition:border-color .15s}",
    ".km .search:focus-within{border-color:var(--ink)}",
    ".km .search svg{width:19px;height:19px;color:var(--mut);flex-shrink:0}",
    ".km .search input{flex:1;min-width:0;width:100%;border:0;outline:none;font:16px var(--sans);color:var(--ink);background:none;height:100%}",
    ".km .search input::-webkit-search-cancel-button{display:none}",
    ".km .search .clear{border:0;background:var(--grey);color:var(--mut);cursor:pointer;font-size:12px;width:30px;height:30px;border-radius:50%;flex-shrink:0}",
    ".km .pop{display:flex;flex-wrap:wrap;justify-content:center;align-items:center;gap:6px 8px;margin-top:14px;font-size:13px;color:var(--mut)}",
    ".km .pop button{border:1px solid var(--line);background:var(--paper);border-radius:99px;padding:5px 12px;font-size:12.5px;font-weight:600;color:var(--ink);cursor:pointer}",
    ".km .pop button:hover{border-color:var(--ink)}",
    /* sections */
    ".km .sec{margin-top:44px}",
    ".km h2+.sec{margin-top:22px}",
    ".km .sec-h{display:flex;align-items:flex-end;justify-content:space-between;gap:12px;margin-bottom:16px}",
    ".km h2{font-size:clamp(22px,3vw,28px);font-weight:900;letter-spacing:-.03em;margin:0}",
    ".km .sec-h p{margin:4px 0 0;color:var(--mut);font-size:13.5px}",
    ".km .more-link{border:0;background:none;padding:0;font:700 13.5px var(--sans);color:var(--ink);cursor:pointer;white-space:nowrap;border-bottom:1.5px solid var(--ink);text-decoration:none}",
    ".km .count{font-size:13.5px;color:var(--mut);font-weight:600;white-space:nowrap}",
    /* catégories */
    ".km .cats{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}",
    "@media(min-width:640px){.km .cats{grid-template-columns:repeat(3,minmax(0,1fr))}}",
    "@media(min-width:960px){.km .cats{grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}}",
    ".km .cat{display:flex;align-items:center;gap:12px;border:1px solid var(--line);border-radius:16px;padding:14px;background:var(--paper);text-align:left;cursor:pointer;transition:border-color .15s,transform .15s;min-width:0;color:var(--ink)}",
    ".km .cat:hover{border-color:var(--ink);transform:translateY(-2px)}",
    ".km .cat i{width:40px;height:40px;border-radius:12px;background:var(--grey);display:flex;align-items:center;justify-content:center;flex-shrink:0}",
    ".km .cat i svg{width:20px;height:20px}",
    ".km .cat b{display:block;font-size:14px;line-height:1.25;overflow-wrap:break-word;hyphens:auto}",
    "@media(max-width:639px){.km .cat{padding:12px;gap:10px}.km .cat i{width:34px;height:34px;border-radius:10px}.km .cat i svg{width:18px;height:18px}.km .cat b{font-size:13.5px}}",
    ".km .cat small{display:block;font-size:12px;color:var(--mut);margin-top:2px}",
    /* filtres (résultats) */
    ".km .bar{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:10px;margin-top:18px}",
    ".km .chips{display:flex;flex-wrap:wrap;gap:6px}",
    ".km .chip{border:1px solid var(--line);background:var(--paper);border-radius:999px;padding:7px 13px;font-size:13px;font-weight:600;color:var(--ink);cursor:pointer;white-space:nowrap}",
    ".km .chip:hover{border-color:var(--ink)}",
    "@media(max-width:639px){.km .bar{flex-wrap:nowrap;flex-direction:column;align-items:stretch}.km .chips{flex-wrap:nowrap;overflow-x:auto;margin:0 -16px;padding:0 16px 4px;scrollbar-width:none}.km .chips::-webkit-scrollbar{display:none}.km .sort{align-self:flex-start}}",
    ".km.in-results .pop{display:none}",
    ".km .chip.on{background:var(--ink);color:var(--paper);border-color:var(--ink)}",
    ".km .sort{border:1px solid var(--line);border-radius:999px;padding:8px 12px;font-size:13px;font-weight:600;background:var(--paper);color:var(--ink)}",
    ".km .back{border:0;background:none;padding:0;font:600 13.5px var(--sans);color:var(--mut);cursor:pointer;margin-top:22px}",
    ".km .back:hover{color:var(--ink)}",
    /* grilles */
    ".km .grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}",
    "@media(min-width:640px){.km .grid{grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}}",
    "@media(min-width:960px){.km .grid{grid-template-columns:repeat(4,minmax(0,1fr));gap:16px}}",
    ".km .pgrid{display:grid;grid-template-columns:minmax(0,1fr);gap:14px}",
    "@media(min-width:600px){.km .pgrid{grid-template-columns:repeat(2,minmax(0,1fr))}}",
    "@media(min-width:960px){.km .pgrid{grid-template-columns:repeat(4,minmax(0,1fr));gap:16px}}",
    /* téléphone : profils de l'accueil en rangée qui défile (dans le cadre, pas la page) */
    "@media(max-width:599px){.km .pgrid.rail{grid-auto-flow:column;grid-template-columns:none;grid-auto-columns:82%;overflow-x:auto;scroll-snap-type:x mandatory;margin:0 -16px;padding:2px 16px 10px;scroll-padding:0 16px;scrollbar-width:none}.km .pgrid.rail::-webkit-scrollbar{display:none}.km .pgrid.rail>*{scroll-snap-align:start}}",
    /* carte offre */
    ".km a.of,.km a.pc{min-width:0;text-decoration:none;color:inherit;background:var(--paper);border:1px solid var(--line);border-radius:18px;overflow:hidden;transition:border-color .15s,transform .15s,box-shadow .15s}",
    ".km a.of:hover,.km a.pc:hover{border-color:var(--ink);transform:translateY(-3px);box-shadow:0 16px 34px -22px rgba(0,0,0,.35)}",
    ".km .of{display:flex;flex-direction:column}",
    ".km .of .im{aspect-ratio:1;background:var(--grey) center/cover;display:flex;align-items:center;justify-content:center;color:var(--mut);position:relative}",
    ".km .of .im svg{width:28px;height:28px;opacity:.5}",
    ".km .of .bg{position:absolute;top:8px;left:8px;font-size:9.5px;font-weight:800;letter-spacing:.06em;text-transform:uppercase;background:var(--paper);padding:3px 8px;border-radius:99px;box-shadow:0 1px 4px rgba(0,0,0,.12)}",
    ".km .of .bd{padding:10px 12px 12px;display:flex;flex-direction:column;gap:3px;flex:1;min-width:0}",
    ".km .of .by{display:flex;align-items:center;gap:6px;font-size:11.5px;color:var(--mut);font-weight:600;min-width:0}",
    ".km .of .by span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}",
    ".km .of .tt{font-size:14px;font-weight:800;line-height:1.3;overflow-wrap:anywhere;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}",
    ".km .of .pr{font-size:14px;font-weight:800;margin-top:auto;padding-top:4px}",
    ".km .av{border-radius:50%;background:var(--ink) center/cover;color:var(--paper);display:flex;align-items:center;justify-content:center;font-weight:900;flex-shrink:0}",
    ".km .of .av{width:20px;height:20px;font-size:9px}",
    /* carte profil détaillée */
    ".km .pc{display:flex;flex-direction:column;position:relative}",
    ".km .pc-ban{height:96px;background:var(--grey) center/cover;position:relative}",
    ".km .pc-tags{position:absolute;top:8px;left:8px;display:flex;gap:5px}",
    ".km .pc-tags span{font-size:9.5px;font-weight:800;letter-spacing:.06em;text-transform:uppercase;background:var(--paper);color:var(--ink);padding:3px 8px;border-radius:99px;box-shadow:0 1px 4px rgba(0,0,0,.12)}",
    ".km .pc-tags span.dk{background:var(--ink);color:var(--paper)}",
    ".km .pc .av{width:60px;height:60px;font-size:22px;margin:-30px 0 0 14px;box-shadow:0 0 0 3px var(--paper);position:relative}",
    ".km .pc-bd{padding:8px 14px 0;display:flex;flex-direction:column;gap:3px;flex:1;min-width:0}",
    ".km .pc-nm{font-size:16px;font-weight:800;letter-spacing:-.01em;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}",
    ".km .pc-jb{font-size:13px;font-weight:600;color:#333;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}",
    ".km .pc-loc{display:flex;align-items:center;gap:4px;font-size:12px;color:var(--mut);font-weight:600;min-width:0}",
    ".km .pc-loc svg{width:13px;height:13px;flex-shrink:0}.km .pc-loc span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}",
    ".km .pc-bio{font-size:12.5px;color:#444;line-height:1.45;margin:6px 0 0;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}",
    ".km .pc-offers{display:flex;flex-direction:column;gap:5px;margin-top:10px}",
    ".km .pc-offers div{display:flex;justify-content:space-between;gap:8px;font-size:12px;border:1px solid var(--line);border-radius:10px;padding:6px 9px}",
    ".km .pc-offers span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:600}",
    ".km .pc-offers b{white-space:nowrap}",
    ".km .pc-ft{display:flex;justify-content:space-between;align-items:center;gap:8px;padding:12px 14px 14px;margin-top:10px;border-top:1px solid var(--line);font-size:12.5px}",
    ".km .pc-ft .pr{color:var(--mut);font-weight:600}.km .pc-ft .pr b{color:var(--ink)}",
    ".km .pc-ft .go{font-weight:800;white-space:nowrap}",
    /* confiance */
    ".km .trust{display:grid;grid-template-columns:minmax(0,1fr);gap:14px;margin-top:48px;border:1px solid var(--line);border-radius:22px;padding:22px;background:var(--grey)}",
    "@media(min-width:760px){.km .trust{grid-template-columns:repeat(3,minmax(0,1fr));gap:20px;padding:26px 28px}}",
    ".km .trust>div{display:flex;gap:12px;align-items:flex-start}",
    ".km .trust i{width:36px;height:36px;border-radius:50%;background:var(--paper);display:flex;align-items:center;justify-content:center;flex-shrink:0}",
    ".km .trust i svg{width:18px;height:18px}",
    ".km .trust b{display:block;font-size:14px}.km .trust small{display:block;font-size:12.5px;color:var(--mut);line-height:1.45;margin-top:2px}",
    /* divers */
    ".km .btn{display:inline-flex;align-items:center;justify-content:center;font-weight:700;font-size:14px;padding:11px 20px;border-radius:999px;border:1px solid var(--ink);background:var(--paper);color:var(--ink);cursor:pointer;text-decoration:none;white-space:nowrap}",
    ".km .more{display:flex;justify-content:center;margin-top:20px}",
    ".km .empty{text-align:center;border:1px solid var(--line);border-radius:18px;padding:28px 16px;color:var(--mut);font-size:14px}",
    ".km .link{border:0;background:none;padding:0;font:600 12px var(--sans);color:var(--mut);cursor:pointer;text-decoration:underline}",
    ".km .rank{text-align:center;margin-top:30px}",
    ".km .skel{border-radius:18px;aspect-ratio:3/4;background:linear-gradient(90deg,#f5f5f4,#fbfbfa,#f5f5f4);background-size:200% 100%;animation:km-sh 1.2s infinite}",
    ".km .pgrid .skel{aspect-ratio:auto;height:300px}",
    "@keyframes km-sh{to{background-position:-200% 0}}",
    ".km .info-bg{position:fixed;inset:0;background:rgba(10,10,10,.5);z-index:99;display:flex;align-items:center;justify-content:center;padding:16px}",
    ".km .info{background:var(--paper);border-radius:20px;max-width:520px;width:100%;padding:24px;position:relative;max-height:92%;overflow-y:auto}",
    ".km .info h3{margin:0 30px 12px 0;font-size:19px;font-weight:900}",
    ".km .info p{font-size:14px;line-height:1.6;color:#333;margin:0 0 12px}",
    ".km .info .x{position:absolute;top:14px;right:14px;width:32px;height:32px;border-radius:50%;border:1px solid var(--line);background:var(--paper);cursor:pointer}",
    ".km.compact .hero{padding-top:8px}"
  ].join("\n");

  function injectCss() {
    if (document.getElementById("kitly-market-css")) return;
    var st = document.createElement("style");
    st.id = "kitly-market-css";
    st.textContent = CSS;
    document.head.appendChild(st);
  }

  // Fichiers servis par l'API à côté de ce script : logo Kitly, panier partagé.
  var HERE = (function () { try { return (document.currentScript && document.currentScript.src) || ""; } catch (e) { return ""; } })();
  function sibling(name) { try { if (HERE) return new URL(name, HERE).href; } catch (e) {} return "/kitly/static/" + name; }
  var LOGO_SRC = sibling("logo.png"), CART_SRC = sibling("cart.js");

  function ico(path) { return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + path + '</svg>'; }
  var I = {
    search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
    pin: '<path d="M12 21s-7-6.2-7-11.5a7 7 0 0 1 14 0C19 14.8 12 21 12 21z"/><circle cx="12" cy="9.5" r="2.5"/>',
    chat: '<path d="M4 5h16v11H8l-4 4z"/>',
    lock: '<rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
    bag: '<path d="M5 7h14l-1 13H6z"/><path d="M9 7a3 3 0 0 1 6 0"/>',
    physical: '<path d="m3 7 9-4 9 4v10l-9 4-9-4z"/><path d="m3 7 9 4 9-4M12 11v10"/>',
    digital: '<path d="M14 3H6v18h12V7z"/><path d="M14 3v4h4M9 13h6M9 17h6"/>',
    service: '<rect x="3" y="7" width="18" height="13" rx="2"/><path d="M8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M3 13h18"/>',
    appointment: '<rect x="3" y="5" width="18" height="16" rx="3"/><path d="M8 3v4M16 3v4M3 10h18"/>'
  };
  var CAT_ICON = {
    design: '<path d="M12 3a9 9 0 1 0 0 18c1 0 1.5-.7 1.5-1.5 0-1.2-1-1.5-1-2.5s.8-1.5 1.8-1.5H17a4 4 0 0 0 4-4c0-4.4-4-8.5-9-8.5z"/><circle cx="7.5" cy="11" r="1"/><circle cx="10" cy="7" r="1"/><circle cx="15" cy="7.5" r="1"/>',
    marketing: '<path d="M3 11v2a1 1 0 0 0 1 1h3l6 4V6L7 10H4a1 1 0 0 0-1 1z"/><path d="M17 8a5 5 0 0 1 0 8"/>',
    dev: '<path d="m8 8-4 4 4 4M16 8l4 4-4 4M14 5l-4 14"/>',
    photo_video: '<path d="M3 8h4l2-3h6l2 3h4v12H3z"/><circle cx="12" cy="13" r="4"/>',
    conseil: '<path d="M9 18h6M10 21h4"/><path d="M12 3a6 6 0 0 0-3.5 10.9c.6.5 1 1.2 1 2.1h5c0-.9.4-1.6 1-2.1A6 6 0 0 0 12 3z"/>',
    redaction: '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="m13 7 4 4"/>',
    formation: '<path d="m2 9 10-5 10 5-10 5z"/><path d="M6 11v5c3 2 9 2 12 0v-5"/>',
    artisanat: '<path d="M12 2v6M8 4l4 4 4-4"/><path d="M5 10h14l-2 11H7z"/>',
    autres: '<circle cx="5" cy="12" r="1.5"/><circle cx="12" cy="12" r="1.5"/><circle cx="19" cy="12" r="1.5"/>'
  };
  var SORT = { relevance: "Pertinence", "new": "Nouveautés", price_asc: "Prix croissant" };

  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }
  function cssUrl(u) { return u ? "url('" + String(u).replace(/['"\\()\s]/g, encodeURIComponent) + "')" : "none"; }
  function initial(name) { return esc((name || "?").charAt(0).toUpperCase()); }
  function eur(c) { return (c / 100).toFixed(2).replace(".", ",").replace(",00", "") + " €"; }

  // Cadre Framer plus large que la zone qui l'affiche (fréquent sur téléphone) :
  // on le ramène, lui et ses conteneurs trop larges, à la largeur disponible.
  function fitToHost(frameEl) {
    try {
      var el = frameEl, win = el.ownerDocument.defaultView;
      for (var i = 0; i < 6 && el && el.parentElement && el !== el.ownerDocument.body; i++) {
        var parent = el.parentElement, cs = win.getComputedStyle(parent);
        var room = parent.clientWidth - (parseFloat(cs.paddingLeft) || 0) - (parseFloat(cs.paddingRight) || 0);
        if (room > 200 && el.getBoundingClientRect().width > room + 1) {
          el.style.width = "100%"; el.style.maxWidth = "100%"; el.style.minWidth = "0"; el.style.flexShrink = "1";
        }
        el = parent;
      }
    } catch (e) {}
  }

  function mount(root, opts) {
    injectCss();
    opts = Object.assign({ mode: "full", pageSize: 12, showcaseCount: 8, hero: true, syncUrl: true, floatingCart: false,
                           profileUrl: "/profile#{slug}", marketplaceUrl: "/marketplace" }, opts || {});
    var apiBase = (opts.apiBase || "").replace(/\/$/, "");
    root.classList.add("km");
    if (!root.getAttribute("lang")) root.setAttribute("lang", "fr");   // césure des mots longs
    if (!opts.hero) root.classList.add("compact");

    var HOST = window, frameEl = null;
    try { if (window.frameElement && window.parent.document) { HOST = window.parent; frameEl = window.frameElement; } } catch (e) {}
    if (frameEl) {
      fitToHost(frameEl);
      setTimeout(function () { fitToHost(frameEl); }, 600); setTimeout(function () { fitToHost(frameEl); }, 2000);
      try { HOST.addEventListener("resize", function () { setTimeout(function () { fitToHost(frameEl); }, 50); }); } catch (e) {}
    }
    if (opts.floatingCart) {   // sinon : icône panier du header du site
      var sc = document.createElement("script");
      sc.src = CART_SRC; sc.onload = function () { try { window.KitlyCart.mountButton({}); } catch (e) {} };
      document.head.appendChild(sc);
    }

    var S = { q: "", cat: "", sort: "relevance", all: false, categories: [], offers: [], offersTotal: 0, offersPage: 1,
              profiles: [], profilesTotal: 0, profilesPage: 1, timer: null, reqId: 0 };

    function $(sel) { return root.querySelector(sel); }
    function get(path) {
      return fetch(apiBase + "/kitly/marketplace" + path).then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      });
    }
    // Lien d'une vitrine (ou d'une offre), ?src=marketplace avant le hash : /profile?src=marketplace#lea/offre-12
    function profileUrl(slug, offerId) {
      var u = opts.profileUrl.replace("{slug}", encodeURIComponent(slug));
      var i = u.indexOf("#"), hash = i >= 0 ? u.slice(i) : "";
      if (i >= 0) u = u.slice(0, i);
      if (offerId) hash = hash ? hash + "/offre-" + offerId : "#offre-" + offerId;
      return u + (u.indexOf("?") >= 0 ? "&" : "?") + "src=marketplace" + hash;
    }
    function catLabel(key) {
      for (var i = 0; i < S.categories.length; i++) if (S.categories[i].key === key) return S.categories[i].label;
      return "";
    }
    function results() { return !!(S.q || S.cat || S.all || S.sort !== "relevance"); }
    function qs(extra) {
      var p = new URLSearchParams();
      if (S.q) p.set("q", S.q);
      if (S.cat) p.set("category", S.cat);
      Object.keys(extra || {}).forEach(function (k) { p.set(k, extra[k]); });
      return p.toString();
    }
    function offersSort() { return S.sort === "new" ? "new" : S.sort === "price_asc" ? "price_asc" : "relevance"; }

    // ── URL de la page parente (lien partageable) ─────────────
    function readUrl() {
      if (!opts.syncUrl) return;
      try {
        var p = new URLSearchParams(HOST.location.search);
        S.q = p.get("q") || ""; S.cat = p.get("categorie") || ""; S.sort = SORT[p.get("tri")] ? p.get("tri") : "relevance";
      } catch (e) {}
    }
    function writeUrl() {
      if (!opts.syncUrl || opts.mode !== "full") return;
      try {
        var p = new URLSearchParams(HOST.location.search);
        var set = function (k, v) { if (v) p.set(k, v); else p.delete(k); };
        set("q", S.q); set("categorie", S.cat); set("tri", S.sort === "relevance" ? "" : S.sort);
        ["lieu", "type", "budget"].forEach(function (k) { p.delete(k); });   // anciens filtres
        var s = p.toString();
        HOST.history.replaceState(HOST.history.state, "", HOST.location.pathname + (s ? "?" + s : "") + HOST.location.hash);
      } catch (e) {}
    }

    // ── Cartes ───────────────────────────────────────────────
    function offerCard(o) {
      var p = o.profile || {};
      return '<a class="of" href="' + esc(profileUrl(p.slug, o.id)) + '" target="_top">' +
        '<div class="im" style="background-image:' + cssUrl(o.image) + '">' + (o.image ? "" : ico(I[o.kind] || I.service)) +
          (o.badge ? '<span class="bg">' + esc(o.badge) + '</span>' : '') + '</div>' +
        '<div class="bd"><div class="by"><span class="av" style="background-image:' + cssUrl(p.avatar_url) + '">' + (p.avatar_url ? "" : initial(p.display_name)) + '</span><span>' + esc(p.display_name) + '</span></div>' +
          '<div class="tt">' + esc(o.title) + '</div>' + (o.price_label ? '<div class="pr">' + esc(o.price_label) + '</div>' : '') + '</div></a>';
    }
    function profileCard(c) {
      var tags = [];
      if (c.sponsored) tags.push('<span class="dk">Sponsorisé</span>');
      if (c.is_new) tags.push('<span>Nouveau</span>');
      if (c.badge) tags.push('<span>' + esc(c.badge) + '</span>');
      var loc = [c.location, catLabel(c.category)].filter(Boolean).join(" · ");
      var offers = (c.top_offers || []).slice(0, 2).map(function (o) {
        return '<div><span>' + esc(o.title) + '</span><b>' + esc(o.price_label) + '</b></div>';
      }).join("");
      var price = c.starting_price_cents != null
        ? (c.starting_price_cents === 0 ? "Offres <b>gratuites</b>" : "À partir de <b>" + esc(eur(c.starting_price_cents)) + "</b>") : "Sur demande";
      return '<a class="pc" href="' + esc(profileUrl(c.slug)) + '" target="_top">' +
        '<div class="pc-ban" style="background-image:' + cssUrl(c.banner_url) + '">' + (tags.length ? '<div class="pc-tags">' + tags.join("") + '</div>' : '') + '</div>' +
        '<span class="av" style="background-image:' + cssUrl(c.avatar_url) + '">' + (c.avatar_url ? "" : initial(c.display_name)) + '</span>' +
        '<div class="pc-bd"><div class="pc-nm">' + esc(c.display_name) + '</div>' +
          (c.headline ? '<div class="pc-jb">' + esc(c.headline) + '</div>' : '') +
          (loc ? '<div class="pc-loc">' + ico(I.pin) + '<span>' + esc(loc) + '</span></div>' : '') +
          (c.bio ? '<p class="pc-bio">' + esc(c.bio) + '</p>' : '') +
          (offers ? '<div class="pc-offers">' + offers + '</div>' : '') + '</div>' +
        '<div class="pc-ft"><span class="pr">' + price + '</span><span class="go">Voir le profil →</span></div></a>';
    }
    function skeletons(n) { var h = ""; for (var i = 0; i < n; i++) h += '<div class="skel"></div>'; return h; }
    function sec(id, title, sub, more) {
      return '<section class="sec" id="' + id + '"><div class="sec-h"><div><h2>' + title + '</h2>' + (sub ? '<p>' + esc(sub) + '</p>' : '') + '</div>' +
        (more || '<span class="count"></span>') + '</div><div class="body"></div></section>';
    }
    function fill(id, html, count) {
      var el = $("#" + id); if (!el) return;
      el.querySelector(".body").innerHTML = html;
      var c = el.querySelector(".count"); if (c && count != null) c.textContent = count;
    }

    // ── Squelette ────────────────────────────────────────────
    function shell() {
      if (opts.mode === "showcase") {
        root.innerHTML = '<section class="sec" style="margin-top:0" id="km-show"><div class="sec-h"><div><h2>' + (opts.title || "Ils sont sur <em>Kitly</em>") + '</h2></div>' +
          '<a class="more-link" href="' + esc(opts.marketplaceUrl) + '" target="_top">Tout voir</a></div><div class="body"><div class="pgrid">' + skeletons(4) + '</div></div></section>';
        return;
      }
      var popular = S.categories.filter(function (c) { return c.count; }).sort(function (a, b) { return b.count - a.count; }).slice(0, 5);
      root.innerHTML =
        '<div class="hero">' + (opts.hero ? '<img class="logo" src="' + LOGO_SRC + '" alt="Kitly" onerror="this.remove()">' : '') +
          '<h1>' + (opts.title || "Trouve le bon <em>freelance</em>") + '</h1>' +
          '<p class="sub">' + esc(opts.subtitle || "Des freelances, leurs offres et leurs prix. Contacte-les directement ou commande en ligne.") + '</p>' +
          '<form class="search" id="km-form" role="search">' + ico(I.search) +
            '<input type="search" id="km-q" placeholder="Une offre, un métier, un nom…" value="' + esc(S.q) + '" autocomplete="off" enterkeyhint="search" aria-label="Rechercher">' +
            '<button type="button" class="clear' + (S.q ? "" : " hidden") + '" id="km-clear" aria-label="Effacer">✕</button></form>' +
          (popular.length ? '<div class="pop"><span>Populaire :</span>' + popular.map(function (c) {
            return '<button type="button" data-cat="' + esc(c.key) + '">' + esc(c.label) + '</button>'; }).join("") + '</div>' : '') +
        '</div><div id="km-body"></div>' +
        '<p class="rank"><button type="button" class="link" data-ranking>Comment sont classés les résultats ?</button></p>';
      var q = $("#km-q");
      $("#km-form").onsubmit = function (e) { e.preventDefault(); clearTimeout(S.timer); S.q = q.value.trim(); q.blur(); refresh(); };
      q.oninput = function () {
        $("#km-clear").classList.toggle("hidden", !q.value);
        clearTimeout(S.timer);
        S.timer = setTimeout(function () { var v = q.value.trim(); if (v !== S.q) { S.q = v; refresh(); } }, 350);
      };
      $("#km-clear").onclick = function () { q.value = ""; S.q = ""; this.classList.add("hidden"); refresh(); q.focus(); };
    }

    // ── Accueil ──────────────────────────────────────────────
    function renderHome() {
      var cats = S.categories.filter(function (c) { return c.count; });
      $("#km-body").innerHTML =
        (cats.length ? '<section class="sec"><div class="sec-h"><div><h2>Explorer par <em>catégorie</em></h2></div></div><div class="cats">' + cats.map(function (c) {
          return '<button type="button" class="cat" data-cat="' + esc(c.key) + '"><i>' + ico(CAT_ICON[c.key] || CAT_ICON.autres) + '</i><span style="min-width:0"><b>' + esc(c.label) + '</b>' +
            '<small>' + c.count + ' profil' + (c.count > 1 ? "s" : "") + '</small></span></button>';
        }).join("") + '</div></section>' : '') +
        sec("km-top", "Profils à la <em>une</em>", "Les vitrines les plus consultées ces dernières semaines.", '<button type="button" class="more-link" data-all="popular">Tout voir</button>') +
        sec("km-offers", "Dernières <em>offres</em>", "Prestations, produits et créations publiés récemment.", '<button type="button" class="more-link" data-all="new">Tout voir</button>') +
        sec("km-new", "Nouveaux sur <em>Kitly</em>", "Ils viennent de publier leur vitrine.") +
        '<div class="trust">' +
          '<div><i>' + ico(I.chat) + '</i><span><b>Contact direct</b><small>Échange avec le freelance sans intermédiaire : questions, devis et rendez-vous.</small></span></div>' +
          '<div><i>' + ico(I.lock) + '</i><span><b>Paiement sécurisé</b><small>Les commandes en ligne sont payées par carte via Stripe.</small></span></div>' +
          '<div><i>' + ico(I.bag) + '</i><span><b>Un seul panier</b><small>Commande chez plusieurs freelances et paie en une fois.</small></span></div>' +
        '</div>';
      fill("km-top", '<div class="pgrid">' + skeletons(4) + '</div>');
      fill("km-offers", '<div class="grid">' + skeletons(4) + '</div>');
      fill("km-new", '<div class="pgrid">' + skeletons(4) + '</div>');
      var rid = ++S.reqId;
      Promise.all([get("/popular?limit=8"), get("/new?limit=8")]).then(function (r) {
        if (rid !== S.reqId) return;
        var top = r[0].items, seen = {};
        top.forEach(function (c) { seen[c.slug] = 1; });
        var fresh = r[1].items.filter(function (c) { return !seen[c.slug]; });
        if (!top.length) { top = fresh.slice(0, 8); fresh = []; }
        top = top.slice(0, top.length >= 8 ? 8 : 4);   // rangées complètes sur ordinateur
        fill("km-top", top.length ? '<div class="pgrid rail">' + top.map(profileCard).join("") + '</div>' : '<div class="empty">Les premières vitrines arrivent bientôt.</div>');
        var nw = $("#km-new");
        if (fresh.length) fill("km-new", '<div class="pgrid rail">' + fresh.slice(0, 4).map(profileCard).join("") + '</div>'); else if (nw) nw.remove();
      }).catch(function () { if (rid === S.reqId) fill("km-top", '<div class="empty">Chargement impossible. <button type="button" class="link" data-retry>Réessayer</button></div>'); });
      get("/offers?sort=new&page_size=8").then(function (r) {
        if (rid !== S.reqId) return;
        fill("km-offers", r.items.length ? '<div class="grid">' + r.items.map(offerCard).join("") + '</div>' : '<div class="empty">Les premières offres arrivent bientôt.</div>');
      }).catch(function () { if (rid === S.reqId) fill("km-offers", '<div class="empty">Chargement impossible.</div>'); });
    }

    // ── Résultats ────────────────────────────────────────────
    function resultsTitle() {
      if (S.q) return "Résultats pour « <em>" + esc(S.q) + "</em> »";
      if (S.cat) return esc(catLabel(S.cat)) || "Résultats";
      if (S.sort === "new") return "Les <em>nouveautés</em>";
      return "Tous les <em>freelances</em>";
    }
    function renderResults() {
      var cats = S.categories.filter(function (c) { return c.count || c.key === S.cat; });
      $("#km-body").innerHTML =
        '<button type="button" class="back" data-home>← Accueil de la marketplace</button>' +
        '<div class="bar"><div class="chips"><button type="button" class="chip' + (S.cat ? "" : " on") + '" data-cat="">Tout</button>' + cats.map(function (c) {
          return '<button type="button" class="chip' + (S.cat === c.key ? " on" : "") + '" data-cat="' + esc(c.key) + '">' + esc(c.label) + '</button>';
        }).join("") + '</div><select class="sort" id="km-sort" aria-label="Trier">' + Object.keys(SORT).map(function (k) {
          return '<option value="' + k + '"' + (S.sort === k ? " selected" : "") + '>' + SORT[k] + '</option>'; }).join("") + '</select></div>' +
        '<h2 style="margin-top:24px">' + resultsTitle() + '</h2>' +
        sec("km-offers", "Offres", "") + sec("km-profiles", "Profils", "");
      $("#km-sort").onchange = function () { S.sort = this.value; S.all = true; refresh(); };
      fill("km-offers", '<div class="grid">' + skeletons(4) + '</div>');
      fill("km-profiles", '<div class="pgrid">' + skeletons(4) + '</div>');
      S.offersPage = S.profilesPage = 1;
      var rid = ++S.reqId;
      get("/offers?" + qs({ sort: offersSort(), page_size: opts.pageSize })).then(function (r) {
        if (rid !== S.reqId) return;
        S.offers = r.items; S.offersTotal = r.total; drawOffers();
      }).catch(function () { if (rid === S.reqId) fill("km-offers", '<div class="empty">Chargement impossible. <button type="button" class="link" data-retry>Réessayer</button></div>'); });
      get("/search?" + qs({ sort: S.sort, page_size: 12 })).then(function (r) {
        if (rid !== S.reqId) return;
        S.profiles = (r.sponsored || []).concat(r.items); S.profilesTotal = r.total; drawProfiles();
      }).catch(function () { if (rid === S.reqId) fill("km-profiles", '<div class="empty">Chargement impossible.</div>'); });
    }
    function plural(n, w) { return n ? n + " " + w + (n > 1 ? "s" : "") : ""; }
    function drawOffers() {
      var html = S.offers.length ? '<div class="grid">' + S.offers.map(offerCard).join("") + '</div>' : '<div class="empty">Aucune offre ne correspond.</div>';
      if (S.offers.length < S.offersTotal) html += '<div class="more"><button type="button" class="btn" data-more="offers">Voir plus d\'offres</button></div>';
      fill("km-offers", html, plural(S.offersTotal, "offre"));
    }
    function drawProfiles() {
      var organic = S.profiles.filter(function (c) { return !c.sponsored; }).length;
      var html = S.profiles.length ? '<div class="pgrid">' + S.profiles.map(profileCard).join("") + '</div>' : '<div class="empty">Aucun profil ne correspond.</div>';
      if (organic < S.profilesTotal) html += '<div class="more"><button type="button" class="btn" data-more="profiles">Voir plus de profils</button></div>';
      fill("km-profiles", html, plural(S.profilesTotal, "profil"));
    }
    function loadMore(kind, btn) {
      btn.disabled = true; btn.textContent = "Chargement…";
      var rid = S.reqId;
      if (kind === "offers") {
        get("/offers?" + qs({ sort: offersSort(), page: S.offersPage + 1, page_size: opts.pageSize })).then(function (r) {
          if (rid !== S.reqId) return;
          S.offersPage += 1; S.offers = S.offers.concat(r.items); S.offersTotal = r.total; drawOffers();
        }).catch(function () { btn.disabled = false; btn.textContent = "Réessayer"; });
      } else {
        get("/search?" + qs({ sort: S.sort, page: S.profilesPage + 1, page_size: 12 })).then(function (r) {
          if (rid !== S.reqId) return;
          S.profilesPage += 1; S.profiles = S.profiles.concat(r.items); S.profilesTotal = r.total; drawProfiles();
        }).catch(function () { btn.disabled = false; btn.textContent = "Réessayer"; });
      }
    }

    function refresh() {
      writeUrl();
      root.classList.toggle("in-results", results());
      if (results()) renderResults(); else renderHome();
    }
    function scrollTop() {
      try { if (frameEl) HOST.scrollTo({ top: HOST.scrollY + frameEl.getBoundingClientRect().top + root.getBoundingClientRect().top - 80, behavior: "smooth" }); } catch (e) {}
    }

    function openRanking() {
      get("/ranking-info").then(function (info) {
        var bg = document.createElement("div");
        bg.className = "info-bg";
        if (frameEl) {
          var r = frameEl.getBoundingClientRect(), top = Math.max(0, -r.top), bottom = Math.min(r.height, HOST.innerHeight - r.top);
          bg.style.position = "absolute"; bg.style.top = top + "px"; bg.style.height = Math.max(240, bottom - top) + "px"; bg.style.bottom = "auto";
        }
        bg.innerHTML = '<div class="info" role="dialog" aria-modal="true"><button type="button" class="x" aria-label="Fermer">✕</button><h3>Comment sont classés les résultats ?</h3>' +
          '<p>' + esc(info.organic) + '</p><p>' + esc(info.sponsored) + '</p><p>' + esc(info.eligibility) + '</p></div>';
        root.appendChild(bg);
        var close = function () { bg.remove(); };
        bg.onclick = function (e) { if (e.target === bg) close(); };
        bg.querySelector(".x").onclick = close;
      }).catch(function () {});
    }

    root.addEventListener("click", function (e) {
      var t = e.target.closest("[data-cat],[data-ranking],[data-retry],[data-all],[data-home],[data-more]");
      if (!t || !root.contains(t)) return;
      if (t.hasAttribute("data-ranking")) return openRanking();
      if (t.hasAttribute("data-retry")) return refresh();
      if (t.hasAttribute("data-more")) return loadMore(t.getAttribute("data-more"), t);
      if (t.hasAttribute("data-home")) {
        S.q = S.cat = ""; S.all = false; S.sort = "relevance";
        var q = $("#km-q"); if (q) q.value = "";
        refresh(); return scrollTop();
      }
      if (t.hasAttribute("data-all")) { S.all = true; S.sort = t.getAttribute("data-all") === "new" ? "new" : "relevance"; refresh(); return scrollTop(); }
      var c = t.getAttribute("data-cat");
      S.cat = c;
      if (!c) S.all = true;   // « Tout » : reste sur les résultats
      refresh();
      if (t.classList.contains("cat") || t.closest(".pop")) scrollTop();
    });

    // ── Démarrage ────────────────────────────────────────────
    if (opts.mode === "showcase") {
      shell();
      get("/categories").then(function (r) { S.categories = r.items; }).catch(function () {}).then(function () {
        return Promise.all([get("/popular?limit=" + opts.showcaseCount), get("/new?limit=" + opts.showcaseCount)]);
      }).then(function (r) {
        var seen = {}, items = [];
        r[0].items.concat(r[1].items).forEach(function (c) { if (!seen[c.slug] && items.length < opts.showcaseCount) { seen[c.slug] = 1; items.push(c); } });
        return items.length ? items : get("/search?sort=new&page_size=" + opts.showcaseCount).then(function (s) { return s.items; });
      }).then(function (items) {
        fill("km-show", items.length ? '<div class="pgrid">' + items.map(profileCard).join("") + '</div>' : '<div class="empty">Les premières vitrines arrivent bientôt.</div>');
      }).catch(function () { fill("km-show", '<div class="empty">Chargement impossible.</div>'); });
      return;
    }
    readUrl();
    get("/categories").then(function (r) { S.categories = r.items; }).catch(function () { S.categories = []; }).then(function () {
      shell();
      refresh();
    });
  }

  global.KitlyMarket = { version: 4, mount: mount };
})(window);
