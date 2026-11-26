// Navire_Parrainage.tsx
// Section « Parrainage » de la console admin (Navire_AdminConsole.tsx).
//
// Parrains (élèves Prép'AdJuris inscrits au parrainage : lien, résultats,
// filleuls, suspendre / réactiver) et récompenses (crédit automatique sur le
// solde client Stripe du parrain après 14 jours ; « Créditer maintenant »,
// annulation, erreurs de crédit).
//
// Même jeton, design tokens et primitives que Navire_Ambassadeurs.tsx : le
// fichier est autonome pour ne pas dépendre de l'ordre des exports.

import * as React from "react"

const API_BASE = "https://navire-app-api.onrender.com"
const TOKEN_KEY = "navire_token"

function getToken(): string {
    try {
        return localStorage.getItem(TOKEN_KEY) || ""
    } catch {
        return ""
    }
}

async function api(path: string, method = "GET", body?: any) {
    const token = getToken()
    if (!token) throw new Error("Non connecté.")
    const res = await fetch(`${API_BASE}${path}`, {
        method,
        headers: {
            Authorization: `Bearer ${token}`,
            ...(body ? { "Content-Type": "application/json" } : {}),
        },
        body: body ? JSON.stringify(body) : undefined,
    })
    const raw = await res.text()
    let data: any = null
    try {
        data = raw ? JSON.parse(raw) : null
    } catch {
        data = raw
    }
    if (!res.ok) {
        const d = data?.detail
        const msg =
            d?.message ||
            (Array.isArray(d) ? d.map((x: any) => x.msg).join(" · ") : d) ||
            raw ||
            `HTTP ${res.status}`
        throw new Error(String(msg))
    }
    return data
}

// ── Design tokens (identiques à la console, charte NAVIRE AI) ──
const mono = "'Inter', system-ui, sans-serif"
const code = "'IBM Plex Mono', monospace"
const b0 = "rgba(255,255,255,0.07)"
const b1 = "rgba(255,255,255,0.14)"
const b2 = "rgba(255,255,255,0.22)"
const t0 = "#ffffff"
const t1 = "rgba(255,255,255,0.55)"
const t2 = "rgba(255,255,255,0.32)"
const s0 = "rgba(255,255,255,0.035)"
const sH = "rgba(255,255,255,0.06)"
const sA = "rgba(255,255,255,0.10)"

const MATIERE_LABELS: Record<string, string> = {
    L1_droit_constit: "L1 Constit.",
    L1_intro_au_droit: "L1 Intro",
    L1_droit_ijae: "L1 IJAE",
    L2_droit_administratif: "L2 Admin.",
    L2_droit_des_obligations: "L2 Oblig.",
    L2_droit_penal: "L2 Pénal",
    L3_droit_des_societes: "L3 Sociétés",
    L3_droit_des_suretes: "L3 Sûretés",
    L3_droit_des_contrats_speciaux: "L3 Contrats sp.",
    M1_distribution: "M1 Distrib.",
}

// ── Types ──
type Statut = "active" | "suspended"
type EtatRecompense = "pending" | "available" | "credited" | "cancelled"
type Vue = "parrains" | "recompenses"

interface Parrain {
    id: number
    user_id: number
    username: string | null
    email: string | null
    status: Statut
    code: string
    link: string
    display_name: string
    stripe_customer_id: string | null
    joined_at: string | null
    stats: { clicks: number; signups: number; paying: number; matieres: number }
    earnings: {
        pending_cents: number
        available_cents: number
        credited_cents: number
        total_earned_cents: number
    }
}
interface Filleul {
    id: number
    username: string | null
    email: string
    source: string
    joined_at: string
    status: "signed_up" | "paid" | "refunded"
    matieres: string[]
    reward_cents: number
}
interface Recompense {
    id: number
    parrain_id: number
    parrain: string | null
    parrain_code: string | null
    referral_email: string | null
    amount_cents: number
    matieres: string[]
    status: EtatRecompense
    available_at: string | null
    credited_at: string | null
    stripe_customer_id: string | null
    stripe_balance_transaction_id: string | null
    last_error: string | null
    cancelled_reason: string | null
    created_at: string | null
}
interface Resume {
    parrains: Record<Statut, number>
    referrals: number
    rewards: Record<string, number>
}

// ── Formats ──
function euros(c: number): string {
    return `${((c || 0) / 100).toFixed(2).replace(".", ",")} €`
}
function fmtDate(s: string | null): string {
    if (!s) return "—"
    return new Date(s).toLocaleDateString("fr-FR", {
        day: "2-digit",
        month: "short",
        year: "2-digit",
    })
}

// ── Primitives (mêmes rendus que la console) ──
function Btn({ onClick, disabled, danger, primary, children, style }: any) {
    const [h, setH] = React.useState(false)
    let bg = s0,
        bgH = sH,
        clr = t0,
        bdr = b1
    if (primary) {
        bg = "#fff"
        bgH = "#e8e8ee"
        clr = "#0a0a12"
        bdr = "#fff"
    } else if (danger) {
        bg = "rgba(255,107,107,0.08)"
        bgH = "rgba(255,107,107,0.14)"
        clr = "#FF6B6B"
        bdr = "rgba(255,107,107,0.3)"
    }
    return (
        <button
            onClick={onClick}
            disabled={disabled}
            onMouseEnter={() => setH(true)}
            onMouseLeave={() => setH(false)}
            style={{
                padding: "5px 10px",
                borderRadius: 10,
                border: `1px solid ${bdr}`,
                background: h ? bgH : bg,
                color: clr,
                fontSize: 10,
                fontFamily: mono,
                cursor: disabled ? "not-allowed" : "pointer",
                opacity: disabled ? 0.4 : 1,
                fontWeight: 600,
                whiteSpace: "nowrap" as const,
                ...style,
            }}
        >
            {children}
        </button>
    )
}

function MiniBtn({ actif, onClick, children }: any) {
    return (
        <button
            onClick={onClick}
            style={{
                fontSize: 10,
                fontFamily: mono,
                padding: "3px 8px",
                borderRadius: 99,
                border: `1px solid ${actif ? b2 : b0}`,
                background: actif ? sA : "transparent",
                color: actif ? t0 : t1,
                cursor: "pointer",
                fontWeight: actif ? 700 : 400,
                whiteSpace: "nowrap" as const,
            }}
        >
            {children}
        </button>
    )
}

function Tag({ children, color, border }: any) {
    return (
        <span
            style={{
                fontSize: 9.5,
                fontFamily: code,
                fontWeight: 600,
                letterSpacing: 0.4,
                padding: "2px 8px",
                borderRadius: 99,
                border: `1px solid ${border}`,
                color,
                whiteSpace: "nowrap" as const,
                flexShrink: 0,
            }}
        >
            {children}
        </span>
    )
}

const VERT = { color: "#5BE3A3", border: "rgba(91,227,163,0.4)" }
const JAUNE = { color: "#FFD166", border: "rgba(255,209,102,0.35)" }
const ROUGE = { color: "#FF6B6B", border: "rgba(255,107,107,0.4)" }
const GRIS = { color: t1, border: b0 }

function StatutTag({ s }: { s: Statut | EtatRecompense }) {
    const map: Record<string, [string, any]> = {
        active: ["ACTIF", VERT],
        suspended: ["SUSPENDU", ROUGE],
        pending: ["14 J", JAUNE],
        available: ["À CRÉDITER", JAUNE],
        credited: ["CRÉDITÉE", VERT],
        cancelled: ["ANNULÉE", ROUGE],
    }
    const [label, c] = map[s] || [String(s).toUpperCase(), GRIS]
    return (
        <Tag color={c.color} border={c.border}>
            {label}
        </Tag>
    )
}

function Msg({ text }: { text: string }) {
    if (!text) return null
    const err = text.startsWith("ERR")
    return (
        <div
            style={{
                fontSize: 10,
                fontFamily: mono,
                color: err ? "#FF6B6B" : "#5BE3A3",
                padding: "6px 14px",
                borderBottom: `1px solid ${b0}`,
            }}
        >
            {text}
        </div>
    )
}

function Case({ l, v }: { l: string; v: React.ReactNode }) {
    return (
        <div
            style={{
                padding: "5px 7px",
                border: `1px solid ${b0}`,
                borderRadius: 10,
                minWidth: 0,
            }}
        >
            <div
                style={{
                    fontSize: 8.5,
                    fontFamily: code,
                    fontWeight: 600,
                    color: t2,
                    textTransform: "uppercase" as const,
                    letterSpacing: 0.8,
                    marginBottom: 2,
                }}
            >
                {l}
            </div>
            <div
                style={{
                    fontSize: 10,
                    fontFamily: mono,
                    color: t0,
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap" as const,
                }}
                title={typeof v === "string" ? v : undefined}
            >
                {v}
            </div>
        </div>
    )
}

const VIDE: React.CSSProperties = {
    padding: "32px 14px",
    textAlign: "center",
    fontFamily: mono,
    fontSize: 12,
    color: t2,
}

const INPUT: React.CSSProperties = {
    height: 26,
    padding: "0 8px",
    background: s0,
    border: `1px solid ${b0}`,
    borderRadius: 10,
    color: t0,
    fontSize: 10,
    fontFamily: mono,
    outline: "none",
    boxSizing: "border-box",
}

// ── Ligne d'un parrain ──

function ParrainRow({
    p,
    onChanged,
    setMsg,
}: {
    p: Parrain
    onChanged: () => void
    setMsg: (m: string) => void
}) {
    const [open, setOpen] = React.useState(false)
    const [hov, setHov] = React.useState(false)
    const [busy, setBusy] = React.useState(false)
    const [filleuls, setFilleuls] = React.useState<Filleul[] | null>(null)

    React.useEffect(() => {
        if (open && filleuls === null) {
            api(`/parrainage/admin/${p.id}`)
                .then((d) => setFilleuls(d?.referrals || []))
                .catch(() => setFilleuls([]))
        }
    }, [open])

    async function action(path: string, ok: string) {
        setBusy(true)
        try {
            await api(`/parrainage/admin/${p.id}/${path}`, "POST")
            setMsg(`✓ ${ok}`)
            onChanged()
        } catch (e: any) {
            setMsg(`ERR: ${e.message}`)
        } finally {
            setBusy(false)
        }
    }

    return (
        <div style={{ borderBottom: `1px solid ${b0}` }}>
            <div
                onClick={() => setOpen((o) => !o)}
                onMouseEnter={() => setHov(true)}
                onMouseLeave={() => setHov(false)}
                style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 8,
                    padding: "8px 14px",
                    cursor: "pointer",
                    background: hov ? s0 : "transparent",
                }}
            >
                <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: 12, fontFamily: mono, color: t0, fontWeight: 600 }}>
                        {p.display_name}{" "}
                        <span style={{ color: t2, fontWeight: 400 }}>· {p.username || "—"}</span>
                    </div>
                    <div style={{ fontSize: 10, fontFamily: code, color: t1, marginTop: 2 }}>
                        {p.code} · {p.stats.signups} filleul(s) · {p.stats.paying} inscrit(s) ·{" "}
                        {euros(p.earnings.total_earned_cents)}
                    </div>
                </div>
                <StatutTag s={p.status} />
            </div>
            {open && (
                <div style={{ padding: "4px 14px 12px", display: "flex", flexDirection: "column", gap: 8 }}>
                    <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0,1fr))", gap: 5 }}>
                        <Case l="Email" v={p.email || "—"} />
                        <Case l="Client Stripe" v={p.stripe_customer_id || "aucun abonnement en cours"} />
                        <Case l="Inscrit le" v={fmtDate(p.joined_at)} />
                        <Case l="Visites" v={String(p.stats.clicks)} />
                        <Case l="14 j / à créditer" v={`${euros(p.earnings.pending_cents)} / ${euros(p.earnings.available_cents)}`} />
                        <Case l="Crédité" v={euros(p.earnings.credited_cents)} />
                    </div>
                    <div style={{ fontSize: 10, fontFamily: code, color: t1, wordBreak: "break-all" as const }}>{p.link}</div>
                    <div>
                        {filleuls === null ? (
                            <div style={{ fontSize: 10, color: t2, fontFamily: mono }}>Chargement des filleuls…</div>
                        ) : filleuls.length === 0 ? (
                            <div style={{ fontSize: 10, color: t2, fontFamily: mono }}>Aucun filleul.</div>
                        ) : (
                            filleuls.map((f) => (
                                <div
                                    key={f.id}
                                    style={{
                                        display: "flex",
                                        gap: 8,
                                        alignItems: "center",
                                        fontSize: 10,
                                        fontFamily: mono,
                                        color: t1,
                                        padding: "3px 0",
                                    }}
                                >
                                    <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis" }}>
                                        {f.username || "—"} · {f.email} · {f.source}
                                    </span>
                                    <span>{f.matieres.map((m) => MATIERE_LABELS[m] || m).join(", ")}</span>
                                    <span style={{ color: t0 }}>{f.reward_cents ? euros(f.reward_cents) : "—"}</span>
                                </div>
                            ))
                        )}
                    </div>
                    <div style={{ display: "flex", gap: 6 }}>
                        {p.status === "active" ? (
                            <Btn danger disabled={busy} onClick={() => action("suspend", "Lien suspendu")}>
                                Suspendre le lien
                            </Btn>
                        ) : (
                            <Btn disabled={busy} onClick={() => action("reactivate", "Lien réactivé")}>
                                Réactiver
                            </Btn>
                        )}
                    </div>
                </div>
            )}
        </div>
    )
}

// ── Récompenses ──

function RecompensesPanel({ setMsg }: { setMsg: (m: string) => void }) {
    const [filtre, setFiltre] = React.useState<EtatRecompense | "">("available")
    const [items, setItems] = React.useState<Recompense[]>([])
    const [total, setTotal] = React.useState(0)
    const [loading, setLoading] = React.useState(true)
    const [busy, setBusy] = React.useState<number | null>(null)
    const [tick, setTick] = React.useState(0)

    React.useEffect(() => {
        setLoading(true)
        api(`/parrainage/admin/rewards${filtre ? `?status=${filtre}` : ""}`)
            .then((d) => {
                setItems(d?.items || [])
                setTotal(d?.total_cents || 0)
            })
            .catch((e) => setMsg(`ERR: ${e.message}`))
            .finally(() => setLoading(false))
    }, [filtre, tick])

    async function crediter(r: Recompense) {
        setBusy(r.id)
        try {
            await api(`/parrainage/admin/rewards/${r.id}/credit`, "POST")
            setMsg(`✓ ${euros(r.amount_cents)} crédités sur le compte Stripe de ${r.parrain}`)
            setTick((t) => t + 1)
        } catch (e: any) {
            setMsg(`ERR: ${e.message}`)
        } finally {
            setBusy(null)
        }
    }
    async function annuler(r: Recompense) {
        const reason = window.prompt("Motif de l'annulation :")
        if (!reason || reason.trim().length < 2) return
        setBusy(r.id)
        try {
            await api(`/parrainage/admin/rewards/${r.id}/cancel`, "POST", { reason: reason.trim() })
            setMsg("✓ Récompense annulée")
            setTick((t) => t + 1)
        } catch (e: any) {
            setMsg(`ERR: ${e.message}`)
        } finally {
            setBusy(null)
        }
    }

    const filtres: [EtatRecompense | "", string][] = [
        ["available", "À créditer"],
        ["pending", "Délai 14 j"],
        ["credited", "Créditées"],
        ["cancelled", "Annulées"],
        ["", "Toutes"],
    ]
    return (
        <div style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0 }}>
            <div
                style={{
                    display: "flex",
                    gap: 5,
                    padding: "8px 14px",
                    borderBottom: `1px solid ${b0}`,
                    alignItems: "center",
                    flexWrap: "wrap" as const,
                }}
            >
                {filtres.map(([k, l]) => (
                    <MiniBtn key={k || "all"} actif={filtre === k} onClick={() => setFiltre(k)}>
                        {l}
                    </MiniBtn>
                ))}
                <span style={{ marginLeft: "auto", fontSize: 10, fontFamily: mono, color: t1 }}>
                    {items.length} · {euros(total)}
                </span>
            </div>
            <div style={{ padding: "6px 14px", fontSize: 10, fontFamily: mono, color: t2, borderBottom: `1px solid ${b0}` }}>
                Le crédit Stripe est automatique (job horaire) une fois le délai passé : rien à verser à la main.
                « À créditer » qui reste affiché = crédit en échec (voir l'erreur).
            </div>
            {loading ? (
                <div style={VIDE}>Chargement…</div>
            ) : items.length === 0 ? (
                <div style={VIDE}>Aucune récompense.</div>
            ) : (
                <div style={{ flex: 1, overflowY: "auto" }}>
                    {items.map((r) => (
                        <div
                            key={r.id}
                            style={{
                                display: "flex",
                                alignItems: "center",
                                gap: 8,
                                padding: "8px 14px",
                                borderBottom: `1px solid ${b0}`,
                            }}
                        >
                            <div style={{ flex: 1, minWidth: 0 }}>
                                <div style={{ fontSize: 11.5, fontFamily: mono, color: t0 }}>
                                    {r.parrain} <span style={{ color: t2 }}>({r.parrain_code})</span> ←{" "}
                                    {r.referral_email || "—"}
                                </div>
                                <div style={{ fontSize: 10, fontFamily: code, color: t1, marginTop: 2 }}>
                                    {r.matieres.map((m) => MATIERE_LABELS[m] || m).join(", ")} · payé le{" "}
                                    {fmtDate(r.created_at)}
                                    {r.status === "credited"
                                        ? ` · crédité le ${fmtDate(r.credited_at)} (${r.stripe_customer_id})`
                                        : r.status === "cancelled"
                                          ? ` · ${r.cancelled_reason || ""}`
                                          : ` · créditable le ${fmtDate(r.available_at)}`}
                                </div>
                                {r.last_error && r.status !== "credited" && (
                                    <div style={{ fontSize: 10, fontFamily: mono, color: "#FF6B6B", marginTop: 2 }}>
                                        {r.last_error}
                                    </div>
                                )}
                            </div>
                            <span style={{ fontSize: 12, fontFamily: mono, color: t0, fontWeight: 700 }}>
                                {euros(r.amount_cents)}
                            </span>
                            <StatutTag s={r.status} />
                            {(r.status === "pending" || r.status === "available") && (
                                <span style={{ display: "flex", gap: 4 }}>
                                    <Btn primary disabled={busy === r.id} onClick={() => crediter(r)}>
                                        Créditer maintenant
                                    </Btn>
                                    <Btn danger disabled={busy === r.id} onClick={() => annuler(r)}>
                                        Annuler
                                    </Btn>
                                </span>
                            )}
                        </div>
                    ))}
                </div>
            )}
        </div>
    )
}

// ── Section ──

export function ParrainageSection() {
    const [vue, setVue] = React.useState<Vue>("parrains")
    const [resume, setResume] = React.useState<Resume | null>(null)
    const [liste, setListe] = React.useState<Parrain[]>([])
    const [loading, setLoading] = React.useState(true)
    const [search, setSearch] = React.useState("")
    const [msg, setMsg] = React.useState("")
    const [tick, setTick] = React.useState(0)

    React.useEffect(() => {
        setLoading(true)
        Promise.all([
            api("/parrainage/admin/summary").then(setResume),
            api("/parrainage/admin/list").then((d) => setListe(d?.items || [])),
        ])
            .catch((e) => setMsg(`ERR: ${e.message}`))
            .finally(() => setLoading(false))
    }, [tick])

    const q = search.toLowerCase()
    const visibles = liste.filter(
        (p) =>
            !q ||
            [p.display_name, p.username || "", p.email || "", p.code].some((v) => v.toLowerCase().includes(q))
    )
    const r = resume?.rewards || {}

    return (
        <div style={{ display: "flex", flexDirection: "column", height: "100%", maxHeight: "100%" }}>
            <div
                style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    padding: "11px 14px",
                    borderBottom: `1px solid ${b0}`,
                    flexShrink: 0,
                    gap: 6,
                }}
            >
                <span style={{ display: "flex", gap: 5 }}>
                    <MiniBtn actif={vue === "parrains"} onClick={() => setVue("parrains")}>
                        Parrains
                    </MiniBtn>
                    <MiniBtn actif={vue === "recompenses"} onClick={() => setVue("recompenses")}>
                        Récompenses
                    </MiniBtn>
                </span>
                <span style={{ display: "flex", gap: 6, alignItems: "center" }}>
                    {vue === "parrains" && (
                        <input
                            value={search}
                            onChange={(e) => setSearch(e.target.value)}
                            placeholder="Rechercher…"
                            style={{ ...INPUT, width: 110, height: 28, fontSize: 12 }}
                        />
                    )}
                    <Btn onClick={() => setTick((t) => t + 1)} style={{ fontSize: 10, padding: "4px 10px" }}>
                        ↻
                    </Btn>
                </span>
            </div>

            <div
                style={{
                    display: "flex",
                    gap: 12,
                    padding: "6px 14px",
                    borderBottom: `1px solid ${b0}`,
                    fontSize: 10,
                    fontFamily: mono,
                    color: t1,
                    flexShrink: 0,
                    flexWrap: "wrap" as const,
                }}
            >
                <span>{resume?.parrains?.active ?? "…"} parrain(s)</span>
                <span>{resume?.referrals ?? "…"} filleul(s)</span>
                <span>14 j : {euros(r.pending_cents || 0)}</span>
                <span style={{ color: (r.available_cents || 0) > 0 ? "#FFD166" : t1 }}>
                    à créditer {euros(r.available_cents || 0)}
                </span>
                <span>crédité {euros(r.credited_cents || 0)}</span>
            </div>

            <Msg text={msg} />

            {vue === "recompenses" ? (
                <RecompensesPanel
                    key={tick}
                    setMsg={(m) => {
                        setMsg(m)
                        if (m.startsWith("✓")) api("/parrainage/admin/summary").then(setResume).catch(() => {})
                    }}
                />
            ) : loading ? (
                <div style={VIDE}>Chargement…</div>
            ) : visibles.length === 0 ? (
                <div style={VIDE}>Aucun parrain.</div>
            ) : (
                <div style={{ flex: 1, overflowY: "auto" }}>
                    {visibles.map((p) => (
                        <ParrainRow
                            key={`${p.id}-${p.status}`}
                            p={p}
                            setMsg={setMsg}
                            onChanged={() => setTick((t) => t + 1)}
                        />
                    ))}
                </div>
            )}
        </div>
    )
}
