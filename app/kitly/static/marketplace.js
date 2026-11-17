/*
 * KITLY — marketplace (source unique).
 *
 * Servi par l'API : GET /kitly/static/marketplace.js
 * Utilisé par :
 *   - embeds/kitly/marketplace.html   page /marketplace, et mode « showcase » de la landing
 *   - embeds/kitly/home.html          accueil : « Crée ta vitrine » puis la marketplace
 *
 * Page minimale : logo, titre, une recherche, puis deux sections en grille —
 * « Offres » (chaque carte ouvre la fiche de l'offre sur la vitrine) et
 * « Profils » (cartes compactes vers la vitrine). Deux colonnes sur téléphone,
 * aucun défilement horizontal : rien ne dépasse du cadre.
 *
 * API :
 *   KitlyMarket.mount(el, opts)
 *     opts = {
 *       apiBase, mode: "full" | "showcase",
 *       profileUrl: "/profile#{slug}", marketplaceUrl,
 *       pageSize: 12,       // offres affichées avant « Voir plus d'offres »
 *       showcaseCount: 8,   // profils du mode showcase
 *       hero: true,         // logo + grand titre (false : sans logo)
 *       syncUrl: true,      // recherche recopiée dans l'URL de la page parente (?q=…)
 *       title               // titre (facultatif)
 *     }
 *
 * Tout texte vient des freelances : il est systématiquement échappé.
 */
(function (global) {
  "use strict";
  if (global.KitlyMarket && global.KitlyMarket.version >= 3) return;

  var CSS = [
    ".km{box-sizing:border-box;--ink:#0a0a0a;--paper:#fff;--grey:#f5f5f4;--line:#e4e4e2;--mut:#6f6f6c;--serif:'Instrument Serif',Georgia,serif;--sans:'Inter',-apple-system,'Segoe UI',Arial,sans-serif;font-family:var(--sans);color:var(--ink);-webkit-font-smoothing:antialiased;width:100%;max-width:1120px;margin:0 auto;padding:8px 16px 40px;overflow-x:hidden;overflow-x:clip}",
    ".km *,.km *::before,.km *::after{box-sizing:border-box}",
    ".km button{font-family:inherit}.km .hidden{display:none!important}",
    ".km .hero{text-align:center;padding:24px 0 4px}",
    ".km .logo{width:56px;height:56px;object-fit:contain;display:block;margin:0 auto 8px}",
    ".km h1{font-size:clamp(28px,7vw,46px);font-weight:900;letter-spacing:-.04em;line-height:1.08;margin:0}",
    ".km h1 em{font-family:var(--serif);font-style:italic;font-weight:400;letter-spacing:-.01em}",
    ".km .search{display:flex;align-items:center;gap:10px;width:100%;max-width:560px;margin:20px auto 0;border:1px solid var(--line);border-radius:999px;padding:0 8px 0 16px;height:50px;background:var(--paper);transition:border-color .15s}",
    ".km .search:focus-within{border-color:var(--ink)}",
    ".km .search svg{width:18px;height:18px;color:var(--mut);flex-shrink:0}",
    ".km .search input{flex:1;min-width:0;width:100%;border:0;outline:none;font:16px var(--sans);color:var(--ink);background:none;height:100%}",
    ".km .search input::-webkit-search-cancel-button{display:none}",
    ".km .search .clear{border:0;background:var(--grey);color:var(--mut);cursor:pointer;font-size:12px;width:28px;height:28px;border-radius:50%;flex-shrink:0}",
    ".km .sec{margin-top:34px}",
    ".km .sec-h{display:flex;align-items:baseline;justify-content:space-between;gap:12px;margin-bottom:14px}",
    ".km h2{font-size:22px;font-weight:900;letter-spacing:-.03em;margin:0}",
    ".km .count{font-size:13px;color:var(--mut);font-weight:600;white-space:nowrap}",
    ".km .grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}",
    "@media(min-width:640px){.km .grid{grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}.km .grid.pfs{grid-template-columns:repeat(4,minmax(0,1fr))}}",
    "@media(min-width:960px){.km .grid{grid-template-columns:repeat(4,minmax(0,1fr));gap:16px}.km .grid.pfs{grid-template-columns:repeat(6,minmax(0,1fr))}}",
    ".km a.of,.km a.pf{min-width:0;text-decoration:none;color:inherit;background:var(--paper);border:1px solid var(--line);border-radius:16px;overflow:hidden;transition:border-color .15s,transform .15s}",
    ".km a.of:hover,.km a.pf:hover{border-color:var(--ink);transform:translateY(-2px)}",
    ".km .of{display:flex;flex-direction:column}",
    ".km .of .im{aspect-ratio:1;background:var(--grey) center/cover;display:flex;align-items:center;justify-content:center;color:var(--mut)}",
    ".km .of .im svg{width:28px;height:28px;opacity:.5}",
    ".km .of .bd{padding:10px 11px 12px;min-width:0}",
    ".km .of .tt{font-size:14px;font-weight:800;line-height:1.3;overflow-wrap:anywhere;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}",
    ".km .of .pr{font-size:13.5px;font-weight:800;margin-top:3px}",
    ".km .of .by{font-size:12px;color:var(--mut);font-weight:600;margin-top:6px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}",
    ".km .pf{display:flex;flex-direction:column;align-items:center;text-align:center;padding:16px 10px 14px;position:relative}",
    ".km .av{border-radius:50%;background:var(--ink) center/cover;color:var(--paper);display:flex;align-items:center;justify-content:center;font-weight:900;flex-shrink:0}",
    ".km .pf .av{width:56px;height:56px;font-size:22px;margin-bottom:8px}",
    ".km .pf .nm{font-size:14px;font-weight:800;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}",
    ".km .pf .jb{font-size:12px;color:var(--mut);font-weight:600;margin-top:2px;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}",
    ".km .pf .tag{position:absolute;top:8px;left:8px;font-size:9px;font-weight:800;letter-spacing:.06em;text-transform:uppercase;background:var(--ink);color:var(--paper);padding:3px 7px;border-radius:99px}",
    ".km .btn{display:inline-flex;align-items:center;justify-content:center;font-weight:700;font-size:14px;padding:11px 20px;border-radius:999px;border:1px solid var(--ink);background:var(--paper);color:var(--ink);cursor:pointer;text-decoration:none;white-space:nowrap}",
    ".km .more{display:flex;justify-content:center;margin-top:18px}",
    ".km .empty{text-align:center;border:1px solid var(--line);border-radius:16px;padding:26px 16px;color:var(--mut);font-size:14px}",
    ".km .link{border:0;background:none;padding:0;font:600 12px var(--sans);color:var(--mut);cursor:pointer;text-decoration:underline}",
    ".km .rank{text-align:center;margin-top:28px}",
    ".km .skel{border-radius:16px;aspect-ratio:3/4;background:linear-gradient(90deg,#f5f5f4,#fbfbfa,#f5f5f4);background-size:200% 100%;animation:km-sh 1.2s infinite}",
    ".km .pfs .skel{aspect-ratio:1}",
    "@keyframes km-sh{to{background-position:-200% 0}}",
    ".km .info-bg{position:fixed;inset:0;background:rgba(10,10,10,.5);z-index:99;display:flex;align-items:center;justify-content:center;padding:16px}",
    ".km .info{background:var(--paper);border-radius:20px;max-width:520px;width:100%;padding:24px;position:relative;max-height:92%;overflow-y:auto}",
    ".km .info h3{margin:0 30px 12px 0;font-size:19px;font-weight:900}",
    ".km .info p{font-size:14px;line-height:1.6;color:#333;margin:0 0 12px}",
    ".km .info .x{position:absolute;top:14px;right:14px;width:32px;height:32px;border-radius:50%;border:1px solid var(--line);background:var(--paper);cursor:pointer}"
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
    opts = Object.assign({ mode: "full", pageSize: 12, showcaseCount: 8, hero: true, syncUrl: true,
                           profileUrl: "/profile#{slug}", marketplaceUrl: "/marketplace" }, opts || {});
    var apiBase = (opts.apiBase || "").replace(/\/$/, "");
    root.classList.add("km");

    var HOST = window, frameEl = null;
    try { if (window.frameElement && window.parent.document) { HOST = window.parent; frameEl = window.frameElement; } } catch (e) {}
    if (frameEl) {
      fitToHost(frameEl);
      setTimeout(function () { fitToHost(frameEl); }, 600); setTimeout(function () { fitToHost(frameEl); }, 2000);
      try { HOST.addEventListener("resize", function () { setTimeout(function () { fitToHost(frameEl); }, 50); }); } catch (e) {}
    }

    var S = { q: "", offers: [], total: 0, page: 1, timer: null, reqId: 0 };

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
    function readUrl() {
      if (!opts.syncUrl) return;
      try { S.q = new URLSearchParams(HOST.location.search).get("q") || ""; } catch (e) {}
    }
    function writeUrl() {
      if (!opts.syncUrl || opts.mode !== "full") return;
      try {
        var p = new URLSearchParams(HOST.location.search);
        if (S.q) p.set("q", S.q); else p.delete("q");
        ["categorie", "lieu", "type", "budget", "tri"].forEach(function (k) { p.delete(k); });   // anciens filtres
        var qs = p.toString();
        HOST.history.replaceState(HOST.history.state, "", HOST.location.pathname + (qs ? "?" + qs : "") + HOST.location.hash);
      } catch (e) {}
    }

    function offerCard(o) {
      var p = o.profile || {};
      return '<a class="of" href="' + esc(profileUrl(p.slug, o.id)) + '" target="_top">' +
        '<div class="im" style="background-image:' + cssUrl(o.image) + '">' +
          (o.image ? "" : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' + (KIND_ICON[o.kind] || KIND_ICON.service) + '</svg>') + '</div>' +
        '<div class="bd"><div class="tt">' + esc(o.title) + '</div>' +
          (o.price_label ? '<div class="pr">' + esc(o.price_label) + '</div>' : '') +
          '<div class="by">' + esc(p.display_name) + '</div></div></a>';
    }
    function profileCard(c) {
      return '<a class="pf" href="' + esc(profileUrl(c.slug)) + '" target="_top">' +
        (c.sponsored ? '<span class="tag">Sponsorisé</span>' : '') +
        '<span class="av" style="background-image:' + cssUrl(c.avatar_url) + '">' + (c.avatar_url ? "" : initial(c.display_name)) + '</span>' +
        '<span class="nm">' + esc(c.display_name) + '</span>' +
        ((c.headline || c.location) ? '<span class="jb">' + esc(c.headline || c.location) + '</span>' : '') + '</a>';
    }
    function skeletons(n) { var h = ""; for (var i = 0; i < n; i++) h += '<div class="skel"></div>'; return h; }

    function shell() {
      if (opts.mode === "showcase") {
        root.innerHTML = '<div class="sec" style="margin-top:0"><div class="sec-h"><h2>' + (opts.title || "Ils sont sur Kitly") + '</h2>' +
          '<a class="btn" href="' + esc(opts.marketplaceUrl) + '" target="_top">Tout voir</a></div><div class="grid pfs" id="km-show">' + skeletons(4) + '</div></div>';
        return;
      }
      root.innerHTML =
        '<div class="hero">' + (opts.hero ? '<img class="logo" src="' + LOGO_SRC + '" alt="Kitly" onerror="this.remove()">' : '') +
          '<h1>' + (opts.title || "Trouve le bon <em>freelance</em>") + '</h1>' +
          '<form class="search" id="km-form" role="search">' + SEARCH_ICON +
            '<input type="search" id="km-q" placeholder="Une offre, un métier, un nom…" value="' + esc(S.q) + '" autocomplete="off" enterkeyhint="search" aria-label="Rechercher">' +
            '<button type="button" class="clear' + (S.q ? "" : " hidden") + '" id="km-clear" aria-label="Effacer">✕</button></form></div>' +
        '<section class="sec" id="km-offers"><div class="sec-h"><h2>Offres</h2><span class="count"></span></div><div class="body"></div></section>' +
        '<section class="sec" id="km-profiles"><div class="sec-h"><h2>Profils</h2><span class="count"></span></div><div class="body"></div></section>' +
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

    function section(id, count, html) {
      var sec = $(id); if (!sec) return;
      sec.querySelector(".count").textContent = count;
      sec.querySelector(".body").innerHTML = html;
    }
    function plural(n, word) { return S.q && n ? n + " " + word + (n > 1 ? "s" : "") : ""; }

    function load() {
      var rid = ++S.reqId, qs = S.q ? "q=" + encodeURIComponent(S.q) + "&" : "";
      S.page = 1;
      section("#km-offers", "", '<div class="grid">' + skeletons(4) + '</div>');
      section("#km-profiles", "", '<div class="grid pfs">' + skeletons(4) + '</div>');
      get("/offers?" + qs + "page_size=" + opts.pageSize).then(function (r) {
        if (rid !== S.reqId) return;
        S.offers = r.items; S.total = r.total;
        renderOffers();
      }).catch(function () { if (rid === S.reqId) section("#km-offers", "", '<div class="empty">Chargement impossible. <button type="button" class="link" data-retry>Réessayer</button></div>'); });
      // Profils : résultats de recherche (sponsorisés étiquetés en tête), sinon populaires puis nouveaux.
      var req = S.q
        ? get("/search?" + qs + "page_size=12").then(function (r) { return { items: (r.sponsored || []).concat(r.items), total: r.total }; })
        : Promise.all([get("/popular?limit=12"), get("/new?limit=12")]).then(function (r) {
            var seen = {}, items = [];
            r[0].items.concat(r[1].items).forEach(function (c) { if (!seen[c.slug] && items.length < 12) { seen[c.slug] = 1; items.push(c); } });
            return items.length ? { items: items, total: items.length } : get("/search?sort=new&page_size=12");
          });
      req.then(function (r) {
        if (rid !== S.reqId) return;
        var items = r.items || [];
        section("#km-profiles", plural(r.total || 0, "profil"), items.length
          ? '<div class="grid pfs">' + items.map(profileCard).join("") + '</div>'
          : '<div class="empty">' + (S.q ? "Aucun profil ne correspond." : "Les premières vitrines arrivent bientôt.") + '</div>');
      }).catch(function () { if (rid === S.reqId) section("#km-profiles", "", '<div class="empty">Chargement impossible. <button type="button" class="link" data-retry>Réessayer</button></div>'); });
    }

    function renderOffers() {
      var html = S.offers.length
        ? '<div class="grid">' + S.offers.map(offerCard).join("") + '</div>'
        : '<div class="empty">' + (S.q ? "Aucune offre ne correspond." : "Les premières offres arrivent bientôt.") + '</div>';
      if (S.offers.length < S.total) html += '<div class="more"><button type="button" class="btn" id="km-more">Voir plus d\'offres</button></div>';
      section("#km-offers", plural(S.total, "offre"), html);
      var m = $("#km-more");
      if (m) m.onclick = function () {
        m.disabled = true; m.textContent = "Chargement…";
        var rid = S.reqId;
        get("/offers?" + (S.q ? "q=" + encodeURIComponent(S.q) + "&" : "") + "page=" + (S.page + 1) + "&page_size=" + opts.pageSize).then(function (r) {
          if (rid !== S.reqId) return;
          S.page += 1; S.offers = S.offers.concat(r.items); S.total = r.total;
          renderOffers();
        }).catch(function () { m.disabled = false; m.textContent = "Réessayer"; });
      };
    }

    function refresh() { writeUrl(); load(); }

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
      var t = e.target.closest("[data-ranking],[data-retry]");
      if (!t || !root.contains(t)) return;
      if (t.hasAttribute("data-ranking")) openRanking(); else load();
    });

    if (opts.mode === "showcase") {
      shell();
      Promise.all([get("/popular?limit=" + opts.showcaseCount), get("/new?limit=" + opts.showcaseCount)]).then(function (r) {
        var seen = {}, items = [];
        r[0].items.concat(r[1].items).forEach(function (c) { if (!seen[c.slug] && items.length < opts.showcaseCount) { seen[c.slug] = 1; items.push(c); } });
        return items.length ? items : get("/search?sort=new&page_size=" + opts.showcaseCount).then(function (s) { return s.items; });
      }).then(function (items) {
        $("#km-show").outerHTML = items.length ? '<div class="grid pfs">' + items.map(profileCard).join("") + '</div>' : '<div class="empty">Les premières vitrines arrivent bientôt.</div>';
      }).catch(function () { $("#km-show").outerHTML = '<div class="empty">Chargement impossible.</div>'; });
      return;
    }
    readUrl();
    shell();
    load();
  }

  global.KitlyMarket = { version: 3, mount: mount };
})(window);
