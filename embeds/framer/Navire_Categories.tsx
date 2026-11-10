// Navire_Categories.tsx
// Section « Catég. » de la console admin (Navire_AdminConsole.tsx).
//
// Trois onglets :
//   - Universités, Niveaux : les listes de l'inscription et du profil
//     (GET/POST/PATCH/DELETE /admin/options). Ajouter, renommer (les comptes
//     suivent le nouveau libellé), réordonner, masquer, supprimer. Un niveau
//     peut « demander une vérification » : le choisir ouvre une demande au
//     lieu d'être appliqué (ex. Doctorant), avec le badge affiché une fois
//     validé et la consigne montrée à l'élève.
//   - Vérifs : demandes de statut en attente (GET /admin/verifications),
//     justificatif, validation, refus motivé, retrait d'un statut accordé.
//
// Mêmes jeton, design tokens et primitives que la console (charte NAVIRE
// AI) : le fichier est autonome pour ne pas dépendre de l'ordre des exports
// de la console.

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

// ── Types ──
type Kind = "university" | "study_level"
type Tab = Kind | "verifs"

interface Option {
    id: number
    kind: Kind
    label: string
    position: number
    active: boolean
    requires_verification: boolean
    badge: string | null
    verification_hint: string | null
    users_count: number
}

interface Verification {
    id: number
    label: string
    badge: string | null
    status: "pending" | "approved" | "rejected" | "revoked"
    message: string
    proof_url: string | null
    rejection_reason: string | null
    created_at: string | null
    reviewed_at: string | null
    user?: {
        id: number
        username: string
        email: string
        university: string | null
        study_level: string | null
    }
}

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

function Toggle({ on, onClick, label }: any) {
    return (
        <label
            onClick={onClick}
            style={{
                display: "flex",
                alignItems: "center",
                gap: 6,
                fontSize: 10.5,
                fontFamily: mono,
                color: t0,
                cursor: "pointer",
                userSelect: "none" as const,
            }}
        >
            <span
                style={{
                    width: 26,
                    height: 15,
                    borderRadius: 99,
                    background: on ? OK : sA,
                    position: "relative" as const,
                    transition: "background .15s",
                    flexShrink: 0,
                }}
            >
                <span
                    style={{
                        position: "absolute" as const,
                        top: 2,
                        left: on ? 13 : 2,
                        width: 11,
                        height: 11,
                        borderRadius: "50%",
                        background: on ? "#0a0a12" : t1,
                        transition: "left .15s",
                    }}
                />
            </span>
            {label}
        </label>
    )
}

const VIDE: React.CSSProperties = {
    padding: "32px 14px",
    textAlign: "center",
    fontFamily: mono,
    fontSize: 12,
    color: t2,
}

function fmtDate(iso: string | null) {
    if (!iso) return ""
    try {
        return new Date(iso).toLocaleDateString("fr-FR", {
            day: "2-digit",
            month: "short",
            hour: "2-digit",
            minute: "2-digit",
        })
    } catch {
        return iso
    }
}

// ══════════════════════════════════════════════════════════
// Listes (universités / niveaux)
// ══════════════════════════════════════════════════════════

function OptionRow({
    o,
    first,
    last,
    onMove,
    onSaved,
    onMsg,
}: {
    o: Option
    first: boolean
    last: boolean
    onMove: (dir: -1 | 1) => void
    onSaved: () => void
    onMsg: (m: string) => void
}) {
    const [open, setOpen] = React.useState(false)
    const [label, setLabel] = React.useState(o.label)
    const [badge, setBadge] = React.useState(o.badge || "")
    const [hint, setHint] = React.useState(o.verification_hint || "")
    const [busy, setBusy] = React.useState(false)

    React.useEffect(() => {
        setLabel(o.label)
        setBadge(o.badge || "")
        setHint(o.verification_hint || "")
    }, [o.id, o.label, o.badge, o.verification_hint])

    async function patch(body: any, ok?: string) {
        setBusy(true)
        try {
            await api(`/admin/options/${o.id}`, "PATCH", body)
            if (ok) onMsg(ok)
            onSaved()
        } catch (e: any) {
            onMsg("ERR " + e.message)
        } finally {
            setBusy(false)
        }
    }

    async function remove() {
        const warn = o.users_count
            ? `\n${o.users_count} compte(s) la portent : ils gardent leur valeur. « Masquer » suffit souvent.`
            : ""
        if (!window.confirm(`Supprimer « ${o.label} » ?${warn}`)) return
        setBusy(true)
        try {
            await api(`/admin/options/${o.id}`, "DELETE")
            onMsg(`« ${o.label} » supprimée.`)
            onSaved()
        } catch (e: any) {
            onMsg("ERR " + e.message)
        } finally {
            setBusy(false)
        }
    }

    return (
        <div style={{ borderBottom: `1px solid ${b0}`, opacity: o.active ? 1 : 0.55 }}>
            <div
                style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 8,
                    padding: "8px 14px",
                    cursor: "pointer",
                }}
                onClick={() => setOpen((v) => !v)}
            >
                <span style={{ display: "flex", flexDirection: "column", gap: 1 }}>
                    <button
                        disabled={first || busy}
                        onClick={(e) => {
                            e.stopPropagation()
                            onMove(-1)
                        }}
                        style={ARROW(first)}
                        title="Monter"
                    >
                        ▲
                    </button>
                    <button
                        disabled={last || busy}
                        onClick={(e) => {
                            e.stopPropagation()
                            onMove(1)
                        }}
                        style={ARROW(last)}
                        title="Descendre"
                    >
                        ▼
                    </button>
                </span>
                <span
                    style={{
                        flex: 1,
                        minWidth: 0,
                        fontSize: 12.5,
                        fontFamily: mono,
                        color: t0,
                        whiteSpace: "nowrap" as const,
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                    }}
                >
                    {o.label}
                </span>
                {o.requires_verification && (
                    <Tag color={WARN} border="rgba(255,209,102,0.35)">
                        vérif · {o.badge || o.label}
                    </Tag>
                )}
                {!o.active && <Tag>masquée</Tag>}
                <Tag color={o.users_count ? BLUE : t2}>{o.users_count} compte(s)</Tag>
            </div>

            {open && (
                <div
                    style={{
                        padding: "4px 14px 12px 40px",
                        display: "flex",
                        flexDirection: "column",
                        gap: 8,
                    }}
                >
                    <div style={{ display: "flex", gap: 6 }}>
                        <TI value={label} onChange={(e: any) => setLabel(e.target.value)} placeholder="Libellé" />
                        <Btn
                            disabled={busy || !label.trim() || label.trim() === o.label}
                            onClick={() => patch({ label }, "Renommée : les comptes suivent le nouveau libellé.")}
                        >
                            Renommer
                        </Btn>
                    </div>

                    {o.kind === "study_level" && (
                        <>
                            <Toggle
                                on={o.requires_verification}
                                onClick={() =>
                                    patch({
                                        requires_verification: !o.requires_verification,
                                        badge: o.badge || o.label,
                                    })
                                }
                                label="Demande une vérification (validée ici, onglet Vérifs)"
                            />
                            {o.requires_verification && (
                                <>
                                    <div style={{ display: "flex", gap: 6 }}>
                                        <TI
                                            value={badge}
                                            onChange={(e: any) => setBadge(e.target.value)}
                                            placeholder="Badge affiché sur le profil (ex. Doctorant)"
                                        />
                                    </div>
                                    <textarea
                                        value={hint}
                                        onChange={(e) => setHint(e.target.value)}
                                        placeholder="Ce que l'élève doit fournir (affiché dans le formulaire)"
                                        rows={2}
                                        maxLength={300}
                                        style={{
                                            ...INPUT,
                                            height: "auto",
                                            padding: "7px 10px",
                                            resize: "vertical",
                                            lineHeight: 1.5,
                                        }}
                                    />
                                    <div>
                                        <Btn
                                            disabled={
                                                busy ||
                                                (badge === (o.badge || "") &&
                                                    hint === (o.verification_hint || ""))
                                            }
                                            onClick={() =>
                                                patch({ badge, verification_hint: hint }, "Badge et consigne enregistrés.")
                                            }
                                        >
                                            Enregistrer badge et consigne
                                        </Btn>
                                    </div>
                                </>
                            )}
                        </>
                    )}

                    <div style={{ display: "flex", gap: 6 }}>
                        <Btn disabled={busy} onClick={() => patch({ active: !o.active })}>
                            {o.active ? "Masquer des listes" : "Réafficher"}
                        </Btn>
                        <Btn danger disabled={busy} onClick={remove}>
                            Supprimer
                        </Btn>
                    </div>
                </div>
            )}
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

function OptionsList({ kind }: { kind: Kind }) {
    const [items, setItems] = React.useState<Option[]>([])
    const [loading, setLoading] = React.useState(true)
    const [msg, setMsg] = React.useState("")
    const [search, setSearch] = React.useState("")
    const [newLabel, setNewLabel] = React.useState("")
    const [newVerif, setNewVerif] = React.useState(false)
    const [busy, setBusy] = React.useState(false)

    function load() {
        return api(`/admin/options?kind=${kind}`)
            .then((d) => setItems(d?.items || []))
            .catch((e) => setMsg("ERR " + e.message))
            .finally(() => setLoading(false))
    }
    React.useEffect(() => {
        setLoading(true)
        setMsg("")
        load()
    }, [kind])

    async function add() {
        const label = newLabel.trim()
        if (!label) return
        setBusy(true)
        try {
            await api("/admin/options", "POST", {
                kind,
                label,
                requires_verification: kind === "study_level" && newVerif,
            })
            setNewLabel("")
            setNewVerif(false)
            setMsg(`« ${label} » ajoutée.`)
            await load()
        } catch (e: any) {
            setMsg("ERR " + e.message)
        } finally {
            setBusy(false)
        }
    }

    // Échange la position avec la voisine (positions renumérotées si égales).
    async function move(index: number, dir: -1 | 1) {
        const a = items[index]
        const b = items[index + dir]
        if (!a || !b) return
        const pa = a.position === b.position ? index : a.position
        const pb = a.position === b.position ? index + dir : b.position
        try {
            await api(`/admin/options/${a.id}`, "PATCH", { position: pb })
            await api(`/admin/options/${b.id}`, "PATCH", { position: pa })
            await load()
        } catch (e: any) {
            setMsg("ERR " + e.message)
        }
    }

    const q = search.trim().toLowerCase()
    const shown = q ? items.filter((o) => o.label.toLowerCase().includes(q)) : items
    const actives = items.filter((o) => o.active).length

    return (
        <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
            <div
                style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 6,
                    padding: "10px 14px",
                    borderBottom: `1px solid ${b0}`,
                    flexShrink: 0,
                    flexWrap: "wrap" as const,
                }}
            >
                <TI
                    value={newLabel}
                    onChange={(e: any) => setNewLabel(e.target.value)}
                    onEnter={add}
                    placeholder={kind === "university" ? "Nouvelle université…" : "Nouveau niveau (ex. Doctorant)…"}
                    style={{ flex: 1, minWidth: 160 }}
                />
                {kind === "study_level" && (
                    <Toggle on={newVerif} onClick={() => setNewVerif((v) => !v)} label="à vérifier" />
                )}
                <Btn primary disabled={busy || !newLabel.trim()} onClick={add}>
                    + Ajouter
                </Btn>
            </div>
            <div
                style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    gap: 8,
                    padding: "8px 14px",
                    flexShrink: 0,
                }}
            >
                <span style={{ fontSize: 11, fontFamily: mono, color: t1 }}>
                    {items.length} au total · {actives} visible(s) à l'inscription
                </span>
                <TI
                    value={search}
                    onChange={(e: any) => setSearch(e.target.value)}
                    placeholder="Rechercher…"
                    style={{ width: 140, height: 26 }}
                />
            </div>
            <Msg text={msg} />
            <div style={{ flex: 1, overflowY: "auto", marginTop: 6, borderTop: `1px solid ${b0}` }}>
                {loading ? (
                    <div style={VIDE}>Chargement…</div>
                ) : shown.length === 0 ? (
                    <div style={VIDE}>Aucune catégorie.</div>
                ) : (
                    shown.map((o) => {
                        const i = items.indexOf(o)
                        return (
                            <OptionRow
                                key={o.id}
                                o={o}
                                first={!!q || i === 0}
                                last={!!q || i === items.length - 1}
                                onMove={(dir) => move(i, dir)}
                                onSaved={load}
                                onMsg={setMsg}
                            />
                        )
                    })
                )}
            </div>
        </div>
    )
}

// ══════════════════════════════════════════════════════════
// Vérifications de statut
// ══════════════════════════════════════════════════════════

const STATUS_LABEL: Record<string, [string, string]> = {
    pending: ["en attente", WARN],
    approved: ["validée", OK],
    rejected: ["refusée", BAD],
    revoked: ["retirée", t1],
}

function VerificationCard({ v, onDone, onMsg }: { v: Verification; onDone: () => void; onMsg: (m: string) => void }) {
    const [reason, setReason] = React.useState("")
    const [busy, setBusy] = React.useState(false)
    const [st, color] = STATUS_LABEL[v.status] || [v.status, t1]

    async function act(action: "approve" | "reject" | "revoke") {
        if (action === "revoke" && !window.confirm(`Retirer le statut ${v.label} à ${v.user?.username} ?`)) return
        setBusy(true)
        try {
            await api(
                `/admin/verifications/${v.id}/${action}`,
                "POST",
                action === "approve" ? undefined : { reason }
            )
            onMsg(
                action === "approve"
                    ? `${v.user?.username} : statut ${v.label} validé, badge ajouté.`
                    : action === "reject"
                      ? `${v.user?.username} : demande refusée.`
                      : `${v.user?.username} : statut retiré.`
            )
            onDone()
        } catch (e: any) {
            onMsg("ERR " + e.message)
        } finally {
            setBusy(false)
        }
    }

    return (
        <div
            style={{
                margin: "8px 14px",
                padding: "11px 12px",
                border: `1px solid ${b0}`,
                borderRadius: 12,
                background: s0,
                display: "flex",
                flexDirection: "column",
                gap: 7,
            }}
        >
            <div style={{ display: "flex", alignItems: "center", gap: 7, flexWrap: "wrap" as const }}>
                <span style={{ fontSize: 13, fontWeight: 700, fontFamily: mono }}>
                    {v.user?.username || "?"}
                </span>
                <span style={{ fontSize: 11, color: t1, fontFamily: code }}>#{v.user?.id}</span>
                <Tag color={WARN} border="rgba(255,209,102,0.35)">
                    {v.label}
                </Tag>
                <Tag color={color}>{st}</Tag>
                <span style={{ marginLeft: "auto", fontSize: 10.5, color: t2, fontFamily: code }}>
                    {fmtDate(v.created_at)}
                </span>
            </div>
            <div style={{ fontSize: 11, color: t1, fontFamily: mono, lineHeight: 1.5 }}>
                {v.user?.email}
                {v.user?.university ? ` · ${v.user.university}` : ""}
                {v.user?.study_level ? ` · niveau actuel : ${v.user.study_level}` : ""}
            </div>
            <div
                style={{
                    fontSize: 12,
                    color: v.message ? t0 : t2,
                    fontFamily: mono,
                    lineHeight: 1.55,
                    whiteSpace: "pre-wrap" as const,
                    padding: "8px 10px",
                    borderRadius: 9,
                    background: "rgba(0,0,0,0.18)",
                }}
            >
                {v.message || "Aucun message."}
            </div>
            {v.proof_url && (
                <a
                    href={v.proof_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    style={{ fontSize: 11.5, color: BLUE, fontFamily: mono }}
                >
                    Ouvrir le justificatif ↗
                </a>
            )}
            {v.rejection_reason && (
                <div style={{ fontSize: 11, color: BAD, fontFamily: mono }}>Motif : {v.rejection_reason}</div>
            )}

            {(v.status === "pending" || v.status === "approved") && (
                <TI
                    value={reason}
                    onChange={(e: any) => setReason(e.target.value)}
                    placeholder={v.status === "pending" ? "Motif du refus (envoyé par mail)" : "Motif du retrait"}
                />
            )}
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" as const }}>
                {(v.status === "pending" || v.status === "rejected") && (
                    <Btn primary disabled={busy} onClick={() => act("approve")}>
                        ✓ Valider le badge {v.badge || v.label}
                    </Btn>
                )}
                {v.status === "pending" && (
                    <Btn danger disabled={busy} onClick={() => act("reject")}>
                        Refuser
                    </Btn>
                )}
                {v.status === "approved" && (
                    <Btn danger disabled={busy} onClick={() => act("revoke")}>
                        Retirer le statut
                    </Btn>
                )}
            </div>
        </div>
    )
}

function VerificationsList({ onCount }: { onCount: (n: number) => void }) {
    const [filtre, setFiltre] = React.useState<"pending" | "approved" | "rejected" | "all">("pending")
    const [items, setItems] = React.useState<Verification[]>([])
    const [loading, setLoading] = React.useState(true)
    const [msg, setMsg] = React.useState("")

    function load() {
        return api(`/admin/verifications?status=${filtre}`)
            .then((d) => {
                setItems(d?.items || [])
                onCount(d?.pending_count || 0)
            })
            .catch((e) => setMsg("ERR " + e.message))
            .finally(() => setLoading(false))
    }
    React.useEffect(() => {
        setLoading(true)
        load()
    }, [filtre])

    return (
        <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
            <div
                style={{
                    display: "flex",
                    gap: 5,
                    padding: "10px 14px",
                    borderBottom: `1px solid ${b0}`,
                    flexShrink: 0,
                }}
            >
                <MiniBtn actif={filtre === "pending"} onClick={() => setFiltre("pending")}>
                    En attente
                </MiniBtn>
                <MiniBtn actif={filtre === "approved"} onClick={() => setFiltre("approved")}>
                    Validées
                </MiniBtn>
                <MiniBtn actif={filtre === "rejected"} onClick={() => setFiltre("rejected")}>
                    Refusées
                </MiniBtn>
                <MiniBtn actif={filtre === "all"} onClick={() => setFiltre("all")}>
                    Toutes
                </MiniBtn>
                <span style={{ marginLeft: "auto" }}>
                    <Btn onClick={() => load()}>↻</Btn>
                </span>
            </div>
            <Msg text={msg} />
            <div style={{ flex: 1, overflowY: "auto", paddingBottom: 10 }}>
                {loading ? (
                    <div style={VIDE}>Chargement…</div>
                ) : items.length === 0 ? (
                    <div style={VIDE}>
                        {filtre === "pending" ? "Aucune demande en attente." : "Aucune demande."}
                    </div>
                ) : (
                    items.map((v) => <VerificationCard key={v.id} v={v} onDone={load} onMsg={setMsg} />)
                )}
            </div>
        </div>
    )
}

// ══════════════════════════════════════════════════════════
// Section
// ══════════════════════════════════════════════════════════

export function CategoriesSection() {
    const [tab, setTab] = React.useState<Tab>("study_level")
    const [pending, setPending] = React.useState(0)

    // Pastille du nombre de demandes en attente, même hors de l'onglet Vérifs.
    React.useEffect(() => {
        api("/admin/verifications?status=pending&limit=1")
            .then((d) => setPending(d?.pending_count || 0))
            .catch(() => {})
    }, [])

    return (
        <div style={{ display: "flex", flexDirection: "column", height: "100%", maxHeight: "100%", color: t0 }}>
            <div
                style={{
                    display: "flex",
                    gap: 5,
                    padding: "10px 14px",
                    borderBottom: `1px solid ${b0}`,
                    flexShrink: 0,
                }}
            >
                <MiniBtn actif={tab === "study_level"} onClick={() => setTab("study_level")}>
                    Niveaux
                </MiniBtn>
                <MiniBtn actif={tab === "university"} onClick={() => setTab("university")}>
                    Universités
                </MiniBtn>
                <MiniBtn actif={tab === "verifs"} onClick={() => setTab("verifs")}>
                    Vérifs
                    {pending > 0 && (
                        <span
                            style={{
                                marginLeft: 6,
                                padding: "0 6px",
                                borderRadius: 99,
                                background: WARN,
                                color: "#0a0a12",
                                fontWeight: 800,
                            }}
                        >
                            {pending}
                        </span>
                    )}
                </MiniBtn>
            </div>
            <div style={{ flex: 1, minHeight: 0 }}>
                {tab === "verifs" ? (
                    <VerificationsList onCount={setPending} />
                ) : (
                    <OptionsList kind={tab} />
                )}
            </div>
        </div>
    )
}
