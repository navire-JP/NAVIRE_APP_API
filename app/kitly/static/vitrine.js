/*
 * KITLY — moteur de rendu des vitrines (source unique).
 *
 * Servi par l'API : GET /kitly/static/vitrine.js
 * Utilisé par :
 *   - la page publique rendue par l'API      GET /kitly/p/{slug}
 *   - l'embed Framer de la page profil       embeds/kitly/profil.html
 *   - l'aperçu du builder                     embeds/kitly/builder.html
 * L'aperçu et la vraie vitrine ont donc exactement le même rendu.
 *
 * API :
 *   KitlyVitrine.render(data)                  → HTML (aperçu, sans interactions)
 *   KitlyVitrine.mount(el, data, opts)         → rendu + interactions (fiches offres,
 *       contact / devis, portfolio, CGV, signalement, comptage de visite)
 *       opts = { apiBase, slug, source: "direct"|"marketplace", privacyUrl, kitlyUrl, countView }
 *
 * `data` a la forme de GET /kitly/public/profiles/{slug}.
 * Tout texte vient des freelances : il est systématiquement échappé.
 */
(function (global) {
  "use strict";
  if (global.KitlyVitrine && global.KitlyVitrine.version >= 2) return;

  // ============================================================
  // Styles (injectés une fois)
  // ============================================================
  var CSS = [
    ".kv{--bg:#fff;--fg:#0a0a0a;--mut2:rgba(10,10,10,.6);--acc:#0a0a0a;--btn-fg:#fff;--line2:rgba(10,10,10,.12);--r-btn:999px;--fh:'Inter',sans-serif;--fb:'Inter',sans-serif;background:var(--bg);color:var(--fg);font-family:var(--fb);position:relative;overflow:hidden;-webkit-font-smoothing:antialiased;min-height:100%}",
    ".kv *,.kv-mbg,.kv-mbg *{box-sizing:border-box}",
    ".kv a{color:inherit}",
    ".kv.bg-blobs::before{content:'';position:absolute;inset:0;pointer-events:none;background:radial-gradient(38% 22% at 15% 12%,color-mix(in srgb,var(--bg) 72%,#000) 0 60%,transparent 61%),radial-gradient(30% 18% at 92% 30%,color-mix(in srgb,var(--bg) 72%,#000) 0 60%,transparent 61%),radial-gradient(42% 20% at 8% 58%,color-mix(in srgb,var(--bg) 72%,#000) 0 60%,transparent 61%),radial-gradient(36% 18% at 85% 82%,color-mix(in srgb,var(--bg) 72%,#000) 0 60%,transparent 61%)}",
    ".kv>*{position:relative}",   // le contenu passe au-dessus des décors de fond (::before)
    ".kv.bg-gradient{background:linear-gradient(180deg,var(--bg),color-mix(in srgb,var(--bg) 70%,var(--acc)))}",
    ".kv.bg-image{background:var(--bg) center/cover}",
    ".kv-col{position:relative;max-width:620px;margin:0 auto;padding-bottom:28px}",
    ".kv-banner{height:130px;background:color-mix(in srgb,var(--fg) 8%,var(--bg)) center/cover}",
    ".kv.wide .kv-banner{height:190px;border-radius:0 0 22px 22px}",
    ".kv-hero{text-align:center;padding:0 22px}",
    ".kv-av{width:92px;height:92px;border-radius:50%;margin:26px auto 14px;background:var(--acc) center/cover;color:var(--btn-fg);display:flex;align-items:center;justify-content:center;font-size:34px;font-weight:900;box-shadow:0 0 0 4px var(--bg)}",
    ".kv-banner+.kv-hero .kv-av{margin-top:-46px}",
    ".kv-name{font-family:var(--fh);font-size:25px;font-weight:800;letter-spacing:-.02em;line-height:1.1;margin:0;overflow-wrap:anywhere}",
    ".kv-meta{font-size:13px;color:var(--mut2);font-weight:600;margin-top:6px}",
    ".kv-bio{font-size:14px;line-height:1.5;margin:12px auto 0;max-width:320px;color:var(--mut2);white-space:pre-wrap;overflow-wrap:anywhere}",
    ".kv-socials{display:flex;justify-content:center;gap:8px;margin-top:14px;flex-wrap:wrap}",
    ".kv-soc{width:34px;height:34px;border-radius:50%;border:1px solid var(--line2);display:flex;align-items:center;justify-content:center;font-size:10.5px;font-weight:800;color:var(--fg);text-decoration:none}",
    ".kv-soc:hover{background:color-mix(in srgb,var(--fg) 8%,transparent)}",
    ".kv-sec{padding:0 18px;margin-top:22px}",
    ".kv-sec-t{font-size:11px;font-weight:800;letter-spacing:.12em;text-transform:uppercase;color:var(--mut2);margin:0 0 10px 4px}",
    ".kv-links{display:flex;flex-direction:column;gap:10px}",
    ".kv-link{display:flex;align-items:center;gap:12px;background:var(--acc);color:var(--btn-fg)!important;border-radius:var(--r-btn);padding:15px 18px;font-weight:700;font-size:15px;text-decoration:none;min-height:54px;transition:transform .15s}",
    ".kv-link:hover{transform:translateY(-2px)}",
    ".kv-link svg{width:20px;height:20px;flex-shrink:0}",
    ".kv-link span{flex:1;text-align:center;padding-right:32px;overflow-wrap:anywhere}",
    ".kv-link.no-ico span{padding-right:0}",
    ".kv-video{border-radius:min(var(--r-btn),22px);overflow:hidden;position:relative;aspect-ratio:16/10;background:linear-gradient(160deg,#f6b26b,#e8743b) center/cover;display:block;text-decoration:none}",
    ".kv-video .play{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);width:58px;height:58px;border-radius:50%;background:#fff;display:flex;align-items:center;justify-content:center}",
    ".kv-video .play::after{content:'';border-left:17px solid #e8743b;border-top:11px solid transparent;border-bottom:11px solid transparent;margin-left:5px}",
    ".kv-video .cap{position:absolute;left:14px;bottom:10px;color:#fff;font-weight:700;font-size:15px;text-shadow:0 1px 8px rgba(0,0,0,.4)}",
    ".kv-text h5{font-family:var(--fh);font-size:17px;margin:4px 0 6px}",
    ".kv-text p{font-size:14px;line-height:1.55;color:var(--mut2);margin:0;white-space:pre-wrap;overflow-wrap:anywhere}",
    ".kv-header{font-family:var(--fh);font-size:18px;font-weight:800;text-align:center;margin:8px 0 0}",
    ".kv-stats{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}",
    ".kv-stat{border:1px solid var(--line2);border-radius:12px;padding:11px 4px;text-align:center}",
    ".kv-stat b{display:block;font-family:var(--fh);font-size:17px;font-weight:800}",
    ".kv-stat small{font-size:9.5px;color:var(--mut2);font-weight:700;text-transform:uppercase;letter-spacing:.05em}",
    ".kv-about{font-size:14px;line-height:1.6;color:var(--mut2);white-space:pre-wrap;margin-top:12px;overflow-wrap:anywhere}",
    ".kv-tags{display:flex;flex-wrap:wrap;gap:6px;margin-top:12px}",
    ".kv-tags span{font-size:11.5px;font-weight:700;border:1px solid var(--line2);border-radius:99px;padding:4px 10px}",
    ".kv-offers{display:grid;grid-template-columns:1fr 1fr;gap:10px}",
    ".kv-offers.list{grid-template-columns:1fr}",
    ".kv-offer{border:1px solid var(--line2);border-radius:14px;overflow:hidden;background:color-mix(in srgb,var(--bg) 92%,var(--fg));text-align:left;font:inherit;color:inherit;padding:0;width:100%;cursor:default;display:flex;flex-direction:column;justify-content:flex-start;align-items:stretch}",
    ".kv.live .kv-offer,.kv.live .kv-row,.kv.live .kv-proj{cursor:pointer;transition:transform .15s,border-color .15s}",
    ".kv.live .kv-offer:hover,.kv.live .kv-row:hover,.kv.live .kv-proj:hover{transform:translateY(-2px);border-color:var(--fg)}",
    ".kv-offer .im{height:110px;background:color-mix(in srgb,var(--fg) 7%,var(--bg)) center/cover;display:flex;align-items:center;justify-content:center;color:var(--mut2)}",
    ".kv-offer .im svg{width:24px;height:24px;opacity:.6}",
    ".kv-offer .bd{padding:10px 12px 12px}",
    ".kv-tg{display:inline-block;font-size:9px;font-weight:800;padding:3px 8px;border-radius:99px;background:var(--acc);color:var(--btn-fg);text-transform:uppercase;letter-spacing:.06em;margin:0 4px 6px 0}",
    ".kv-tg.out{background:transparent;color:var(--mut2);border:1px solid var(--line2)}",
    ".kv-offer .tt{font-weight:700;font-size:13.5px;line-height:1.3;overflow-wrap:anywhere}",
    ".kv-offer .pr{font-weight:800;font-size:13px;margin-top:3px}",
    ".kv-offer .ds{font-size:11.5px;color:var(--mut2);margin-top:4px;line-height:1.4;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}",
    ".kv-offer .ct{font-size:10.5px;font-weight:800;margin-top:8px;text-transform:uppercase;letter-spacing:.06em}",
    ".kv-row{display:flex;gap:12px;align-items:center;border:1px solid var(--line2);border-radius:14px;padding:12px 14px;text-align:left;font:inherit;color:inherit;background:none;width:100%}",
    ".kv-row .tt{font-weight:700;font-size:14px;overflow-wrap:anywhere}",
    ".kv-row .ds{font-size:12px;color:var(--mut2);margin-top:2px}",
    ".kv-row .pr{font-weight:800;font-size:13.5px;white-space:nowrap}",
    ".kv-projs{display:grid;grid-template-columns:1fr 1fr;gap:10px}",
    ".kv-proj{border:1px solid var(--line2);border-radius:14px;overflow:hidden;padding:0;background:none;text-align:left;font:inherit;color:inherit;width:100%;display:flex;flex-direction:column;justify-content:flex-start;align-items:stretch}",
    ".kv-proj .im{aspect-ratio:4/3;background:color-mix(in srgb,var(--fg) 7%,var(--bg)) center/cover}",
    ".kv-proj .tt{padding:9px 12px 11px;font-weight:700;font-size:13px}",
    ".kv-contact{display:block;width:100%;text-align:center;border:1.5px solid var(--fg);color:var(--fg);background:none;border-radius:var(--r-btn);padding:14px;font:800 14.5px var(--fb);text-decoration:none;cursor:pointer}",
    ".kv.live .kv-contact:hover{background:var(--fg);color:var(--bg)}",
    ".kv-cinfo{display:flex;justify-content:center;flex-wrap:wrap;gap:6px 16px;margin-top:12px;font-size:12.5px;color:var(--mut2)}",
    ".kv-cinfo a{text-decoration:none;border-bottom:1px solid var(--line2)}",
    ".kv-foot{margin:30px 18px 0;padding-top:16px;border-top:1px solid var(--line2);font-size:11.5px;color:var(--mut2);text-align:center;line-height:1.6}",
    ".kv-foot .lk{display:flex;justify-content:center;flex-wrap:wrap;gap:4px 14px;margin-top:6px}",
    ".kv-foot button,.kv-foot a{background:none;border:0;padding:0;font:inherit;color:var(--mut2);text-decoration:underline;cursor:pointer}",
    ".kv-brand{text-align:center;font-size:11px;color:var(--mut2);margin-top:18px;font-weight:600}",
    ".kv-brand a{text-decoration:none}.kv-brand b{color:var(--fg)}",
    ".kv-empty{text-align:center;color:var(--mut2);font-size:13px;padding:10px 0}",
    ".kv.t-cadre{box-shadow:inset 0 0 0 5px var(--fg)}",
    ".kv.t-noir .kv-offer,.kv.t-noir .kv-row,.kv.t-noir .kv-stat{border-color:rgba(255,255,255,.16)}",
    ".kv.t-bold .kv-name{text-transform:uppercase;font-size:46px;color:var(--acc);letter-spacing:-.01em;line-height:.95;margin-top:4px}",
    ".kv.t-bold .kv-av{width:150px;height:150px;box-shadow:none}",
    ".kv.t-bold .kv-banner+.kv-hero .kv-av{margin-top:-75px}",
    ".kv.t-bold .kv-meta,.kv.t-bold .kv-bio{color:color-mix(in srgb,var(--fg) 75%,transparent)}",
    "@media(min-width:700px){.kv.wide .kv-name{font-size:32px}.kv.wide.t-bold .kv-name{font-size:60px}.kv.wide .kv-offers:not(.list){grid-template-columns:repeat(3,1fr)}.kv.wide .kv-projs{grid-template-columns:repeat(3,1fr)}}",
    /* ── rendu « site » ── */
    ".kv.lay-site{min-height:100%}",
    ".kv-wrap{max-width:1120px;margin:0 auto;padding:0 20px}",
    ".kv-wrap.narrow{max-width:640px}",
    ".kv.lay-site .kv-s{padding:40px 0}",
    ".kv.lay-site .kv-s:nth-of-type(even){background:color-mix(in srgb,var(--fg) 3.5%,transparent)}",
    ".kv-h{font-family:var(--fh);font-size:28px;font-weight:800;letter-spacing:-.02em;line-height:1.1;margin:0 0 22px}",
    ".kv-it{opacity:.7}",
    ".kv.lay-site .kv-nav{position:sticky;top:0;z-index:5;background:color-mix(in srgb,var(--bg) 88%,transparent);-webkit-backdrop-filter:blur(10px);backdrop-filter:blur(10px);border-bottom:1px solid var(--line2)}",
    ".kv-nav-in{display:flex;align-items:center;justify-content:space-between;height:62px;gap:16px}",
    ".kv-brandmark{display:flex;align-items:center;gap:10px;background:none;border:0;color:var(--fg);font:800 16px var(--fh);cursor:pointer;padding:0;min-width:0}",
    ".kv-brandmark span{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}",
    ".kv-av-xs{width:32px;height:32px;border-radius:50%;background:var(--acc) center/cover;color:var(--btn-fg);display:flex;align-items:center;justify-content:center;font-size:14px;font-weight:900;flex-shrink:0}",
    ".kv-menu{display:none;gap:2px}",
    ".kv-menu button,.kv-drawer button,.kv-foot-top nav button{background:none;border:0;color:var(--fg);font:600 14px var(--fb);padding:8px 12px;border-radius:99px;cursor:pointer;opacity:.78}",
    ".kv-menu button:hover{opacity:1;background:color-mix(in srgb,var(--fg) 8%,transparent)}",
    ".kv-nav-act{display:flex;gap:8px;align-items:center}",
    ".kv-btn{display:inline-flex;align-items:center;justify-content:center;gap:8px;background:var(--acc);color:var(--btn-fg);border:1.5px solid var(--acc);border-radius:var(--r-btn);padding:13px 22px;font:700 15px var(--fb);cursor:pointer;text-decoration:none;transition:transform .15s}",
    ".kv-btn:hover{transform:translateY(-1px)}",
    ".kv-btn.ghost{background:transparent;color:var(--fg);border-color:var(--line2)}",
    ".kv-btn.sm{padding:8px 16px;font-size:13.5px}",
    ".kv-burger{width:40px;height:40px;border-radius:50%;border:1px solid var(--line2);background:none;display:flex;flex-direction:column;justify-content:center;align-items:center;gap:5px;cursor:pointer;padding:0}",
    ".kv-burger i{display:block;width:16px;height:2px;background:var(--fg);border-radius:2px}",
    ".kv-drawer{display:none;flex-direction:column;padding:6px 14px 14px;border-top:1px solid var(--line2)}",
    ".kv.nav-open .kv-drawer{display:flex}",
    ".kv-drawer button{text-align:left;padding:12px 8px;font-size:15px;border-radius:12px}",
    ".kv.lay-site:not(.is-wide) .kv-nav-act .kv-btn{display:none}",
    ".kv.lay-site .kv-hero2{padding:36px 0 30px;text-align:center}",
    ".kv-hero-grid{display:grid;gap:28px}",
    ".kv-av-mob{width:104px;height:104px;border-radius:50%;margin:0 auto 18px;background:var(--acc) center/cover;color:var(--btn-fg);display:flex;align-items:center;justify-content:center;font-size:38px;font-weight:900}",
    ".kv.t-bold .kv-av-mob{width:150px;height:150px}",
    ".kv-kick{font-size:12.5px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:var(--mut2);margin:0 0 10px}",
    ".kv.lay-site .kv-name{font-size:36px}",
    ".kv.lay-site.t-bold .kv-name{font-size:48px}",
    ".kv-lead{font-size:16px;line-height:1.55;color:var(--mut2);margin:14px auto 0;max-width:540px;white-space:pre-wrap;overflow-wrap:anywhere}",
    ".kv-actions{display:flex;gap:10px;justify-content:center;flex-wrap:wrap;margin-top:24px}",
    ".kv.lay-site:not(.is-wide) .kv-actions .kv-btn{flex:1 1 100%}",
    ".kv-hero2 .kv-socials{margin-top:18px}",
    ".kv-hero-media{display:none}",
    ".kv-av-big{width:min(380px,100%);aspect-ratio:1;border-radius:36px;background:var(--acc) center/cover;color:var(--btn-fg);display:flex;align-items:center;justify-content:center;font-size:130px;font-weight:900;box-shadow:0 30px 60px -32px rgba(0,0,0,.4)}",
    ".kv-hero-media.banner{width:100%;aspect-ratio:4/3;border-radius:30px;background:color-mix(in srgb,var(--fg) 10%,var(--bg)) center/cover;position:relative;box-shadow:0 30px 60px -32px rgba(0,0,0,.4)}",
    ".kv-av3{position:absolute;left:-22px;bottom:-22px;width:116px;height:116px;border-radius:50%;background:var(--acc) center/cover;box-shadow:0 0 0 6px var(--bg);color:var(--btn-fg);display:flex;align-items:center;justify-content:center;font-size:42px;font-weight:900}",
    ".kv-about-grid{display:grid;gap:24px;align-items:start}",
    ".kv.lay-site .kv-about{font-size:16px;line-height:1.7;margin:0;color:var(--mut2);white-space:pre-wrap}",
    ".kv-about-grid .kv-stats{grid-template-columns:repeat(auto-fit,minmax(110px,1fr))}",
    ".kv-svc-grid{display:grid;gap:12px}",
    ".kv-svc{display:flex;flex-direction:column;text-align:left;border:1px solid var(--line2);border-radius:18px;background:color-mix(in srgb,var(--bg) 94%,var(--fg));padding:0;overflow:hidden;font:inherit;color:inherit;width:100%}",
    ".kv.live .kv-svc{cursor:pointer;transition:transform .15s,border-color .15s}",
    ".kv.live .kv-svc:hover{transform:translateY(-2px);border-color:var(--fg)}",
    ".kv-svc .im{height:150px;background:center/cover}",
    ".kv-svc .bd{padding:18px 20px 20px;display:flex;flex-direction:column;gap:6px;flex:1}",
    ".kv-svc .top{display:flex;justify-content:space-between;gap:12px;align-items:baseline}",
    ".kv-svc .tt{font-family:var(--fh);font-weight:800;font-size:17px;overflow-wrap:anywhere}",
    ".kv-svc .pr{font-weight:800;white-space:nowrap}",
    ".kv-svc .sb{font-size:12.5px;color:var(--mut2);font-weight:600}",
    ".kv-svc .ds{font-size:14px;line-height:1.5;color:var(--mut2);margin:4px 0 0;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}",
    ".kv-svc .ct{margin-top:auto;padding-top:10px;font-size:11.5px;font-weight:800;text-transform:uppercase;letter-spacing:.06em}",
    ".kv-contact-grid{display:grid;gap:26px;align-items:start}",
    ".kv-contact-txt p{font-size:16px;color:var(--mut2);line-height:1.55;margin:0}",
    ".kv-coords{list-style:none;padding:0;margin:20px 0;display:flex;flex-direction:column;gap:10px}",
    ".kv-coords li{display:flex;gap:12px;font-size:14.5px;overflow-wrap:anywhere}",
    ".kv-coords span{width:92px;flex-shrink:0;color:var(--mut2);font-weight:700;font-size:11.5px;text-transform:uppercase;letter-spacing:.06em;padding-top:3px}",
    ".kv-coords a{color:var(--fg);text-decoration:none;border-bottom:1px solid var(--line2)}",
    ".kv-contact-txt .kv-socials{justify-content:flex-start}",
    ".kv-form{background:#fff;color:#0a0a0a;border-radius:22px;padding:22px;box-shadow:0 22px 50px -30px rgba(0,0,0,.35);border:1px solid rgba(10,10,10,.08)}",
    ".kv-form label.lb{display:block;font-size:12px;font-weight:700;color:#6f6f6c;margin:12px 0 5px}",
    ".kv-form label.lb:first-child{margin-top:0}",
    ".kv-form input[type=text],.kv-form input[type=email],.kv-form input[type=tel],.kv-form textarea{width:100%;border:1px solid #e4e4e2;border-radius:11px;padding:12px 13px;font:15px var(--fb);color:#0a0a0a;background:#fff;outline:none}",
    ".kv-form input:focus,.kv-form textarea:focus{border-color:#0a0a0a}",
    ".kv-form textarea{resize:vertical;line-height:1.5}",
    ".kv-form .row{display:grid;grid-template-columns:1fr 1fr;gap:10px}",
    ".kv-form .row label.lb{margin-top:0}",
    ".kv-form .chk{display:flex;gap:10px;align-items:flex-start;font-size:12.5px;line-height:1.45;color:#333;margin:14px 0 0}",
    ".kv-form .chk input{width:17px;height:17px;margin:1px 0 0;accent-color:#0a0a0a;flex-shrink:0}",
    ".kv-form .chk a{color:#0a0a0a;font-weight:700}",
    ".kv-form .err{color:#c2410c;font-size:13px;margin-top:10px}.kv-form .err:empty{display:none}",
    ".kv-form .hp{position:absolute;left:-9999px;width:1px;height:1px;opacity:0}",
    ".kv-form .cta{display:block;width:100%;margin-top:16px;border:0;border-radius:999px;padding:15px;font:800 15px var(--fb);background:#0a0a0a;color:#fff;cursor:pointer}",
    ".kv-form .cta:disabled{opacity:.5;cursor:default}",
    ".kv-form .done{text-align:center;padding:30px 0}.kv-form .done b{display:block;font-size:20px;margin-bottom:6px}.kv-form .done p{color:#6f6f6c;margin:0}",
    ".kv-foot2{border-top:1px solid var(--line2);padding:28px 0 36px;font-size:12.5px;color:var(--mut2)}",
    ".kv-foot-top{display:flex;justify-content:space-between;align-items:center;gap:8px 12px;flex-wrap:wrap;margin-bottom:10px}",
    ".kv-foot-top b{color:var(--fg);font-family:var(--fh);font-size:15px}",
    ".kv-foot-top nav{display:flex;flex-wrap:wrap}.kv-foot-top nav button{font-size:12.5px;padding:4px 8px}",
    ".kv-foot-legal{line-height:1.7}",
    ".kv-foot-links{display:flex;flex-wrap:wrap;gap:14px;margin-top:8px}",
    ".kv-foot-links button{background:none;border:0;padding:0;color:var(--mut2);text-decoration:underline;cursor:pointer;font:inherit}",
    ".kv.lay-site .kv-brand{text-align:left;margin-top:14px}",
    ".kv.lay-site .kv-offers{grid-template-columns:1fr 1fr}",
    ".kv.is-wide .kv-menu{display:flex}.kv.is-wide .kv-burger,.kv.is-wide .kv-drawer{display:none!important}",
    ".kv.is-wide .kv-s{padding:68px 0}",
    ".kv.is-wide .kv-h{font-size:38px;margin-bottom:28px}",
    ".kv.is-wide .kv-hero2{padding:80px 0;text-align:left}",
    ".kv.is-wide .kv-hero-grid{grid-template-columns:1.1fr .9fr;align-items:center;gap:60px}",
    ".kv.is-wide .kv-av-mob{display:none}",
    ".kv.is-wide .kv-hero-media{display:flex;justify-content:center;align-items:center}",
    ".kv.is-wide .kv-name{font-size:60px;line-height:1.02}",
    ".kv.is-wide.t-bold .kv-name{font-size:84px;line-height:.92}",
    ".kv.is-wide .kv-lead{margin-left:0;font-size:18px}",
    ".kv.is-wide .kv-actions,.kv.is-wide .kv-hero2 .kv-socials{justify-content:flex-start}",
    ".kv.is-wide .kv-about-grid{grid-template-columns:1.4fr 1fr;gap:48px}",
    ".kv.is-wide .kv-svc-grid{grid-template-columns:repeat(2,1fr);gap:16px}",
    ".kv.is-wide .kv-shop-grid{grid-template-columns:repeat(4,1fr);gap:16px}",
    ".kv.is-wide .kv-shop-grid .kv-offer .im{height:180px}",
    ".kv.is-wide .kv-projs{grid-template-columns:repeat(3,1fr);gap:16px}",
    ".kv.is-wide .kv-contact-grid{grid-template-columns:1fr 1.1fr;gap:56px}",
    "@media(max-width:460px){.kv-form .row{grid-template-columns:1fr}}",
    /* modales — toujours lisibles : fond blanc, texte noir, quel que soit le thème */
    ".kv-mbg{position:fixed;inset:0;z-index:2147483000;background:rgba(10,10,10,.55);display:flex;align-items:flex-end;justify-content:center;padding:0;font-family:var(--fb)}",
    "@media(min-width:620px){.kv-mbg{align-items:center;padding:20px}}",
    ".kv-m{background:#fff;color:#0a0a0a;width:100%;max-width:520px;max-height:92%;overflow-y:auto;border-radius:22px 22px 0 0;padding:24px 22px 26px;position:relative;animation:kvpop .2s ease}",
    "@media(min-width:620px){.kv-m{border-radius:22px}}",
    "@keyframes kvpop{from{transform:translateY(14px);opacity:0}to{transform:none;opacity:1}}",
    ".kv-m h3{font-family:var(--fh);font-size:22px;font-weight:800;letter-spacing:-.02em;margin:0 30px 4px 0;line-height:1.2}",
    ".kv-m .x{position:absolute;top:14px;right:14px;width:32px;height:32px;border-radius:50%;border:1px solid #e4e4e2;background:#fff;cursor:pointer;font-size:14px;color:#6f6f6c}",
    ".kv-m .sub{color:#6f6f6c;font-size:13.5px;margin:0 0 16px;line-height:1.5}",
    ".kv-m .gal{display:flex;gap:8px;overflow-x:auto;scroll-snap-type:x mandatory;margin:0 -22px 16px;padding:0 22px}",
    ".kv-m .gal img{height:230px;max-width:88%;object-fit:cover;border-radius:14px;scroll-snap-align:start;flex-shrink:0}",
    ".kv-m .price{font-size:26px;font-weight:900;letter-spacing:-.02em;margin:6px 0 10px}",
    ".kv-m .desc{font-size:14px;line-height:1.6;color:#333;white-space:pre-wrap;overflow-wrap:anywhere}",
    ".kv-m .facts{list-style:none;padding:0;margin:14px 0 0;display:flex;flex-direction:column;gap:7px;font-size:13px}",
    ".kv-m .facts li{display:flex;gap:8px}.kv-m .facts li::before{content:'→';font-weight:800}",
    ".kv-m .cta{display:block;width:100%;margin-top:18px;border:0;border-radius:999px;padding:15px;font:800 15px var(--fb);background:#0a0a0a;color:#fff;cursor:pointer;text-align:center}",
    ".kv-m .cta:disabled{opacity:.5;cursor:default}",
    ".kv-m .ghost{background:#fff;color:#0a0a0a;border:1px solid #0a0a0a;margin-top:8px}",
    ".kv-m label.lb{display:block;font-size:12px;font-weight:700;color:#6f6f6c;margin:12px 0 5px}",
    ".kv-m input[type=text],.kv-m input[type=email],.kv-m input[type=tel],.kv-m textarea,.kv-m select{width:100%;border:1px solid #e4e4e2;border-radius:11px;padding:12px 13px;font:15px var(--fb);color:#0a0a0a;background:#fff;outline:none}",
    ".kv-m input:focus,.kv-m textarea:focus,.kv-m select:focus{border-color:#0a0a0a}",
    ".kv-m textarea{resize:vertical;line-height:1.5}",
    ".kv-m .row{display:grid;grid-template-columns:1fr 1fr;gap:10px}",
    "@media(max-width:460px){.kv-m .row{grid-template-columns:1fr}}",
    ".kv-m .chk{display:flex;gap:10px;align-items:flex-start;font-size:12.5px;line-height:1.45;color:#333;margin:14px 0 0;cursor:pointer}",
    ".kv-m .chk input{width:17px;height:17px;margin:1px 0 0;accent-color:#0a0a0a;flex-shrink:0}",
    ".kv-m .chk a{color:#0a0a0a;font-weight:700}",
    ".kv-m .err{color:#c2410c;font-size:13px;margin-top:10px}",
    ".kv-m .opts{display:flex;flex-direction:column;gap:8px}",
    ".kv-m .opt{display:flex;gap:10px;align-items:center;border:1px solid #e4e4e2;border-radius:12px;padding:11px 13px;font-size:13.5px;cursor:pointer}",
    ".kv-m .opt:has(input:checked){border-color:#0a0a0a;box-shadow:0 0 0 1px #0a0a0a}",
    ".kv-m .opt input{accent-color:#0a0a0a;margin:0;flex-shrink:0}",
    ".kv-m .opt span{flex:1}.kv-m .opt b{white-space:nowrap}",
    ".kv-m .opt small{display:block;color:#6f6f6c;font-size:12px}",
    ".kv-m .tot{display:flex;justify-content:space-between;align-items:baseline;border-top:1px solid #e4e4e2;margin-top:16px;padding-top:14px;font-size:14px}",
    ".kv-m .tot b{font-size:22px;font-weight:900;letter-spacing:-.02em}",
    ".kv-m .lnk{background:none;border:0;padding:0;font:inherit;color:#0a0a0a;font-weight:700;text-decoration:underline;cursor:pointer}",
    ".kv-m .tpanel{max-height:220px;overflow:auto;border:1px solid #e4e4e2;border-radius:10px;padding:4px 12px 12px;margin-top:10px}",
    ".kv-m .secure{text-align:center;font-size:11.5px;color:#6f6f6c;margin-top:10px}",
    ".kv-m .err:empty{display:none}",
    ".kv-m .hp{position:absolute;left:-9999px;width:1px;height:1px;opacity:0}",
    ".kv-m .done{text-align:center;padding:20px 0 6px}",
    ".kv-m .done b{display:block;font-size:20px;margin-bottom:6px}",
    ".kv-m .terms h4{font-size:15px;margin:18px 0 6px}",
    ".kv-m .terms p{font-size:13.5px;line-height:1.6;color:#333;white-space:pre-wrap;margin:0}",
    ".kv-m .disc{font-size:11.5px;color:#6f6f6c;background:#f5f5f4;border-radius:10px;padding:10px 12px;margin-top:18px;line-height:1.5}"
  ].join("\n");

  function injectCss() {
    if (document.getElementById("kitly-vitrine-css")) return;
    var st = document.createElement("style");
    st.id = "kitly-vitrine-css";
    st.textContent = CSS;
    document.head.appendChild(st);
  }

  // ============================================================
  // Référentiels
  // ============================================================
  var ICONS = {
    link: '<path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7"/><path d="M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7"/>',
    book: '<path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v15H6.5A2.5 2.5 0 0 0 4 20.5z"/><path d="M4 20.5A2.5 2.5 0 0 0 6.5 23H20v-5"/>',
    waves: '<path d="M2 7c3-2 5-2 8 0s5 2 8 0 3-1 4-1"/><path d="M2 12c3-2 5-2 8 0s5 2 8 0 3-1 4-1"/><path d="M2 17c3-2 5-2 8 0s5 2 8 0 3-1 4-1"/>',
    play: '<circle cx="12" cy="12" r="10"/><path d="m10 8 6 4-6 4z"/>',
    mail: '<rect x="2" y="4" width="20" height="16" rx="3"/><path d="m3 6 9 7 9-7"/>',
    shop: '<path d="M5 7h14l-1 13H6z"/><path d="M9 7a3 3 0 0 1 6 0"/>',
    calendar: '<rect x="3" y="5" width="18" height="16" rx="3"/><path d="M8 3v4M16 3v4M3 10h18"/>',
    star: '<path d="m12 3 2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1L3.2 9.5l6.1-.9z"/>',
    music: '<path d="M9 18V5l11-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="17" cy="16" r="3"/>',
    camera: '<path d="M3 8h4l2-3h6l2 3h4v12H3z"/><circle cx="12" cy="13" r="4"/>',
    heart: '<path d="M12 21s-8-5.2-8-11a4.5 4.5 0 0 1 8-2.8A4.5 4.5 0 0 1 20 10c0 5.8-8 11-8 11z"/>',
    sparkle: '<path d="M12 3v4M12 17v4M3 12h4M17 12h4M5.6 5.6l2.8 2.8M15.6 15.6l2.8 2.8M5.6 18.4l2.8-2.8M15.6 8.4l2.8-2.8"/>',
    box: '<path d="m3 7 9-4 9 4v10l-9 4-9-4z"/><path d="m3 7 9 4 9-4M12 11v10"/>',
    file: '<path d="M14 3H6v18h12V7z"/><path d="M14 3v4h4M9 13h6M9 17h6"/>',
    brief: '<rect x="3" y="7" width="18" height="13" rx="2"/><path d="M8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M3 13h18"/>'
  };
  function svg(name) {
    return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + (ICONS[name] || ICONS.link) + '</svg>';
  }
  var KIND_ICON = { physical: "box", digital: "file", service: "brief", appointment: "calendar" };
  var CTA_LABEL = { order: "Commander", quote: "Demander un devis", book: "Réserver", contact: "Me contacter" };
  var SOCIAL_ABBR = { instagram: "IG", tiktok: "TT", youtube: "YT", linkedin: "in", x: "X", facebook: "f", behance: "Be", dribbble: "Dr", github: "GH", pinterest: "P", twitch: "Tw", spotify: "Sp", soundcloud: "SC", threads: "@", whatsapp: "WA", malt: "M", website: "www" };
  var FONT_FAMILY = { "inter": "Inter", "instrument-serif": "Instrument Serif", "space-grotesk": "Space Grotesk", "dm-sans": "DM Sans", "playfair": "Playfair Display", "archivo-black": "Archivo Black", "syne": "Syne", "manrope": "Manrope" };
  var TEMPLATE_PRESET = {
    blanc: { colors: { background: "#ffffff", text: "#0a0a0a", accent: "#0a0a0a", button_text: "#ffffff" }, fh: "inter", bg: "solid" },
    noir:  { colors: { background: "#0a0a0a", text: "#ffffff", accent: "#ffffff", button_text: "#0a0a0a" }, fh: "inter", bg: "solid" },
    cadre: { colors: { background: "#ffffff", text: "#0a0a0a", accent: "#0a0a0a", button_text: "#ffffff" }, fh: "inter", bg: "solid" },
    edito: { colors: { background: "#ffffff", text: "#0a0a0a", accent: "#0a0a0a", button_text: "#ffffff" }, fh: "instrument-serif", bg: "solid" },
    bold:  { colors: { background: "#8B1E1E", text: "#ffffff", accent: "#FF4757", button_text: "#ffffff" }, fh: "archivo-black", bg: "blobs" },
    studio:{ colors: { background: "#F2EFE9", text: "#1d1d1b", accent: "#1d1d1b", button_text: "#F2EFE9" }, fh: "syne", bg: "solid" }
  };
  var CUSTOMIZABLE = { bold: true, studio: true };
  var REPORT_REASONS = {
    fraud: "Arnaque ou fraude", illegal_content: "Contenu illicite", impersonation: "Usurpation d'identité",
    ip_infringement: "Atteinte à mes droits (marque, droit d'auteur)", inappropriate: "Contenu inapproprié", other: "Autre"
  };

  // ============================================================
  // Utilitaires
  // ============================================================
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function safeUrl(u) {
    u = String(u || "").trim();
    if (!u) return "";
    if (!/^(https?:|mailto:|tel:)/i.test(u)) u = "https://" + u;
    return u;
  }
  function cssUrl(u) { return u ? "url('" + String(u).replace(/['"\\()\s]/g, encodeURIComponent) + "')" : "none"; }
  var loadedFonts = {};
  function ensureFont(key) {
    if (!key || loadedFonts[key] || !FONT_FAMILY[key]) return;
    loadedFonts[key] = 1;
    var l = document.createElement("link");
    l.rel = "stylesheet";
    l.href = "https://fonts.googleapis.com/css2?family=" + FONT_FAMILY[key].replace(/ /g, "+") + ":wght@400;600;700;800;900&display=swap";
    document.head.appendChild(l);
  }
  function ytThumb(url) {
    var m = String(url || "").match(/(?:youtu\.be\/|v=|shorts\/|embed\/)([\w-]{11})/);
    return m ? "https://img.youtube.com/vi/" + m[1] + "/hqdefault.jpg" : "";
  }

  function themeVars(theme) {
    theme = theme || {};
    var tpl = TEMPLATE_PRESET[theme.template] ? theme.template : "blanc";
    var preset = TEMPLATE_PRESET[tpl];
    var custom = CUSTOMIZABLE[tpl];
    var c = custom ? Object.assign({}, preset.colors, theme.colors || {}) : preset.colors;
    var fh = custom && theme.font_heading ? theme.font_heading : preset.fh;
    var fb = custom && theme.font_body ? theme.font_body : "inter";
    var bg = custom ? ((theme.background && theme.background.type) || preset.bg) : "solid";
    ensureFont("inter"); ensureFont(fh); ensureFont(fb);
    var radius = { pill: "999px", rounded: "14px", square: "4px" }[theme.button_shape || "pill"] || "999px";
    return {
      tpl: tpl, bg: bg, colors: c,
      style: "--bg:" + c.background + ";--fg:" + c.text + ";--acc:" + c.accent + ";--btn-fg:" + c.button_text +
        ";--mut2:color-mix(in srgb," + c.text + " 62%,transparent);--line2:color-mix(in srgb," + c.text + " 14%,transparent)" +
        ";--r-btn:" + radius + ";--fh:'" + FONT_FAMILY[fh] + "',sans-serif;--fb:'" + FONT_FAMILY[fb] + "',sans-serif;" +
        (bg === "image" && theme.background && theme.background.image_url ? "background-image:" + cssUrl(theme.background.image_url) + ";" : "")
    };
  }

  function offerSub(o) {
    var d = o.details || {};
    if (o.kind === "appointment") {
      var where = { visio: "visio", onsite: "sur place", phone: "téléphone" }[d.location_mode] || "";
      return [d.duration_minutes ? d.duration_minutes + " min" : "", where].filter(Boolean).join(" · ");
    }
    if (o.kind === "service") return [d.duration_label, { remote: "à distance", onsite: "en présentiel", both: "à distance ou en présentiel" }[d.location_mode]].filter(Boolean).join(" · ");
    return "";
  }

  // ============================================================
  // Rendu
  // ============================================================
  function renderLinks(data, opts) {
    opts = opts || {};
    var p = data || {};
    var th = themeVars(p.theme);
    var offers = p.offers || [];
    var services = offers.filter(function (o) { return o.kind === "service" || o.kind === "appointment"; });
    var products = offers.filter(function (o) { return o.kind === "physical" || o.kind === "digital"; });
    var contact = p.contact || {};

    var parts = {
      hero: function () {
        var soc = (p.socials || []).filter(function (s) { return s.url; });
        var meta = [p.headline, p.location].filter(Boolean).join(" · ");
        return (p.banner_url ? '<div class="kv-banner" style="background-image:' + cssUrl(p.banner_url) + '"></div>' : '') +
          '<div class="kv-hero"><div class="kv-av" role="img" aria-label="' + esc(p.display_name) + '" style="background-image:' + cssUrl(p.avatar_url) + '">' + (p.avatar_url ? "" : esc((p.display_name || "?").charAt(0).toUpperCase())) + '</div>' +
          '<h1 class="kv-name">' + esc(p.display_name || "Ton nom") + '</h1>' +
          (meta ? '<div class="kv-meta">' + esc(meta) + '</div>' : '') +
          (p.bio ? '<p class="kv-bio">' + esc(p.bio) + '</p>' : '') +
          (soc.length ? '<div class="kv-socials">' + soc.map(function (s) {
            return '<a class="kv-soc" href="' + esc(safeUrl(s.url)) + '" target="_blank" rel="noopener nofollow" title="' + esc(s.network) + '" aria-label="' + esc(s.network) + '">' + esc(SOCIAL_ABBR[s.network] || "•") + '</a>';
          }).join("") + '</div>' : '') + '</div>';
      },
      links: function () {
        var blocks = (p.blocks || []).filter(function (b) { return b.visible !== false && b.type !== "offer"; });
        if (!blocks.length) return "";
        return '<div class="kv-sec"><div class="kv-links">' + blocks.map(function (b) {
          if (b.type === "link") return '<a class="kv-link' + (b.icon ? "" : " no-ico") + '" href="' + esc(safeUrl(b.url)) + '" target="_blank" rel="noopener nofollow">' + (b.icon ? svg(b.icon) : "") + '<span>' + esc(b.label || "Mon lien") + '</span></a>';
          if (b.type === "video") { var t = b.thumbnail_url || ytThumb(b.url); return '<a class="kv-video" href="' + esc(safeUrl(b.url)) + '" target="_blank" rel="noopener nofollow" style="' + (t ? "background-image:" + cssUrl(t) : "") + '"><span class="play"></span>' + (b.label ? '<span class="cap">' + esc(b.label) + '</span>' : '') + '</a>'; }
          if (b.type === "text") return '<div class="kv-text">' + (b.title ? '<h5>' + esc(b.title) + '</h5>' : '') + '<p>' + esc(b.body || "") + '</p></div>';
          if (b.type === "header") return '<h2 class="kv-header">' + esc(b.title || "") + '</h2>';
          return "";
        }).join("") + '</div></div>';
      },
      about: function () {
        var stats = (p.stats || []).filter(function (s) { return s.label || s.value; });
        var tags = (p.skills || []).concat(p.languages || []);
        if (!stats.length && !p.about && !tags.length) return "";
        return '<div class="kv-sec">' + (stats.length ? '<div class="kv-stats">' + stats.map(function (s) {
          return '<div class="kv-stat"><b>' + esc(s.value) + '</b><small>' + esc(s.label) + '</small></div>';
        }).join("") + '</div>' : '') + (p.about ? '<div class="kv-about">' + esc(p.about) + '</div>' : '') +
          (tags.length ? '<div class="kv-tags">' + tags.map(function (t) { return '<span>' + esc(t) + '</span>'; }).join("") + '</div>' : '') + '</div>';
      },
      offers: function () {
        if (!services.length) return "";
        return '<div class="kv-sec"><div class="kv-sec-t">Prestations</div><div class="kv-offers list">' + services.map(function (o) {
          var sub = offerSub(o) || (o.description || "").slice(0, 90);
          return '<button type="button" class="kv-row" data-kv-offer="' + o.id + '"><div style="flex:1;min-width:0"><div class="tt">' + esc(o.title) + '</div>' +
            (sub ? '<div class="ds">' + esc(sub) + '</div>' : '') + '</div><div class="pr">' + esc(o.price_label || "") + '</div></button>';
        }).join("") + '</div></div>';
      },
      products: function () {
        if (!products.length) return "";
        return '<div class="kv-sec"><div class="kv-sec-t">Boutique</div><div class="kv-offers">' + products.map(function (o) {
          var img = o.images && o.images[0];
          var out = o.details && o.details.in_stock === false;
          return '<button type="button" class="kv-offer" data-kv-offer="' + o.id + '"><div class="im" style="background-image:' + cssUrl(img) + '">' + (img ? "" : svg(KIND_ICON[o.kind])) + '</div>' +
            '<div class="bd">' + (o.badge ? '<span class="kv-tg">' + esc(o.badge) + '</span>' : '') + (out ? '<span class="kv-tg out">Épuisé</span>' : '') +
            '<div class="tt">' + esc(o.title) + '</div><div class="pr">' + esc(o.price_label || "") + '</div>' +
            (o.description ? '<div class="ds">' + esc(o.description) + '</div>' : '') +
            '<div class="ct">' + esc(out ? CTA_LABEL.contact : o.buyable ? "Acheter" : (CTA_LABEL[o.cta] || "Voir")) + ' →</div></div></button>';
        }).join("") + '</div></div>';
      },
      portfolio: function () {
        var items = p.portfolio || [];
        if (!items.length) return "";
        return '<div class="kv-sec"><div class="kv-sec-t">Réalisations</div><div class="kv-projs">' + items.map(function (it) {
          var cover = it.cover_url || (it.media && it.media[0] && it.media[0].type === "image" ? it.media[0].url : "");
          return '<button type="button" class="kv-proj" data-kv-project="' + it.id + '"><div class="im" style="background-image:' + cssUrl(cover) + '"></div><div class="tt">' + esc(it.title) + '</div></button>';
        }).join("") + '</div></div>';
      },
      contact: function () {
        var info = [];
        if (contact.website) info.push('<a href="' + esc(safeUrl(contact.website)) + '" target="_blank" rel="noopener">' + esc(String(contact.website).replace(/^https?:\/\//, "").replace(/\/$/, "")) + '</a>');
        if (contact.phone) info.push('<a href="tel:' + esc(String(contact.phone).replace(/[^\d+]/g, "")) + '">' + esc(contact.phone) + '</a>');
        return '<div class="kv-sec"><button type="button" class="kv-contact" data-kv-contact>Me contacter</button>' +
          (info.length ? '<div class="kv-cinfo">' + info.join("") + '</div>' : '') + '</div>';
      }
    };

    var order = p.sections && p.sections.length ? p.sections : Object.keys(parts).map(function (k) { return { key: k, visible: true }; });
    var html = order.filter(function (s) { return s.visible !== false && parts[s.key]; }).map(function (s) { return parts[s.key](); }).join("");
    if (opts.emptyHint && !offers.length && !(p.blocks || []).length && !p.bio) {
      html += '<div class="kv-sec kv-empty">' + esc(opts.emptyHint) + '</div>';
    }

    // Pied de vitrine : identité du vendeur, CGV, signalement, Kitly
    var legal = p.legal || {};
    var seller = legal.seller;
    var foot = "";
    if (seller || legal.has_terms || opts.footer) {
      var lines = [];
      if (seller) {
        lines.push(esc([seller.legal_name || seller.name, seller.legal_form].filter(Boolean).join(" — ")));
        var ids = [seller.siren ? "SIREN " + seller.siren : "", seller.vat_number ? "TVA " + seller.vat_number : ""].filter(Boolean).join(" · ");
        if (ids) lines.push(esc(ids));
        if (seller.address) lines.push(esc(seller.address));
        if (seller.email) lines.push('<a href="mailto:' + esc(seller.email) + '">' + esc(seller.email) + '</a>');
        if (seller.mediator_name) lines.push("Médiateur de la consommation : " + (seller.mediator_url ? '<a href="' + esc(safeUrl(seller.mediator_url)) + '" target="_blank" rel="noopener">' + esc(seller.mediator_name) + '</a>' : esc(seller.mediator_name)));
      }
      var links = [];
      if (legal.has_terms) links.push('<button type="button" data-kv-terms>Conditions générales de vente</button>');
      if (opts.footer) links.push('<button type="button" data-kv-report>Signaler ce profil</button>');
      foot = '<div class="kv-foot">' + lines.join("<br>") + (links.length ? '<div class="lk">' + links.join("") + '</div>' : '') + '</div>';
    }
    var brand = p.show_kitly_branding
      ? '<div class="kv-brand">' + (opts.kitlyUrl ? '<a href="' + esc(opts.kitlyUrl) + '" target="_blank" rel="noopener">Propulsé par <b>kitly.</b></a>' : 'Propulsé par <b>kitly.</b>') + '</div>'
      : "";

    var cls = "kv t-" + th.tpl + " bg-" + th.bg + (opts.wide ? " wide" : "") + (opts.live ? " live" : "");
    return '<div class="' + cls + '" style="' + th.style + '"><div class="kv-col">' + html + foot + brand + '</div></div>';
  }


  // ============================================================
  // Rendu « site » : mini-site à sections (navigation, hero, à propos,
  // prestations, boutique, réalisations, contact, pied de page). Sur un bloc
  // étroit (téléphone), il se replie en colonne façon page de liens.
  // ============================================================
  function render(data, opts) {
    var layout = (data && data.theme && data.theme.layout) || "site";
    return layout === "links" ? renderLinks(data, opts) : renderSite(data, opts);
  }

  function initials(name) { return esc((name || "?").charAt(0).toUpperCase()); }
  function avatarHtml(p, cls) {
    return '<div class="' + cls + '" role="img" aria-label="' + esc(p.display_name) + '" style="background-image:' + cssUrl(p.avatar_url) + '">' + (p.avatar_url ? "" : initials(p.display_name)) + '</div>';
  }
  function socialsHtml(p) {
    var soc = (p.socials || []).filter(function (s) { return s.url; });
    return soc.length ? '<div class="kv-socials">' + soc.map(function (s) {
      return '<a class="kv-soc" href="' + esc(safeUrl(s.url)) + '" target="_blank" rel="noopener nofollow" title="' + esc(s.network) + '" aria-label="' + esc(s.network) + '">' + esc(SOCIAL_ABBR[s.network] || "•") + '</a>';
    }).join("") + '</div>' : "";
  }
  function blocksHtml(p) {
    var blocks = (p.blocks || []).filter(function (b) { return b.visible !== false && b.type !== "offer"; });
    return blocks.map(function (b) {
      if (b.type === "link") return '<a class="kv-link' + (b.icon ? "" : " no-ico") + '" href="' + esc(safeUrl(b.url)) + '" target="_blank" rel="noopener nofollow">' + (b.icon ? svg(b.icon) : "") + '<span>' + esc(b.label || "Mon lien") + '</span></a>';
      if (b.type === "video") { var t = b.thumbnail_url || ytThumb(b.url); return '<a class="kv-video" href="' + esc(safeUrl(b.url)) + '" target="_blank" rel="noopener nofollow" style="' + (t ? "background-image:" + cssUrl(t) : "") + '"><span class="play"></span>' + (b.label ? '<span class="cap">' + esc(b.label) + '</span>' : '') + '</a>'; }
      if (b.type === "text") return '<div class="kv-text">' + (b.title ? '<h5>' + esc(b.title) + '</h5>' : '') + '<p>' + esc(b.body || "") + '</p></div>';
      if (b.type === "header") return '<h3 class="kv-header">' + esc(b.title || "") + '</h3>';
      return "";
    }).join("");
  }
  function productCard(o) {
    var img = o.images && o.images[0];
    var out = o.details && o.details.in_stock === false;
    return '<button type="button" class="kv-offer" data-kv-offer="' + o.id + '"><div class="im" style="background-image:' + cssUrl(img) + '">' + (img ? "" : svg(KIND_ICON[o.kind])) + '</div>' +
      '<div class="bd">' + (o.badge ? '<span class="kv-tg">' + esc(o.badge) + '</span>' : '') + (out ? '<span class="kv-tg out">Épuisé</span>' : '') +
      '<div class="tt">' + esc(o.title) + '</div><div class="pr">' + esc(o.price_label || "") + '</div>' +
      (o.description ? '<div class="ds">' + esc(o.description) + '</div>' : '') +
      '<div class="ct">' + esc(out ? CTA_LABEL.contact : o.buyable ? "Acheter" : (CTA_LABEL[o.cta] || "Voir")) + ' →</div></div></button>';
  }
  function serviceCard(o) {
    var sub = offerSub(o);
    var img = o.images && o.images[0];
    return '<button type="button" class="kv-svc" data-kv-offer="' + o.id + '">' +
      (img ? '<div class="im" style="background-image:' + cssUrl(img) + '"></div>' : '') +
      '<div class="bd"><div class="top"><span class="tt">' + esc(o.title) + '</span><span class="pr">' + esc(o.price_label || "") + '</span></div>' +
      (sub ? '<div class="sb">' + esc(sub) + '</div>' : '') +
      (o.description ? '<p class="ds">' + esc(o.description) + '</p>' : '') +
      '<span class="ct">' + esc(o.buyable ? "Acheter" : (CTA_LABEL[o.cta] || "En savoir plus")) + ' →</span></div></button>';
  }
  function legalLines(p) {
    var seller = (p.legal || {}).seller, lines = [];
    if (!seller) return lines;
    lines.push(esc([seller.legal_name || seller.name, seller.legal_form].filter(Boolean).join(" — ")));
    var ids = [seller.siren ? "SIREN " + seller.siren : "", seller.vat_number ? "TVA " + seller.vat_number : ""].filter(Boolean).join(" · ");
    if (ids) lines.push(esc(ids));
    if (seller.address) lines.push(esc(seller.address));
    if (seller.email) lines.push('<a href="mailto:' + esc(seller.email) + '">' + esc(seller.email) + '</a>');
    if (seller.mediator_name) lines.push("Médiateur de la consommation : " + (seller.mediator_url ? '<a href="' + esc(safeUrl(seller.mediator_url)) + '" target="_blank" rel="noopener">' + esc(seller.mediator_name) + '</a>' : esc(seller.mediator_name)));
    return lines;
  }
  function contactFields(kind, prefill, name, privacyUrl, disabled) {
    var dis = disabled ? " disabled" : "";
    var privacy = privacyUrl ? ' (<a href="' + esc(privacyUrl) + '" target="_blank" rel="noopener">politique de confidentialité</a>)' : "";
    return '<div class="row"><div><label class="lb">Nom</label><input type="text" name="name" autocomplete="name" maxlength="120"' + dis + '></div>' +
      '<div><label class="lb">Email</label><input type="email" name="email" autocomplete="email"' + dis + '></div></div>' +
      (kind === "quote" ? '<div class="row"><div><label class="lb">Téléphone (facultatif)</label><input type="tel" name="phone" autocomplete="tel" maxlength="40"' + dis + '></div><div><label class="lb">Budget (facultatif)</label><input type="text" name="budget" maxlength="60" placeholder="ex. 500 €"' + dis + '></div></div>'
        : '<label class="lb">Téléphone (facultatif)</label><input type="tel" name="phone" autocomplete="tel" maxlength="40"' + dis + '>') +
      '<label class="lb">Message</label><textarea name="message" rows="5" maxlength="5000"' + dis + '>' + esc(prefill || "") + '</textarea>' +
      '<input type="text" name="website" class="hp" tabindex="-1" autocomplete="off" aria-hidden="true">' +
      '<label class="chk"><input type="checkbox" name="consent"' + dis + '><span>J\'accepte que ma demande et mes coordonnées soient transmises à ' + esc(name) + ' pour qu\'il ou elle me réponde' + privacy + '.</span></label>' +
      '<div class="err"></div><button type="submit" class="cta"' + dis + '>Envoyer</button>';
  }

  function renderSite(data, opts) {
    opts = opts || {};
    var p = data || {};
    var th = themeVars(p.theme);
    var offers = p.offers || [];
    var services = offers.filter(function (o) { return o.kind === "service" || o.kind === "appointment"; });
    var products = offers.filter(function (o) { return o.kind === "physical" || o.kind === "digital"; });
    var portfolio = p.portfolio || [];
    var contact = p.contact || {};
    var stats = (p.stats || []).filter(function (s) { return s.label || s.value; });
    var tags = (p.skills || []).concat(p.languages || []);
    var links = blocksHtml(p);
    var order = (p.sections && p.sections.length ? p.sections : ["hero", "links", "about", "offers", "products", "portfolio", "contact"].map(function (k) { return { key: k, visible: true }; }))
      .filter(function (x) { return x.visible !== false; }).map(function (x) { return x.key; });

    var has = {
      links: !!links, about: !!(p.about || stats.length || tags.length), offers: services.length > 0,
      products: products.length > 0, portfolio: portfolio.length > 0, contact: true
    };
    var NAV = { links: ["kv-links", "Liens"], about: ["kv-about", "À propos"], offers: ["kv-offers", "Prestations"],
                products: ["kv-shop", "Boutique"], portfolio: ["kv-work", "Réalisations"], contact: ["kv-contact", "Contact"] };
    var navItems = order.filter(function (k) { return NAV[k] && has[k] && k !== "links"; })
      .map(function (k) { return '<button type="button" data-kv-nav="' + NAV[k][0] + '">' + NAV[k][1] + '</button>'; }).join("");
    var firstOffers = has.offers ? "kv-offers" : has.products ? "kv-shop" : "";
    var meta = [p.headline, p.location].filter(Boolean).join(" · ");

    var parts = {
      links: function () {
        return '<section class="kv-s kv-s-links" id="kv-links"><div class="kv-wrap narrow"><div class="kv-links">' + links + '</div></div></section>';
      },
      about: function () {
        return '<section class="kv-s" id="kv-about"><div class="kv-wrap"><h2 class="kv-h">À propos</h2><div class="kv-about-grid"><div>' +
          (p.about ? '<p class="kv-about">' + esc(p.about) + '</p>' : (p.bio ? '<p class="kv-about">' + esc(p.bio) + '</p>' : '')) +
          (tags.length ? '<div class="kv-tags">' + tags.map(function (t) { return '<span>' + esc(t) + '</span>'; }).join("") + '</div>' : '') +
          '</div>' + (stats.length ? '<div class="kv-stats">' + stats.map(function (x) {
            return '<div class="kv-stat"><b>' + esc(x.value) + '</b><small>' + esc(x.label) + '</small></div>';
          }).join("") + '</div>' : '') + '</div></div></section>';
      },
      offers: function () {
        return '<section class="kv-s" id="kv-offers"><div class="kv-wrap"><h2 class="kv-h">Prestations</h2><div class="kv-svc-grid">' +
          services.map(serviceCard).join("") + '</div></div></section>';
      },
      products: function () {
        return '<section class="kv-s" id="kv-shop"><div class="kv-wrap"><h2 class="kv-h">Boutique</h2><div class="kv-offers kv-shop-grid">' +
          products.map(productCard).join("") + '</div></div></section>';
      },
      portfolio: function () {
        return '<section class="kv-s" id="kv-work"><div class="kv-wrap"><h2 class="kv-h">Réalisations</h2><div class="kv-projs">' + portfolio.map(function (it) {
          var cover = it.cover_url || (it.media && it.media[0] && it.media[0].type === "image" ? it.media[0].url : "");
          return '<button type="button" class="kv-proj" data-kv-project="' + it.id + '"><div class="im" style="background-image:' + cssUrl(cover) + '"></div><div class="tt">' + esc(it.title) + '</div></button>';
        }).join("") + '</div></div></section>';
      },
      contact: function () {
        var info = [];
        if (contact.email) info.push('<li><span>Email</span><a href="mailto:' + esc(contact.email) + '">' + esc(contact.email) + '</a></li>');
        if (contact.phone) info.push('<li><span>Téléphone</span><a href="tel:' + esc(String(contact.phone).replace(/[^\d+]/g, "")) + '">' + esc(contact.phone) + '</a></li>');
        if (contact.website) info.push('<li><span>Site</span><a href="' + esc(safeUrl(contact.website)) + '" target="_blank" rel="noopener">' + esc(String(contact.website).replace(/^https?:\/\//, "").replace(/\/$/, "")) + '</a></li>');
        if (p.location) info.push('<li><span>Lieu</span>' + esc(p.location) + '</li>');
        return '<section class="kv-s kv-s-contact" id="kv-contact"><div class="kv-wrap kv-contact-grid"><div class="kv-contact-txt">' +
          '<h2 class="kv-h">Travaillons <span class="kv-it">ensemble</span></h2>' +
          '<p>Un projet, une question ? Écris-moi : je te réponds directement par email.</p>' +
          (info.length ? '<ul class="kv-coords">' + info.join("") + '</ul>' : '') + socialsHtml(p) + '</div>' +
          '<form class="kv-form" data-kv-inline-form novalidate>' + contactFields("contact", "", p.display_name, opts.privacyUrl, !opts.live) + '</form></div></section>';
      }
    };

    var body = order.filter(function (k) { return parts[k] && has[k]; }).map(function (k) { return parts[k](); }).join("");
    if (opts.emptyHint && !offers.length && !links && !p.bio) {
      body += '<section class="kv-s"><div class="kv-wrap kv-empty">' + esc(opts.emptyHint) + '</div></section>';
    }

    var legal = p.legal || {};
    var lines = legalLines(p);
    var flinks = [];
    if (legal.has_terms) flinks.push('<button type="button" data-kv-terms>Conditions générales de vente</button>');
    if (opts.footer) flinks.push('<button type="button" data-kv-report>Signaler ce profil</button>');
    var brand = p.show_kitly_branding
      ? '<div class="kv-brand">' + (opts.kitlyUrl ? '<a href="' + esc(opts.kitlyUrl) + '" target="_blank" rel="noopener">Propulsé par <b>kitly.</b></a>' : 'Propulsé par <b>kitly.</b>') + '</div>'
      : "";

    var mediaSide = p.banner_url
      ? '<div class="kv-hero-media banner" style="background-image:' + cssUrl(p.banner_url) + '">' + avatarHtml(p, "kv-av3") + '</div>'
      : '<div class="kv-hero-media">' + avatarHtml(p, "kv-av-big") + '</div>';

    var html =
      '<header class="kv-nav"><div class="kv-wrap kv-nav-in">' +
        '<button type="button" class="kv-brandmark" data-kv-nav="kv-top">' + avatarHtml(p, "kv-av-xs") + '<span>' + esc(p.display_name || "Ton nom") + '</span></button>' +
        '<nav class="kv-menu">' + navItems + '</nav>' +
        '<div class="kv-nav-act"><button type="button" class="kv-btn sm" data-kv-contact-scroll>Me contacter</button>' +
        (navItems ? '<button type="button" class="kv-burger" data-kv-burger aria-label="Menu"><i></i><i></i></button>' : '') + '</div>' +
      '</div><nav class="kv-drawer">' + navItems + '</nav></header>' +
      '<section class="kv-s kv-hero2" id="kv-top"><div class="kv-wrap kv-hero-grid">' +
        '<div class="kv-hero-txt">' + avatarHtml(p, "kv-av-mob") +
          (meta ? '<p class="kv-kick">' + esc(meta) + '</p>' : '') +
          '<h1 class="kv-name">' + esc(p.display_name || "Ton nom") + '</h1>' +
          (p.bio ? '<p class="kv-lead">' + esc(p.bio) + '</p>' : '') +
          '<div class="kv-actions"><button type="button" class="kv-btn" data-kv-contact-scroll>Me contacter</button>' +
          (firstOffers ? '<button type="button" class="kv-btn ghost" data-kv-nav="' + firstOffers + '">Voir mes offres</button>' : '') + '</div>' +
          socialsHtml(p) +
        '</div>' + mediaSide +
      '</div></section>' + body +
      '<footer class="kv-foot2"><div class="kv-wrap"><div class="kv-foot-top"><b>' + esc(p.display_name || "") + '</b>' +
        (navItems ? '<nav>' + navItems + '</nav>' : '') + '</div>' +
        (lines.length ? '<div class="kv-foot-legal">' + lines.join(" · ") + '</div>' : '') +
        (flinks.length ? '<div class="kv-foot-links">' + flinks.join("") + '</div>' : '') + brand +
      '</div></footer>';

    var cls = "kv lay-site t-" + th.tpl + " bg-" + th.bg + (opts.live ? " live" : "") + (opts.wideNow ? " is-wide" : "");
    return '<div class="' + cls + '" style="' + th.style + '">' + html + '</div>';
  }

  // Adapte le rendu à la largeur réelle de son bloc (et non de l'écran) : le
  // builder peut ainsi afficher la même vitrine en « Mobile » et « Ordinateur ».
  function fit(root) {
    var kv = root && (root.classList && root.classList.contains("kv") ? root : root.querySelector(".kv"));
    if (!kv) return;
    var apply = function () { kv.classList.toggle("is-wide", kv.getBoundingClientRect().width / (kv.__scale || 1) >= 860 || kv.offsetWidth >= 860); };
    apply();
    try { new ResizeObserver(apply).observe(kv); } catch (e) { window.addEventListener("resize", apply); }
  }

  // ============================================================
  // Interactions (page publique)
  // ============================================================
  function mount(el, data, opts) {
    injectCss();
    opts = opts || {};
    var apiBase = (opts.apiBase || "").replace(/\/$/, "");
    var slug = data.slug;
    var source = opts.source === "marketplace" ? "marketplace" : "direct";
    el.innerHTML = render(data, { wide: opts.wide !== false, live: true, footer: true, kitlyUrl: opts.kitlyUrl, privacyUrl: opts.privacyUrl });
    var root = el.querySelector(".kv");
    fit(root);
    var HOST = window, frameEl = null;
    try { if (window.frameElement && window.parent.document) { HOST = window.parent; frameEl = window.frameElement; } } catch (e) {}

    // Défilement vers une section, y compris depuis un embed (c'est la page Framer qui défile).
    function scrollToId(id) {
      var target = root.querySelector("#" + id);
      if (!target) return;
      var offset = root.classList.contains("lay-site") ? 70 : 10;
      try {
        if (frameEl) {
          var fr = frameEl.getBoundingClientRect(), r = target.getBoundingClientRect();
          HOST.scrollTo({ top: HOST.scrollY + fr.top + r.top - (id === "kv-top" ? 0 : 10), behavior: "smooth" });
          return;
        }
      } catch (e) {}
      window.scrollTo({ top: window.scrollY + target.getBoundingClientRect().top - (id === "kv-top" ? 0 : offset), behavior: "smooth" });
    }
    // Lien direct vers une fiche offre : …#offre-12 (partageable).
    function setHash(h) {
      try { HOST.history.replaceState(HOST.history.state, "", HOST.location.pathname + HOST.location.search + (h ? "#" + h : "")); } catch (e) {}
    }
    var offersById = {};
    (data.offers || []).forEach(function (o) { offersById[o.id] = o; });
    var projectsById = {};
    (data.portfolio || []).forEach(function (p) { projectsById[p.id] = p; });

    function call(method, path, body) {
      return fetch(apiBase + "/kitly/public/profiles/" + encodeURIComponent(slug) + path, {
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

    function modal(html, onClose) {
      var bg = document.createElement("div");
      bg.className = "kv-mbg";
      bg.setAttribute("style", root.getAttribute("style"));  // typographies du thème
      bg.style.background = "rgba(10,10,10,.55)";
      bg.innerHTML = '<div class="kv-m" role="dialog" aria-modal="true"><button type="button" class="x" aria-label="Fermer">✕</button>' + html + '</div>';
      // Dans une iframe qui prend toute la hauteur du contenu (embed Framer), la
      // modale est placée sur la partie visible de la page, et la page est figée.
      var vp = opts.frameViewport ? opts.frameViewport() : null;
      if (vp) { bg.style.position = "absolute"; bg.style.top = vp.top + "px"; bg.style.height = vp.height + "px"; bg.style.bottom = "auto"; }
      document.body.appendChild(bg);
      var prevOverflow = document.body.style.overflow;
      document.body.style.overflow = "hidden";
      if (opts.lockScroll) opts.lockScroll(true);
      function close() { bg.remove(); document.body.style.overflow = prevOverflow; if (opts.lockScroll) opts.lockScroll(false); document.removeEventListener("keydown", onKey); if (onClose) onClose(); }
      function onKey(e) { if (e.key === "Escape") close(); }
      document.addEventListener("keydown", onKey);
      bg.addEventListener("click", function (e) { if (e.target === bg) close(); });
      bg.querySelector(".x").onclick = close;
      return { el: bg.querySelector(".kv-m"), close: close };
    }

    function offerFacts(o) {
      var d = o.details || {}, f = [];
      if (o.kind === "physical") {
        if (d.delivery_delay) f.push("Livraison : " + d.delivery_delay);
        if (d.pickup_available) f.push("Retrait sur place possible" + (d.pickup_location ? " (" + d.pickup_location + ")" : ""));
        if (d.in_stock === false) f.push("Actuellement épuisé");
      } else if (o.kind === "digital") {
        if (d.file_format) f.push("Format : " + d.file_format);
        f.push({ download: "Téléchargement après paiement", email_link: "Lien envoyé par email", account_access: "Accès en ligne" }[d.delivery_mode] || "Produit numérique");
        if (d.access_duration_days) f.push("Accès pendant " + d.access_duration_days + " jours");
        if (d.license) f.push("Licence : " + d.license);
      } else if (o.kind === "service") {
        if (d.duration_label) f.push("Délai : " + d.duration_label);
        if (d.location_mode) f.push({ remote: "Réalisée à distance", onsite: "En présentiel", both: "À distance ou en présentiel" }[d.location_mode]);
        if (d.revisions_included != null) f.push(d.revisions_included + " modification" + (d.revisions_included > 1 ? "s" : "") + " incluse" + (d.revisions_included > 1 ? "s" : ""));
        (d.deliverables || []).forEach(function (x) { f.push(x); });
      } else if (o.kind === "appointment") {
        if (d.duration_minutes) f.push("Durée : " + d.duration_minutes + " min");
        if (d.location_mode) f.push({ visio: "En visioconférence", onsite: "Sur place", phone: "Par téléphone" }[d.location_mode] + (d.location ? " — " + d.location : ""));
      }
      return f;
    }

    function openOffer(o) {
      var soldOut = o.details && o.details.in_stock === false;
      var imgs = (o.images || []).map(function (u) { return '<img src="' + esc(u) + '" alt="" loading="lazy">'; }).join("");
      var facts = offerFacts(o);
      setHash("offre-" + o.id);
      var m = modal(
        (imgs ? '<div class="gal">' + imgs + '</div>' : '') +
        (o.badge ? '<span class="kv-tg" style="--acc:#0a0a0a;--btn-fg:#fff">' + esc(o.badge) + '</span>' : '') +
        '<h3>' + esc(o.title) + '</h3><div class="price">' + esc(o.price_label || "") + '</div>' +
        (o.description ? '<div class="desc">' + esc(o.description) + '</div>' : '') +
        (facts.length ? '<ul class="facts">' + facts.map(function (x) { return '<li>' + esc(x) + '</li>'; }).join("") + '</ul>' : '') +
        (o.buyable && !soldOut
          ? '<button type="button" class="cta" data-buy>Acheter — ' + esc(money(o.price_cents)) + '</button>' +
            '<button type="button" class="cta ghost" data-go>Poser une question</button>'
          : '<button type="button" class="cta" data-go>' + esc(soldOut ? CTA_LABEL.contact : (CTA_LABEL[o.cta] || "Me contacter")) + '</button>'),
        function () { setHash(""); }
      );
      var buy = m.el.querySelector("[data-buy]");
      if (buy) buy.onclick = function () { m.close(); openBuy(o); };
      m.el.querySelector("[data-go]").onclick = function () {
        m.close();
        openContact(soldOut || o.buyable ? Object.assign({}, o, { cta: "contact" }) : o);
      };
    }

    function money(c) { return ((c || 0) / 100).toFixed(2).replace(".", ",").replace(",00", "") + " €"; }

    function pageUrl() {
      try { return HOST.location.href.split("#")[0]; } catch (e) { return window.location.href.split("#")[0]; }
    }
    function goTo(url) {
      try { if (HOST !== window) { HOST.location.href = url; return; } } catch (e) {}
      try { if (window.top && window.top !== window) { window.top.location.href = url; return; } } catch (e) {}
      window.location.href = url;
    }

    function openBuy(o) {
      var d = o.details || {};
      var variants = o.kind === "physical" ? (d.variants || []) : [];
      var ships = o.kind === "physical" ? (d.shipping_options || []) : [];
      var firstVariant = variants.filter(function (v) { return v.in_stock !== false; })[0];
      var delivery = "";
      if (o.kind === "physical") {
        var opts = ships.map(function (sh, i) {
          return '<label class="opt"><input type="radio" name="delivery" value="' + i + '"' + (i === 0 ? " checked" : "") + '><span>' + esc(sh.label) +
            (sh.delay ? '<small>' + esc(sh.delay) + '</small>' : '') + '</span><b>' + (sh.price_cents ? esc(money(sh.price_cents)) : "Offerte") + '</b></label>';
        });
        if (!ships.length) opts.push('<label class="opt"><input type="radio" name="delivery" value="ship" checked><span>Livraison' +
          (d.delivery_delay ? '<small>' + esc(d.delivery_delay) + '</small>' : '') + '</span><b>Incluse</b></label>');
        if (d.pickup_available) opts.push('<label class="opt"><input type="radio" name="delivery" value="pickup"><span>Retrait sur place' +
          (d.pickup_location ? '<small>' + esc(d.pickup_location) + '</small>' : '') + '</span><b>Gratuit</b></label>');
        delivery = '<label class="lb">Livraison</label><div class="opts">' + opts.join("") + '</div>';
      }
      var m = modal(
        '<h3>' + esc(o.title) + '</h3><p class="sub">Vendu par ' + esc(data.display_name) + ' · paiement sécurisé</p><form novalidate>' +
        (variants.length ? '<label class="lb">Option</label><select name="variant">' + variants.map(function (v) {
          return '<option value="' + esc(v.id) + '"' + (v.in_stock === false ? " disabled" : "") + (firstVariant && v.id === firstVariant.id ? " selected" : "") + '>' +
            esc(v.label) + (v.price_cents != null ? " — " + esc(money(v.price_cents)) : "") + (v.in_stock === false ? " (épuisé)" : "") + '</option>';
        }).join("") + '</select>' : '') +
        (o.kind === "physical" ? '<label class="lb">Quantité</label><select name="qty">' + [1, 2, 3, 4, 5, 6, 7, 8, 9, 10].map(function (n) {
          return '<option>' + n + '</option>'; }).join("") + '</select>' : '') +
        delivery +
        '<div class="row"><div><label class="lb">Email</label><input type="email" name="email" autocomplete="email" placeholder="pour la confirmation"></div>' +
        '<div><label class="lb">Nom</label><input type="text" name="name" autocomplete="name" maxlength="120"></div></div>' +
        '<label class="chk"><input type="checkbox" name="terms"><span>J\'ai lu et j\'accepte les <button type="button" class="lnk" data-read>conditions générales de vente</button> de ' + esc(data.display_name) + '.</span></label>' +
        '<div class="tpanel" hidden></div>' +
        (o.kind === "digital" ? '<label class="chk"><input type="checkbox" name="waive"><span>Je demande l\'accès immédiat au contenu numérique et je reconnais perdre mon droit de rétractation dès sa fourniture.</span></label>' : '') +
        (o.kind === "service" ? '<label class="chk"><input type="checkbox" name="early"><span>Je souhaite que la prestation commence avant la fin du délai de rétractation de 14 jours (facultatif). Si je me rétracte ensuite, je paierai la part déjà réalisée.</span></label>' : '') +
        '<div class="tot"><span>Total TTC</span><b data-total></b></div>' +
        '<div class="err"></div><button type="submit" class="cta" data-pay></button>' +
        '<div class="secure">Paiement par carte via Stripe. ' + esc(data.display_name) + ' reçoit ta commande dès le paiement confirmé.</div></form>'
      );
      var form = m.el.querySelector("form"), err = form.querySelector(".err"), pay = form.querySelector("[data-pay]");
      function val(n) { var x = form.querySelector('[name="' + n + '"]'); return x ? x.value : ""; }
      function total() {
        var v = variants.filter(function (x) { return x.id === val("variant"); })[0];
        var unit = v && v.price_cents != null ? v.price_cents : (o.price_cents || 0);
        var qty = parseInt(val("qty") || "1", 10);
        var chosen = form.querySelector('[name="delivery"]:checked');
        var ship = chosen && /^\d+$/.test(chosen.value) ? (ships[+chosen.value].price_cents || 0) : 0;
        return unit * qty + ship;
      }
      function refresh() { var t = money(total()); form.querySelector("[data-total]").textContent = t; pay.textContent = "Payer " + t; }
      form.addEventListener("change", refresh);
      refresh();
      form.querySelector("[data-read]").onclick = function () {
        var panel = form.querySelector(".tpanel");
        if (!panel.hidden) { panel.hidden = true; return; }
        panel.hidden = false;
        panel.innerHTML = '<p class="sub" style="margin:10px 0 0">Chargement…</p>';
        call("GET", "/terms").then(function (r) {
          panel.innerHTML = '<div class="terms">' + (r.terms.articles || []).map(function (a) { return '<h4>' + esc(a.title) + '</h4><p>' + esc(a.body) + '</p>'; }).join("") + '</div>';
        }).catch(function (e) { panel.innerHTML = '<p class="sub" style="margin:10px 0 0">' + esc(e.message) + '</p>'; });
      };
      form.onsubmit = function (e) {
        e.preventDefault();
        var email = val("email").trim();
        var chosen = form.querySelector('[name="delivery"]:checked');
        if (!/^[^\s@]+@[^\s@]+\.[a-zA-Z]{2,}$/.test(email)) return (err.textContent = "Indique une adresse email valide.");
        if (!form.querySelector('[name="terms"]').checked) return (err.textContent = "Merci d'accepter les conditions générales de vente.");
        var waive = form.querySelector('[name="waive"]');
        if (waive && !waive.checked) return (err.textContent = "Coche la case d'accès immédiat pour un produit numérique.");
        var early = form.querySelector('[name="early"]');
        var body = { email: email, name: val("name").trim(), accept_terms: true, source: source, return_url: pageUrl(),
                     quantity: parseInt(val("qty") || "1", 10), waive_withdrawal: !!(waive && waive.checked), early_start: !!(early && early.checked) };
        if (variants.length) body.variant_id = val("variant");
        if (chosen) body.delivery = chosen.value;
        pay.disabled = true; pay.textContent = "Redirection vers le paiement…"; err.textContent = "";
        call("POST", "/offers/" + o.id + "/checkout", body).then(function (r) { goTo(r.url); })
          .catch(function (e2) { pay.disabled = false; refresh(); err.textContent = e2.message; });
      };
    }

    // Retour de Stripe : …?commande=ok&ref=K… (paiement confirmé) ou ?commande=annulee.
    function orderReturn() {
      var loc;
      try { loc = HOST.location; } catch (e) { loc = window.location; }
      var q = new URLSearchParams(loc.search || ""), state = q.get("commande");
      if (!state) return;
      var ref = q.get("ref") || "";
      q.delete("commande"); q.delete("ref");
      try { HOST.history.replaceState(HOST.history.state, "", loc.pathname + (q.toString() ? "?" + q.toString() : "") + loc.hash); } catch (e) {}
      var m = modal(state === "ok"
        ? '<div class="done"><b>Merci pour ta commande ✓</b><p class="sub">' + (ref ? 'Référence <span style="font-weight:800;color:#0a0a0a">' + esc(ref) + '</span>. ' : '') +
          'Dès que Stripe a confirmé le paiement, tu reçois un email de confirmation et ' + esc(data.display_name) + ' est prévenu(e).</p>' +
          '<button type="button" class="cta" data-ok>Fermer</button></div>'
        : '<div class="done"><b>Paiement annulé</b><p class="sub">Aucun montant n\'a été débité. Tu peux réessayer quand tu veux.</p>' +
          '<button type="button" class="cta" data-ok>Fermer</button></div>');
      m.el.querySelector("[data-ok]").onclick = m.close;
    }

    function contactKind(offer) {
      return !offer ? "contact" : offer.cta === "quote" ? "quote" : offer.cta === "book" ? "booking" : "contact";
    }

    function bindContact(form, kind, offer, onDone) {
      var err = form.querySelector(".err");
      form.onsubmit = function (e) {
        e.preventDefault();
        var g = function (n) { var x = form.querySelector('[name="' + n + '"]'); return x ? x.value.trim() : ""; };
        var body = { kind: kind, name: g("name"), email: g("email"), phone: g("phone"), budget: g("budget"), message: g("message"),
                     website: g("website"), source: source, consent: form.querySelector('[name="consent"]').checked };
        if (offer) body.offer_id = offer.id;
        if (!body.name) return (err.textContent = "Indique ton nom.");
        if (!/^[^\s@]+@[^\s@]+\.[a-zA-Z]{2,}$/.test(body.email)) return (err.textContent = "Adresse email invalide.");
        if (!body.message) return (err.textContent = "Écris ton message.");
        if (!body.consent) return (err.textContent = "Merci d'accepter la transmission de ta demande.");
        var btn = form.querySelector(".cta"); btn.disabled = true; btn.textContent = "Envoi…"; err.textContent = "";
        call("POST", "/leads", body).then(function () { onDone(body.email); })
          .catch(function (e2) { btn.disabled = false; btn.textContent = "Envoyer"; err.textContent = e2.message; });
      };
    }

    function openContact(offer) {
      var kind = contactKind(offer);
      var titles = { contact: "Me contacter", quote: "Demander un devis", booking: "Demander un rendez-vous" };
      var prefill = "";
      if (offer && offer.cta === "order") prefill = "Bonjour, je souhaite commander « " + offer.title + " ».";
      else if (offer && kind === "booking") prefill = "Bonjour, je souhaite réserver « " + offer.title + " ». Mes disponibilités : ";
      else if (offer) prefill = "Bonjour, je suis intéressé(e) par « " + offer.title + " ». ";
      var m = modal(
        '<h3>' + titles[kind] + '</h3><p class="sub">' + esc(data.display_name) + ' te répondra directement par email.</p>' +
        '<form novalidate>' + contactFields(kind, prefill, data.display_name, opts.privacyUrl, false) + '</form>'
      );
      var form = m.el.querySelector("form");
      bindContact(form, kind, offer, function (email) {
        m.el.innerHTML = '<button type="button" class="x" aria-label="Fermer">✕</button><div class="done"><b>Message envoyé ✓</b><p class="sub">' +
          esc(data.display_name) + ' a bien reçu ta demande et te répondra à ' + esc(email) + '.</p><button type="button" class="cta" data-ok>Fermer</button></div>';
        m.el.querySelector(".x").onclick = m.close; m.el.querySelector("[data-ok]").onclick = m.close;
      });
      setTimeout(function () { var f = form.querySelector('[name="name"]'); if (f && window.innerWidth > 700) f.focus(); }, 50);
    }

    function openProject(it) {
      var media = (it.media || []).map(function (x) {
        return x.type === "video" ? '<a class="cta ghost" href="' + esc(safeUrl(x.url)) + '" target="_blank" rel="noopener">▶ Voir la vidéo</a>' : '<img src="' + esc(x.url) + '" alt="' + esc(x.caption || "") + '" loading="lazy">';
      });
      var imgs = (it.cover_url ? ['<img src="' + esc(it.cover_url) + '" alt="">'] : []).concat(media.filter(function (h) { return h.indexOf("<img") === 0; }));
      var videos = media.filter(function (h) { return h.indexOf("<a") === 0; });
      modal(
        (imgs.length ? '<div class="gal">' + imgs.join("") + '</div>' : '') + '<h3>' + esc(it.title) + '</h3>' +
        (it.client_name ? '<p class="sub">Client : ' + esc(it.client_name) + '</p>' : '') +
        (it.description ? '<div class="desc">' + esc(it.description) + '</div>' : '') + videos.join("") +
        (it.external_url ? '<a class="cta" href="' + esc(safeUrl(it.external_url)) + '" target="_blank" rel="noopener">Voir le projet</a>' : '')
      );
    }

    function openTerms() {
      var m = modal('<h3>Conditions générales de vente</h3><p class="sub">Chargement…</p>');
      call("GET", "/terms").then(function (r) {
        var t = r.terms;
        m.el.innerHTML = '<button type="button" class="x" aria-label="Fermer">✕</button><h3>Conditions générales de vente</h3>' +
          '<p class="sub">' + esc(r.seller) + ' — version ' + t.number + (t.effective_at ? ", en vigueur depuis le " + new Date(t.effective_at).toLocaleDateString("fr-FR") : "") + '</p>' +
          '<div class="terms">' + (t.articles || []).map(function (a) { return '<h4>' + esc(a.title) + '</h4><p>' + esc(a.body) + '</p>'; }).join("") + '</div>' +
          '<div class="disc">' + esc(t.disclaimer) + '</div>' +
          '<button type="button" class="cta ghost" data-print>Imprimer / enregistrer en PDF</button>';
        m.el.querySelector(".x").onclick = m.close;
        m.el.querySelector("[data-print]").onclick = function () { printTerms(r); };
      }).catch(function (e) { m.el.querySelector(".sub").textContent = e.message; });
    }

    function printTerms(r) {
      var w = window.open("", "_blank");
      if (!w) return;
      var t = r.terms;
      w.document.write('<!doctype html><meta charset="utf-8"><title>CGV — ' + esc(r.seller) + '</title><body style="font:14px/1.6 Arial;max-width:720px;margin:40px auto;padding:0 20px">' +
        '<h1>Conditions générales de vente</h1><p>' + esc(r.seller) + ' — version ' + t.number + '</p>' +
        (t.articles || []).map(function (a) { return '<h3>' + esc(a.title) + '</h3><p style="white-space:pre-wrap">' + esc(a.body) + '</p>'; }).join("") +
        '<p style="color:#666;font-size:12px;margin-top:30px">' + esc(t.disclaimer) + '<br>Empreinte : ' + esc(t.content_hash) + '</p></body>');
      w.document.close(); w.focus(); w.print();
    }

    function openReport() {
      var m = modal('<h3>Signaler ce profil</h3><p class="sub">L\'équipe Kitly examinera ton signalement.</p><form novalidate>' +
        '<label class="lb">Motif</label><select name="reason">' + Object.keys(REPORT_REASONS).map(function (k) { return '<option value="' + k + '">' + REPORT_REASONS[k] + '</option>'; }).join("") + '</select>' +
        '<label class="lb">Détails</label><textarea name="details" rows="4" maxlength="5000"></textarea>' +
        '<label class="lb">Ton email (facultatif, pour être tenu informé)</label><input type="email" name="email">' +
        '<div class="err"></div><button type="submit" class="cta">Envoyer le signalement</button></form>');
      var form = m.el.querySelector("form"), err = m.el.querySelector(".err");
      form.onsubmit = function (e) {
        e.preventDefault();
        var email = form.querySelector('[name="email"]').value.trim();
        var body = { reason: form.querySelector('[name="reason"]').value, details: form.querySelector('[name="details"]').value.trim() };
        if (email) body.email = email;
        call("POST", "/report", body).then(function () {
          m.el.innerHTML = '<button type="button" class="x" aria-label="Fermer">✕</button><div class="done"><b>Merci ✓</b><p class="sub">Ton signalement a été transmis à l\'équipe Kitly.</p></div>';
          m.el.querySelector(".x").onclick = m.close;
        }).catch(function (e2) { err.textContent = e2.message; });
      };
    }

    var inline = root.querySelector("[data-kv-inline-form]");
    if (inline) bindContact(inline, "contact", null, function (email) {
      inline.innerHTML = '<div class="done"><b>Message envoyé ✓</b><p>' + esc(data.display_name) + ' te répondra à ' + esc(email) + '.</p></div>';
    });

    root.addEventListener("click", function (e) {
      var nav = e.target.closest("[data-kv-nav],[data-kv-burger],[data-kv-contact-scroll]");
      if (nav) {
        e.preventDefault();
        if (nav.hasAttribute("data-kv-burger")) { root.classList.toggle("nav-open"); return; }
        root.classList.remove("nav-open");
        if (nav.hasAttribute("data-kv-contact-scroll")) {
          if (inline && root.querySelector("#kv-contact")) {
            scrollToId("kv-contact");
            setTimeout(function () { var f = inline.querySelector('[name="name"]'); if (f) try { f.focus({ preventScroll: true }); } catch (x) {} }, 500);
          } else openContact(null);
          return;
        }
        scrollToId(nav.getAttribute("data-kv-nav"));
        return;
      }
      var t = e.target.closest("[data-kv-offer],[data-kv-project],[data-kv-contact],[data-kv-terms],[data-kv-report]");
      if (!t) return;
      e.preventDefault();
      if (t.hasAttribute("data-kv-offer")) { var o = offersById[t.getAttribute("data-kv-offer")]; if (o) openOffer(o); }
      else if (t.hasAttribute("data-kv-project")) { var it = projectsById[t.getAttribute("data-kv-project")]; if (it) openProject(it); }
      else if (t.hasAttribute("data-kv-contact")) openContact(null);
      else if (t.hasAttribute("data-kv-terms")) openTerms();
      else if (t.hasAttribute("data-kv-report")) openReport();
    });

    setTimeout(orderReturn, 200);
    try {
      var m0 = (HOST.location.hash || "").match(/^#offre-(\d+)$/);
      if (m0 && offersById[m0[1]]) setTimeout(function () { openOffer(offersById[m0[1]]); }, 300);
    } catch (e) {}

    if (opts.countView !== false && slug) {
      // Une visite par visiteur et par jour, comptée côté serveur.
      call("POST", "/view", { source: source }).catch(function () {});
    }
    return { openContact: openContact, openOffer: function (id) { if (offersById[id]) openOffer(offersById[id]); } };
  }

  global.KitlyVitrine = {
    version: 2,
    render: function (data, opts) { injectCss(); return render(data, opts); },
    fit: fit,
    mount: mount,
    themeVars: themeVars,
    presets: TEMPLATE_PRESET,
    esc: esc
  };
})(window);
