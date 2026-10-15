// Navire_Ambassadeurs.tsx
// Section « Ambassadeurs » de la console admin (Navire_AdminConsole.tsx).
//
// Candidatures (valider / refuser avec motif), ambassadeurs (lien, résultats,
// filleuls, suspendre / réactiver) et commissions (à verser, export CSV par
// IBAN, « marquer comme versées », annulation).
//
// Mêmes jeton, design tokens et primitives que la console : le fichier est
// autonome pour ne pas dépendre de l'ordre des exports de la console.

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
type Statut = "pending" | "approved" | "rejected" | "suspended"
type EtatCommission = "pending" | "available" | "paid" | "cancelled"
type Vue = "candidatures" | "ambassadeurs" | "commissions"

interface Ambassadeur {
    id: number
    user_id: number
    username: string | null
    email: string | null
    status: Statut
    code: string | null
    link: string | null
    display_name: string
    prenom: string
    nom: string
    telephone: string | null
    university: string | null
    study_level: string | null
    motivation: string
    reseaux: string
    iban: string | null
    applied_at: string | null
    decided_at: string | null
    rejection_reason: string | null
    stats: { clicks: number; signups: number; paying: number; matieres: number }
    earnings: {
        pending_cents: number
        available_cents: number
        paid_cents: number
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
    commission_cents: number
}
interface Commission {
    id: number
    ambassador_id: number
    ambassador: string | null
    ambassador_code: string | null
    iban: string | null
    referral_email: string | null
    amount_cents: number
    matieres: string[]
    status: EtatCommission
    available_at: string | null
    paid_at: string | null
    paid_reference: string | null
    cancelled_reason: string | null
    created_at: string | null
}
interface Resume {
    ambassadors: Record<Statut, number>
    referrals: number
    commissions: Record<string, number>
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

function StatutTag({ s }: { s: Statut | EtatCommission }) {
    const map: Record<string, [string, any]> = {
        pending: ["EN ATTENTE", JAUNE],
        approved: ["ACTIF", VERT],
        rejected: ["REFUSÉ", ROUGE],
        suspended: ["SUSPENDU", ROUGE],
        available: ["À VERSER", VERT],
        paid: ["VERSÉE", GRIS],
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

function Bloc({ l, children }: { l: string; children: React.ReactNode }) {
    return (
        <div>
            <div
                style={{
                    fontSize: 8.5,
                    fontFamily: code,
                    fontWeight: 600,
                    color: t2,
                    textTransform: "uppercase" as const,
                    letterSpacing: 0.8,
                    marginBottom: 3,
                }}
            >
                {l}
            </div>
            <div
                style={{
                    fontSize: 11,
                    color: t0,
                    lineHeight: 1.55,
                    whiteSpace: "pre-wrap" as const,
                    padding: "6px 8px",
                    border: `1px solid ${b0}`,
                    borderRadius: 10,
                }}
            >
                {children}
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

// ── Ligne d'un ambassadeur / d'une candidature ──

function AmbassadeurRow({
    a,
    onChanged,
    setMsg,
}: {
    a: Ambassadeur
    onChanged: () => void
    setMsg: (m: string) => void
}) {
    const [open, setOpen] = React.useState(a.status === "pending")
    const [hov, setHov] = React.useState(false)
    const [busy, setBusy] = React.useState(false)
    const [motif, setMotif] = React.useState("")
    const [refus, setRefus] = React.useState(false)
    const [filleuls, setFilleuls] = React.useState<Filleul[] | null>(null)
    const [copie, setCopie] = React.useState(false)

    React.useEffect(() => {
        if (open && a.status !== "pending" && filleuls === null) {
            api(`/ambassadors/admin/${a.id}`)
                .then((d) => setFilleuls(d?.referrals || []))
                .catch(() => setFilleuls([]))
        }
    }, [open])

    async function action(chemin: string, corps?: any, ok?: string) {
        setBusy(true)
        try {
            await api(`/ambassadors/admin/${a.id}/${chemin}`, "POST", corps)
            setMsg(`✓ ${ok || chemin} · ${a.prenom} ${a.nom}`)
            onChanged()
        } catch (e: any) {
            setMsg(`ERR: ${e.message}`)
        } finally {
            setBusy(false)
        }
    }

    const nom = `${a.prenom} ${a.nom}`.trim() || a.username || "—"

    return (
        <div style={{ borderBottom: `1px solid rgba(255,255,255,0.04)` }}>
            <div
                onClick={() => setOpen((v) => !v)}
                onMouseEnter={() => setHov(true)}
                onMouseLeave={() => setHov(false)}
                style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 8,
                    padding: "9px 14px",
                    background: open ? sA : hov ? sH : "transparent",
                    borderLeft: `2px solid ${open ? b2 : "transparent"}`,
                    cursor: "pointer",
                    minWidth: 0,
                }}
            >
                <div style={{ flex: 1, minWidth: 0 }}>
                    <div
                        style={{
                            fontSize: 13,
                            fontWeight: 500,
                            color: t0,
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            whiteSpace: "nowrap" as const,
                        }}
                    >
                        {nom}
                        <span style={{ color: t1, fontSize: 11 }}>
                            {"  "}« {a.display_name} »
                        </span>
                    </div>
                    <div
                        style={{
                            fontSize: 10,
                            fontFamily: mono,
                            color: t1,
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            whiteSpace: "nowrap" as const,
                            marginTop: 1,
                        }}
                    >
                        {a.email} · {a.university || "—"}
                        {a.study_level ? ` · ${a.study_level}` : ""}
                    </div>
                </div>
                {a.status !== "pending" && (
                    <span
                        title="Comptes créés · inscriptions payées"
                        style={{
                            fontSize: 10,
                            fontFamily: mono,
                            color: t1,
                            flexShrink: 0,
                        }}
                    >
                        {a.stats.signups}·{a.stats.paying}
                    </span>
                )}
                {a.earnings.available_cents > 0 && (
                    <Tag color={VERT.color} border={VERT.border}>
                        {euros(a.earnings.available_cents)}
                    </Tag>
                )}
                <StatutTag s={a.status} />
                <span
                    style={{
                        fontSize: 11,
                        color: open ? t0 : t2,
                        flexShrink: 0,
                    }}
                >
                    ›
                </span>
            </div>

            {open && (
                <div
                    style={{
                        padding: "10px 14px 14px",
                        background: "rgba(255,255,255,0.015)",
                        display: "flex",
                        flexDirection: "column",
                        gap: 8,
                    }}
                >
                    <div
                        style={{
                            display: "grid",
                            gridTemplateColumns: "1fr 1fr 1fr",
                            gap: 6,
                        }}
                    >
                        <Case l="Compte" v={`#${a.user_id} ${a.username || ""}`} />
                        <Case l="Téléphone" v={a.telephone || "—"} />
                        <Case l="Candidature" v={fmtDate(a.applied_at)} />
                    </div>
                    <Case l="IBAN" v={a.iban || "non renseigné"} />

                    {a.code && (
                        <div style={{ display: "flex", gap: 6 }}>
                            <div style={{ flex: 1, minWidth: 0 }}>
                                <Case l={`Lien · ${a.code}`} v={a.link} />
                            </div>
                            <Btn
                                onClick={() => {
                                    try {
                                        navigator.clipboard.writeText(a.link || "")
                                    } catch {}
                                    setCopie(true)
                                    setTimeout(() => setCopie(false), 1400)
                                }}
                                style={{ alignSelf: "stretch" }}
                            >
                                {copie ? "✓" : "cp"}
                            </Btn>
                        </div>
                    )}

                    {a.status !== "pending" && (
                        <div
                            style={{
                                display: "grid",
                                gridTemplateColumns: "repeat(4, 1fr)",
                                gap: 6,
                            }}
                        >
                            <Case l="Visites" v={a.stats.clicks} />
                            <Case l="Comptes" v={a.stats.signups} />
                            <Case l="Payants" v={a.stats.paying} />
                            <Case
                                l="Gagné"
                                v={euros(a.earnings.total_earned_cents)}
                            />
                        </div>
                    )}

                    <Bloc l="Motivation">{a.motivation || "—"}</Bloc>
                    {a.reseaux && <Bloc l="Diffusion prévue">{a.reseaux}</Bloc>}
                    {a.rejection_reason && (
                        <Bloc l="Motif du refus">{a.rejection_reason}</Bloc>
                    )}

                    {a.status !== "pending" && (
                        <div>
                            <div
                                style={{
                                    fontSize: 8,
                                    fontFamily: mono,
                                    color: t2,
                                    textTransform: "uppercase" as const,
                                    letterSpacing: 0.8,
                                    marginBottom: 3,
                                }}
                            >
                                Filleuls{filleuls ? ` (${filleuls.length})` : ""}
                            </div>
                            <div
                                style={{
                                    border: `1px solid ${b0}`,
                                    borderRadius: 10,
                                    maxHeight: 150,
                                    overflowY: "auto",
                                }}
                            >
                                {filleuls === null ? (
                                    <div
                                        style={{
                                            fontSize: 10,
                                            fontFamily: mono,
                                            color: t2,
                                            padding: "6px 8px",
                                        }}
                                    >
                                        …
                                    </div>
                                ) : filleuls.length === 0 ? (
                                    <div
                                        style={{
                                            fontSize: 10,
                                            fontFamily: mono,
                                            color: t2,
                                            padding: "6px 8px",
                                        }}
                                    >
                                        Aucun.
                                    </div>
                                ) : (
                                    filleuls.map((f, i) => (
                                        <div
                                            key={f.id}
                                            style={{
                                                display: "flex",
                                                alignItems: "center",
                                                gap: 6,
                                                padding: "5px 8px",
                                                borderBottom:
                                                    i < filleuls.length - 1
                                                        ? `1px solid rgba(255,255,255,0.04)`
                                                        : "none",
                                                fontSize: 10,
                                                fontFamily: mono,
                                            }}
                                        >
                                            <span
                                                style={{
                                                    flex: 1,
                                                    color: t0,
                                                    overflow: "hidden",
                                                    textOverflow: "ellipsis",
                                                    whiteSpace: "nowrap" as const,
                                                }}
                                            >
                                                {f.username || "sans compte"} ·{" "}
                                                <span style={{ color: t1 }}>
                                                    {f.email}
                                                </span>
                                            </span>
                                            <span style={{ color: t2 }}>
                                                {fmtDate(f.joined_at)}
                                            </span>
                                            {f.status === "paid" ? (
                                                <Tag
                                                    color={VERT.color}
                                                    border={VERT.border}
                                                >
                                                    {euros(f.commission_cents)}
                                                </Tag>
                                            ) : f.status === "refunded" ? (
                                                <Tag
                                                    color={ROUGE.color}
                                                    border={ROUGE.border}
                                                >
                                                    REMB.
                                                </Tag>
                                            ) : (
                                                <Tag
                                                    color={GRIS.color}
                                                    border={GRIS.border}
                                                >
                                                    COMPTE
                                                </Tag>
                                            )}
                                        </div>
                                    ))
                                )}
                            </div>
                        </div>
                    )}

                    {refus && (
                        <input
                            value={motif}
                            onChange={(e) => setMotif(e.target.value)}
                            placeholder="Motif envoyé par email (facultatif)"
                            style={{ ...INPUT, width: "100%" }}
                        />
                    )}

                    <div style={{ display: "flex", gap: 6, flexWrap: "wrap" as const }}>
                        {a.status === "pending" && !refus && (
                            <>
                                <Btn
                                    primary
                                    disabled={busy}
                                    onClick={() =>
                                        action("approve", undefined, "Validé")
                                    }
                                >
                                    {busy ? "…" : "Valider →"}
                                </Btn>
                                <Btn
                                    danger
                                    disabled={busy}
                                    onClick={() => setRefus(true)}
                                >
                                    Refuser
                                </Btn>
                            </>
                        )}
                        {a.status === "pending" && refus && (
                            <>
                                <Btn
                                    danger
                                    disabled={busy}
                                    onClick={() =>
                                        action(
                                            "reject",
                                            { reason: motif || null },
                                            "Refusé"
                                        )
                                    }
                                >
                                    {busy ? "…" : "Confirmer le refus"}
                                </Btn>
                                <Btn onClick={() => setRefus(false)}>Retour</Btn>
                            </>
                        )}
                        {a.status === "approved" && (
                            <Btn
                                danger
                                disabled={busy}
                                onClick={() => {
                                    if (
                                        confirm(
                                            `Suspendre le lien de ${nom} ? Plus de nouveaux filleuls ni commissions ; les commissions acquises restent dues.`
                                        )
                                    )
                                        action("suspend", undefined, "Suspendu")
                                }}
                            >
                                Suspendre
                            </Btn>
                        )}
                        {a.status === "suspended" && (
                            <Btn
                                disabled={busy}
                                onClick={() =>
                                    action("reactivate", undefined, "Réactivé")
                                }
                            >
                                Réactiver
                            </Btn>
                        )}
                    </div>
                </div>
            )}
        </div>
    )
}

// ── Commissions ──

function CommissionsPanel({ setMsg }: { setMsg: (m: string) => void }) {
    const [filtre, setFiltre] = React.useState<EtatCommission | "">("available")
    const [items, setItems] = React.useState<Commission[]>([])
    const [loading, setLoading] = React.useState(true)
    const [sel, setSel] = React.useState<number[]>([])
    const [reference, setReference] = React.useState("")
    const [busy, setBusy] = React.useState(false)

    function load() {
        setLoading(true)
        setSel([])
        api(`/ambassadors/admin/commissions${filtre ? `?status=${filtre}` : ""}`)
            .then((d) => setItems(d?.items || []))
            .catch((e) => setMsg(`ERR: ${e.message}`))
            .finally(() => setLoading(false))
    }
    React.useEffect(() => {
        load()
    }, [filtre])

    const selectionnables = filtre === "available"
    const totalSel = items
        .filter((c) => sel.includes(c.id))
        .reduce((t, c) => t + c.amount_cents, 0)
    const total = items.reduce((t, c) => t + c.amount_cents, 0)

    async function verser() {
        if (!confirm(`Confirmer le virement de ${sel.length} commission(s) · ${euros(totalSel)} ?`))
            return
        setBusy(true)
        try {
            const r = await api("/ambassadors/admin/commissions/mark-paid", "POST", {
                ids: sel,
                reference: reference.trim() || null,
            })
            setMsg(`✓ ${r.paid.length} commission(s) versée(s) · ${euros(r.total_cents)}`)
            setReference("")
            load()
        } catch (e: any) {
            setMsg(`ERR: ${e.message}`)
        } finally {
            setBusy(false)
        }
    }

    async function annuler(c: Commission) {
        const motif = prompt("Motif de l'annulation :", "")
        if (!motif) return
        try {
            await api(`/ambassadors/admin/commissions/${c.id}/cancel`, "POST", {
                reason: motif,
            })
            setMsg(`✓ Commission #${c.id} annulée`)
            load()
        } catch (e: any) {
            setMsg(`ERR: ${e.message}`)
        }
    }

    // Une ligne par ambassadeur : prêt pour préparer les virements.
    function exporterCsv() {
        const parAmb: Record<number, any> = {}
        items.forEach((c) => {
            const a = (parAmb[c.ambassador_id] = parAmb[c.ambassador_id] || {
                nom: c.ambassador,
                code: c.ambassador_code,
                iban: c.iban || "",
                n: 0,
                total: 0,
                ids: [] as number[],
            })
            a.n += 1
            a.total += c.amount_cents
            a.ids.push(c.id)
        })
        const rows = [["Ambassadeur", "Code", "IBAN", "Commissions", "Montant (EUR)", "IDs"]]
        Object.values(parAmb).forEach((a: any) =>
            rows.push([
                a.nom,
                a.code,
                a.iban,
                a.n,
                (a.total / 100).toFixed(2).replace(".", ","),
                a.ids.join(" "),
            ])
        )
        const csv =
            "﻿" +
            rows
                .map((r) => r.map((v) => `"${String(v ?? "").replace(/"/g, '""')}"`).join(";"))
                .join("\n")
        const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }))
        const el = document.createElement("a")
        el.href = url
        el.download = `commissions-ambassadeurs-${new Date().toISOString().slice(0, 10)}.csv`
        document.body.appendChild(el)
        el.click()
        el.remove()
        setTimeout(() => URL.revokeObjectURL(url), 1500)
    }

    const filtres: [EtatCommission | "", string][] = [
        ["available", "À verser"],
        ["pending", "14 j"],
        ["paid", "Versées"],
        ["cancelled", "Annulées"],
        ["", "Toutes"],
    ]

    return (
        <div style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0 }}>
            <div
                style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 5,
                    padding: "7px 14px",
                    borderBottom: `1px solid ${b0}`,
                    flexShrink: 0,
                }}
            >
                {filtres.map(([id, label]) => (
                    <MiniBtn key={id || "all"} actif={filtre === id} onClick={() => setFiltre(id)}>
                        {label}
                    </MiniBtn>
                ))}
                <div style={{ flex: 1 }} />
                <span style={{ fontSize: 10, fontFamily: mono, color: t2 }}>
                    {items.length} · {euros(total)}
                </span>
                <Btn onClick={exporterCsv} disabled={!items.length} style={{ fontSize: 9, padding: "3px 8px" }}>
                    ↓ CSV
                </Btn>
            </div>

            {selectionnables && items.length > 0 && (
                <div
                    style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 6,
                        padding: "7px 14px",
                        borderBottom: `1px solid ${b0}`,
                        flexShrink: 0,
                    }}
                >
                    <input
                        type="checkbox"
                        checked={sel.length === items.length}
                        onChange={(e) => setSel(e.target.checked ? items.map((c) => c.id) : [])}
                        title="Tout sélectionner"
                    />
                    <input
                        value={reference}
                        onChange={(e) => setReference(e.target.value)}
                        placeholder="Réf. du virement"
                        style={{ ...INPUT, flex: 1 }}
                    />
                    <Btn primary disabled={busy || !sel.length} onClick={verser}>
                        {busy ? "…" : `Versées (${sel.length}) · ${euros(totalSel)}`}
                    </Btn>
                </div>
            )}

            <div style={{ flex: 1, overflowY: "auto" }}>
                {loading ? (
                    <div style={VIDE}>Chargement…</div>
                ) : items.length === 0 ? (
                    <div style={VIDE}>Rien ici.</div>
                ) : (
                    items.map((c) => {
                        const info =
                            c.status === "pending"
                                ? `versable le ${fmtDate(c.available_at)}`
                                : c.status === "paid"
                                  ? `${fmtDate(c.paid_at)}${c.paid_reference ? ` · ${c.paid_reference}` : ""}`
                                  : c.status === "cancelled"
                                    ? c.cancelled_reason || ""
                                    : `depuis le ${fmtDate(c.available_at)}`
                        return (
                            <div
                                key={c.id}
                                style={{
                                    display: "flex",
                                    alignItems: "center",
                                    gap: 8,
                                    padding: "8px 14px",
                                    borderBottom: `1px solid rgba(255,255,255,0.04)`,
                                    minWidth: 0,
                                    background: sel.includes(c.id) ? sA : "transparent",
                                }}
                            >
                                {selectionnables && (
                                    <input
                                        type="checkbox"
                                        checked={sel.includes(c.id)}
                                        onChange={(e) =>
                                            setSel((l) =>
                                                e.target.checked
                                                    ? [...l, c.id]
                                                    : l.filter((x) => x !== c.id)
                                            )
                                        }
                                    />
                                )}
                                <div style={{ flex: 1, minWidth: 0 }}>
                                    <div
                                        style={{
                                            fontSize: 12,
                                            color: t0,
                                            overflow: "hidden",
                                            textOverflow: "ellipsis",
                                            whiteSpace: "nowrap" as const,
                                        }}
                                    >
                                        {c.ambassador || "—"}{" "}
                                        <span style={{ fontSize: 10, fontFamily: mono, color: t1 }}>
                                            ← {c.referral_email || "—"}
                                        </span>
                                    </div>
                                    <div
                                        style={{
                                            fontSize: 9,
                                            fontFamily: mono,
                                            color: t1,
                                            overflow: "hidden",
                                            textOverflow: "ellipsis",
                                            whiteSpace: "nowrap" as const,
                                        }}
                                    >
                                        {fmtDate(c.created_at)} ·{" "}
                                        {c.matieres.map((k) => MATIERE_LABELS[k] || k).join(", ")}
                                        {" · "}
                                        {c.iban || "IBAN manquant"}
                                        {info ? ` · ${info}` : ""}
                                    </div>
                                </div>
                                <span
                                    style={{
                                        fontSize: 12,
                                        fontFamily: mono,
                                        fontWeight: 700,
                                        color: t0,
                                        flexShrink: 0,
                                    }}
                                >
                                    {euros(c.amount_cents)}
                                </span>
                                <StatutTag s={c.status} />
                                {(c.status === "pending" || c.status === "available") && (
                                    <button
                                        onClick={() => annuler(c)}
                                        title="Annuler la commission"
                                        style={{
                                            background: "none",
                                            border: "none",
                                            color: t2,
                                            cursor: "pointer",
                                            fontSize: 12,
                                            flexShrink: 0,
                                        }}
                                    >
                                        ✕
                                    </button>
                                )}
                            </div>
                        )
                    })
                )}
            </div>
        </div>
    )
}

// ══════════════════════════════════════════════════════════
// Section exportée
// ══════════════════════════════════════════════════════════

export function AmbassadeursSection() {
    const [vue, setVue] = React.useState<Vue>("candidatures")
    const [resume, setResume] = React.useState<Resume | null>(null)
    const [liste, setListe] = React.useState<Ambassadeur[]>([])
    const [loading, setLoading] = React.useState(true)
    const [search, setSearch] = React.useState("")
    const [msg, setMsg] = React.useState("")
    const [tick, setTick] = React.useState(0)

    function load() {
        setLoading(true)
        Promise.all([
            api("/ambassadors/admin/summary").then(setResume),
            api("/ambassadors/admin/list").then((d) => setListe(d?.items || [])),
        ])
            .catch((e) => setMsg(`ERR: ${e.message}`))
            .finally(() => setLoading(false))
    }
    React.useEffect(() => {
        load()
    }, [tick])

    const q = search.toLowerCase()
    const visibles = liste.filter(
        (a) =>
            (vue === "candidatures" ? a.status === "pending" : a.status !== "pending") &&
            (!q ||
                [a.prenom, a.nom, a.email || "", a.display_name, a.code || "", a.university || ""].some(
                    (v) => v.toLowerCase().includes(q)
                ))
    )
    const nbAttente = resume?.ambassadors?.pending || 0
    const c = resume?.commissions || {}

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
                    <MiniBtn actif={vue === "candidatures"} onClick={() => setVue("candidatures")}>
                        Candidatures{nbAttente ? ` (${nbAttente})` : ""}
                    </MiniBtn>
                    <MiniBtn actif={vue === "ambassadeurs"} onClick={() => setVue("ambassadeurs")}>
                        Ambassadeurs
                    </MiniBtn>
                    <MiniBtn actif={vue === "commissions"} onClick={() => setVue("commissions")}>
                        Commissions
                    </MiniBtn>
                </span>
                <span style={{ display: "flex", gap: 6, alignItems: "center" }}>
                    {vue !== "commissions" && (
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
                <span>{resume?.ambassadors?.approved ?? "…"} actif(s)</span>
                <span>{resume?.referrals ?? "…"} filleul(s)</span>
                <span style={{ color: (c.available_cents || 0) > 0 ? "#5BE3A3" : t1 }}>
                    à verser {euros(c.available_cents || 0)}
                </span>
                <span>14 j : {euros(c.pending_cents || 0)}</span>
                <span>versé {euros(c.paid_cents || 0)}</span>
            </div>

            <Msg text={msg} />

            {vue === "commissions" ? (
                <CommissionsPanel
                    key={tick}
                    setMsg={(m) => {
                        setMsg(m)
                        if (m.startsWith("✓")) api("/ambassadors/admin/summary").then(setResume).catch(() => {})
                    }}
                />
            ) : loading ? (
                <div style={VIDE}>Chargement…</div>
            ) : visibles.length === 0 ? (
                <div style={VIDE}>
                    {vue === "candidatures" ? "Aucune candidature en attente." : "Aucun ambassadeur."}
                </div>
            ) : (
                <div style={{ flex: 1, overflowY: "auto" }}>
                    {visibles.map((a) => (
                        <AmbassadeurRow
                            key={`${a.id}-${a.status}`}
                            a={a}
                            setMsg={setMsg}
                            onChanged={() => setTick((t) => t + 1)}
                        />
                    ))}
                </div>
            )}
        </div>
    )
}
