// NAVIRE — bannière Prép'AdJuris, fine, au-dessus du header du site
// ====================================================================
// À coller dans Framer : Assets → Code → New Code File → « Navire_AdjurisBanner ».
// Puis placer le composant tout en haut du composant Header (ou de chaque
// page, au-dessus du header) : largeur « Fill », hauteur « Fit content ».
//
// Ce qu'elle affiche (même DA que NAVIRE Lib : bleu et blanc, sans trait) :
//   - inscrit Prép'AdJuris : « Prép'AdJuris L2 · Prochaine séance demain à
//     18:00 · TD 4 » → Mon espace Prépa. Si une séance commence dans moins
//     de 30 min (ou est en cours) et a un lien : « Rejoindre la séance ».
//   - paiement en échec / accès suspendu : bannière rouge → Mettre à jour ma carte.
//   - pas inscrit (ou pas connecté) : présentation courte → S'abonner
//     (masquable avec la propriété « Visiteurs »).
// Une croix la ferme jusqu'au lendemain (sauf le paiement en échec, qui
// revient à chaque visite). Si l'API ne répond pas, rien ne s'affiche :
// on ne propose jamais « S'abonner » à un inscrit par erreur.
//
// Routes : GET /lib/prepa, GET /prepa/adjuris/me (jeton « navire_token »
// du localStorage, comme les autres embeds NAVIRE).

import { useEffect, useState } from "react"
import { addPropertyControls, ControlType, RenderTarget } from "framer"

const API = "https://navire-app-api.onrender.com"
const TOKEN_KEY = "navire_token"
const DISMISS_KEY = "nv_adj_banner_closed"

type Seance = { date_debut: string; duree_minutes?: number; statut?: string; titre?: string; matiere?: string; lien?: string }
type Banner =
    | { kind: "member"; niveau: string; seance: Seance | null; live: boolean; inMin: number }
    | { kind: "warn"; label: string; suspendu: boolean }
    | { kind: "pitch" }
    | null

type Props = {
    subscribeUrl: string
    espaceUrl: string
    profilUrl: string
    showVisitors: boolean
}

function today() {
    const d = new Date()
    return `${d.getFullYear()}-${d.getMonth() + 1}-${d.getDate()}`
}
function safeUrl(u?: string) {
    return u && /^https?:\/\//.test(u) ? u : ""
}
function when(iso: string) {
    const d = new Date(iso)
    if (isNaN(d.getTime())) return ""
    const t0 = new Date()
    t0.setHours(0, 0, 0, 0)
    const diff = Math.round((new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime() - t0.getTime()) / 86400000)
    const day =
        diff === 0 ? "aujourd'hui" : diff === 1 ? "demain" : d.toLocaleDateString("fr-FR", { weekday: "long", day: "numeric", month: "long" })
    return `${day} à ${d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" })}`
}
// Version courte pour téléphone : « Demain 18:00 », « jeu. 14 · 18:00 »
function shortWhen(iso: string) {
    const d = new Date(iso)
    if (isNaN(d.getTime())) return ""
    const t0 = new Date()
    t0.setHours(0, 0, 0, 0)
    const diff = Math.round((new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime() - t0.getTime()) / 86400000)
    const h = d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" })
    if (diff === 0) return `Auj. ${h}`
    if (diff === 1) return `Demain ${h}`
    return `${d.toLocaleDateString("fr-FR", { weekday: "short", day: "numeric" })} · ${h}`
}
async function get(path: string, token: string) {
    const r = await fetch(API + path, { headers: { Authorization: `Bearer ${token}` } })
    if (!r.ok) {
        const e: any = new Error(`HTTP ${r.status}`)
        e.status = r.status
        throw e
    }
    return r.json()
}

/**
 * @framerSupportedLayoutWidth any
 * @framerSupportedLayoutHeight auto
 */
export default function Navire_AdjurisBanner(props: Props) {
    const { subscribeUrl, espaceUrl, profilUrl, showVisitors } = props
    const onCanvas = RenderTarget.current() === RenderTarget.canvas
    const [banner, setBanner] = useState<Banner>(null)
    const [closed, setClosed] = useState(false)

    useEffect(() => {
        if (onCanvas) {
            // Aperçu dans l'éditeur Framer
            setBanner({ kind: "member", niveau: "L2", seance: { date_debut: new Date(Date.now() + 86400000).toISOString(), titre: "TD 4 · La formation du contrat" }, live: false, inMin: 0 })
            return
        }
        let alive = true
        async function load() {
            let token = ""
            try {
                token = localStorage.getItem(TOKEN_KEY) || ""
            } catch {}
            if (!token) {
                if (alive) setBanner({ kind: "pitch" })
                return
            }
            try {
                const [p, me] = await Promise.all([get("/lib/prepa", token), get("/prepa/adjuris/me", token).catch(() => null)])
                const bad = ((me && me.items) || []).filter((i: any) => i.status === "payment_failed" || i.status === "suspendu")
                let next: Banner
                if (bad.length) {
                    next = { kind: "warn", label: bad.map((b: any) => b.label).join(", "), suspendu: bad[0].status === "suspendu" }
                } else if (p && p.prepa && !p.apercu) {
                    const s: Seance | null = p.prochaine_seance || null
                    const now = Date.now()
                    const t0 = s ? new Date(s.date_debut).getTime() : 0
                    const live = !!(s && safeUrl(s.lien) && t0 - now < 30 * 60000 && t0 + (s.duree_minutes || 60) * 60000 > now)
                    next = { kind: "member", niveau: p.niveau || "", seance: s, live, inMin: Math.max(0, Math.ceil((t0 - now) / 60000)) }
                } else {
                    next = { kind: "pitch" }
                }
                if (alive) setBanner(next)
            } catch (e: any) {
                // Session expirée : présentation ; autre erreur : rien
                if (alive) setBanner(e && e.status === 401 ? { kind: "pitch" } : null)
            }
        }
        load()
        // Connexion / déconnexion dans un autre onglet
        const onStorage = (e: StorageEvent) => {
            if (e.key === TOKEN_KEY) load()
        }
        window.addEventListener("storage", onStorage)
        return () => {
            alive = false
            window.removeEventListener("storage", onStorage)
        }
    }, [onCanvas])

    useEffect(() => {
        if (!banner || onCanvas || banner.kind === "warn") return setClosed(false)
        try {
            setClosed(localStorage.getItem(DISMISS_KEY) === `${banner.kind}:${today()}`)
        } catch {}
    }, [banner, onCanvas])

    if (!banner || closed) return null
    if (banner.kind === "pitch" && !showVisitors && !onCanvas) return null

    function close() {
        setClosed(true)
        try {
            localStorage.setItem(DISMISS_KEY, `${banner!.kind}:${today()}`)
        } catch {}
    }

    let text: JSX.Element
    let short: string
    let cta: { label: string; short?: string; href: string; blank?: boolean }
    const warn = banner.kind === "warn"

    if (banner.kind === "member") {
        const s = banner.seance
        const label = s ? s.titre || s.matiere || "" : ""
        text = (
            <>
                <b>Prép'AdJuris</b>
                {banner.niveau && <span className="nv-adjb-tag">{banner.niveau}</span>}
                <span className="nv-adjb-long">
                    {s ? (
                        <>
                            {banner.live ? (banner.inMin ? "Séance dans " : "Séance en cours") : "Prochaine séance "}
                            <b>{banner.live ? (banner.inMin ? `${banner.inMin} min` : "") : when(s.date_debut)}</b>
                            {label ? ` · ${label}` : ""}
                        </>
                    ) : (
                        "Ton emploi du temps, tes cours et tes devoirs"
                    )}
                </span>
            </>
        )
        short = s ? (banner.live ? (banner.inMin ? `Dans ${banner.inMin} min` : "En cours") : shortWhen(s.date_debut)) : ""
        cta = banner.live && s ? { label: "Rejoindre la séance", short: "Rejoindre", href: s.lien!, blank: true } : { label: "Mon espace Prépa", short: "Espace", href: espaceUrl }
    } else if (warn) {
        text = (
            <>
                <b className="nv-adjb-long">{banner.suspendu ? "Accès Prép'AdJuris suspendu" : "Paiement Prép'AdJuris en échec"}</b>
                <span className="nv-adjb-long">{banner.label}</span>
            </>
        )
        short = banner.suspendu ? "Accès suspendu" : "Paiement en échec"
        cta = { label: "Mettre à jour ma carte", short: "Ma carte", href: profilUrl }
    } else {
        text = (
            <>
                <b>Prép'AdJuris</b>
                <span className="nv-adjb-long">TD en petit groupe avec un chargé de TD, copies corrigées, cours de ta classe</span>
            </>
        )
        short = "TD en petit groupe"
        cta = { label: "S'abonner", href: subscribeUrl }
    }

    return (
        <div className={"nv-adjb" + (warn ? " is-warn" : "")} role="region" aria-label="Prép'AdJuris">
            <style>{CSS}</style>
            <a className="nv-adjb-main" href={cta.href} target={cta.blank ? "_blank" : "_top"} rel={cta.blank ? "noopener" : undefined}>
                <span className="nv-adjb-dot" />
                <span className="nv-adjb-text">
                    {text}
                    {short && <span className="nv-adjb-short nv-adjb-sub">{short}</span>}
                </span>
                <span className="nv-adjb-cta">
                    <span className="nv-adjb-long">{cta.label}</span>
                    <span className="nv-adjb-short">{cta.short || cta.label}</span>
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
                        <path d="M5 12h14M13 6l6 6-6 6" />
                    </svg>
                </span>
            </a>
            {!warn && (
                <button className="nv-adjb-x" onClick={close} aria-label="Fermer la bannière" title="Fermer">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round">
                        <path d="M18 6 6 18M6 6l12 12" />
                    </svg>
                </button>
            )}
        </div>
    )
}

const CSS = `
.nv-adjb { position: relative; width: 100%; height: 36px; display: flex; align-items: center; justify-content: center;
  font-family: 'Inter', system-ui, sans-serif; font-size: 12.5px; color: #fff; -webkit-font-smoothing: antialiased;
  background: linear-gradient(90deg, rgba(79,125,255,0.22), rgba(122,167,255,0.12) 50%, rgba(79,125,255,0.22)), #0b0d18;
  overflow: hidden; box-sizing: border-box; padding: 0 40px; }
.nv-adjb.is-warn { background: linear-gradient(90deg, rgba(255,107,107,0.22), rgba(255,107,107,0.12) 50%, rgba(255,107,107,0.22)), #140b10; }
.nv-adjb * { box-sizing: border-box; }
.nv-adjb-main { display: flex; align-items: center; gap: 10px; min-width: 0; max-width: 100%; color: inherit; text-decoration: none; }
.nv-adjb-dot { width: 6px; height: 6px; border-radius: 50%; background: #7AA7FF; box-shadow: 0 0 0 3px rgba(122,167,255,0.2); flex-shrink: 0; animation: nv-adjb-pulse 2.4s ease-in-out infinite; }
.is-warn .nv-adjb-dot { background: #FF6B6B; box-shadow: 0 0 0 3px rgba(255,107,107,0.2); }
@keyframes nv-adjb-pulse { 50% { box-shadow: 0 0 0 6px rgba(122,167,255,0); } }
.nv-adjb-text { display: flex; align-items: center; gap: 8px; min-width: 0; white-space: nowrap; overflow: hidden; color: rgba(255,255,255,0.75); }
.nv-adjb-text b { color: #fff; font-weight: 600; }
.nv-adjb-long { overflow: hidden; text-overflow: ellipsis; }
.nv-adjb-short { display: none; }
.nv-adjb-tag { font-family: 'IBM Plex Mono', monospace; font-size: 10px; font-weight: 600; padding: 1px 7px; border-radius: 99px; background: #7AA7FF; color: #0a0a12; }
.nv-adjb-cta { display: inline-flex; align-items: center; gap: 5px; flex-shrink: 0; font-weight: 600; color: #B5CDFF; }
.is-warn .nv-adjb-cta { color: #ffc4c4; }
.nv-adjb-cta svg { width: 13px; height: 13px; transition: transform .2s; }
.nv-adjb-main:hover .nv-adjb-cta svg { transform: translateX(3px); }
.nv-adjb-main:hover .nv-adjb-cta { color: #fff; }
.nv-adjb-x { position: absolute; right: 8px; top: 50%; transform: translateY(-50%); width: 26px; height: 26px; border: 0; border-radius: 8px;
  background: transparent; color: rgba(255,255,255,0.45); cursor: pointer; display: inline-flex; align-items: center; justify-content: center; padding: 0; }
.nv-adjb-x:hover { background: rgba(255,255,255,0.08); color: #fff; }
.nv-adjb-x svg { width: 14px; height: 14px; }
@media (max-width: 640px) {
  .nv-adjb { justify-content: flex-start; padding: 0 38px 0 14px; font-size: 12px; }
  .nv-adjb-main { flex: 1; }
  .nv-adjb-long { display: none; }
  .nv-adjb-short { display: inline; }
  .nv-adjb-sub { overflow: hidden; text-overflow: ellipsis; color: rgba(255,255,255,0.85); }
  .is-warn .nv-adjb-sub { color: #fff; font-weight: 600; }
  .nv-adjb-cta { margin-left: auto; }
}
@media (prefers-reduced-motion: reduce) { .nv-adjb-dot { animation: none; } }
`

Navire_AdjurisBanner.defaultProps = {
    subscribeUrl: "https://navire-ai.com/adjuris",
    espaceUrl: "https://navire-ai.com/lib?prepa=edt",
    profilUrl: "https://navire-ai.com/profil",
    showVisitors: true,
}

addPropertyControls(Navire_AdjurisBanner, {
    subscribeUrl: { type: ControlType.String, title: "Lien S'abonner" },
    espaceUrl: { type: ControlType.String, title: "Lien Espace" },
    profilUrl: { type: ControlType.String, title: "Lien carte" },
    showVisitors: { type: ControlType.Boolean, title: "Visiteurs", enabledTitle: "Afficher", disabledTitle: "Masquer" },
})
