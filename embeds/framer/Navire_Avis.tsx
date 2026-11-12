// Navire_Avis.tsx
// Section « Avis » de la console admin (Navire_AdminConsole.tsx).
//
// Avis affichés sur la page d'accueil (embeds/landing_avis.html). Chaque avis
// est relié à un vrai compte NAVIRE : le pseudo et la photo affichés sont ceux
// du compte. Routes : GET/POST /admin/avis, PATCH/DELETE /admin/avis/{id} ;
// recherche de compte : GET /admin/users?q=.
//
// Ajouter (compte, texte, note, sous-titre facultatif), modifier, réordonner,
// masquer, supprimer. Sans aucun avis affiché, la section de la page
// d'accueil ne montre rien.
//
// Mêmes jeton, design tokens et primitives que la console (charte NAVIRE
// AI) : le fichier est autonome.

import * as React from "react"

const API_BASE = "https://navire-app-api.onrender.com"
const TOKEN_KEY = "navire_token"
const ADMIN_CODE = "THORKISHERE"

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
            "X-Admin-Code": ADMIN_CODE,
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

// ── Design tokens (charte NAVIRE AI, identiques à la console) ──
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
const OK = "#5BE3A3"
const WARN = "#FFD166"
const BAD = "#FF6B6B"
const BLUE = "#7AA7FF"

// ── Primitives ──
const INPUT: React.CSSProperties = {
    width: "100%",
    height: 30,
    padding: "0 10px",
    background: s0,
    border: `1px solid ${b0}`,
    borderRadius: 10,
    color: t0,
    fontSize: 12,
    fontFamily: mono,
    outline: "none",
    boxSizing: "border-box",
}

function TI({ value, onChange, placeholder, style, onEnter }: any) {
    return (
        <input
            value={value}
            onChange={onChange}
            placeholder={placeholder}
            onKeyDown={(e) => {
                if (e.key === "Enter" && onEnter) onEnter()
            }}
            style={{ ...INPUT, ...style }}
        />
    )
}

function Btn({ onClick, disabled, danger, primary, children, style, title }: any) {
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
        clr = BAD
        bdr = "rgba(255,107,107,0.3)"
    }
    return (
        <button
            onClick={onClick}
            disabled={disabled}
            title={title}
            onMouseEnter={() => setH(true)}
            onMouseLeave={() => setH(false)}
            style={{
                padding: "5px 11px",
                borderRadius: 99,
                border: `1px solid ${bdr}`,
                background: h ? bgH : bg,
                color: clr,
                fontSize: 11,
                fontFamily: mono,
                cursor: disabled ? "not-allowed" : "pointer",
                opacity: disabled ? 0.4 : 1,
                fontWeight: 600,
                whiteSpace: "nowrap" as const,
                transition: "background 0.1s",
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
                fontSize: 10.5,
                fontFamily: mono,
                padding: "3px 10px",
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

function Tag({ children, color = t1, border = b0 }: any) {
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

function Msg({ text }: { text: string }) {
    if (!text) return null
    const err = text.startsWith("ERR")
    return (
        <div
            style={{
                margin: "8px 14px 0",
                fontSize: 11,
                fontFamily: mono,
                color: err ? BAD : OK,
                padding: "7px 11px",
                background: err ? "rgba(255,107,107,0.08)" : "rgba(91,227,163,0.08)",
                border: `1px solid ${err ? "rgba(255,107,107,0.2)" : "rgba(91,227,163,0.2)"}`,
                borderRadius: 10,
            }}
        >
            {text}
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

// ── Types ──
interface Compte {
    id: number
    username: string
    email: string
    avatar_url?: string | null
}

interface Avis {
    id: number
    texte: string
    note: number
    mention: string | null
    mention_affichee: string
    position: number
    active: boolean
    created_at: string | null
    user: Compte & { university: string; study_level: string }
}

const AREA: React.CSSProperties = {
    ...INPUT,
    height: "auto",
    padding: "7px 10px",
    resize: "vertical",
    lineHeight: 1.5,
}

function Avatar({ u, size = 26 }: { u: { username: string; avatar_url?: string | null }; size?: number }) {
    return (
        <span
            style={{
                width: size,
                height: size,
                borderRadius: "50%",
                flexShrink: 0,
                background: u.avatar_url ? `#1a2133 center / cover no-repeat url("${u.avatar_url}")` : sA,
                border: `1px solid ${b1}`,
                display: "grid",
                placeItems: "center",
                fontSize: size * 0.42,
                fontWeight: 700,
                fontFamily: mono,
                color: BLUE,
            }}
        >
            {u.avatar_url ? "" : (u.username || "?").charAt(0).toUpperCase()}
        </span>
    )
}

function Notes({ value, onChange }: { value: number; onChange: (n: number) => void }) {
    return (
        <span style={{ display: "inline-flex", gap: 2 }}>
            {[1, 2, 3, 4, 5].map((n) => (
                <button
                    key={n}
                    type="button"
                    onClick={() => onChange(n)}
                    title={`${n} / 5`}
                    style={{
                        border: "none",
                        background: "transparent",
                        padding: 0,
                        fontSize: 15,
                        lineHeight: 1,
                        cursor: "pointer",
                        color: n <= value ? WARN : "rgba(255,255,255,0.18)",
                    }}
                >
                    ★
                </button>
            ))}
        </span>
    )
}

// ── Recherche d'un compte (GET /admin/users?q=) ──
function ComptePicker({ value, onPick }: { value: Compte | null; onPick: (c: Compte | null) => void }) {
    const [q, setQ] = React.useState("")
    const [res, setRes] = React.useState<Compte[]>([])
    const [err, setErr] = React.useState("")

    React.useEffect(() => {
        const s = q.trim()
        if (s.length < 2) {
            setRes([])
            return
        }
        const t = setTimeout(() => {
            api(`/admin/users?q=${encodeURIComponent(s)}&limit=8`)
                .then((d) => {
                    setRes(d?.items || [])
                    setErr("")
                })
                .catch((e) => setErr(e.message))
        }, 250)
        return () => clearTimeout(t)
    }, [q])

    if (value) {
        return (
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <Avatar u={value} />
                <span style={{ fontSize: 12.5, fontFamily: mono, color: t0, fontWeight: 600 }}>{value.username}</span>
                <span style={{ fontSize: 11, fontFamily: mono, color: t2 }}>{value.email}</span>
                <span style={{ marginLeft: "auto" }}>
                    <Btn onClick={() => onPick(null)}>Changer</Btn>
                </span>
            </div>
        )
    }
    return (
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <TI value={q} onChange={(e: any) => setQ(e.target.value)} placeholder="Compte NAVIRE : pseudo ou email" />
            {err && <span style={{ fontSize: 11, color: BAD, fontFamily: mono }}>{err}</span>}
            {res.length > 0 && (
                <div style={{ border: `1px solid ${b0}`, borderRadius: 10, overflow: "hidden" }}>
                    {res.map((c) => (
                        <div
                            key={c.id}
                            onClick={() => {
                                onPick(c)
                                setQ("")
                                setRes([])
                            }}
                            style={{
                                display: "flex",
                                gap: 8,
                                alignItems: "center",
                                padding: "6px 10px",
                                cursor: "pointer",
                                fontFamily: mono,
                                fontSize: 12,
                                borderBottom: `1px solid ${b0}`,
                            }}
                            onMouseEnter={(e) => (e.currentTarget.style.background = sH)}
                            onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
                        >
                            <span style={{ color: t0, fontWeight: 600 }}>{c.username}</span>
                            <span style={{ color: t2, fontSize: 11 }}>{c.email}</span>
                            <span style={{ marginLeft: "auto", color: t2, fontSize: 10.5, fontFamily: code }}>#{c.id}</span>
                        </div>
                    ))}
                </div>
            )}
        </div>
    )
}

// ── Formulaire (ajout et modification) ──
function AvisForm({
    initial,
    onSubmit,
    onCancel,
    busy,
    submitLabel,
}: {
    initial?: Avis
    onSubmit: (body: any) => void
    onCancel?: () => void
    busy: boolean
    submitLabel: string
}) {
    const [compte, setCompte] = React.useState<Compte | null>(initial ? initial.user : null)
    const [texte, setTexte] = React.useState(initial?.texte || "")
    const [note, setNote] = React.useState(initial?.note || 5)
    const [mention, setMention] = React.useState(initial?.mention || "")

    return (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <ComptePicker value={compte} onPick={setCompte} />
            <textarea
                value={texte}
                onChange={(e) => setTexte(e.target.value)}
                placeholder="Texte de l'avis"
                rows={3}
                maxLength={600}
                style={AREA}
            />
            <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" as const }}>
                <Notes value={note} onChange={setNote} />
                <TI
                    value={mention}
                    onChange={(e: any) => setMention(e.target.value)}
                    placeholder="Sous-titre (vide : niveau · fac du compte)"
                    style={{ flex: 1, minWidth: 180 }}
                />
            </div>
            <div style={{ display: "flex", gap: 6 }}>
                <Btn
                    primary
                    disabled={busy || !compte || !texte.trim()}
                    onClick={() => compte && onSubmit({ user_id: compte.id, texte, note, mention })}
                >
                    {submitLabel}
                </Btn>
                {onCancel && <Btn onClick={onCancel}>Annuler</Btn>}
                <span style={{ marginLeft: "auto", fontSize: 10.5, fontFamily: code, color: t2, alignSelf: "center" }}>
                    {texte.length}/600
                </span>
            </div>
        </div>
    )
}

const ARROW = (off: boolean): React.CSSProperties => ({
    width: 16,
    height: 11,
    lineHeight: "9px",
    fontSize: 7,
    padding: 0,
    border: "none",
    background: "transparent",
    color: off ? "transparent" : t2,
    cursor: off ? "default" : "pointer",
})

function AvisRow({
    a,
    first,
    last,
    onMove,
    onSaved,
    onMsg,
}: {
    a: Avis
    first: boolean
    last: boolean
    onMove: (dir: -1 | 1) => void
    onSaved: () => void
    onMsg: (m: string) => void
}) {
    const [edit, setEdit] = React.useState(false)
    const [busy, setBusy] = React.useState(false)

    async function patch(body: any, ok?: string) {
        setBusy(true)
        try {
            await api(`/admin/avis/${a.id}`, "PATCH", body)
            if (ok) onMsg(ok)
            setEdit(false)
            onSaved()
        } catch (e: any) {
            onMsg("ERR " + e.message)
        } finally {
            setBusy(false)
        }
    }

    async function remove() {
        if (!window.confirm(`Supprimer l'avis de ${a.user.username} ?`)) return
        setBusy(true)
        try {
            await api(`/admin/avis/${a.id}`, "DELETE")
            onMsg("Avis supprimé.")
            onSaved()
        } catch (e: any) {
            onMsg("ERR " + e.message)
        } finally {
            setBusy(false)
        }
    }

    return (
        <div style={{ borderBottom: `1px solid ${b0}`, padding: "10px 14px", opacity: a.active ? 1 : 0.55 }}>
            {edit ? (
                <AvisForm initial={a} busy={busy} submitLabel="Enregistrer" onCancel={() => setEdit(false)}
                    onSubmit={(body) => patch(body, "Avis modifié.")} />
            ) : (
                <div style={{ display: "flex", gap: 10 }}>
                    <span style={{ display: "flex", flexDirection: "column", gap: 1, paddingTop: 4 }}>
                        <button disabled={first || busy} onClick={() => onMove(-1)} style={ARROW(first)} title="Monter">▲</button>
                        <button disabled={last || busy} onClick={() => onMove(1)} style={ARROW(last)} title="Descendre">▼</button>
                    </span>
                    <Avatar u={a.user} size={30} />
                    <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: 4 }}>
                        <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" as const }}>
                            <span style={{ fontSize: 12.5, fontFamily: mono, fontWeight: 700, color: t0 }}>{a.user.username}</span>
                            {a.mention_affichee && (
                                <span style={{ fontSize: 11, fontFamily: mono, color: t1 }}>{a.mention_affichee}</span>
                            )}
                            <span style={{ color: WARN, fontSize: 11, letterSpacing: 1 }}>
                                {"★".repeat(a.note)}
                                <span style={{ color: "rgba(255,255,255,0.18)" }}>{"★".repeat(5 - a.note)}</span>
                            </span>
                            {!a.active && <Tag>masqué</Tag>}
                        </div>
                        <div style={{ fontSize: 12, fontFamily: mono, color: t1, lineHeight: 1.5, whiteSpace: "pre-line" as const }}>
                            {a.texte}
                        </div>
                        <div style={{ display: "flex", gap: 6, marginTop: 4 }}>
                            <Btn disabled={busy} onClick={() => setEdit(true)}>Modifier</Btn>
                            <Btn disabled={busy} onClick={() => patch({ active: !a.active })}>
                                {a.active ? "Masquer" : "Réafficher"}
                            </Btn>
                            <Btn danger disabled={busy} onClick={remove}>Supprimer</Btn>
                        </div>
                    </div>
                </div>
            )}
        </div>
    )
}

// ══════════════════════════════════════════════════════════
// Section
// ══════════════════════════════════════════════════════════

export function AvisSection() {
    const [items, setItems] = React.useState<Avis[]>([])
    const [loading, setLoading] = React.useState(true)
    const [msg, setMsg] = React.useState("")
    const [adding, setAdding] = React.useState(false)
    const [busy, setBusy] = React.useState(false)
    const [formKey, setFormKey] = React.useState(0)

    function load() {
        return api("/admin/avis")
            .then((d) => setItems(d?.items || []))
            .catch((e) => setMsg("ERR " + e.message))
            .finally(() => setLoading(false))
    }
    React.useEffect(() => {
        load()
    }, [])

    async function add(body: any) {
        setBusy(true)
        try {
            await api("/admin/avis", "POST", body)
            setMsg("Avis ajouté : il apparaît sur la page d'accueil.")
            setAdding(false)
            setFormKey((k) => k + 1)
            load()
        } catch (e: any) {
            setMsg("ERR " + e.message)
        } finally {
            setBusy(false)
        }
    }

    // Échange la position avec le voisin (positions renumérotées si égales).
    async function move(i: number, dir: -1 | 1) {
        const j = i + dir
        if (j < 0 || j >= items.length) return
        const ordre = items.map((a) => a.id)
        ;[ordre[i], ordre[j]] = [ordre[j], ordre[i]]
        try {
            await Promise.all(
                ordre.map((id, k) => {
                    const a = items.find((x) => x.id === id)!
                    return a.position === k + 1 ? null : api(`/admin/avis/${id}`, "PATCH", { position: k + 1 })
                })
            )
        } catch (e: any) {
            setMsg("ERR " + e.message)
        }
        load()
    }

    const visibles = items.filter((a) => a.active).length

    return (
        <div style={{ display: "flex", flexDirection: "column", height: "100%", maxHeight: "100%", color: t0 }}>
            <div
                style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 8,
                    padding: "10px 14px",
                    borderBottom: `1px solid ${b0}`,
                    flexShrink: 0,
                }}
            >
                <span style={{ fontSize: 12, fontFamily: mono, color: t1 }}>
                    Avis de la page d'accueil · <span style={{ color: visibles ? OK : t2 }}>{visibles} affiché(s)</span>
                </span>
                <span style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
                    <Btn onClick={() => load()}>↻</Btn>
                    <Btn primary onClick={() => setAdding((v) => !v)}>{adding ? "Fermer" : "+ Ajouter un avis"}</Btn>
                </span>
            </div>
            <Msg text={msg} />
            {adding && (
                <div style={{ padding: "12px 14px", borderBottom: `1px solid ${b0}`, flexShrink: 0 }}>
                    <AvisForm key={formKey} busy={busy} submitLabel="Ajouter" onSubmit={add} />
                </div>
            )}
            <div style={{ flex: 1, overflowY: "auto", paddingBottom: 10 }}>
                {loading ? (
                    <div style={VIDE}>Chargement…</div>
                ) : items.length === 0 ? (
                    <div style={VIDE}>Aucun avis : la section avis de la page d'accueil n'affiche rien.</div>
                ) : (
                    items.map((a, i) => (
                        <AvisRow
                            key={a.id}
                            a={a}
                            first={i === 0}
                            last={i === items.length - 1}
                            onMove={(d) => move(i, d)}
                            onSaved={load}
                            onMsg={setMsg}
                        />
                    ))
                )}
            </div>
        </div>
    )
}
