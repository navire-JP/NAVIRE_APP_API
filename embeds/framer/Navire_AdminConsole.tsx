// Navire_AdminConsole.tsx
// 2 overrides : AdminConsoleFrame (583×590) · AdminUserPanel (261×189)
// Communication via localStorage("navire_admin_selected_user") + CustomEvent
//
// Terminal : ajout de /mails (liste des emails de l'app) et
// /mails test <clé|all> [email] (envoi de test, données fictives).
//
// AdJuris : onglet « Agenda » (calendrier des cours, séries, glisser-déposer,
// annulation avec aperçu de l'impact sur la facturation) et onglet
// « Factu. » (échéancier de chaque élève, alertes, lien de paiement manuel).
// Niveau M1 (Distribution) ajouté.
//
// Ambassadeurs : onglet « Amb. » (candidatures, ambassadeurs, commissions à
// verser). Le code de la section est dans Navire_Ambassadeurs.tsx.
//
// Charte NAVIRE AI : Inter (IBM Plex Mono pour les petits libellés),
// panneaux blancs translucides, pastilles, vert / jaune / rouge d'état.
// L'onglet Carts (paniers MEOLES) est retiré.
//
// NAVIRE Lib : onglet « Lib » (import des cours .docx / .pdf, points de
// passage, Elo, questions). Le code de la section est dans Navire_Lib.tsx.
//
// Professeurs : onglet « Prof. » (chargés de TD, groupes, salons Discord et
// liens, pilotage des rémunérations). Le code de la section est dans
// Navire_Professeurs.tsx.

import type { Override } from "framer"
import * as React from "react"
import { AmbassadeursSection } from "./Navire_Ambassadeurs.tsx"
import { LibSection } from "./Navire_Lib.tsx"
import { ProfesseursSection } from "./Navire_Professeurs.tsx"

// ── Hide all scrollbars globally ──
if (typeof document !== "undefined") {
    const _s = document.createElement("style")
    _s.textContent = `* { scrollbar-width: none !important; } *::-webkit-scrollbar { display: none !important; }`
    document.head.appendChild(_s)
    if (!document.getElementById("navire-fonts")) {
        const _f = document.createElement("link")
        _f.id = "navire-fonts"
        _f.rel = "stylesheet"
        _f.href =
            "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=IBM+Plex+Mono:wght@400;600&display=swap"
        document.head.appendChild(_f)
    }
}

const API_BASE = "https://navire-app-api.onrender.com"
const TOKEN_KEY = "navire_token"
const ADMIN_CODE = "THORKISHERE"
const SESSION_KEY = "navire_admin_v1"
const SELECTED_USER_KEY = "navire_admin_selected_user"

function getToken(): string {
    try {
        return localStorage.getItem(TOKEN_KEY) || ""
    } catch {
        return ""
    }
}

function uid(): string {
    return `${Date.now()}-${Math.random().toString(16).slice(2)}`
}

async function apiFetch(path: string, init: RequestInit = {}) {
    const token = getToken()
    if (!token) throw new Error("Non connecté.")
    const headers: Record<string, string> = {
        Authorization: `Bearer ${token}`,
        "X-Admin-Code": ADMIN_CODE,
        ...(init.headers as any),
    }
    const res = await fetch(`${API_BASE}${path}`, { ...init, headers })
    const raw = await res.text()
    let data: any = null
    try {
        data = raw ? JSON.parse(raw) : null
    } catch {
        data = raw
    }
    if (!res.ok) {
        const detail =
            data?.detail?.message ||
            data?.detail ||
            data?.message ||
            raw ||
            `HTTP ${res.status}`
        throw new Error(String(detail))
    }
    return data
}

// Télécharge un fichier protégé (Bearer + X-Admin-Code) puis l'ouvre / enregistre.
async function downloadAuth(path: string, filename: string) {
    const token = getToken()
    const res = await fetch(`${API_BASE}${path}`, {
        headers: {
            Authorization: `Bearer ${token}`,
            "X-Admin-Code": ADMIN_CODE,
        },
    })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const blob = await res.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement("a")
    a.href = url
    a.download = filename || "fichier.pdf"
    document.body.appendChild(a)
    a.click()
    a.remove()
    setTimeout(() => URL.revokeObjectURL(url), 1500)
}

function fmtDate(s: string | null): string {
    if (!s) return "—"
    return new Date(s).toLocaleDateString("fr-FR", {
        day: "2-digit",
        month: "short",
        year: "2-digit",
    })
}
function fmtDateLong(s: string): string {
    return new Date(s).toLocaleDateString("fr-FR", {
        day: "2-digit",
        month: "long",
        year: "numeric",
        hour: "2-digit",
        minute: "2-digit",
    })
}
function fmtSize(b: number): string {
    if (b < 1024) return `${b} o`
    if (b < 1024 * 1024) return `${(b / 1024).toFixed(0)} Ko`
    return `${(b / 1024 / 1024).toFixed(1)} Mo`
}
function fmtDateTime(s: string | null): string {
    if (!s) return "—"
    return new Date(s).toLocaleString("fr-FR", {
        day: "2-digit",
        month: "short",
        year: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
    })
}
function toLocalInput(iso: string | null): string {
    if (!iso) return ""
    const d = new Date(iso)
    const p = (n: number) => String(n).padStart(2, "0")
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`
}
function fromLocalInput(v: string): string {
    return v ? new Date(v).toISOString() : ""
}

// ── TYPES & INTERFACES ──

type Section =
    | "users"
    | "promos"
    | "actus"
    | "prepa"
    | "terminal"
    | "adjuris"
    | "ambassadeurs"
    | "lib"
    | "professeurs"

type Onglet =
    | "etudiants"
    | "prospects"
    | "ressources"
    | "agenda"
    | "facturation"
    | "promo"
type LogLine = { id: string; kind: "in" | "out" | "err" | "sys"; text: string }

interface User {
    id: number
    username: string
    email: string
    elo: number
    universite: string | null
    created_at: string
    subscription_tier: string | null
    subscription_status: string | null
    is_manual_subscription: boolean
}
interface PdfFile {
    id: number
    filename_original: string
    size_bytes: number
    created_at: string | null
    expires_at: string | null
}
interface QcmFileStat {
    file_id: number
    file_name: string
    sessions_count: number
    usage_pct: number
    success_rate_pct: number
}
interface UserStats {
    current_streak: number
    longest_streak: number
    total_days: number
    last_activity: string | null
    success_rate_pct: number
    total_correct: number
    total_wrong: number
    total_answered: number
    total_sessions: number
    complete_sessions: number
    abandoned_sessions: number
    qcm_files: QcmFileStat[]
    files: PdfFile[]
    files_count: number
}
interface PlanView {
    plan: string
    billing_cycle: string | null
    status: string
    is_manual: boolean
    current_period_end: string | null
    stripe_subscription_id: string | null
    stripe_customer_id: string | null
    stripe: {
        status?: string
        current_period_end?: string | null
        cancel_at_period_end?: boolean
        error?: string
    } | null
}

interface MatiereRow {
    matiere_key: string
    label: string
    niveau: string
    status: string
    stripe_subscription_id: string | null
    created_at: string | null
}
interface Etudiant {
    email: string
    prenom: string
    nom: string
    telephone?: string | null
    user_id: number | null
    username: string | null
    compte_cree: boolean
    discord_lie: boolean
    niveau: string
    matieres: MatiereRow[]
    nb_actives: number
    a_un_impaye: boolean
    premiere_inscription: string | null
}
interface Inscription {
    id: number
    prenom: string
    nom: string
    email: string
    telephone?: string | null
    niveau: string
    matieres: string[]
    matieres_labels: string[]
    created_at: string | null
}
interface Ressource {
    id: number
    niveau: string
    matiere_key: string | null
    matiere_label: string
    titre: string
    description: string
    filename_original: string
    size_bytes: number
    ordre: number
    is_published: boolean
}
interface Seance {
    id: number
    niveau: string
    matiere_key: string | null
    matiere_label: string
    titre: string
    date_debut: string | null
    duree_minutes: number
    lien: string
    statut: string
}

// Emails connus de l'API — alimente /mails dans le terminal.
interface MailRow {
    key: string
    label: string
    trigger: string
    subject: string
}

// Clés identiques à PREPA_PRICES côté backend.
const MATIERES: Record<string, { key: string; label: string }[]> = {
    L1: [
        { key: "L1_droit_constit", label: "Droit constitutionnel" },
        { key: "L1_intro_au_droit", label: "Introduction au droit" },
        { key: "L1_droit_ijae", label: "Droit IJAE" },
    ],
    L2: [
        { key: "L2_droit_administratif", label: "Droit administratif" },
        { key: "L2_droit_des_obligations", label: "Droit des obligations" },
        { key: "L2_droit_penal", label: "Droit pénal" },
    ],
    L3: [
        { key: "L3_droit_des_societes", label: "Droit des sociétés" },
        { key: "L3_droit_des_suretes", label: "Droit des sûretés" },
        {
            key: "L3_droit_des_contrats_speciaux",
            label: "Droit des contrats spéciaux",
        },
    ],
    M1: [{ key: "M1_distribution", label: "Distribution" }],
}

// Liste à plat pour un unique <select> (utilisé dans le panneau profil).
const MATIERES_FLAT: { key: string; label: string }[] = Object.entries(
    MATIERES
).flatMap(([niveau, list]) =>
    list.map((m) => ({ key: m.key, label: `${niveau} — ${m.label}` }))
)

// ── Design tokens (charte NAVIRE AI) ──
// `mono` reste le nom historique de la police d'interface : c'est désormais
// Inter. IBM Plex Mono (`code`) est réservée aux petits libellés et tags.
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
const FIELD = "#101018"

// ── Helpers pour diffuser la sélection ──
function broadcastSelectedUser(user: User | null) {
    try {
        localStorage.setItem(
            SELECTED_USER_KEY,
            user ? JSON.stringify(user) : ""
        )
    } catch {}
    try {
        window.dispatchEvent(
            new CustomEvent("navire:admin:user_selected", { detail: user })
        )
    } catch {}
}
function readSelectedUser(): User | null {
    try {
        const raw = localStorage.getItem(SELECTED_USER_KEY)
        return raw ? JSON.parse(raw) : null
    } catch {
        return null
    }
}

// ── Primitives ──

function FL({ children }: { children: React.ReactNode }) {
    return (
        <div
            style={{
                fontSize: 10,
                fontFamily: code,
                fontWeight: 600,
                color: t2,
                textTransform: "uppercase" as const,
                letterSpacing: 1.1,
                marginBottom: 4,
            }}
        >
            {children}
        </div>
    )
}

function PlanBadge({
    tier,
    manual,
    status,
}: {
    tier: string | null
    manual: boolean
    status: string | null
}) {
    const t = (tier || "free").toLowerCase()
    const ok = status === "active"
    let label = "FREE",
        color = t2,
        border = b0
    if (t === "prepa") {
        label = "PREPA"
        color = "#9B6FFF"
        border = "rgba(155,111,255,0.4)"
    } else if (t === "membre+" || t === "membre_plus") {
        label = manual ? "M+ ·M" : "M+"
        color = ok ? t0 : t1
        border = ok ? b2 : b0
    } else if (t === "membre") {
        label = manual ? "M ·M" : "M"
        color = ok ? t1 : t2
        border = ok ? b1 : b0
    }
    return (
        <span
            style={{
                fontSize: 10,
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
            {label}
        </span>
    )
}

function TI({ value, onChange, placeholder, type = "text", style }: any) {
    return (
        <input
            type={type}
            value={value}
            onChange={onChange}
            placeholder={placeholder}
            style={{
                width: "100%",
                height: 32,
                padding: "0 10px",
                background: type === "datetime-local" ? FIELD : s0,
                border: `1px solid ${b0}`,
                borderRadius: 10,
                color: t0,
                fontSize: 12,
                fontFamily: mono,
                outline: "none",
                boxSizing: "border-box" as const,
                ...style,
            }}
        />
    )
}

function SI({ value, onChange, children, style }: any) {
    return (
        <select
            value={value}
            onChange={onChange}
            style={{
                width: "100%",
                height: 34,
                padding: "0 10px",
                background: FIELD,
                border: `1px solid ${b0}`,
                borderRadius: 10,
                color: t0,
                fontSize: 12,
                fontFamily: mono,
                outline: "none",
                boxSizing: "border-box" as const,
                ...style,
            }}
        >
            {children}
        </select>
    )
}

function TAI({ value, onChange, placeholder, rows = 3 }: any) {
    return (
        <textarea
            value={value}
            onChange={onChange}
            placeholder={placeholder}
            rows={rows}
            style={{
                width: "100%",
                padding: "8px 10px",
                background: s0,
                border: `1px solid ${b0}`,
                borderRadius: 10,
                color: t0,
                fontSize: 12,
                fontFamily: mono,
                outline: "none",
                boxSizing: "border-box" as const,
                resize: "vertical" as const,
                lineHeight: 1.6,
            }}
        />
    )
}

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
        clr = BAD
        bdr = "rgba(255,107,107,0.3)"
    }
    return (
        <button
            onClick={onClick}
            disabled={disabled}
            onMouseEnter={() => setH(true)}
            onMouseLeave={() => setH(false)}
            style={{
                padding: "7px 14px",
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

function SM({ text }: { text: string }) {
    if (!text) return null
    const err = text.startsWith("Erreur") || text.startsWith("ERR")
    return (
        <div
            style={{
                fontSize: 11,
                fontFamily: mono,
                color: err ? BAD : OK,
                padding: "8px 12px",
                background: err ? "rgba(255,107,107,0.08)" : "rgba(91,227,163,0.08)",
                border: `1px solid ${err ? "rgba(255,107,107,0.2)" : "rgba(91,227,163,0.2)"}`,
                borderRadius: 10,
            }}
        >
            {text}
        </div>
    )
}

function SBar({
    left,
    right,
}: {
    left: React.ReactNode
    right?: React.ReactNode
}) {
    return (
        <div
            style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                padding: "11px 14px",
                borderBottom: `1px solid ${b0}`,
            }}
        >
            <span style={{ fontSize: 11, fontFamily: mono, color: t1 }}>
                {left}
            </span>
            {right}
        </div>
    )
}

function Tag({
    children,
    color,
    border,
}: {
    children: React.ReactNode
    color: string
    border: string
}) {
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

function StatusTag({ status }: { status: string }) {
    if (status === "active")
        return (
            <Tag color={OK} border="rgba(91,227,163,0.4)">
                ACTIVE
            </Tag>
        )
    if (status === "payment_failed")
        return (
            <Tag color={BAD} border="rgba(255,107,107,0.4)">
                IMPAYÉ
            </Tag>
        )
    if (status === "suspendu")
        return (
            <Tag color={BAD} border="rgba(255,107,107,0.6)">
                SUSPENDU
            </Tag>
        )
    return (
        <Tag color={t2} border={b0}>
            {status.toUpperCase()}
        </Tag>
    )
}

// ── Auth Gate ──

function AuthGate({ onSuccess }: { onSuccess: () => void }) {
    const [code, setCode] = React.useState("")
    const [error, setError] = React.useState(false)
    const ref = React.useRef<HTMLInputElement>(null)
    React.useEffect(() => {
        setTimeout(() => ref.current?.focus(), 80)
    }, [])

    function submit() {
        if (code.trim() === ADMIN_CODE) {
            try {
                sessionStorage.setItem(SESSION_KEY, "1")
            } catch {}
            onSuccess()
        } else {
            setError(true)
            setCode("")
            setTimeout(() => ref.current?.focus(), 0)
        }
    }

    return (
        <div
            style={{
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                justifyContent: "center",
                minHeight: 320,
                gap: 12,
                padding: 32,
            }}
        >
            <span
                style={{
                    fontSize: 10,
                    fontFamily: mono,
                    color: t2,
                    letterSpacing: 3,
                    textTransform: "uppercase" as const,
                }}
            >
                Accès administrateur
            </span>
            <input
                ref={ref}
                type="password"
                value={code}
                onChange={(e) => {
                    setCode(e.target.value)
                    setError(false)
                }}
                onKeyDown={(e) => {
                    if (e.key === "Enter") {
                        e.preventDefault()
                        submit()
                    }
                }}
                placeholder="••••••••"
                style={{
                    width: 240,
                    height: 42,
                    background: "transparent",
                    border: `1px solid ${error ? BAD : b1}`,
                    borderRadius: 12,
                    color: t0,
                    fontSize: 18,
                    fontFamily: mono,
                    textAlign: "center",
                    letterSpacing: 4,
                    outline: "none",
                }}
            />
            {error && (
                <span style={{ fontSize: 11, fontFamily: mono, color: BAD }}>
                    Code invalide
                </span>
            )}
            <button
                onClick={submit}
                style={{
                    width: 240,
                    height: 38,
                    background: "#fff",
                    border: "1px solid #fff",
                    borderRadius: 99,
                    color: "#0a0a12",
                    fontSize: 13,
                    fontFamily: mono,
                    fontWeight: 600,
                    letterSpacing: 0.2,
                    cursor: "pointer",
                }}
            >
                VALIDER →
            </button>
        </div>
    )
}

// ── Users section ──

function UserRow({
    user,
    selected,
    onClick,
}: {
    user: User
    selected: boolean
    onClick: () => void
}) {
    const [hov, setHov] = React.useState(false)
    return (
        <div
            onClick={onClick}
            onMouseEnter={() => setHov(true)}
            onMouseLeave={() => setHov(false)}
            style={{
                display: "flex",
                alignItems: "center",
                gap: 10,
                padding: "9px 14px",
                borderBottom: `1px solid rgba(255,255,255,0.04)`,
                borderLeft: `2px solid ${selected ? b2 : "transparent"}`,
                background: selected ? sA : hov ? sH : "transparent",
                cursor: "pointer",
                transition: "background 0.1s",
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
                    {user.username}
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
                    {user.email}
                </div>
            </div>
            <span
                style={{
                    fontSize: 11,
                    fontFamily: mono,
                    color: t1,
                    flexShrink: 0,
                }}
            >
                {user.elo}
            </span>
            <PlanBadge
                tier={user.subscription_tier}
                manual={user.is_manual_subscription}
                status={user.subscription_status}
            />
            <span
                style={{
                    fontSize: 11,
                    color: selected ? t0 : t2,
                    flexShrink: 0,
                }}
            >
                ›
            </span>
        </div>
    )
}

function UsersSection({
    selectedUser,
    onSelectUser,
}: {
    selectedUser: User | null
    onSelectUser: (u: User | null) => void
}) {
    const [users, setUsers] = React.useState<User[]>([])
    const [total, setTotal] = React.useState(0)
    const [loading, setLoading] = React.useState(true)
    const [search, setSearch] = React.useState("")
    const [sortBy, setSortBy] = React.useState<
        "created_at" | "elo" | "username"
    >("created_at")
    const [sortDir, setSortDir] = React.useState<"asc" | "desc">("desc")

    React.useEffect(() => {
        apiFetch("/admin/users")
            .then((d) => {
                setUsers(d?.users || [])
                setTotal(d?.total || 0)
            })
            .catch(() => {})
            .finally(() => setLoading(false))
    }, [])

    function select(u: User) {
        const next = selectedUser?.id === u.id ? null : u
        onSelectUser(next)
        broadcastSelectedUser(next)
    }

    function toggleSort(col: "created_at" | "elo" | "username") {
        if (sortBy === col) setSortDir((d) => (d === "asc" ? "desc" : "asc"))
        else {
            setSortBy(col)
            setSortDir("desc")
        }
    }

    const filtered = users
        .filter(
            (u) =>
                !search ||
                [u.username, u.email, u.universite || ""].some((v) =>
                    v.toLowerCase().includes(search.toLowerCase())
                )
        )
        .sort((a, b) => {
            const va: any = a[sortBy],
                vb: any = b[sortBy]
            return sortDir === "asc" ? (va < vb ? -1 : 1) : va > vb ? -1 : 1
        })

    function SortBtn({
        col,
        label,
    }: {
        col: "created_at" | "elo" | "username"
        label: string
    }) {
        const active = sortBy === col
        return (
            <span
                onClick={() => toggleSort(col)}
                style={{
                    cursor: "pointer",
                    userSelect: "none" as const,
                    fontSize: 9,
                    fontFamily: mono,
                    color: active ? t0 : t2,
                    textTransform: "uppercase" as const,
                    letterSpacing: 0.8,
                }}
            >
                {label}
                {active ? (sortDir === "asc" ? " ↑" : " ↓") : ""}
            </span>
        )
    }

    return (
        <div
            style={{
                display: "flex",
                flexDirection: "column",
                height: "100%",
                maxHeight: "100%",
            }}
        >
            <SBar
                left={`${filtered.length} / ${total} utilisateurs`}
                right={
                    <TI
                        value={search}
                        onChange={(e: any) => setSearch(e.target.value)}
                        placeholder="Rechercher…"
                        style={{ width: 180, height: 28 }}
                    />
                }
            />
            <div
                style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 10,
                    padding: "6px 14px",
                    background: "rgba(255,255,255,0.015)",
                    borderBottom: `1px solid ${b0}`,
                    flexShrink: 0,
                }}
            >
                <SortBtn col="username" label="Utilisateur" />
                <div style={{ flex: 1 }} />
                <SortBtn col="elo" label="ELO" />
                <span
                    style={{
                        fontSize: 9,
                        fontFamily: mono,
                        color: t2,
                        textTransform: "uppercase" as const,
                        letterSpacing: 0.8,
                    }}
                >
                    Plan
                </span>
                <SortBtn col="created_at" label="Date" />
                <span style={{ width: 11 }} />
            </div>
            {loading ? (
                <div
                    style={{
                        padding: "32px 14px",
                        textAlign: "center",
                        fontFamily: mono,
                        fontSize: 12,
                        color: t2,
                    }}
                >
                    Chargement…
                </div>
            ) : filtered.length === 0 ? (
                <div
                    style={{
                        padding: "32px 14px",
                        textAlign: "center",
                        fontFamily: mono,
                        fontSize: 12,
                        color: t2,
                    }}
                >
                    Aucun résultat.
                </div>
            ) : (
                <div
                    style={{
                        flex: 1,
                        overflowY: "auto",
                        position: "relative" as const,
                    }}
                >
                    {filtered.map((u) => (
                        <UserRow
                            key={u.id}
                            user={u}
                            selected={selectedUser?.id === u.id}
                            onClick={() => select(u)}
                        />
                    ))}
                    <div
                        style={{
                            position: "sticky" as const,
                            bottom: 0,
                            left: 0,
                            right: 0,
                            height: 32,
                            pointerEvents: "none",
                            background:
                                "linear-gradient(to top, rgba(0,0,0,0.4) 0%, transparent 100%)",
                        }}
                    />
                </div>
            )}
        </div>
    )
}

// ── Promos ──

function PromosSection() {
    const [promos, setPromos] = React.useState<any[]>([])
    const [loading, setLoading] = React.useState(true)
    const [showForm, setShowForm] = React.useState(false)
    const [form, setForm] = React.useState({
        code: "",
        type: "percent",
        value: "",
        cycle: "both",
    })
    const [saving, setSaving] = React.useState(false)
    const [msg, setMsg] = React.useState("")

    function load() {
        setLoading(true)
        apiFetch("/subscriptions/admin/promo")
            .then((d) => setPromos(d?.items || []))
            .catch(() => {})
            .finally(() => setLoading(false))
    }
    React.useEffect(() => {
        load()
    }, [])

    async function createPromo() {
        if (!form.code || !form.value) return
        setSaving(true)
        setMsg("")
        try {
            await apiFetch("/subscriptions/admin/promo", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    code: form.code.toUpperCase(),
                    discount_type: form.type,
                    discount_value: Number(form.value),
                    applies_to: form.cycle,
                }),
            })
            setMsg("Code créé.")
            setShowForm(false)
            setForm({ code: "", type: "percent", value: "", cycle: "both" })
            load()
        } catch (e: any) {
            setMsg(`Erreur: ${e.message}`)
        } finally {
            setSaving(false)
        }
    }
    async function togglePromo(p: any) {
        try {
            await apiFetch(`/subscriptions/admin/promo/${p.id}`, {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ is_active: !p.is_active }),
            })
            setPromos((prev) =>
                prev.map((x) =>
                    x.id === p.id ? { ...x, is_active: !x.is_active } : x
                )
            )
        } catch {}
    }
    async function deletePromo(p: any) {
        if (!confirm(`Supprimer "${p.code}" ?`)) return
        try {
            await apiFetch(`/subscriptions/admin/promo/${p.id}`, {
                method: "DELETE",
            })
            setPromos((prev) => prev.filter((x) => x.id !== p.id))
        } catch {}
    }

    return (
        <div>
            <SBar
                left={`${promos.length} code(s)`}
                right={
                    <Btn onClick={() => setShowForm((v) => !v)}>
                        {showForm ? "✕ Annuler" : "+ Nouveau"}
                    </Btn>
                }
            />
            {showForm && (
                <div
                    style={{
                        padding: "14px",
                        borderBottom: `1px solid ${b0}`,
                        display: "flex",
                        flexDirection: "column",
                        gap: 10,
                    }}
                >
                    <div
                        style={{
                            display: "grid",
                            gridTemplateColumns: "1fr 1fr",
                            gap: 8,
                        }}
                    >
                        <div>
                            <FL>Code</FL>
                            <TI
                                value={form.code}
                                onChange={(e: any) =>
                                    setForm((f) => ({
                                        ...f,
                                        code: e.target.value.toUpperCase(),
                                    }))
                                }
                                placeholder="NAVIRE20"
                            />
                        </div>
                        <div>
                            <FL>Valeur</FL>
                            <TI
                                type="number"
                                value={form.value}
                                onChange={(e: any) =>
                                    setForm((f) => ({
                                        ...f,
                                        value: e.target.value,
                                    }))
                                }
                                placeholder="20"
                            />
                        </div>
                        <div>
                            <FL>Type</FL>
                            <SI
                                value={form.type}
                                onChange={(e: any) =>
                                    setForm((f) => ({
                                        ...f,
                                        type: e.target.value,
                                    }))
                                }
                            >
                                <option value="percent">%</option>
                                <option value="fixed">€</option>
                            </SI>
                        </div>
                        <div>
                            <FL>Cycle</FL>
                            <SI
                                value={form.cycle}
                                onChange={(e: any) =>
                                    setForm((f) => ({
                                        ...f,
                                        cycle: e.target.value,
                                    }))
                                }
                            >
                                <option value="both">Les deux</option>
                                <option value="monthly">Mensuel</option>
                                <option value="annual">Annuel</option>
                            </SI>
                        </div>
                    </div>
                    <SM text={msg} />
                    <Btn
                        onClick={createPromo}
                        disabled={saving || !form.code || !form.value}
                    >
                        {saving ? "Création…" : "Créer →"}
                    </Btn>
                </div>
            )}
            {loading ? (
                <div
                    style={{
                        padding: "32px 14px",
                        textAlign: "center",
                        fontFamily: mono,
                        fontSize: 12,
                        color: t2,
                    }}
                >
                    Chargement…
                </div>
            ) : promos.length === 0 ? (
                <div
                    style={{
                        padding: "32px 14px",
                        textAlign: "center",
                        fontFamily: mono,
                        fontSize: 12,
                        color: t2,
                    }}
                >
                    Aucun code.
                </div>
            ) : (
                promos.map((p) => (
                    <div
                        key={p.id}
                        style={{
                            display: "flex",
                            alignItems: "center",
                            gap: 10,
                            padding: "10px 14px",
                            borderBottom: `1px solid rgba(255,255,255,0.04)`,
                            opacity: p.is_active ? 1 : 0.4,
                            minWidth: 0,
                        }}
                    >
                        <div style={{ flex: 1, minWidth: 0 }}>
                            <div
                                style={{
                                    display: "flex",
                                    alignItems: "center",
                                    gap: 8,
                                    marginBottom: 3,
                                    flexWrap: "wrap" as const,
                                }}
                            >
                                <span
                                    style={{
                                        fontSize: 13,
                                        fontWeight: 700,
                                        fontFamily: mono,
                                        color: t0,
                                    }}
                                >
                                    {p.code}
                                </span>
                                <span
                                    style={{
                                        fontSize: 10,
                                        fontFamily: mono,
                                        color: t1,
                                        padding: "2px 6px",
                                        border: `1px solid ${b0}`,
                                        borderRadius: 99,
                                    }}
                                >
                                    {p.discount_type === "percent"
                                        ? `${p.discount_value}%`
                                        : `${p.discount_value}€`}
                                </span>
                                <span
                                    style={{
                                        fontSize: 10,
                                        fontFamily: mono,
                                        color: t1,
                                        padding: "2px 6px",
                                        border: `1px solid ${b0}`,
                                        borderRadius: 99,
                                    }}
                                >
                                    {p.applies_to || "both"}
                                </span>
                            </div>
                            <div
                                style={{
                                    fontSize: 10,
                                    fontFamily: mono,
                                    color: t2,
                                }}
                            >
                                {p.uses_count}
                                {p.max_uses ? `/${p.max_uses}` : ""} util.
                                {p.expires_at
                                    ? ` · exp. ${p.expires_at.slice(0, 10)}`
                                    : ""}
                                {p.stripe_coupon_id ? " · Stripe ✓" : ""}
                            </div>
                        </div>
                        <div style={{ display: "flex", gap: 6, flexShrink: 0 }}>
                            <Btn
                                onClick={() => togglePromo(p)}
                                style={{ fontSize: 10, padding: "4px 10px" }}
                            >
                                {p.is_active ? "Actif" : "Inactif"}
                            </Btn>
                            <Btn
                                danger
                                onClick={() => deletePromo(p)}
                                style={{ fontSize: 10, padding: "4px 10px" }}
                            >
                                Suppr.
                            </Btn>
                        </div>
                    </div>
                ))
            )}
        </div>
    )
}

// ── Actus ──

const CATS = ["civil", "penal", "public", "affaires", "general"]

function ActusSection() {
    const [actus, setActus] = React.useState<any[]>([])
    const [loading, setLoading] = React.useState(true)
    const [selected, setSelected] = React.useState<any>(null)
    const [showForm, setShowForm] = React.useState(false)
    const [form, setForm] = React.useState({
        title: "",
        essentiel: "",
        impact: "",
        category: "civil",
        source_name: "",
        source_url: "",
    })
    const [saving, setSaving] = React.useState(false)
    const [msg, setMsg] = React.useState("")

    function load() {
        setLoading(true)
        apiFetch("/veille/feed?limit=50")
            .then((d) => setActus(d?.items || []))
            .catch(() => {})
            .finally(() => setLoading(false))
    }
    React.useEffect(() => {
        load()
    }, [])

    async function createActu() {
        setSaving(true)
        setMsg("")
        try {
            const data = await apiFetch("/veille/admin/items", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(form),
            })
            setMsg(`Actu #${data.id} publiée.`)
            setShowForm(false)
            setForm({
                title: "",
                essentiel: "",
                impact: "",
                category: "civil",
                source_name: "",
                source_url: "",
            })
            load()
        } catch (e: any) {
            setMsg(`Erreur: ${e.message}`)
        } finally {
            setSaving(false)
        }
    }
    async function deleteActu(a: any) {
        if (!confirm(`Supprimer "${a.title}" ?`)) return
        try {
            await apiFetch(`/veille/admin/items/${a.id}`, { method: "DELETE" })
            setActus((prev) => prev.filter((x) => x.id !== a.id))
            if (selected?.id === a.id) setSelected(null)
        } catch {}
    }

    return (
        <div>
            <SBar
                left={`${actus.length} actu(s)`}
                right={
                    <Btn onClick={() => setShowForm((v) => !v)}>
                        {showForm ? "✕ Annuler" : "+ Nouvelle"}
                    </Btn>
                }
            />
            {showForm && (
                <div
                    style={{
                        padding: "14px",
                        borderBottom: `1px solid ${b0}`,
                        display: "flex",
                        flexDirection: "column",
                        gap: 10,
                    }}
                >
                    <div
                        style={{
                            display: "grid",
                            gridTemplateColumns: "1fr 1fr",
                            gap: 8,
                        }}
                    >
                        <div style={{ gridColumn: "1/-1" }}>
                            <FL>Titre</FL>
                            <TI
                                value={form.title}
                                onChange={(e: any) =>
                                    setForm((f) => ({
                                        ...f,
                                        title: e.target.value,
                                    }))
                                }
                                placeholder="Titre…"
                            />
                        </div>
                        <div>
                            <FL>Catégorie</FL>
                            <SI
                                value={form.category}
                                onChange={(e: any) =>
                                    setForm((f) => ({
                                        ...f,
                                        category: e.target.value,
                                    }))
                                }
                            >
                                {CATS.map((c) => (
                                    <option key={c} value={c}>
                                        {c}
                                    </option>
                                ))}
                            </SI>
                        </div>
                        <div>
                            <FL>Source</FL>
                            <TI
                                value={form.source_name}
                                onChange={(e: any) =>
                                    setForm((f) => ({
                                        ...f,
                                        source_name: e.target.value,
                                    }))
                                }
                                placeholder="Nom source"
                            />
                        </div>
                        <div style={{ gridColumn: "1/-1" }}>
                            <FL>URL</FL>
                            <TI
                                value={form.source_url}
                                onChange={(e: any) =>
                                    setForm((f) => ({
                                        ...f,
                                        source_url: e.target.value,
                                    }))
                                }
                                placeholder="https://…"
                            />
                        </div>
                        <div style={{ gridColumn: "1/-1" }}>
                            <FL>Essentiel</FL>
                            <TAI
                                value={form.essentiel}
                                onChange={(e: any) =>
                                    setForm((f) => ({
                                        ...f,
                                        essentiel: e.target.value,
                                    }))
                                }
                                placeholder="Ce qu'il faut retenir…"
                            />
                        </div>
                        <div style={{ gridColumn: "1/-1" }}>
                            <FL>Impact</FL>
                            <TAI
                                value={form.impact}
                                onChange={(e: any) =>
                                    setForm((f) => ({
                                        ...f,
                                        impact: e.target.value,
                                    }))
                                }
                                placeholder="Impact pratique…"
                            />
                        </div>
                    </div>
                    <SM text={msg} />
                    <Btn
                        onClick={createActu}
                        disabled={saving || !form.title || !form.source_url}
                    >
                        {saving ? "Publication…" : "Publier →"}
                    </Btn>
                </div>
            )}
            {loading ? (
                <div
                    style={{
                        padding: "32px 14px",
                        textAlign: "center",
                        fontFamily: mono,
                        fontSize: 12,
                        color: t2,
                    }}
                >
                    Chargement…
                </div>
            ) : actus.length === 0 ? (
                <div
                    style={{
                        padding: "32px 14px",
                        textAlign: "center",
                        fontFamily: mono,
                        fontSize: 12,
                        color: t2,
                    }}
                >
                    Aucune actu.
                </div>
            ) : (
                actus.map((a) => (
                    <div key={a.id}>
                        <div
                            onClick={() =>
                                setSelected(selected?.id === a.id ? null : a)
                            }
                            style={{
                                display: "flex",
                                alignItems: "flex-start",
                                gap: 10,
                                padding: "10px 14px",
                                borderBottom: `1px solid rgba(255,255,255,0.04)`,
                                cursor: "pointer",
                                background:
                                    selected?.id === a.id ? sA : "transparent",
                                borderLeft: `2px solid ${selected?.id === a.id ? b2 : "transparent"}`,
                                minWidth: 0,
                            }}
                        >
                            <span
                                style={{
                                    fontSize: 9,
                                    fontFamily: mono,
                                    color: t1,
                                    padding: "2px 6px",
                                    border: `1px solid ${b0}`,
                                    borderRadius: 99,
                                    whiteSpace: "nowrap" as const,
                                    marginTop: 2,
                                }}
                            >
                                {a.category}
                            </span>
                            <div style={{ flex: 1, minWidth: 0 }}>
                                <div
                                    style={{
                                        fontSize: 13,
                                        fontWeight: 500,
                                        color: t0,
                                        marginBottom: 2,
                                        overflow: "hidden",
                                        textOverflow: "ellipsis",
                                        whiteSpace: "nowrap" as const,
                                    }}
                                >
                                    {a.title}
                                </div>
                                <div
                                    style={{
                                        fontSize: 10,
                                        fontFamily: mono,
                                        color: t2,
                                    }}
                                >
                                    {a.source_name} · #{a.id}
                                </div>
                            </div>
                            <button
                                onClick={(e) => {
                                    e.stopPropagation()
                                    deleteActu(a)
                                }}
                                style={{
                                    background: "none",
                                    border: "none",
                                    color: t2,
                                    cursor: "pointer",
                                    fontSize: 13,
                                    padding: "0 2px",
                                    lineHeight: 1,
                                    flexShrink: 0,
                                }}
                            >
                                ✕
                            </button>
                        </div>
                        {selected?.id === a.id && (
                            <div
                                style={{
                                    padding: "12px 16px 16px",
                                    borderBottom: `1px solid ${b0}`,
                                    background: "rgba(255,255,255,0.015)",
                                }}
                            >
                                <div style={{ marginBottom: 10 }}>
                                    <FL>Source</FL>
                                    <div
                                        style={{
                                            display: "flex",
                                            gap: 8,
                                            alignItems: "center",
                                        }}
                                    >
                                        <span
                                            style={{
                                                fontSize: 11,
                                                fontFamily: mono,
                                                color: t1,
                                            }}
                                        >
                                            {a.source_name}
                                        </span>
                                        <a
                                            href={a.source_url}
                                            target="_blank"
                                            rel="noreferrer"
                                            style={{
                                                fontSize: 11,
                                                fontFamily: mono,
                                                color: t1,
                                                textDecoration: "none",
                                            }}
                                        >
                                            ↗
                                        </a>
                                    </div>
                                </div>
                                {a.essentiel && (
                                    <div style={{ marginBottom: 10 }}>
                                        <FL>Essentiel</FL>
                                        <div
                                            style={{
                                                fontSize: 12,
                                                color: t0,
                                                lineHeight: 1.65,
                                            }}
                                        >
                                            {a.essentiel}
                                        </div>
                                    </div>
                                )}
                                {a.impact && (
                                    <div>
                                        <FL>Impact</FL>
                                        <div
                                            style={{
                                                fontSize: 12,
                                                color: t0,
                                                lineHeight: 1.65,
                                            }}
                                        >
                                            {a.impact}
                                        </div>
                                    </div>
                                )}
                            </div>
                        )}
                    </div>
                ))
            )}
        </div>
    )
}

// ── Terminal ──

async function runCmd(cmd: string): Promise<string> {
    const parts = cmd.trim().split(/\s+/)
    const head = parts[0]
    if (head === "/help")
        return [
            "Commandes :",
            "  /stats <id>",
            "  /elo add <id> <delta>",
            "  /elo set <id> <value>",
            "  /user <id> delete",
            "  /plan <id> set <plan>  ← free | membre | membre+",
            "  /prepa <id>            ← active PREPASSERELLE (choix L1/L2/L3)",
            "  /mails                 ← liste les emails de l'app",
            "  /mails diag            ← clé Brevo + expéditeur validé ?",
            "  /mails test <clé|all> [email]",
        ].join("\n")
    // ── Emails : catalogue et envoi de test ──
    // /mails                          liste ce que l'API sait envoyer
    // /mails test <clé|all> [email]   envoie, données fictives, objet [TEST]
    // Sans email, l'API expédie à l'adresse du premier compte admin.
    if (head === "/mails") {
        // Diagnostic : interroge Brevo (clé valide ? expéditeur validé ?).
        // C'est la seule façon de voir un expéditeur non validé, qui fait
        // accepter l'appel puis ne délivre rien.
        if (parts[1] === "diag" || parts[1] === "diagnostic") {
            const d = await apiFetch("/admin/emails/diagnostic")
            const b = d?.brevo || {}
            const c = d?.config || {}
            const lines: string[] = ["── Diagnostic envoi ──"]
            lines.push(
                `Clé Brevo : ${b.key_valid === true ? "valide" : b.key_valid === false ? "INVALIDE ou absente" : "?"}`
            )
            if (b.account) {
                lines.push(
                    `Compte : ${b.account.email || "?"}${b.account.company ? ` (${b.account.company})` : ""}`
                )
                ;(b.account.plan || []).forEach((p: any) =>
                    lines.push(
                        `Plan : ${p.type || "?"}${p.credits != null ? ` · ${p.credits} crédit(s)` : ""}`
                    )
                )
            }
            lines.push("")
            lines.push(`Expéditeur configuré : ${c.sender_email || "?"}`)
            lines.push(
                `Validé chez Brevo : ${
                    b.sender_is_verified === true
                        ? "OUI"
                        : b.sender_is_verified === false
                          ? "NON — c'est la cause : Brevo accepte puis ne délivre pas"
                          : "?"
                }`
            )
            if ((b.senders || []).length) {
                lines.push("")
                lines.push("Expéditeurs déclarés :")
                b.senders.forEach((sd: any) =>
                    lines.push(
                        `  ${sd.active ? "✓" : "✗"} ${sd.email}${sd.name ? ` (${sd.name})` : ""}`
                    )
                )
            }
            if (b.erreur) {
                lines.push("")
                lines.push(`! ${b.erreur}`)
            }
            return lines.join("\n")
        }

        if (parts[1] === "test") {
            const key = parts[2] || "all"
            const to = parts[3] || ""
            const d = await apiFetch(
                `/admin/emails/test?key=${encodeURIComponent(key)}` +
                    (to ? `&to=${encodeURIComponent(to)}` : ""),
                { method: "POST" }
            )
            const results = d?.results || []
            const lines: string[] = [`── Envoi de test → ${d.to} ──`]
            results.forEach((r: any) => {
                lines.push(
                    `[${r.sent ? " OK  " : "ÉCHEC"}] ${r.key} · ${r.subject}`
                )
                // La raison n'apparaît qu'en cas de refus : en cas de succès
                // elle répète l'expéditeur à chaque ligne et noie le rapport.
                if (!r.sent && r.detail) lines.push(`        ↳ ${r.detail}`)
            })
            lines.push(
                `${d.sent}/${d.requested} envoyé(s)${d.failed ? ` · ${d.failed} échec(s)` : ""}.`
            )
            if (d.sent && !d.failed) {
                lines.push(
                    `Accepté par Brevo depuis ${d.config?.sender_email || "?"} — si rien n'arrive, regarde les indésirables puis les logs Brevo.`
                )
            }
            return lines.join("\n")
        }

        const d = await apiFetch("/admin/emails")
        const items: MailRow[] = d?.items || []
        if (items.length === 0) return "Aucun email au catalogue."
        const cfg = d?.config || {}
        return [
            `── Emails NAVIRE (${items.length}) ──`,
            ...items.map((m) => `${m.key}\n   ${m.label}\n   ↳ ${m.trigger}`),
            "",
            `Expéditeur : ${cfg.sender_name || "?"} <${cfg.sender_email || "?"}>`,
            `Clé Brevo : ${cfg.brevo_key_present ? "présente" : "ABSENTE — aucun envoi ne partira"}`,
            "",
            "/mails diag pour vérifier la configuration Brevo.",
            "/mails test <clé|all> [email] pour se l'envoyer.",
        ].join("\n")
    }

    if (head === "/stats") {
        if (parts.length !== 2) throw new Error("Syntaxe: /stats <id>")
        const id = Number(parts[1])
        const tz = new Date().getTimezoneOffset()
        const [ud, stats] = await Promise.all([
            apiFetch("/admin/users"),
            apiFetch(`/admin/users/${id}/stats?tz_offset=${tz}`).catch(
                () => null
            ),
        ])
        const u = (ud?.users || []).find((x: any) => x.id === id)
        if (!u) throw new Error(`User #${id} introuvable.`)
        if (!stats) return `User #${id} — ${u.username}\nAucune stat.`
        return [
            `── #${u.id} ${u.username} (${(u.subscription_tier || "free").toUpperCase()}) ──`,
            `Streak : ${stats.current_streak ?? 0}j | Record : ${stats.longest_streak ?? 0}j`,
            `Réussite : ${stats.success_rate_pct ?? 0}% (${stats.total_correct ?? 0}/${stats.total_answered ?? 0})`,
            `Sessions : ${stats.total_sessions ?? 0} | Complètes : ${stats.complete_sessions ?? 0}`,
            `Fichiers : ${stats.files_count ?? 0}`,
        ].join("\n")
    }
    if (head === "/elo") {
        const action = parts[1],
            id = Number(parts[2])
        if (!action || !Number.isFinite(id))
            throw new Error("Syntaxe: /elo <add|set> <id> <value>")
        if (action === "add") {
            const delta = Number(parts[3])
            const d = await apiFetch(`/admin/users/${id}/elo/add`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ delta }),
            })
            return `OK: ELO += ${delta} → ${d.new_elo}`
        }
        if (action === "set") {
            const value = Number(parts[3])
            const d = await apiFetch(`/admin/users/${id}/elo/set`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ value }),
            })
            return `OK: ELO = ${d.new_elo}`
        }
        throw new Error("Action invalide: add | set")
    }
    if (head === "/user" && parts[2] === "delete") {
        const id = Number(parts[1])
        await apiFetch(`/admin/users/${id}`, { method: "DELETE" })
        return `OK: User #${id} supprimé.`
    }
    if (head === "/plan" && parts[2] === "set") {
        const id = Number(parts[1])
        const planMap: Record<string, string> = {
            free: "free",
            membre: "membre",
            "m+": "membre+",
            "membre+": "membre+",
        }
        const plan = planMap[parts[3]?.toLowerCase()]
        if (!plan) throw new Error("Plan invalide: free | membre | membre+")
        const ud = await apiFetch("/admin/users")
        const u = (ud?.users || []).find((x: any) => x.id === id)
        if (!u) throw new Error(`User #${id} introuvable.`)
        await apiFetch(
            `/admin/set-subscription?email=${encodeURIComponent(u.email)}&plan=${encodeURIComponent(plan)}`,
            { method: "POST" }
        )
        return `OK: Plan ${plan.toUpperCase()} → ${u.email}`
    }
    throw new Error("Commande inconnue. /help")
}

function TerminalSection() {
    const [logs, setLogs] = React.useState<LogLine[]>([
        {
            id: uid(),
            kind: "sys",
            text: "Terminal NAVIRE — /help pour les commandes.",
        },
    ])
    const [input, setInput] = React.useState("")
    const [history, setHistory] = React.useState<string[]>([])
    const [histIdx, setHistIdx] = React.useState(-1)
    const [busy, setBusy] = React.useState(false)
    const [pendingPrepa, setPendingPrepa] = React.useState<{
        id: number
        email: string
        username: string
    } | null>(null)
    const outRef = React.useRef<HTMLDivElement>(null)
    const inputRef = React.useRef<HTMLInputElement>(null)

    React.useEffect(() => {
        if (outRef.current)
            outRef.current.scrollTop = outRef.current.scrollHeight
    }, [logs])

    function push(kind: LogLine["kind"], text: string) {
        setLogs((prev) => [...prev, { id: uid(), kind, text }].slice(-300))
    }

    async function exec(cmd: string) {
        const c = cmd.trim()
        if (!c) return

        if (pendingPrepa && !c.startsWith("/")) {
            setBusy(true)
            push("in", c)
            const choice = c.toLowerCase()
            if (["annuler", "cancel", "x"].includes(choice)) {
                push("sys", "Annulé.")
                setPendingPrepa(null)
                setBusy(false)
                return
            }
            const map: Record<string, string> = {
                "1": "L1",
                "2": "L2",
                "3": "L3",
                l1: "L1",
                l2: "L2",
                l3: "L3",
            }
            const annee = map[choice]
            if (!annee) {
                push(
                    "err",
                    "Choix invalide. Tape 1 (L1), 2 (L2), 3 (L3) ou annuler."
                )
                setBusy(false)
                return
            }
            try {
                await apiFetch(
                    `/admin/set-prepa?email=${encodeURIComponent(pendingPrepa.email)}&annee=${annee}`,
                    { method: "POST" }
                )
                push("out", `OK: PREPA ${annee} → ${pendingPrepa.email}`)
            } catch (e: any) {
                push("err", `ERR: ${e.message}`)
            }
            setPendingPrepa(null)
            setBusy(false)
            return
        }

        if (pendingPrepa && c.startsWith("/")) {
            setPendingPrepa(null)
        }

        setBusy(true)
        push("in", c)
        setHistory((prev) => [c, ...prev].slice(0, 80))
        setHistIdx(-1)

        const parts = c.split(/\s+/)
        const isPrepa =
            parts[0] === "/prepa" ||
            (parts[0] === "/set" && (parts[1] || "").toLowerCase() === "prepa")
        if (isPrepa) {
            const rawId = parts[0] === "/prepa" ? parts[1] : parts[2]
            const id = Number((rawId || "").replace(/^@/, ""))
            if (!Number.isFinite(id)) {
                push("err", "Syntaxe: /prepa <id>")
                setBusy(false)
                return
            }
            try {
                const ud = await apiFetch("/admin/users")
                const u = (ud?.users || []).find((x: any) => x.id === id)
                if (!u) throw new Error(`User #${id} introuvable.`)
                setPendingPrepa({ id, email: u.email, username: u.username })
                push(
                    "sys",
                    `Année pour ${u.username} ?  1=L1 · 2=L2 · 3=L3 · annuler`
                )
            } catch (e: any) {
                push("err", `ERR: ${e.message}`)
            }
            setBusy(false)
            return
        }

        try {
            const out = await runCmd(c)
            if (out) push("out", out)
        } catch (e: any) {
            push("err", `ERR: ${e.message}`)
        } finally {
            setBusy(false)
        }
    }

    function onKD(e: React.KeyboardEvent<HTMLInputElement>) {
        if (e.key === "Enter") {
            const c = input
            setInput("")
            exec(c)
            return
        }
        if (e.key === "ArrowUp") {
            e.preventDefault()
            setHistIdx((i) => {
                const n = Math.min(i + 1, history.length - 1)
                if (history[n]) setInput(history[n])
                return n
            })
        }
        if (e.key === "ArrowDown") {
            e.preventDefault()
            setHistIdx((i) => {
                const n = Math.max(i - 1, -1)
                setInput(n === -1 ? "" : history[n] || "")
                return n
            })
        }
    }

    const lc = (kind: LogLine["kind"]) =>
        kind === "err" ? BAD : kind === "sys" ? t1 : kind === "in" ? t2 : t0

    return (
        <div
            style={{ display: "flex", flexDirection: "column" }}
            onMouseDown={() => inputRef.current?.focus()}
        >
            <div
                ref={outRef}
                style={{
                    padding: "14px 16px",
                    fontFamily: code,
                    fontSize: 12,
                    lineHeight: 1.75,
                    minHeight: 280,
                }}
            >
                {logs.map((l) => (
                    <div
                        key={l.id}
                        style={{ marginBottom: 2, color: lc(l.kind) }}
                    >
                        {l.kind === "in" && (
                            <span style={{ color: t2, marginRight: 8 }}>›</span>
                        )}
                        {l.kind === "err" && (
                            <span style={{ color: BAD, marginRight: 8 }}>
                                !
                            </span>
                        )}
                        {l.kind === "sys" && (
                            <span style={{ color: t2, marginRight: 8 }}>#</span>
                        )}
                        <span style={{ whiteSpace: "pre-wrap" }}>{l.text}</span>
                    </div>
                ))}
                {busy && (
                    <div style={{ color: t2 }}>
                        <span style={{ marginRight: 8 }}>›</span>…
                    </div>
                )}
            </div>
            <div
                style={{
                    padding: "10px 14px",
                    borderTop: `1px solid ${b0}`,
                    display: "flex",
                    gap: 8,
                    alignItems: "center",
                }}
            >
                <span style={{ fontSize: 14, fontFamily: code, color: t1 }}>
                    ›
                </span>
                <input
                    ref={inputRef}
                    value={input}
                    onChange={(e) => setInput((e.target as any).value)}
                    onKeyDown={onKD}
                    placeholder={
                        pendingPrepa
                            ? "1 = L1 · 2 = L2 · 3 = L3 · annuler"
                            : "/help · /stats 12 · /mails"
                    }
                    disabled={busy}
                    style={{
                        flex: 1,
                        height: 34,
                        background: "transparent",
                        border: `1px solid ${b0}`,
                        borderRadius: 10,
                        color: t0,
                        fontSize: 12,
                        fontFamily: code,
                        padding: "0 10px",
                        outline: "none",
                    }}
                />
                <Btn
                    onClick={() => {
                        const c = input
                        setInput("")
                        exec(c)
                    }}
                    disabled={busy || !input.trim()}
                    style={{ padding: "6px 12px" }}
                >
                    ↵
                </Btn>
            </div>
        </div>
    )
}

// ── Prepa (dossiers par licence + fichiers PDF) ──

function PrepaDossier({
    course,
    onChanged,
}: {
    course: any
    onChanged: () => void
}) {
    const [open, setOpen] = React.useState(false)
    const [busy, setBusy] = React.useState(false)
    const [fileTitre, setFileTitre] = React.useState("")
    const [file, setFile] = React.useState<File | null>(null)
    const [uploading, setUploading] = React.useState(false)
    const [msg, setMsg] = React.useState("")
    const fileRef = React.useRef<HTMLInputElement>(null)

    const files = course.files || []
    const published = !!course.is_published

    async function togglePublish() {
        setBusy(true)
        try {
            await apiFetch(`/prepa/admin/courses/${course.id}`, {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ is_published: !published }),
            })
            onChanged()
        } catch {
        } finally {
            setBusy(false)
        }
    }

    async function del() {
        if (
            !confirm(
                `Supprimer "${course.titre}" et ses ${files.length} fichier(s) ?`
            )
        )
            return
        setBusy(true)
        try {
            await apiFetch(`/prepa/admin/courses/${course.id}`, {
                method: "DELETE",
            })
            onChanged()
        } catch {
        } finally {
            setBusy(false)
        }
    }

    async function uploadFile() {
        if (!file || !fileTitre.trim()) {
            setMsg("Erreur: titre + PDF requis.")
            return
        }
        setUploading(true)
        setMsg("")
        try {
            const fd = new FormData()
            fd.append("file", file)
            fd.append("titre", fileTitre.trim())
            fd.append("ordre", String(files.length + 1))
            await apiFetch(`/prepa/admin/courses/${course.id}/files`, {
                method: "POST",
                body: fd,
            })
            setFile(null)
            setFileTitre("")
            if (fileRef.current) fileRef.current.value = ""
            setMsg("Fichier ajouté.")
            onChanged()
        } catch (e: any) {
            setMsg(`Erreur: ${e.message}`)
        } finally {
            setUploading(false)
        }
    }

    async function delFile(fid: number) {
        if (!confirm("Supprimer ce fichier ?")) return
        try {
            await apiFetch(`/prepa/admin/course-files/${fid}`, {
                method: "DELETE",
            })
            onChanged()
        } catch {}
    }

    return (
        <div style={{ borderBottom: `1px solid rgba(255,255,255,0.04)` }}>
            <div
                style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 8,
                    padding: "10px 14px",
                    background: open ? sA : "transparent",
                    borderLeft: `2px solid ${open ? b2 : "transparent"}`,
                }}
            >
                <div
                    onClick={() => setOpen((v) => !v)}
                    style={{ flex: 1, minWidth: 0, cursor: "pointer" }}
                >
                    <div
                        style={{
                            fontSize: 13,
                            fontWeight: 600,
                            color: t0,
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            whiteSpace: "nowrap" as const,
                        }}
                    >
                        {course.titre}
                    </div>
                    <div
                        style={{
                            fontSize: 10,
                            fontFamily: mono,
                            color: t2,
                            marginTop: 1,
                        }}
                    >
                        #{course.id} · {files.length} fichier(s)
                    </div>
                </div>
                <span
                    style={{
                        fontSize: 9,
                        fontFamily: mono,
                        letterSpacing: 0.5,
                        padding: "2px 7px",
                        borderRadius: 99,
                        border: `1px solid ${published ? "rgba(91,227,163,0.4)" : b0}`,
                        color: published ? OK : t2,
                        whiteSpace: "nowrap" as const,
                        flexShrink: 0,
                    }}
                >
                    {published ? "PUBLIÉ" : "BROUILLON"}
                </span>
                <Btn
                    onClick={togglePublish}
                    disabled={busy}
                    style={{ fontSize: 9, padding: "4px 8px" }}
                >
                    {published ? "Dépublier" : "Publier"}
                </Btn>
                <Btn
                    danger
                    onClick={del}
                    disabled={busy}
                    style={{ fontSize: 9, padding: "4px 8px" }}
                >
                    ✕
                </Btn>
                <span
                    onClick={() => setOpen((v) => !v)}
                    style={{
                        fontSize: 11,
                        color: open ? t0 : t2,
                        cursor: "pointer",
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
                    {files.length === 0 ? (
                        <div
                            style={{
                                fontSize: 11,
                                fontFamily: mono,
                                color: t2,
                            }}
                        >
                            Aucun fichier.
                        </div>
                    ) : (
                        <div
                            style={{
                                border: `1px solid ${b0}`,
                                borderRadius: 10,
                                overflow: "hidden",
                            }}
                        >
                            {files.map((f: any, i: number) => (
                                <div
                                    key={f.id}
                                    style={{
                                        display: "flex",
                                        alignItems: "center",
                                        gap: 8,
                                        padding: "7px 10px",
                                        borderBottom:
                                            i < files.length - 1
                                                ? `1px solid rgba(255,255,255,0.04)`
                                                : "none",
                                    }}
                                >
                                    <div style={{ flex: 1, minWidth: 0 }}>
                                        <div
                                            style={{
                                                fontSize: 11,
                                                color: t0,
                                                overflow: "hidden",
                                                textOverflow: "ellipsis",
                                                whiteSpace: "nowrap" as const,
                                            }}
                                        >
                                            {f.titre}
                                        </div>
                                        <div
                                            style={{
                                                fontSize: 9,
                                                fontFamily: mono,
                                                color: t2,
                                            }}
                                        >
                                            #{f.id} · {fmtSize(f.size_bytes)}
                                        </div>
                                    </div>
                                    <button
                                        onClick={() => delFile(f.id)}
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
                                </div>
                            ))}
                        </div>
                    )}
                    <FL>Ajouter un module (PDF)</FL>
                    <TI
                        value={fileTitre}
                        onChange={(e: any) => setFileTitre(e.target.value)}
                        placeholder="Titre du module (ex: Module 1)"
                    />
                    <input
                        ref={fileRef}
                        type="file"
                        accept="application/pdf"
                        onChange={(e) => setFile(e.target.files?.[0] || null)}
                        style={{
                            fontSize: 11,
                            fontFamily: mono,
                            color: t1,
                        }}
                    />
                    <SM text={msg} />
                    <Btn
                        onClick={uploadFile}
                        disabled={uploading || !file || !fileTitre.trim()}
                    >
                        {uploading ? "Envoi…" : "Uploader →"}
                    </Btn>
                </div>
            )}
        </div>
    )
}

function PrepaSubmissions({ exerciseId }: { exerciseId: number }) {
    const [subs, setSubs] = React.useState<any[]>([])
    const [loading, setLoading] = React.useState(true)
    const [grades, setGrades] = React.useState<
        Record<
            number,
            { note: string; feedback: string; busy: boolean; msg: string }
        >
    >({})

    function load() {
        setLoading(true)
        apiFetch(`/prepa/admin/exercises/${exerciseId}/submissions`)
            .then((d) => {
                const list = d?.items || []
                setSubs(list)
                const g: any = {}
                list.forEach((s: any) => {
                    g[s.id] = {
                        note: s.note != null ? String(s.note) : "",
                        feedback: s.feedback || "",
                        busy: false,
                        msg: "",
                    }
                })
                setGrades(g)
            })
            .catch(() => {})
            .finally(() => setLoading(false))
    }
    React.useEffect(() => {
        load()
    }, [exerciseId])

    async function grade(sid: number) {
        const g = grades[sid]
        if (!g) return
        setGrades((p) => ({ ...p, [sid]: { ...p[sid], busy: true, msg: "" } }))
        try {
            await apiFetch(`/prepa/admin/submissions/${sid}/grade`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    note: g.note === "" ? null : Number(g.note),
                    feedback: g.feedback,
                }),
            })
            setGrades((p) => ({
                ...p,
                [sid]: { ...p[sid], busy: false, msg: "✓ Noté" },
            }))
            load()
        } catch (e: any) {
            setGrades((p) => ({
                ...p,
                [sid]: { ...p[sid], busy: false, msg: `ERR: ${e.message}` },
            }))
        }
    }

    async function dl(sid: number, name: string) {
        try {
            await downloadAuth(`/prepa/admin/submissions/${sid}/download`, name)
        } catch {}
    }

    if (loading)
        return (
            <div
                style={{
                    fontSize: 10,
                    fontFamily: mono,
                    color: t2,
                    padding: "6px 0",
                }}
            >
                Chargement des copies…
            </div>
        )
    if (subs.length === 0)
        return (
            <div
                style={{
                    fontSize: 10,
                    fontFamily: mono,
                    color: t2,
                    padding: "6px 0",
                }}
            >
                Aucune copie déposée.
            </div>
        )

    return (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {subs.map((s) => {
                const g = grades[s.id] || {
                    note: "",
                    feedback: "",
                    busy: false,
                    msg: "",
                }
                return (
                    <div
                        key={s.id}
                        style={{
                            border: `1px solid ${b0}`,
                            borderRadius: 10,
                            padding: "8px 10px",
                            display: "flex",
                            flexDirection: "column",
                            gap: 6,
                        }}
                    >
                        <div
                            style={{
                                display: "flex",
                                alignItems: "center",
                                gap: 8,
                            }}
                        >
                            <div style={{ flex: 1, minWidth: 0 }}>
                                <div
                                    style={{
                                        fontSize: 11,
                                        color: t0,
                                        overflow: "hidden",
                                        textOverflow: "ellipsis",
                                        whiteSpace: "nowrap" as const,
                                    }}
                                >
                                    {s.user?.username || `#${s.user?.id}`}
                                </div>
                                <div
                                    style={{
                                        fontSize: 9,
                                        fontFamily: mono,
                                        color: t2,
                                    }}
                                >
                                    {s.status === "corrected"
                                        ? "corrigé"
                                        : "à corriger"}
                                    {s.submitted_at
                                        ? ` · ${fmtDate(s.submitted_at)}`
                                        : ""}
                                </div>
                            </div>
                            <Btn
                                onClick={() => dl(s.id, s.filename_original)}
                                style={{ fontSize: 9, padding: "4px 8px" }}
                            >
                                ↓ Copie
                            </Btn>
                        </div>
                        <div
                            style={{
                                display: "flex",
                                gap: 6,
                                alignItems: "center",
                            }}
                        >
                            <TI
                                type="number"
                                value={g.note}
                                onChange={(e: any) =>
                                    setGrades((p) => ({
                                        ...p,
                                        [s.id]: {
                                            ...p[s.id],
                                            note: e.target.value,
                                        },
                                    }))
                                }
                                placeholder="/20"
                                style={{ width: 60, height: 28 }}
                            />
                            <TI
                                value={g.feedback}
                                onChange={(e: any) =>
                                    setGrades((p) => ({
                                        ...p,
                                        [s.id]: {
                                            ...p[s.id],
                                            feedback: e.target.value,
                                        },
                                    }))
                                }
                                placeholder="Feedback…"
                                style={{ flex: 1, height: 28 }}
                            />
                            <Btn
                                onClick={() => grade(s.id)}
                                disabled={g.busy}
                                style={{ fontSize: 9, padding: "4px 10px" }}
                            >
                                {g.busy ? "…" : "Noter"}
                            </Btn>
                        </div>
                        {g.msg && (
                            <div
                                style={{
                                    fontSize: 9,
                                    fontFamily: mono,
                                    color: g.msg.startsWith("ERR")
                                        ? BAD
                                        : OK,
                                }}
                            >
                                {g.msg}
                            </div>
                        )}
                    </div>
                )
            })}
        </div>
    )
}

const PREPA_SAT = [
    { label: "18 juil 2026", date: "2026-07-18" },
    { label: "25 juil 2026", date: "2026-07-25" },
    { label: "1 août 2026", date: "2026-08-01" },
    { label: "8 août 2026", date: "2026-08-08" },
    { label: "15 août 2026", date: "2026-08-15" },
    { label: "22 août 2026", date: "2026-08-22" },
]

function PrepaExoRow({
    annee,
    week,
    exercise,
    onChanged,
}: {
    annee: string
    week: number
    exercise: any | null
    onChanged: () => void
}) {
    const sat = PREPA_SAT[week - 1]
    const [open, setOpen] = React.useState(false)
    const [busy, setBusy] = React.useState(false)
    const [msg, setMsg] = React.useState("")
    const [titre, setTitre] = React.useState(
        exercise?.titre || `Devoir ${week}`
    )
    const [consigne, setConsigne] = React.useState(exercise?.consigne || "")
    const [dateVal, setDateVal] = React.useState(
        exercise?.due_date ? String(exercise.due_date).slice(0, 10) : sat.date
    )
    const [showSubs, setShowSubs] = React.useState(false)
    const subjRef = React.useRef<HTMLInputElement>(null)
    const corrRef = React.useRef<HTMLInputElement>(null)

    React.useEffect(() => {
        setTitre(exercise?.titre || `Devoir ${week}`)
        setConsigne(exercise?.consigne || "")
        setDateVal(
            exercise?.due_date
                ? String(exercise.due_date).slice(0, 10)
                : sat.date
        )
    }, [exercise?.id])

    function dueIso() {
        return `${dateVal}T23:59:59+02:00`
    }

    async function create() {
        setBusy(true)
        setMsg("")
        try {
            await apiFetch("/prepa/admin/exercises", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    annee,
                    week_number: week,
                    titre: `Devoir ${week}`,
                    consigne: "",
                    due_date: `${sat.date}T23:59:59+02:00`,
                }),
            })
            onChanged()
            setOpen(true)
        } catch (e: any) {
            setMsg(`Erreur: ${e.message}`)
        } finally {
            setBusy(false)
        }
    }

    async function save() {
        if (!exercise) return
        setBusy(true)
        setMsg("")
        try {
            await apiFetch(`/prepa/admin/exercises/${exercise.id}`, {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    titre: titre.trim() || `Devoir ${week}`,
                    consigne,
                    due_date: dueIso(),
                }),
            })
            setMsg("✓ Enregistré")
            onChanged()
        } catch (e: any) {
            setMsg(`Erreur: ${e.message}`)
        } finally {
            setBusy(false)
        }
    }

    async function patch(body: any) {
        if (!exercise) return
        setBusy(true)
        try {
            await apiFetch(`/prepa/admin/exercises/${exercise.id}`, {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(body),
            })
            onChanged()
        } catch {
        } finally {
            setBusy(false)
        }
    }

    async function del() {
        if (!exercise) return
        if (
            !confirm(
                `Supprimer le devoir de la semaine ${week} (sujet, corrigé, copies) ?`
            )
        )
            return
        setBusy(true)
        try {
            await apiFetch(`/prepa/admin/exercises/${exercise.id}`, {
                method: "DELETE",
            })
            onChanged()
        } catch {
        } finally {
            setBusy(false)
        }
    }

    async function upload(kind: "subject" | "corrige", f: File | null) {
        if (!exercise || !f) return
        setBusy(true)
        setMsg("")
        try {
            const fd = new FormData()
            fd.append("file", f)
            await apiFetch(`/prepa/admin/exercises/${exercise.id}/${kind}`, {
                method: "POST",
                body: fd,
            })
            setMsg(kind === "subject" ? "✓ Sujet ajouté" : "✓ Corrigé ajouté")
            onChanged()
        } catch (e: any) {
            setMsg(`Erreur: ${e.message}`)
        } finally {
            setBusy(false)
        }
    }

    const published = !!exercise?.is_published

    return (
        <div style={{ borderBottom: `1px solid rgba(255,255,255,0.04)` }}>
            <div
                style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 8,
                    padding: "10px 14px",
                    background: open ? sA : "transparent",
                    borderLeft: `2px solid ${open ? b2 : "transparent"}`,
                }}
            >
                <span
                    style={{
                        fontFamily: mono,
                        fontSize: 11,
                        fontWeight: 700,
                        color: t1,
                        flexShrink: 0,
                    }}
                >
                    S{week}
                </span>
                <div
                    onClick={() => exercise && setOpen((v) => !v)}
                    style={{
                        flex: 1,
                        minWidth: 0,
                        cursor: exercise ? "pointer" : "default",
                    }}
                >
                    <div
                        style={{
                            fontSize: 12,
                            fontWeight: 600,
                            color: exercise ? t0 : t2,
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            whiteSpace: "nowrap" as const,
                        }}
                    >
                        {exercise ? exercise.titre : "Semaine " + week}
                    </div>
                    <div
                        style={{
                            fontSize: 9,
                            fontFamily: mono,
                            color: t2,
                            marginTop: 1,
                        }}
                    >
                        {sat.label}
                    </div>
                </div>
                {exercise ? (
                    <>
                        <span
                            style={{
                                fontSize: 9,
                                fontFamily: mono,
                                padding: "2px 7px",
                                borderRadius: 99,
                                border: `1px solid ${published ? "rgba(91,227,163,0.4)" : b0}`,
                                color: published ? OK : t2,
                                whiteSpace: "nowrap" as const,
                                flexShrink: 0,
                            }}
                        >
                            {published ? "PUBLIÉ" : "BROUILLON"}
                        </span>
                        <span
                            onClick={() => setOpen((v) => !v)}
                            style={{
                                fontSize: 11,
                                color: open ? t0 : t2,
                                cursor: "pointer",
                            }}
                        >
                            ›
                        </span>
                    </>
                ) : (
                    <Btn
                        onClick={create}
                        disabled={busy}
                        style={{ fontSize: 9, padding: "4px 10px" }}
                    >
                        {busy ? "…" : "Créer"}
                    </Btn>
                )}
            </div>

            {open && exercise && (
                <div
                    style={{
                        padding: "10px 14px 14px",
                        background: "rgba(255,255,255,0.015)",
                        display: "flex",
                        flexDirection: "column",
                        gap: 8,
                    }}
                >
                    <div>
                        <FL>Titre</FL>
                        <TI
                            value={titre}
                            onChange={(e: any) => setTitre(e.target.value)}
                            placeholder={`Devoir ${week}`}
                        />
                    </div>
                    <div>
                        <FL>Consigne</FL>
                        <TAI
                            value={consigne}
                            onChange={(e: any) => setConsigne(e.target.value)}
                            placeholder="Type d'exercice + instructions…"
                            rows={2}
                        />
                    </div>
                    <div
                        style={{
                            display: "flex",
                            gap: 8,
                            alignItems: "flex-end",
                        }}
                    >
                        <div style={{ flex: 1 }}>
                            <FL>Deadline</FL>
                            <input
                                type="date"
                                value={dateVal}
                                onChange={(e) => setDateVal(e.target.value)}
                                style={{
                                    width: "100%",
                                    height: 34,
                                    padding: "0 10px",
                                    background: FIELD,
                                    border: `1px solid ${b0}`,
                                    borderRadius: 10,
                                    color: t0,
                                    fontSize: 12,
                                    fontFamily: mono,
                                    outline: "none",
                                    boxSizing: "border-box" as const,
                                }}
                            />
                        </div>
                        <Btn
                            onClick={save}
                            disabled={busy}
                            style={{ height: 34 }}
                        >
                            {busy ? "…" : "Enregistrer"}
                        </Btn>
                    </div>

                    <div style={{ display: "flex", gap: 6 }}>
                        <Btn
                            onClick={() => patch({ is_published: !published })}
                            disabled={busy}
                            style={{ fontSize: 10, flex: 1 }}
                        >
                            {published
                                ? "Dépublier le devoir"
                                : "Publier le devoir"}
                        </Btn>
                        <Btn
                            danger
                            onClick={del}
                            disabled={busy}
                            style={{ fontSize: 10 }}
                        >
                            Suppr.
                        </Btn>
                    </div>

                    <div
                        style={{
                            border: `1px solid ${b0}`,
                            borderRadius: 10,
                            padding: "8px 10px",
                            display: "flex",
                            flexDirection: "column",
                            gap: 6,
                        }}
                    >
                        <div
                            style={{
                                display: "flex",
                                alignItems: "center",
                                gap: 8,
                            }}
                        >
                            <span
                                style={{
                                    fontSize: 10,
                                    fontFamily: mono,
                                    color: t1,
                                    flex: 1,
                                }}
                            >
                                Sujet :{" "}
                                {exercise.has_subject
                                    ? exercise.subject_filename || "présent"
                                    : "aucun"}
                            </span>
                            {exercise.has_subject && (
                                <Btn
                                    onClick={() =>
                                        downloadAuth(
                                            `/prepa/admin/exercises/${exercise.id}/subject/download`,
                                            exercise.subject_filename ||
                                                "sujet.pdf"
                                        ).catch(() => {})
                                    }
                                    style={{ fontSize: 9, padding: "3px 8px" }}
                                >
                                    ↓
                                </Btn>
                            )}
                        </div>
                        <input
                            ref={subjRef}
                            type="file"
                            accept="application/pdf"
                            onChange={(e) =>
                                upload("subject", e.target.files?.[0] || null)
                            }
                            style={{
                                fontSize: 10,
                                fontFamily: mono,
                                color: t1,
                            }}
                        />
                    </div>

                    <div
                        style={{
                            border: `1px solid ${b0}`,
                            borderRadius: 10,
                            padding: "8px 10px",
                            display: "flex",
                            flexDirection: "column",
                            gap: 6,
                        }}
                    >
                        <div
                            style={{
                                display: "flex",
                                alignItems: "center",
                                gap: 8,
                            }}
                        >
                            <span
                                style={{
                                    fontSize: 10,
                                    fontFamily: mono,
                                    color: t1,
                                    flex: 1,
                                }}
                            >
                                Corrigé :{" "}
                                {exercise.has_corrige
                                    ? exercise.corrige_filename || "présent"
                                    : "aucun"}
                            </span>
                            {exercise.has_corrige && (
                                <Btn
                                    onClick={() =>
                                        patch({
                                            corrige_published:
                                                !exercise.corrige_published,
                                        })
                                    }
                                    disabled={busy}
                                    style={{ fontSize: 9, padding: "3px 8px" }}
                                >
                                    {exercise.corrige_published
                                        ? "Masquer"
                                        : "Publier"}
                                </Btn>
                            )}
                        </div>
                        <input
                            ref={corrRef}
                            type="file"
                            accept="application/pdf"
                            onChange={(e) =>
                                upload("corrige", e.target.files?.[0] || null)
                            }
                            style={{
                                fontSize: 10,
                                fontFamily: mono,
                                color: t1,
                            }}
                        />
                    </div>

                    <SM text={msg} />

                    <Btn
                        onClick={() => setShowSubs((v) => !v)}
                        style={{ fontSize: 10 }}
                    >
                        {showSubs
                            ? "Masquer les copies"
                            : "Voir les copies déposées"}
                    </Btn>
                    {showSubs && <PrepaSubmissions exerciseId={exercise.id} />}
                </div>
            )}
        </div>
    )
}

function PrepaSection() {
    const [mode, setMode] = React.useState<"dossiers" | "exercices">("dossiers")
    const [annee, setAnnee] = React.useState<"L1" | "L2" | "L3">("L1")
    const [courses, setCourses] = React.useState<any[]>([])
    const [exercises, setExercises] = React.useState<any[]>([])
    const [loading, setLoading] = React.useState(true)
    const [showCreate, setShowCreate] = React.useState(false)
    const [newTitre, setNewTitre] = React.useState("")
    const [newDesc, setNewDesc] = React.useState("")
    const [creating, setCreating] = React.useState(false)
    const [msg, setMsg] = React.useState("")

    function loadCourses() {
        return apiFetch("/prepa/admin/courses")
            .then((d) => setCourses(d?.items || []))
            .catch(() => {})
    }
    function loadExercises() {
        return apiFetch("/prepa/admin/exercises")
            .then((d) => setExercises(d?.items || []))
            .catch(() => {})
    }
    function loadAll() {
        setLoading(true)
        Promise.all([loadCourses(), loadExercises()]).finally(() =>
            setLoading(false)
        )
    }
    React.useEffect(() => {
        loadAll()
    }, [])

    const filteredCourses = courses.filter((c) => c.annee === annee)
    const exByWeek: Record<number, any> = {}
    exercises
        .filter((e) => e.annee === annee)
        .forEach((e) => {
            exByWeek[e.week_number] = e
        })

    async function createDossier() {
        if (!newTitre.trim()) return
        setCreating(true)
        setMsg("")
        try {
            await apiFetch("/prepa/admin/courses", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    annee,
                    titre: newTitre.trim(),
                    description: newDesc.trim(),
                    ordre: filteredCourses.length + 1,
                }),
            })
            setNewTitre("")
            setNewDesc("")
            setShowCreate(false)
            setMsg("")
            loadCourses()
        } catch (e: any) {
            setMsg(`Erreur: ${e.message}`)
        } finally {
            setCreating(false)
        }
    }

    function ModeBtn({
        id,
        label,
    }: {
        id: "dossiers" | "exercices"
        label: string
    }) {
        const active = mode === id
        return (
            <button
                onClick={() => setMode(id)}
                style={{
                    fontSize: 10,
                    fontFamily: mono,
                    padding: "3px 11px",
                    borderRadius: 99,
                    border: `1px solid ${active ? b2 : b0}`,
                    background: active ? sA : "transparent",
                    color: active ? t0 : t1,
                    cursor: "pointer",
                    fontWeight: active ? 700 : 400,
                }}
            >
                {label}
            </button>
        )
    }

    return (
        <div
            style={{
                display: "flex",
                flexDirection: "column",
                height: "100%",
                maxHeight: "100%",
            }}
        >
            <SBar
                left={
                    <span style={{ display: "flex", gap: 6 }}>
                        <ModeBtn id="dossiers" label="Dossiers" />
                        <ModeBtn id="exercices" label="Exercices" />
                    </span>
                }
                right={
                    mode === "dossiers" ? (
                        <Btn onClick={() => setShowCreate((v) => !v)}>
                            {showCreate ? "✕ Annuler" : "+ Dossier"}
                        </Btn>
                    ) : undefined
                }
            />
            <div
                style={{
                    display: "flex",
                    gap: 6,
                    padding: "8px 14px",
                    borderBottom: `1px solid ${b0}`,
                    flexShrink: 0,
                }}
            >
                {(["L1", "L2", "L3"] as const).map((y) => (
                    <button
                        key={y}
                        onClick={() => setAnnee(y)}
                        style={{
                            fontSize: 10,
                            fontFamily: mono,
                            padding: "3px 14px",
                            borderRadius: 99,
                            border: `1px solid ${annee === y ? b2 : b0}`,
                            background: annee === y ? sA : "transparent",
                            color: annee === y ? t0 : t1,
                            cursor: "pointer",
                            fontWeight: annee === y ? 700 : 400,
                        }}
                    >
                        {y}
                    </button>
                ))}
            </div>

            {loading ? (
                <div
                    style={{
                        padding: "32px 14px",
                        textAlign: "center",
                        fontFamily: mono,
                        fontSize: 12,
                        color: t2,
                    }}
                >
                    Chargement…
                </div>
            ) : mode === "exercices" ? (
                <div style={{ flex: 1, overflowY: "auto" }}>
                    {[1, 2, 3, 4, 5, 6].map((w) => (
                        <PrepaExoRow
                            key={`${annee}-${w}`}
                            annee={annee}
                            week={w}
                            exercise={exByWeek[w] || null}
                            onChanged={loadExercises}
                        />
                    ))}
                </div>
            ) : (
                <>
                    {showCreate && (
                        <div
                            style={{
                                padding: "12px 14px",
                                borderBottom: `1px solid ${b0}`,
                                display: "flex",
                                flexDirection: "column",
                                gap: 8,
                                flexShrink: 0,
                            }}
                        >
                            <div>
                                <FL>Nouveau dossier en {annee}</FL>
                                <TI
                                    value={newTitre}
                                    onChange={(e: any) =>
                                        setNewTitre(e.target.value)
                                    }
                                    placeholder="Titre (ex: Introduction au droit)"
                                />
                            </div>
                            <TI
                                value={newDesc}
                                onChange={(e: any) =>
                                    setNewDesc(e.target.value)
                                }
                                placeholder="Description (optionnel)"
                            />
                            <SM text={msg} />
                            <Btn
                                onClick={createDossier}
                                disabled={creating || !newTitre.trim()}
                            >
                                {creating ? "Création…" : "Créer le dossier →"}
                            </Btn>
                        </div>
                    )}
                    {filteredCourses.length === 0 ? (
                        <div
                            style={{
                                padding: "32px 14px",
                                textAlign: "center",
                                fontFamily: mono,
                                fontSize: 12,
                                color: t2,
                            }}
                        >
                            Aucun dossier en {annee}.
                        </div>
                    ) : (
                        <div style={{ flex: 1, overflowY: "auto" }}>
                            {filteredCourses.map((c) => (
                                <PrepaDossier
                                    key={c.id}
                                    course={c}
                                    onChanged={loadCourses}
                                />
                            ))}
                        </div>
                    )}
                </>
            )}
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

function EtudiantRow({ e }: { e: Etudiant }) {
    const [open, setOpen] = React.useState(false)
    const [hov, setHov] = React.useState(false)
    const nom = [e.prenom, e.nom].filter(Boolean).join(" ") || e.username || "—"

    return (
        <div style={{ borderBottom: `1px solid rgba(255,255,255,0.04)` }}>
            <div
                onClick={() => setOpen((v) => !v)}
                onMouseEnter={() => setHov(true)}
                onMouseLeave={() => setHov(false)}
                style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 10,
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
                        {e.email}
                        {e.telephone ? ` · ${e.telephone}` : ""}
                    </div>
                </div>

                {e.a_un_impaye && (
                    <Tag color={BAD} border="rgba(255,107,107,0.4)">
                        IMPAYÉ
                    </Tag>
                )}
                {!e.compte_cree && (
                    <Tag color="#e0a" border="rgba(220,0,170,0.35)">
                        SANS COMPTE
                    </Tag>
                )}
                <Tag color={t1} border={b0}>
                    {e.niveau}
                </Tag>
                <span
                    style={{
                        fontSize: 11,
                        fontFamily: mono,
                        color: t0,
                        flexShrink: 0,
                    }}
                >
                    {e.nb_actives}
                </span>
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
                        {[
                            {
                                l: "Compte",
                                v: e.compte_cree ? `#${e.user_id}` : "non créé",
                            },
                            {
                                l: "Discord",
                                v: e.discord_lie ? "lié" : "non lié",
                            },
                            {
                                l: "Inscrit le",
                                v: fmtDate(e.premiere_inscription),
                            },
                        ].map(({ l, v }) => (
                            <div
                                key={l}
                                style={{
                                    padding: "6px 8px",
                                    border: `1px solid ${b0}`,
                                    borderRadius: 10,
                                }}
                            >
                                <div
                                    style={{
                                        fontSize: 8,
                                        fontFamily: mono,
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
                                        fontSize: 11,
                                        fontFamily: mono,
                                        color: t0,
                                    }}
                                >
                                    {v}
                                </div>
                            </div>
                        ))}
                    </div>

                    <FL>Matières ({e.matieres.length})</FL>
                    <div
                        style={{
                            border: `1px solid ${b0}`,
                            borderRadius: 10,
                            overflow: "hidden",
                        }}
                    >
                        {e.matieres.map((m, i) => (
                            <div
                                key={m.matiere_key + i}
                                style={{
                                    display: "flex",
                                    alignItems: "center",
                                    gap: 8,
                                    padding: "7px 10px",
                                    borderBottom:
                                        i < e.matieres.length - 1
                                            ? `1px solid rgba(255,255,255,0.04)`
                                            : "none",
                                }}
                            >
                                <Tag color={t1} border={b0}>
                                    {m.niveau}
                                </Tag>
                                <span
                                    style={{
                                        flex: 1,
                                        fontSize: 11,
                                        color: t0,
                                        overflow: "hidden",
                                        textOverflow: "ellipsis",
                                        whiteSpace: "nowrap" as const,
                                    }}
                                >
                                    {m.label}
                                </span>
                                <span
                                    style={{
                                        fontSize: 9,
                                        fontFamily: mono,
                                        color: t2,
                                    }}
                                >
                                    {fmtDate(m.created_at)}
                                </span>
                                <StatusTag status={m.status} />
                            </div>
                        ))}
                    </div>
                </div>
            )}
        </div>
    )
}

function InscriptionRow({ i }: { i: Inscription }) {
    return (
        <div
            style={{
                display: "flex",
                alignItems: "center",
                gap: 10,
                padding: "9px 14px",
                borderBottom: `1px solid rgba(255,255,255,0.04)`,
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
                    {[i.prenom, i.nom].filter(Boolean).join(" ") || "—"}
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
                    {i.email}
                    {i.telephone ? ` · ${i.telephone}` : ""} ·{" "}
                    {(i.matieres_labels || []).join(", ")}
                </div>
            </div>
            <Tag color={t1} border={b0}>
                {i.niveau}
            </Tag>
            <span
                style={{
                    fontSize: 9,
                    fontFamily: mono,
                    color: t2,
                    flexShrink: 0,
                }}
            >
                {fmtDate(i.created_at)}
            </span>
        </div>
    )
}

function RessourcesPanel({ niveau }: { niveau: Niveau }) {
    const [rows, setRows] = React.useState<Ressource[]>([])
    const [loading, setLoading] = React.useState(true)
    const [form, setForm] = React.useState({ titre: "", matiere: "", desc: "" })
    const [file, setFile] = React.useState<File | null>(null)
    const [busy, setBusy] = React.useState(false)
    const [msg, setMsg] = React.useState("")
    const fileRef = React.useRef<HTMLInputElement>(null)

    function load() {
        setLoading(true)
        apiFetch("/prepa/adjuris/admin/ressources")
            .then((d) => setRows(d?.items || []))
            .catch((e) => setMsg(`ERR: ${e.message}`))
            .finally(() => setLoading(false))
    }
    React.useEffect(() => {
        load()
    }, [])

    async function upload() {
        if (!file || !form.titre.trim()) {
            setMsg("ERR: titre + PDF requis.")
            return
        }
        setBusy(true)
        setMsg("")
        try {
            const fd = new FormData()
            fd.append("file", file)
            fd.append("niveau", niveau)
            fd.append("titre", form.titre.trim())
            fd.append("matiere_key", form.matiere)
            fd.append("description", form.desc.trim())
            fd.append("ordre", String(rows.length + 1))
            await apiFetch("/prepa/adjuris/admin/ressources", {
                method: "POST",
                body: fd,
            })
            setForm({ titre: "", matiere: "", desc: "" })
            setFile(null)
            if (fileRef.current) fileRef.current.value = ""
            setMsg("✓ Ressource ajoutée")
            load()
        } catch (e: any) {
            setMsg(`ERR: ${e.message}`)
        } finally {
            setBusy(false)
        }
    }

    async function togglePublish(r: Ressource) {
        try {
            await apiFetch(`/prepa/adjuris/admin/ressources/${r.id}`, {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ is_published: !r.is_published }),
            })
            setRows((p) =>
                p.map((x) =>
                    x.id === r.id ? { ...x, is_published: !x.is_published } : x
                )
            )
        } catch {}
    }

    async function del(r: Ressource) {
        if (!confirm(`Supprimer « ${r.titre} » ?`)) return
        try {
            await apiFetch(`/prepa/adjuris/admin/ressources/${r.id}`, {
                method: "DELETE",
            })
            setRows((p) => p.filter((x) => x.id !== r.id))
        } catch {}
    }

    const filtrees = rows.filter((r) => r.niveau === niveau)

    return (
        <div style={{ flex: 1, overflowY: "auto" }}>
            <div
                style={{
                    padding: "12px 14px",
                    borderBottom: `1px solid ${b0}`,
                    display: "flex",
                    flexDirection: "column",
                    gap: 8,
                }}
            >
                <FL>Ajouter une ressource en {niveau}</FL>
                <div
                    style={{
                        display: "grid",
                        gridTemplateColumns: "1fr 1fr",
                        gap: 8,
                    }}
                >
                    <TI
                        value={form.titre}
                        onChange={(e: any) =>
                            setForm((f) => ({ ...f, titre: e.target.value }))
                        }
                        placeholder="Titre (ex: Séance 1)"
                    />
                    <SI
                        value={form.matiere}
                        onChange={(e: any) =>
                            setForm((f) => ({ ...f, matiere: e.target.value }))
                        }
                    >
                        <option value="">Méthodologie (bloc commun)</option>
                        {(MATIERES[niveau] || []).map((m) => (
                            <option key={m.key} value={m.key}>
                                {m.label}
                            </option>
                        ))}
                    </SI>
                </div>
                <TI
                    value={form.desc}
                    onChange={(e: any) =>
                        setForm((f) => ({ ...f, desc: e.target.value }))
                    }
                    placeholder="Description (optionnel)"
                />
                <input
                    ref={fileRef}
                    type="file"
                    accept="application/pdf"
                    onChange={(e) => setFile(e.target.files?.[0] || null)}
                    style={{ fontSize: 11, fontFamily: mono, color: t1 }}
                />
                {msg && (
                    <div
                        style={{
                            fontSize: 10,
                            fontFamily: mono,
                            color: msg.startsWith("ERR") ? BAD : OK,
                        }}
                    >
                        {msg}
                    </div>
                )}
                <Btn
                    onClick={upload}
                    disabled={busy || !file || !form.titre.trim()}
                >
                    {busy ? "Envoi…" : "Uploader →"}
                </Btn>
            </div>

            {loading ? (
                <div style={VIDE}>Chargement…</div>
            ) : filtrees.length === 0 ? (
                <div style={VIDE}>Aucune ressource en {niveau}.</div>
            ) : (
                filtrees.map((r) => (
                    <div
                        key={r.id}
                        style={{
                            display: "flex",
                            alignItems: "center",
                            gap: 8,
                            padding: "9px 14px",
                            borderBottom: `1px solid rgba(255,255,255,0.04)`,
                            opacity: r.is_published ? 1 : 0.45,
                            minWidth: 0,
                        }}
                    >
                        <Tag
                            color={r.matiere_key ? t1 : "#FFD166"}
                            border={
                                r.matiere_key ? b0 : "rgba(255,209,102,0.35)"
                            }
                        >
                            {r.matiere_key ? r.matiere_label : "MÉTHODO"}
                        </Tag>
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
                                {r.titre}
                            </div>
                            <div
                                style={{
                                    fontSize: 9,
                                    fontFamily: mono,
                                    color: t2,
                                }}
                            >
                                #{r.id} · {fmtSize(r.size_bytes)} ·{" "}
                                {r.filename_original}
                            </div>
                        </div>
                        <Btn
                            onClick={() =>
                                downloadAuth(
                                    `/prepa/adjuris/admin/ressources/${r.id}/download`,
                                    r.filename_original
                                ).catch(() => {})
                            }
                            style={{ fontSize: 9, padding: "3px 8px" }}
                        >
                            ↓
                        </Btn>
                        <Btn
                            onClick={() => togglePublish(r)}
                            style={{ fontSize: 9, padding: "3px 8px" }}
                        >
                            {r.is_published ? "Publié" : "Masqué"}
                        </Btn>
                        <Btn
                            danger
                            onClick={() => del(r)}
                            style={{ fontSize: 9, padding: "3px 8px" }}
                        >
                            ✕
                        </Btn>
                    </div>
                ))
            )}
        </div>
    )
}

// ══════════════════════════════════════════════════════════
// AdJuris — Agenda des cours et facturation
// ══════════════════════════════════════════════════════════
// L'agenda fait foi pour la facturation : chaque création, déplacement,
// annulation ou suppression recalcule les prélèvements des élèves de la
// matière. Avant d'écrire, la console demande un aperçu à l'API
// (?simulation=true) et affiche l'impact dans une fenêtre de confirmation.
//
// Les heures saisies sont envoyées SANS fuseau ("2026-10-08T21:00:00") :
// l'API les interprète en heure de Paris.

type Niveau = "L1" | "L2" | "L3" | "M1"
const NIVEAUX: Niveau[] = ["L1", "L2", "L3", "M1"]

interface AgendaMatiere {
    key: string
    label: string
    niveau: string
    prix_configures: boolean
    inscrits: number
}
interface Creneau {
    jour_semaine: number
    heure: string
    duree_minutes: number
}
interface AgendaData {
    items: Seance[]
    matieres: AgendaMatiere[]
    creneaux: Record<string, Creneau>
    facturation: {
        prix_seance_cents: number
        heure_prelevement: string
        fin_programme: string
    }
}
interface Impact {
    enrollment_id: number
    email: string
    matiere_key: string
    matiere_label: string
    mois?: string
    date_prelevement?: string
    avant: any
    apres: any
    delta_cents: number
    type: string
}
interface Ecriture {
    ok: boolean
    simulation: boolean
    impacts: Impact[]
    resume: {
        nb_eleves: number
        delta_total_cents: number
        credits_cents: number
    }
    seance?: Seance
    crees?: number
    ignores?: number
    dates?: string[]
}
interface MoisFacture {
    mois: string
    date: string
    seances: number
    montant_cents: number
    credits: number
    preleve: boolean
}
interface Facturation {
    enrollment_id: number
    email: string | null
    matiere_key: string
    matiere_label: string
    niveau: string
    status: string
    source: string | null
    echeancier_statut: string | null
    inscrit_le: string | null
    impaye_depuis: string | null
    historique: boolean
    seance_prepayee: string | null
    mois: MoisFacture[]
    prochain_prelevement: MoisFacture | null
}
interface Alerte {
    niveau: "erreur" | "alerte" | "info"
    type: string
    message: string
    matiere_key?: string
    enrollment_id?: number
}

const PRIX_SEANCE_CENTS = 2000

const JOURS_COURTS = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"]
const JOURS_LONGS = [
    "lundi",
    "mardi",
    "mercredi",
    "jeudi",
    "vendredi",
    "samedi",
    "dimanche",
]
const MOIS_FR = [
    "janvier",
    "février",
    "mars",
    "avril",
    "mai",
    "juin",
    "juillet",
    "août",
    "septembre",
    "octobre",
    "novembre",
    "décembre",
]

const MOIS_COURTS = [
    "janv.",
    "févr.",
    "mars",
    "avr.",
    "mai",
    "juin",
    "juil.",
    "août",
    "sept.",
    "oct.",
    "nov.",
    "déc.",
]

// Libellé court d'une matière pour les cases de l'agenda :
// « Droit des sociétés » → « sociétés », « Introduction au droit » → « Introduction ».
const MOTS_VIDES = ["droit", "des", "du", "de", "la", "le", "au", "et"]
function libelleCourt(label: string): string {
    const mots = (label || "").split(" ")
    return mots.find((m) => !MOTS_VIDES.includes(m.toLowerCase())) || label
}

// Une couleur par matière, stable (ordre de MATIERES).
const COULEURS_MATIERES = [
    OK,
    "#FFD166",
    "#EF476F",
    "#9B6FFF",
    "#06D6A0",
    "#F78C6B",
    "#7AA2F7",
    "#E0A458",
    "#C77DFF",
    "#5BC0EB",
]
function couleurMatiere(key: string | null): string {
    if (!key) return "#9a9a9a"
    const i = MATIERES_FLAT.findIndex((m) => m.key === key)
    return COULEURS_MATIERES[(i < 0 ? 0 : i) % COULEURS_MATIERES.length]
}

// ── Dates (heure locale du navigateur = heure de Paris pour l'équipe) ──
const p2 = (n: number) => String(n).padStart(2, "0")
function ymd(d: Date): string {
    return `${d.getFullYear()}-${p2(d.getMonth() + 1)}-${p2(d.getDate())}`
}
function hm(d: Date): string {
    return `${p2(d.getHours())}:${p2(d.getMinutes())}`
}
// "2026-10-08T21:00:00" : sans fuseau, l'API lit une heure de Paris.
function naif(jour: string, heure: string): string {
    return `${jour}T${heure.length === 5 ? heure + ":00" : heure}`
}
function lundiDe(d: Date): Date {
    const r = new Date(d.getFullYear(), d.getMonth(), d.getDate())
    r.setDate(r.getDate() - ((r.getDay() + 6) % 7))
    return r
}
function ajouterJours(d: Date, n: number): Date {
    const r = new Date(d)
    r.setDate(r.getDate() + n)
    return r
}
function dateCourte(iso: string | null): string {
    if (!iso) return "—"
    const d = new Date(iso)
    return `${d.getDate()} ${MOIS_COURTS[d.getMonth()]}`
}
function dateLongue(iso: string | null, avecHeure = true): string {
    if (!iso) return "—"
    const d = new Date(iso)
    const base = `${JOURS_LONGS[(d.getDay() + 6) % 7]} ${d.getDate()} ${MOIS_FR[d.getMonth()]}`
    return avecHeure
        ? `${base} à ${d.getHours()} h ${p2(d.getMinutes())}`
        : base
}
function moisLibelle(cle: string): string {
    const [a, m] = cle.split("-").map(Number)
    return `${MOIS_FR[m - 1]} ${a}`
}
function euros(cents: number): string {
    const v = cents / 100
    return `${Number.isInteger(v) ? v : v.toFixed(2).replace(".", ",")} €`
}

function apiJson(path: string, method: string, body?: any) {
    return apiFetch(path, {
        method,
        headers: body ? { "Content-Type": "application/json" } : undefined,
        body: body ? JSON.stringify(body) : undefined,
    })
}

// ── Primitives propres à l'agenda ──

function Overlay({
    children,
    onClose,
}: {
    children: React.ReactNode
    onClose: () => void
}) {
    return (
        <div
            onMouseDown={(e) => {
                if (e.target === e.currentTarget) onClose()
            }}
            style={{
                position: "absolute" as const,
                inset: 0,
                background: "rgba(0,0,0,0.78)",
                display: "flex",
                alignItems: "flex-start",
                justifyContent: "center",
                padding: "18px 12px",
                zIndex: 20,
                overflowY: "auto",
            }}
        >
            <div
                style={{
                    width: "100%",
                    maxWidth: 500,
                    background: "#0f0f16",
                    border: `1px solid ${b1}`,
                    borderRadius: 14,
                    padding: 14,
                    display: "flex",
                    flexDirection: "column",
                    gap: 10,
                }}
            >
                {children}
            </div>
        </div>
    )
}

function TitreModal({
    children,
    onClose,
}: {
    children: React.ReactNode
    onClose: () => void
}) {
    return (
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span
                style={{
                    flex: 1,
                    fontSize: 13,
                    fontWeight: 600,
                    color: t0,
                }}
            >
                {children}
            </span>
            <button
                onClick={onClose}
                style={{
                    background: "none",
                    border: "none",
                    color: t1,
                    cursor: "pointer",
                    fontSize: 14,
                }}
            >
                ✕
            </button>
        </div>
    )
}

function Pastille({ color }: { color: string }) {
    return (
        <span
            style={{
                width: 7,
                height: 7,
                borderRadius: "50%",
                background: color,
                flexShrink: 0,
                display: "inline-block",
            }}
        />
    )
}

function MiniBtn({ actif, onClick, children }: any) {
    return (
        <button
            onClick={onClick}
            style={{
                fontSize: 10,
                fontFamily: mono,
                padding: "3px 9px",
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

// Détail lisible d'un impact de facturation.
function ligneImpact(i: Impact): string {
    if (i.type === "seance_prepayee")
        return `séance payée d'avance : ${dateLongue(i.avant)} → ${dateLongue(i.apres)}`
    const quand = i.date_prelevement
        ? dateCourte(i.date_prelevement)
        : i.mois || ""
    if (i.type === "ajustement")
        return `prélèvement du ${quand} : ${i.avant} → ${i.apres} séance(s) (${i.delta_cents > 0 ? "+" : ""}${euros(i.delta_cents)})`
    if (i.type === "credit")
        return `${moisLibelle(i.mois || "")} déjà prélevé : ${euros(-i.delta_cents)} crédités sur le prochain prélèvement`
    if (i.type === "non_facture")
        return `séance ajoutée dans ${moisLibelle(i.mois || "")}, déjà prélevé : non facturée`
    return i.type
}

function ImpactListe({ ecriture }: { ecriture: Ecriture }) {
    const { impacts, resume } = ecriture
    if (!impacts.length)
        return (
            <div style={{ fontSize: 11, fontFamily: mono, color: t1 }}>
                Aucun élève concerné : la facturation ne change pas.
            </div>
        )
    return (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <div style={{ fontSize: 11, fontFamily: mono, color: t0 }}>
                {resume.nb_eleves} inscription(s) concernée(s)
                {resume.delta_total_cents !== 0 &&
                    ` · ${resume.delta_total_cents > 0 ? "+" : ""}${euros(resume.delta_total_cents)} au total`}
                {resume.credits_cents > 0 &&
                    ` · dont ${euros(resume.credits_cents)} de crédits`}
            </div>
            <div
                style={{
                    border: `1px solid ${b0}`,
                    borderRadius: 10,
                    maxHeight: 180,
                    overflowY: "auto",
                }}
            >
                {impacts.map((i, n) => (
                    <div
                        key={n}
                        style={{
                            padding: "6px 9px",
                            borderBottom:
                                n < impacts.length - 1
                                    ? `1px solid rgba(255,255,255,0.04)`
                                    : "none",
                        }}
                    >
                        <div
                            style={{
                                fontSize: 10,
                                fontFamily: mono,
                                color: t0,
                                overflow: "hidden",
                                textOverflow: "ellipsis",
                                whiteSpace: "nowrap" as const,
                            }}
                        >
                            {i.email} · {i.matiere_label}
                        </div>
                        <div
                            style={{
                                fontSize: 10,
                                fontFamily: mono,
                                color: i.type === "credit" ? "#FFD166" : t1,
                            }}
                        >
                            {ligneImpact(i)}
                        </div>
                    </div>
                ))}
            </div>
        </div>
    )
}

// ── Agenda ──

type Vue = "mois" | "semaine" | "liste"
type ModalAgenda =
    | { type: "seance"; seance: Seance }
    | { type: "creer"; jour: string }
    | { type: "serie" }
    | null
interface Confirmation {
    titre: string
    appel: (simulation: boolean) => Promise<Ecriture>
    apercu: Ecriture
    danger?: boolean
}

export function AgendaPanel({ niveau }: { niveau: "tous" | Niveau }) {
    const [data, setData] = React.useState<AgendaData | null>(null)
    const [loading, setLoading] = React.useState(true)
    const [msg, setMsg] = React.useState("")
    const [vue, setVue] = React.useState<Vue>("mois")
    const [curseur, setCurseur] = React.useState<Date>(() => {
        const d = new Date()
        return new Date(d.getFullYear(), d.getMonth(), 1)
    })
    const [semaine, setSemaine] = React.useState<Date>(() =>
        lundiDe(new Date())
    )
    const [masquerAnnulees, setMasquerAnnulees] = React.useState(false)
    const [modal, setModal] = React.useState<ModalAgenda>(null)
    const [confirmation, setConfirmation] = React.useState<Confirmation | null>(
        null
    )
    const [busy, setBusy] = React.useState(false)
    const [survol, setSurvol] = React.useState<string | null>(null)
    const [deplacement, setDeplacement] = React.useState<{
        seance: Seance
        jour: string
    } | null>(null)

    function load() {
        setLoading(true)
        apiFetch("/prepa/adjuris/admin/agenda")
            .then((d) => setData(d))
            .catch((e) => setMsg(`ERR: ${e.message}`))
            .finally(() => setLoading(false))
    }
    React.useEffect(() => {
        load()
    }, [])

    const seances = (data?.items || []).filter(
        (s) =>
            (niveau === "tous" || s.niveau === niveau) &&
            (!masquerAnnulees || s.statut !== "annulee")
    )
    const parJour: Record<string, Seance[]> = {}
    seances.forEach((s) => {
        if (!s.date_debut) return
        const k = ymd(new Date(s.date_debut))
        ;(parJour[k] = parJour[k] || []).push(s)
    })
    Object.values(parJour).forEach((l) =>
        l.sort((a, b) => (a.date_debut! < b.date_debut! ? -1 : 1))
    )

    const inscrits = (key: string | null) =>
        data?.matieres.find((m) => m.key === key)?.inscrits || 0

    // Écriture : aperçu d'abord, confirmation si quelqu'un est concerné.
    async function proposer(
        titre: string,
        appel: (simulation: boolean) => Promise<Ecriture>,
        toujoursConfirmer = false,
        danger = false
    ) {
        setBusy(true)
        setMsg("")
        try {
            const apercu = await appel(true)
            if (!apercu.impacts.length && !toujoursConfirmer) {
                await appel(false)
                setMsg(`✓ ${titre}`)
                setModal(null)
                load()
            } else {
                setConfirmation({ titre, appel, apercu, danger })
            }
        } catch (e: any) {
            setMsg(`ERR: ${e.message}`)
        } finally {
            setBusy(false)
        }
    }

    async function confirmer() {
        if (!confirmation) return
        setBusy(true)
        try {
            const r = await confirmation.appel(false)
            setMsg(
                `✓ ${confirmation.titre}` +
                    (r.impacts.length
                        ? ` · facturation mise à jour (${r.resume.nb_eleves} inscription(s))`
                        : "")
            )
            setConfirmation(null)
            setModal(null)
            setDeplacement(null)
            load()
        } catch (e: any) {
            setMsg(`ERR: ${e.message}`)
        } finally {
            setBusy(false)
        }
    }

    // Glisser-déposer : on demande d'abord la portée (ce cours, les
    // suivants, toute la récurrence), puis l'aperçu de l'impact.
    function deplacer(s: Seance, jour: string) {
        if (!s.date_debut) return
        if (ymd(new Date(s.date_debut)) === jour) return
        setDeplacement({ seance: s, jour })
    }

    // ── Cellule d'un jour (vue mois et semaine) ──
    // Fonctions de rendu, pas des composants : un composant défini ici
    // serait recréé à chaque rendu, et le survol pendant un glisser-déposer
    // démonterait la séance en cours de déplacement.
    function jourCellule(date: Date, horsMois = false, grand = false) {
        const cle = ymd(date)
        const duJour = parJour[cle] || []
        const estAujourdhui = cle === ymd(new Date())
        const max = grand ? 12 : 3
        return (
            <div
                key={cle}
                onClick={() => setModal({ type: "creer", jour: cle })}
                onDragOver={(e) => {
                    e.preventDefault()
                    if (survol !== cle) setSurvol(cle)
                }}
                onDragLeave={() => setSurvol(null)}
                onDrop={(e) => {
                    e.preventDefault()
                    setSurvol(null)
                    const id = Number(e.dataTransfer.getData("text/plain"))
                    const s = (data?.items || []).find((x) => x.id === id)
                    if (s) deplacer(s, cle)
                }}
                style={{
                    minHeight: grand ? 300 : 62,
                    padding: 3,
                    borderRight: `1px solid ${b0}`,
                    borderBottom: `1px solid ${b0}`,
                    background:
                        survol === cle
                            ? "rgba(91,227,163,0.12)"
                            : estAujourdhui
                              ? "rgba(255,255,255,0.04)"
                              : "transparent",
                    opacity: horsMois ? 0.35 : 1,
                    cursor: "cell",
                    display: "flex",
                    flexDirection: "column",
                    gap: 2,
                    minWidth: 0,
                    overflow: "hidden",
                }}
            >
                <span
                    style={{
                        fontSize: 9,
                        fontFamily: mono,
                        color: estAujourdhui ? t0 : t1,
                        fontWeight: estAujourdhui ? 700 : 400,
                    }}
                >
                    {grand
                        ? `${JOURS_COURTS[(date.getDay() + 6) % 7]} ${date.getDate()}`
                        : date.getDate()}
                </span>
                {duJour.slice(0, max).map((s) => {
                    const c = couleurMatiere(s.matiere_key)
                    const annulee = s.statut === "annulee"
                    return (
                        <div
                            key={s.id}
                            draggable
                            onDragStart={(e) => {
                                e.dataTransfer.setData(
                                    "text/plain",
                                    String(s.id)
                                )
                                e.dataTransfer.effectAllowed = "move"
                            }}
                            onClick={(e) => {
                                e.stopPropagation()
                                setModal({ type: "seance", seance: s })
                            }}
                            title={`${s.matiere_label} · ${hm(new Date(s.date_debut!))}${annulee ? " · annulée" : ""}`}
                            style={{
                                fontSize: 8.5,
                                fontFamily: mono,
                                lineHeight: "13px",
                                padding: "0 3px",
                                borderRadius: 2,
                                borderLeft: `2px solid ${c}`,
                                background: `${c}22`,
                                color: annulee ? t1 : t0,
                                textDecoration: annulee
                                    ? "line-through"
                                    : "none",
                                whiteSpace: "nowrap" as const,
                                overflow: "hidden",
                                textOverflow: "ellipsis",
                                cursor: "grab",
                            }}
                        >
                            {hm(new Date(s.date_debut!))}{" "}
                            {grand
                                ? s.matiere_label
                                : libelleCourt(s.matiere_label)}
                        </div>
                    )
                })}
                {duJour.length > max && (
                    <span style={{ fontSize: 8, fontFamily: mono, color: t1 }}>
                        +{duJour.length - max}
                    </span>
                )}
            </div>
        )
    }

    // ── Grilles ──
    function grilleMois() {
        const debut = lundiDe(curseur)
        const jours = Array.from({ length: 42 }, (_, i) =>
            ajouterJours(debut, i)
        )
        // 5 semaines suffisent quand le mois s'y tient.
        const visibles =
            jours[35].getMonth() !== curseur.getMonth()
                ? jours.slice(0, 35)
                : jours
        return (
            <div
                style={{
                    display: "grid",
                    gridTemplateColumns: "repeat(7, minmax(0, 1fr))",
                    borderLeft: `1px solid ${b0}`,
                    borderTop: `1px solid ${b0}`,
                }}
            >
                {JOURS_COURTS.map((j) => (
                    <div
                        key={j}
                        style={{
                            fontSize: 9,
                            fontFamily: mono,
                            color: t2,
                            padding: "3px 4px",
                            borderRight: `1px solid ${b0}`,
                            borderBottom: `1px solid ${b0}`,
                            textTransform: "uppercase" as const,
                        }}
                    >
                        {j}
                    </div>
                ))}
                {visibles.map((d) =>
                    jourCellule(d, d.getMonth() !== curseur.getMonth())
                )}
            </div>
        )
    }

    function grilleSemaine() {
        const jours = Array.from({ length: 7 }, (_, i) =>
            ajouterJours(semaine, i)
        )
        return (
            <div
                style={{
                    display: "grid",
                    gridTemplateColumns: "repeat(7, minmax(0, 1fr))",
                    borderLeft: `1px solid ${b0}`,
                    borderTop: `1px solid ${b0}`,
                }}
            >
                {jours.map((d) => jourCellule(d, false, true))}
            </div>
        )
    }

    function liste() {
        const debutMois = curseur
        const finMois = new Date(
            curseur.getFullYear(),
            curseur.getMonth() + 1,
            1
        )
        const duMois = seances.filter((s) => {
            const d = new Date(s.date_debut!)
            return d >= debutMois && d < finMois
        })
        if (!duMois.length)
            return (
                <div style={VIDE}>
                    Aucune séance en {moisLibelle(ymd(curseur).slice(0, 7))}.
                </div>
            )
        return (
            <div>
                {duMois.map((s) => {
                    const annulee = s.statut === "annulee"
                    return (
                        <div
                            key={s.id}
                            onClick={() =>
                                setModal({ type: "seance", seance: s })
                            }
                            style={{
                                display: "flex",
                                alignItems: "center",
                                gap: 8,
                                padding: "7px 10px",
                                borderBottom: `1px solid rgba(255,255,255,0.04)`,
                                cursor: "pointer",
                                opacity: annulee ? 0.5 : 1,
                            }}
                        >
                            <Pastille color={couleurMatiere(s.matiere_key)} />
                            <span
                                style={{
                                    fontSize: 10,
                                    fontFamily: mono,
                                    color: t1,
                                    width: 128,
                                    flexShrink: 0,
                                }}
                            >
                                {dateLongue(s.date_debut)}
                            </span>
                            <span
                                style={{
                                    flex: 1,
                                    fontSize: 11,
                                    color: t0,
                                    textDecoration: annulee
                                        ? "line-through"
                                        : "none",
                                    overflow: "hidden",
                                    textOverflow: "ellipsis",
                                    whiteSpace: "nowrap" as const,
                                }}
                            >
                                {s.niveau} · {s.matiere_label}
                                {s.titre ? ` — ${s.titre}` : ""}
                            </span>
                            {annulee && (
                                <Tag color={BAD} border="rgba(255,107,107,0.4)">
                                    ANNULÉE
                                </Tag>
                            )}
                        </div>
                    )
                })}
            </div>
        )
    }

    function naviguer(sens: number) {
        if (vue === "semaine") setSemaine((s) => ajouterJours(s, 7 * sens))
        else
            setCurseur((c) => new Date(c.getFullYear(), c.getMonth() + sens, 1))
    }
    function aujourdhui() {
        const d = new Date()
        setCurseur(new Date(d.getFullYear(), d.getMonth(), 1))
        setSemaine(lundiDe(d))
    }
    const titrePeriode =
        vue === "semaine"
            ? `Semaine du ${semaine.getDate()} ${MOIS_FR[semaine.getMonth()]}`
            : `${MOIS_FR[curseur.getMonth()].replace(/^./, (c) => c.toUpperCase())} ${curseur.getFullYear()}`

    const matieresVisibles = (data?.matieres || []).filter(
        (m) => niveau === "tous" || m.niveau === niveau
    )

    return (
        <div
            style={{
                flex: 1,
                display: "flex",
                flexDirection: "column",
                position: "relative" as const,
                minHeight: 0,
            }}
        >
            <div
                style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 5,
                    padding: "7px 10px",
                    borderBottom: `1px solid ${b0}`,
                    flexShrink: 0,
                }}
            >
                <MiniBtn onClick={() => naviguer(-1)}>◂</MiniBtn>
                <MiniBtn onClick={aujourdhui}>Auj.</MiniBtn>
                <MiniBtn onClick={() => naviguer(1)}>▸</MiniBtn>
                <span
                    style={{
                        fontSize: 11,
                        fontFamily: mono,
                        color: t0,
                        fontWeight: 700,
                        marginLeft: 4,
                        flex: 1,
                        whiteSpace: "nowrap" as const,
                    }}
                >
                    {titrePeriode}
                </span>
                <MiniBtn actif={vue === "mois"} onClick={() => setVue("mois")}>
                    Mois
                </MiniBtn>
                <MiniBtn
                    actif={vue === "semaine"}
                    onClick={() => setVue("semaine")}
                >
                    Sem.
                </MiniBtn>
                <MiniBtn
                    actif={vue === "liste"}
                    onClick={() => setVue("liste")}
                >
                    Liste
                </MiniBtn>
                <Btn
                    onClick={() => setModal({ type: "serie" })}
                    style={{ fontSize: 10, padding: "4px 8px" }}
                >
                    + Série
                </Btn>
            </div>

            <div
                style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 8,
                    padding: "5px 10px",
                    borderBottom: `1px solid ${b0}`,
                    flexWrap: "wrap" as const,
                    flexShrink: 0,
                }}
            >
                {matieresVisibles.map((m) => (
                    <span
                        key={m.key}
                        title={`${m.inscrits} inscrit(s)${m.prix_configures ? "" : " · prix Stripe manquants"}`}
                        style={{
                            display: "flex",
                            alignItems: "center",
                            gap: 4,
                            fontSize: 9,
                            fontFamily: mono,
                            color: t1,
                        }}
                    >
                        <Pastille color={couleurMatiere(m.key)} />
                        {niveau === "tous" ? `${m.niveau} ` : ""}
                        {m.label}
                        <span style={{ color: t2 }}>({m.inscrits})</span>
                    </span>
                ))}
                <label
                    style={{
                        marginLeft: "auto",
                        display: "flex",
                        alignItems: "center",
                        gap: 4,
                        fontSize: 9,
                        fontFamily: mono,
                        color: t1,
                    }}
                >
                    <input
                        type="checkbox"
                        checked={masquerAnnulees}
                        onChange={(e) => setMasquerAnnulees(e.target.checked)}
                    />
                    masquer annulées
                </label>
            </div>

            {msg && (
                <div style={{ padding: "6px 10px", flexShrink: 0 }}>
                    <SM text={msg} />
                </div>
            )}

            <div style={{ flex: 1, overflowY: "auto", padding: "6px 8px" }}>
                {loading ? (
                    <div style={VIDE}>Chargement…</div>
                ) : vue === "mois" ? (
                    grilleMois()
                ) : vue === "semaine" ? (
                    grilleSemaine()
                ) : (
                    liste()
                )}
                <div
                    style={{
                        fontSize: 9,
                        fontFamily: mono,
                        color: t2,
                        padding: "6px 2px",
                    }}
                >
                    Clic sur un jour : nouveau cours · clic sur un cours :
                    détails · glisser un cours sur un autre jour : déplacer.
                </div>
            </div>

            {modal?.type === "seance" && (
                <SeanceModal
                    seance={modal.seance}
                    inscrits={inscrits(modal.seance.matiere_key)}
                    busy={busy}
                    onClose={() => setModal(null)}
                    onProposer={proposer}
                />
            )}
            {modal?.type === "creer" && data && (
                <CreerSeanceModal
                    jour={modal.jour}
                    niveauDefaut={niveau === "tous" ? "L1" : niveau}
                    data={data}
                    busy={busy}
                    onClose={() => setModal(null)}
                    onProposer={proposer}
                />
            )}
            {modal?.type === "serie" && data && (
                <SerieModal
                    niveauDefaut={niveau === "tous" ? "L1" : niveau}
                    data={data}
                    busy={busy}
                    onClose={() => setModal(null)}
                    onProposer={proposer}
                />
            )}

            {deplacement && !confirmation && (
                <DeplacementModal
                    seance={deplacement.seance}
                    jour={deplacement.jour}
                    busy={busy}
                    onClose={() => setDeplacement(null)}
                    onProposer={(titre, appel, toujours, danger) => {
                        proposer(titre, appel, toujours, danger)
                    }}
                />
            )}

            {confirmation && (
                <Overlay onClose={() => setConfirmation(null)}>
                    <TitreModal onClose={() => setConfirmation(null)}>
                        Confirmer : {confirmation.titre}
                    </TitreModal>
                    {(confirmation.apercu as any).concernees != null && (
                        <div
                            style={{
                                fontSize: 11,
                                fontFamily: mono,
                                color: t0,
                            }}
                        >
                            {(confirmation.apercu as any).concernees} cours
                            concerné(s)
                        </div>
                    )}
                    {confirmation.apercu.crees != null && (
                        <div
                            style={{
                                fontSize: 11,
                                fontFamily: mono,
                                color: t0,
                            }}
                        >
                            {confirmation.apercu.crees} séance(s) à créer
                            {confirmation.apercu.ignores
                                ? ` · ${confirmation.apercu.ignores} déjà présente(s), ignorée(s)`
                                : ""}
                        </div>
                    )}
                    <FL>Impact sur la facturation</FL>
                    <ImpactListe ecriture={confirmation.apercu} />
                    <div
                        style={{
                            display: "flex",
                            gap: 8,
                            justifyContent: "flex-end",
                        }}
                    >
                        <Btn
                            onClick={() => setConfirmation(null)}
                            disabled={busy}
                        >
                            Retour
                        </Btn>
                        <Btn
                            primary={!confirmation.danger}
                            danger={confirmation.danger}
                            onClick={confirmer}
                            disabled={busy}
                        >
                            {busy ? "…" : "Confirmer"}
                        </Btn>
                    </div>
                </Overlay>
            )}
        </div>
    )
}

type Proposer = (
    titre: string,
    appel: (simulation: boolean) => Promise<Ecriture>,
    toujoursConfirmer?: boolean,
    danger?: boolean
) => void

// ── Portée d'une action sur une récurrence ──
type Portee = "une" | "suivantes" | "toutes"

function PorteeChoix({
    portee,
    setPortee,
    toutNiveau,
    setToutNiveau,
    commune,
}: {
    portee: Portee
    setPortee: (p: Portee) => void
    toutNiveau: boolean
    setToutNiveau: (v: boolean) => void
    commune: boolean
}) {
    const options: { id: Portee; label: string }[] = [
        { id: "une", label: "Ce cours seulement" },
        { id: "suivantes", label: "Ce cours et les suivants" },
        { id: "toutes", label: "Toute la récurrence" },
    ]
    return (
        <div
            style={{
                border: `1px solid ${b0}`,
                borderRadius: 10,
                padding: "7px 9px",
                display: "flex",
                flexDirection: "column",
                gap: 6,
            }}
        >
            <FL>Appliquer à</FL>
            <div style={{ display: "flex", gap: 5, flexWrap: "wrap" as const }}>
                {options.map((o) => (
                    <MiniBtn
                        key={o.id}
                        actif={portee === o.id}
                        onClick={() => setPortee(o.id)}
                    >
                        {o.label}
                    </MiniBtn>
                ))}
            </div>
            {portee !== "une" && !commune && (
                <label
                    style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 5,
                        fontSize: 10,
                        fontFamily: mono,
                        color: t0,
                    }}
                >
                    <input
                        type="checkbox"
                        checked={toutNiveau}
                        onChange={(e) => setToutNiveau(e.target.checked)}
                    />
                    Toutes les matières du niveau sur ce créneau
                </label>
            )}
            {portee !== "une" && (
                <div style={{ fontSize: 9, fontFamily: mono, color: t2 }}>
                    Récurrence = les cours au même jour de la semaine et à la
                    même heure.
                </div>
            )}
        </div>
    )
}

// Appel « récurrence » de l'API (déplacer / annuler / rétablir / supprimer).
function appelRecurrence(
    seanceId: number,
    corps: any
): (simulation: boolean) => Promise<Ecriture> {
    return (simulation) =>
        apiJson(
            `/prepa/adjuris/admin/agenda/seances/${seanceId}/recurrence${simulation ? "?simulation=true" : ""}`,
            "POST",
            corps
        )
}

function libellePortee(portee: Portee, toutNiveau: boolean): string {
    const base =
        portee === "une"
            ? ""
            : portee === "suivantes"
              ? " (et les suivants"
              : " (toute la récurrence"
    if (!base) return ""
    return `${base}${toutNiveau ? ", tout le niveau" : ""})`
}

function DeplacementModal({
    seance,
    jour,
    busy,
    onClose,
    onProposer,
}: {
    seance: Seance
    jour: string
    busy: boolean
    onClose: () => void
    onProposer: Proposer
}) {
    const heure = hm(new Date(seance.date_debut!))
    const [portee, setPortee] = React.useState<Portee>("une")
    const [toutNiveau, setToutNiveau] = React.useState(false)
    const cible = naif(jour, heure)
    return (
        <Overlay onClose={onClose}>
            <TitreModal onClose={onClose}>
                <span
                    style={{
                        display: "inline-flex",
                        alignItems: "center",
                        gap: 6,
                    }}
                >
                    <Pastille color={couleurMatiere(seance.matiere_key)} />
                    Déplacer {seance.niveau} · {seance.matiere_label}
                </span>
            </TitreModal>
            <div style={{ fontSize: 11, fontFamily: mono, color: t0 }}>
                {dateLongue(seance.date_debut)} → {dateLongue(cible)}
            </div>
            <PorteeChoix
                portee={portee}
                setPortee={setPortee}
                toutNiveau={toutNiveau}
                setToutNiveau={setToutNiveau}
                commune={!seance.matiere_key}
            />
            <div
                style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}
            >
                <Btn onClick={onClose} disabled={busy}>
                    Annuler
                </Btn>
                <Btn
                    primary
                    disabled={busy}
                    onClick={() =>
                        onProposer(
                            `Cours déplacé au ${dateLongue(cible)}${libellePortee(portee, toutNiveau)}`,
                            portee === "une"
                                ? (s) =>
                                      apiJson(
                                          `/prepa/adjuris/admin/agenda/seances/${seance.id}${s ? "?simulation=true" : ""}`,
                                          "PATCH",
                                          { date_debut: cible }
                                      )
                                : appelRecurrence(seance.id, {
                                      action: "deplacer",
                                      portee,
                                      tout_le_niveau: toutNiveau,
                                      nouvelle_date: cible,
                                  }),
                            true
                        )
                    }
                >
                    {busy ? "…" : "Aperçu →"}
                </Btn>
            </div>
        </Overlay>
    )
}

function SeanceModal({
    seance,
    inscrits,
    busy,
    onClose,
    onProposer,
}: {
    seance: Seance
    inscrits: number
    busy: boolean
    onClose: () => void
    onProposer: Proposer
}) {
    const d = new Date(seance.date_debut!)
    const [jour, setJour] = React.useState(ymd(d))
    const [heure, setHeure] = React.useState(hm(d))
    const [duree, setDuree] = React.useState(String(seance.duree_minutes))
    const [titre, setTitre] = React.useState(seance.titre || "")
    const [lien, setLien] = React.useState(seance.lien || "")
    const [portee, setPortee] = React.useState<Portee>("une")
    const [toutNiveau, setToutNiveau] = React.useState(false)
    const annulee = seance.statut === "annulee"
    const base = `/prepa/adjuris/admin/agenda/seances/${seance.id}`
    const sim = (s: boolean) => (s ? "?simulation=true" : "")
    const suffixe = libellePortee(portee, toutNiveau)
    const recurrence = (action: string, extra: any = {}) =>
        appelRecurrence(seance.id, {
            action,
            portee,
            tout_le_niveau: toutNiveau,
            ...extra,
        })

    function enregistrer() {
        const deplace = ymd(d) !== jour || hm(d) !== heure
        if (portee === "une") {
            onProposer(
                "Cours modifié",
                (s) =>
                    apiJson(`${base}${sim(s)}`, "PATCH", {
                        date_debut: naif(jour, heure),
                        duree_minutes: Number(duree) || 60,
                        titre,
                        lien,
                    }),
                deplace
            )
            return
        }
        // Récurrence : on déplace (jour et heure) et on applique la durée.
        // Le titre et le lien restent propres à chaque cours.
        onProposer(
            `Cours déplacés au ${dateLongue(naif(jour, heure))}${suffixe}`,
            recurrence("deplacer", {
                nouvelle_date: naif(jour, heure),
                duree_minutes: Number(duree) || 60,
            }),
            true
        )
    }

    function annulerOuRetablir() {
        const action = annulee ? "retablir" : "annuler"
        onProposer(
            `${annulee ? "Cours rétabli" : "Cours annulé"}${suffixe}`,
            portee === "une"
                ? (s) => apiJson(`${base}/${action}${sim(s)}`, "POST")
                : recurrence(action),
            true
        )
    }

    function supprimer() {
        onProposer(
            `Séance supprimée${suffixe}`,
            portee === "une"
                ? (s) => apiJson(`${base}${sim(s)}`, "DELETE")
                : recurrence("supprimer"),
            true,
            true
        )
    }

    return (
        <Overlay onClose={onClose}>
            <TitreModal onClose={onClose}>
                <span
                    style={{
                        display: "inline-flex",
                        alignItems: "center",
                        gap: 6,
                    }}
                >
                    <Pastille color={couleurMatiere(seance.matiere_key)} />
                    {seance.niveau} · {seance.matiere_label}
                </span>
            </TitreModal>
            <div style={{ fontSize: 10, fontFamily: mono, color: t1 }}>
                {dateLongue(seance.date_debut)} ·{" "}
                {seance.matiere_key
                    ? `${inscrits} inscrit(s) facturé(s) sur cette matière`
                    : "séance commune, jamais facturée"}
                {annulee ? " · ANNULÉE" : ""}
            </div>
            <div
                style={{
                    display: "grid",
                    gridTemplateColumns: "1.3fr 1fr 0.8fr",
                    gap: 6,
                }}
            >
                <div>
                    <FL>Jour</FL>
                    <TI
                        type="date"
                        value={jour}
                        onChange={(e: any) => setJour(e.target.value)}
                        style={{ background: FIELD }}
                    />
                </div>
                <div>
                    <FL>Heure</FL>
                    <TI
                        type="time"
                        value={heure}
                        onChange={(e: any) => setHeure(e.target.value)}
                        style={{ background: FIELD }}
                    />
                </div>
                <div>
                    <FL>Durée (min)</FL>
                    <TI
                        type="number"
                        value={duree}
                        onChange={(e: any) => setDuree(e.target.value)}
                    />
                </div>
            </div>
            {portee === "une" && (
                <>
                    <TI
                        value={titre}
                        onChange={(e: any) => setTitre(e.target.value)}
                        placeholder="Titre (optionnel)"
                    />
                    <TI
                        value={lien}
                        onChange={(e: any) => setLien(e.target.value)}
                        placeholder="Lien Discord / visio"
                    />
                </>
            )}
            <PorteeChoix
                portee={portee}
                setPortee={setPortee}
                toutNiveau={toutNiveau}
                setToutNiveau={setToutNiveau}
                commune={!seance.matiere_key}
            />
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" as const }}>
                <Btn primary onClick={enregistrer} disabled={busy}>
                    {portee === "une" ? "Enregistrer" : "Déplacer →"}
                </Btn>
                <Btn onClick={annulerOuRetablir} disabled={busy}>
                    {annulee
                        ? portee === "une"
                            ? "Rétablir le cours"
                            : "Rétablir"
                        : portee === "une"
                          ? "Annuler le cours"
                          : "Annuler"}
                </Btn>
                <div style={{ flex: 1 }} />
                <Btn danger onClick={supprimer} disabled={busy}>
                    Supprimer
                </Btn>
            </div>
            <div style={{ fontSize: 9, fontFamily: mono, color: t2 }}>
                « Annuler » garde le cours barré dans l'agenda ; « Supprimer »
                est réservé aux erreurs de saisie. Dans les deux cas, la
                facturation des inscrits est recalculée.
            </div>
        </Overlay>
    )
}

function CreerSeanceModal({
    jour,
    niveauDefaut,
    data,
    busy,
    onClose,
    onProposer,
}: {
    jour: string
    niveauDefaut: Niveau
    data: AgendaData
    busy: boolean
    onClose: () => void
    onProposer: Proposer
}) {
    const [niv, setNiv] = React.useState<Niveau>(niveauDefaut)
    const creneau = data.creneaux[niv]
    const [matiere, setMatiere] = React.useState(MATIERES[niv]?.[0]?.key || "")
    const [date, setDate] = React.useState(jour)
    const [heure, setHeure] = React.useState(creneau?.heure || "21:00")
    const [duree, setDuree] = React.useState(
        String(creneau?.duree_minutes || 60)
    )
    const [titre, setTitre] = React.useState("")
    const [lien, setLien] = React.useState("")

    function changerNiveau(n: Niveau) {
        setNiv(n)
        setMatiere(MATIERES[n]?.[0]?.key || "")
        const c = data.creneaux[n]
        if (c) {
            setHeure(c.heure)
            setDuree(String(c.duree_minutes))
        }
    }

    return (
        <Overlay onClose={onClose}>
            <TitreModal onClose={onClose}>
                Nouveau cours — {dateLongue(naif(date, heure), false)}
            </TitreModal>
            <div
                style={{
                    display: "grid",
                    gridTemplateColumns: "0.6fr 1.4fr",
                    gap: 6,
                }}
            >
                <div>
                    <FL>Niveau</FL>
                    <SI
                        value={niv}
                        onChange={(e: any) => changerNiveau(e.target.value)}
                    >
                        {NIVEAUX.map((n) => (
                            <option key={n} value={n}>
                                {n}
                            </option>
                        ))}
                    </SI>
                </div>
                <div>
                    <FL>Matière</FL>
                    <SI
                        value={matiere}
                        onChange={(e: any) => setMatiere(e.target.value)}
                    >
                        {(MATIERES[niv] || []).map((m) => (
                            <option key={m.key} value={m.key}>
                                {m.label}
                            </option>
                        ))}
                        <option value="">Séance commune (non facturée)</option>
                    </SI>
                </div>
            </div>
            <div
                style={{
                    display: "grid",
                    gridTemplateColumns: "1.3fr 1fr 0.8fr",
                    gap: 6,
                }}
            >
                <div>
                    <FL>Jour</FL>
                    <TI
                        type="date"
                        value={date}
                        onChange={(e: any) => setDate(e.target.value)}
                        style={{ background: FIELD }}
                    />
                </div>
                <div>
                    <FL>Heure</FL>
                    <TI
                        type="time"
                        value={heure}
                        onChange={(e: any) => setHeure(e.target.value)}
                        style={{ background: FIELD }}
                    />
                </div>
                <div>
                    <FL>Durée</FL>
                    <TI
                        type="number"
                        value={duree}
                        onChange={(e: any) => setDuree(e.target.value)}
                    />
                </div>
            </div>
            <TI
                value={titre}
                onChange={(e: any) => setTitre(e.target.value)}
                placeholder="Titre (optionnel)"
            />
            <TI
                value={lien}
                onChange={(e: any) => setLien(e.target.value)}
                placeholder="Lien Discord / visio"
            />
            <div
                style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}
            >
                <Btn onClick={onClose} disabled={busy}>
                    Fermer
                </Btn>
                <Btn
                    primary
                    disabled={busy || !date || !heure}
                    onClick={() =>
                        onProposer("Cours programmé", (s) =>
                            apiJson(
                                `/prepa/adjuris/admin/agenda/seances${s ? "?simulation=true" : ""}`,
                                "POST",
                                {
                                    niveau: niv,
                                    matiere_key: matiere || null,
                                    date_debut: naif(date, heure),
                                    duree_minutes: Number(duree) || 60,
                                    titre,
                                    lien,
                                }
                            )
                        )
                    }
                >
                    {busy ? "…" : "Programmer →"}
                </Btn>
            </div>
        </Overlay>
    )
}

function SerieModal({
    niveauDefaut,
    data,
    busy,
    onClose,
    onProposer,
}: {
    niveauDefaut: Niveau
    data: AgendaData
    busy: boolean
    onClose: () => void
    onProposer: Proposer
}) {
    const finProgramme = (() => {
        const [a, m] = (data.facturation?.fin_programme || "2026-12")
            .split("-")
            .map(Number)
        return ymd(new Date(a, m, 0))
    })()
    const [niv, setNiv] = React.useState<Niveau>(niveauDefaut)
    const c0 = data.creneaux[niveauDefaut]
    const [matieres, setMatieres] = React.useState<string[]>(
        (MATIERES[niveauDefaut] || []).map((m) => m.key)
    )
    const [jourSemaine, setJourSemaine] = React.useState(c0?.jour_semaine ?? 0)
    const [heure, setHeure] = React.useState(c0?.heure || "21:00")
    const [duree, setDuree] = React.useState(String(c0?.duree_minutes || 60))
    const [du, setDu] = React.useState(ymd(new Date()))
    const [au, setAu] = React.useState(finProgramme)
    const [sauf, setSauf] = React.useState<string[]>([])
    const [exclusion, setExclusion] = React.useState("")
    const [lien, setLien] = React.useState("")
    const [titre, setTitre] = React.useState("")

    function changerNiveau(n: Niveau) {
        setNiv(n)
        setMatieres((MATIERES[n] || []).map((m) => m.key))
        const c = data.creneaux[n]
        if (c) {
            setJourSemaine(c.jour_semaine)
            setHeure(c.heure)
            setDuree(String(c.duree_minutes))
        }
    }
    function basculer(key: string) {
        setMatieres((l) =>
            l.includes(key) ? l.filter((k) => k !== key) : [...l, key]
        )
    }

    return (
        <Overlay onClose={onClose}>
            <TitreModal onClose={onClose}>
                Série de cours hebdomadaires
            </TitreModal>
            <div
                style={{
                    display: "grid",
                    gridTemplateColumns: "0.6fr 1fr 0.8fr 0.7fr",
                    gap: 6,
                }}
            >
                <div>
                    <FL>Niveau</FL>
                    <SI
                        value={niv}
                        onChange={(e: any) => changerNiveau(e.target.value)}
                    >
                        {NIVEAUX.map((n) => (
                            <option key={n} value={n}>
                                {n}
                            </option>
                        ))}
                    </SI>
                </div>
                <div>
                    <FL>Jour</FL>
                    <SI
                        value={jourSemaine}
                        onChange={(e: any) =>
                            setJourSemaine(Number(e.target.value))
                        }
                    >
                        {JOURS_LONGS.map((j, i) => (
                            <option key={j} value={i}>
                                {j}
                            </option>
                        ))}
                    </SI>
                </div>
                <div>
                    <FL>Heure</FL>
                    <TI
                        type="time"
                        value={heure}
                        onChange={(e: any) => setHeure(e.target.value)}
                        style={{ background: FIELD }}
                    />
                </div>
                <div>
                    <FL>Durée</FL>
                    <TI
                        type="number"
                        value={duree}
                        onChange={(e: any) => setDuree(e.target.value)}
                    />
                </div>
            </div>
            <div>
                <FL>Matières</FL>
                <div
                    style={{
                        display: "flex",
                        gap: 10,
                        flexWrap: "wrap" as const,
                    }}
                >
                    {(MATIERES[niv] || []).map((m) => (
                        <label
                            key={m.key}
                            style={{
                                display: "flex",
                                alignItems: "center",
                                gap: 4,
                                fontSize: 10,
                                fontFamily: mono,
                                color: t0,
                            }}
                        >
                            <input
                                type="checkbox"
                                checked={matieres.includes(m.key)}
                                onChange={() => basculer(m.key)}
                            />
                            <Pastille color={couleurMatiere(m.key)} />
                            {m.label}
                        </label>
                    ))}
                </div>
            </div>
            <div
                style={{
                    display: "grid",
                    gridTemplateColumns: "1fr 1fr",
                    gap: 6,
                }}
            >
                <div>
                    <FL>Du</FL>
                    <TI
                        type="date"
                        value={du}
                        onChange={(e: any) => setDu(e.target.value)}
                        style={{ background: FIELD }}
                    />
                </div>
                <div>
                    <FL>Au (inclus)</FL>
                    <TI
                        type="date"
                        value={au}
                        onChange={(e: any) => setAu(e.target.value)}
                        style={{ background: FIELD }}
                    />
                </div>
            </div>
            <div>
                <FL>Semaines sans cours (vacances…)</FL>
                <div style={{ display: "flex", gap: 6 }}>
                    <TI
                        type="date"
                        value={exclusion}
                        onChange={(e: any) => setExclusion(e.target.value)}
                        style={{ background: FIELD }}
                    />
                    <Btn
                        onClick={() => {
                            if (exclusion && !sauf.includes(exclusion))
                                setSauf((l) => [...l, exclusion].sort())
                            setExclusion("")
                        }}
                        disabled={!exclusion}
                        style={{ padding: "4px 10px" }}
                    >
                        + Exclure
                    </Btn>
                </div>
                {sauf.length > 0 && (
                    <div
                        style={{
                            display: "flex",
                            gap: 4,
                            flexWrap: "wrap" as const,
                            marginTop: 6,
                        }}
                    >
                        {sauf.map((d) => (
                            <span
                                key={d}
                                onClick={() =>
                                    setSauf((l) => l.filter((x) => x !== d))
                                }
                                title="Retirer"
                                style={{
                                    fontSize: 9,
                                    fontFamily: mono,
                                    color: t0,
                                    padding: "2px 6px",
                                    border: `1px solid ${b1}`,
                                    borderRadius: 99,
                                    cursor: "pointer",
                                }}
                            >
                                {dateLongue(naif(d, "12:00"), false)} ✕
                            </span>
                        ))}
                    </div>
                )}
            </div>
            <TI
                value={lien}
                onChange={(e: any) => setLien(e.target.value)}
                placeholder="Lien Discord / visio (optionnel)"
            />
            <TI
                value={titre}
                onChange={(e: any) => setTitre(e.target.value)}
                placeholder="Titre (optionnel)"
            />
            <div style={{ fontSize: 9, fontFamily: mono, color: t2 }}>
                Une séance déjà présente (même matière, même heure) est ignorée
                : la série peut être relancée sans créer de doublon.
            </div>
            <div
                style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}
            >
                <Btn onClick={onClose} disabled={busy}>
                    Fermer
                </Btn>
                <Btn
                    primary
                    disabled={busy || !matieres.length || !du || !au}
                    onClick={() =>
                        onProposer(
                            `Série ${niv} du ${dateCourte(naif(du, "12:00"))} au ${dateCourte(naif(au, "12:00"))}`,
                            (s) =>
                                apiJson(
                                    `/prepa/adjuris/admin/agenda/serie${s ? "?simulation=true" : ""}`,
                                    "POST",
                                    {
                                        niveau: niv,
                                        matieres,
                                        jour_semaine: jourSemaine,
                                        heure,
                                        duree_minutes: Number(duree) || 60,
                                        du,
                                        au,
                                        sauf,
                                        lien,
                                        titre,
                                    }
                                ),
                            true
                        )
                    }
                >
                    {busy ? "…" : "Aperçu →"}
                </Btn>
            </div>
        </Overlay>
    )
}

// ── Facturation ──

function EcheancierTag({ f }: { f: Facturation }) {
    if (f.historique)
        return (
            <Tag color={t1} border={b0}>
                HISTORIQUE
            </Tag>
        )
    if (f.echeancier_statut === "erreur")
        return (
            <Tag color={BAD} border="rgba(255,107,107,0.4)">
                STRIPE ✕
            </Tag>
        )
    if (f.echeancier_statut === "a_resynchroniser")
        return (
            <Tag color="#FFD166" border="rgba(255,209,102,0.35)">
                À SYNCHRO
            </Tag>
        )
    return null
}

function FacturationRow({
    f,
    onChanged,
}: {
    f: Facturation
    onChanged: () => void
}) {
    const [open, setOpen] = React.useState(false)
    const [busy, setBusy] = React.useState(false)
    const [msg, setMsg] = React.useState("")
    const p = f.prochain_prelevement

    async function resynchroniser() {
        setBusy(true)
        setMsg("")
        try {
            const r = await apiJson(
                `/prepa/adjuris/admin/facturation/${f.enrollment_id}/resynchroniser`,
                "POST"
            )
            setMsg(
                r.ok
                    ? "✓ Échéancier Stripe à jour"
                    : "ERR: Stripe n'a pas pu être mis à jour (voir logs)"
            )
            onChanged()
        } catch (e: any) {
            setMsg(`ERR: ${e.message}`)
        } finally {
            setBusy(false)
        }
    }

    return (
        <div style={{ borderBottom: `1px solid rgba(255,255,255,0.04)` }}>
            <div
                onClick={() => setOpen((v) => !v)}
                style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 8,
                    padding: "8px 14px",
                    cursor: "pointer",
                    background: open ? sA : "transparent",
                    borderLeft: `2px solid ${open ? b2 : "transparent"}`,
                    minWidth: 0,
                }}
            >
                <Pastille color={couleurMatiere(f.matiere_key)} />
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
                        {f.email || "—"}
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
                        {f.matiere_label}
                    </div>
                </div>
                <EcheancierTag f={f} />
                <StatusTag status={f.status} />
                <span
                    style={{
                        fontSize: 10,
                        fontFamily: mono,
                        color: t0,
                        flexShrink: 0,
                        textAlign: "right" as const,
                        width: 92,
                    }}
                >
                    {p
                        ? `${euros(p.montant_cents)} · ${dateCourte(p.date)}`
                        : f.historique
                          ? "—"
                          : "terminé"}
                </span>
            </div>
            {open && (
                <div
                    style={{
                        padding: "8px 14px 12px",
                        background: "rgba(255,255,255,0.015)",
                        display: "flex",
                        flexDirection: "column",
                        gap: 8,
                    }}
                >
                    <div
                        style={{
                            display: "grid",
                            gridTemplateColumns: "1fr 1fr",
                            gap: 6,
                        }}
                    >
                        {[
                            {
                                l: "Inscrit le",
                                v: f.inscrit_le
                                    ? dateLongue(f.inscrit_le)
                                    : "—",
                            },
                            {
                                l: "Payée d'avance (20 €)",
                                v: f.seance_prepayee
                                    ? dateLongue(f.seance_prepayee)
                                    : "—",
                            },
                        ].map(({ l, v }) => (
                            <div
                                key={l}
                                style={{
                                    padding: "5px 7px",
                                    border: `1px solid ${b0}`,
                                    borderRadius: 10,
                                }}
                            >
                                <div
                                    style={{
                                        fontSize: 8,
                                        fontFamily: mono,
                                        color: t2,
                                        textTransform: "uppercase" as const,
                                        letterSpacing: 0.8,
                                    }}
                                >
                                    {l}
                                </div>
                                <div
                                    style={{
                                        fontSize: 10,
                                        fontFamily: mono,
                                        color: t0,
                                    }}
                                >
                                    {v}
                                </div>
                            </div>
                        ))}
                    </div>
                    {f.historique ? (
                        <div
                            style={{
                                fontSize: 10,
                                fontFamily: mono,
                                color: t1,
                            }}
                        >
                            Inscription antérieure à la facturation par séance :
                            son échéancier se gère dans le dashboard Stripe.
                        </div>
                    ) : (
                        <div
                            style={{
                                border: `1px solid ${b0}`,
                                borderRadius: 10,
                                overflow: "hidden",
                            }}
                        >
                            {f.mois.map((m, i) => (
                                <div
                                    key={m.mois}
                                    style={{
                                        display: "grid",
                                        gridTemplateColumns:
                                            "1.3fr 0.9fr 0.8fr 0.8fr",
                                        gap: 6,
                                        padding: "5px 9px",
                                        fontSize: 10,
                                        fontFamily: mono,
                                        color: m.preleve ? t1 : t0,
                                        borderBottom:
                                            i < f.mois.length - 1
                                                ? `1px solid rgba(255,255,255,0.04)`
                                                : "none",
                                    }}
                                >
                                    <span>{dateCourte(m.date)}</span>
                                    <span>{m.seances} séance(s)</span>
                                    <span>{euros(m.montant_cents)}</span>
                                    <span
                                        style={{
                                            color: m.credits
                                                ? "#FFD166"
                                                : m.preleve
                                                  ? OK
                                                  : t1,
                                        }}
                                    >
                                        {m.credits
                                            ? `−${euros(m.credits * PRIX_SEANCE_CENTS)}`
                                            : m.preleve
                                              ? "prélevé"
                                              : "à venir"}
                                    </span>
                                </div>
                            ))}
                        </div>
                    )}
                    {f.impaye_depuis && (
                        <div
                            style={{
                                fontSize: 10,
                                fontFamily: mono,
                                color: BAD,
                            }}
                        >
                            Impayé depuis le{" "}
                            {dateLongue(f.impaye_depuis, false)}
                            {f.status === "suspendu"
                                ? " — accès suspendu."
                                : " — accès conservé 7 jours."}
                        </div>
                    )}
                    {!f.historique && (
                        <div
                            style={{
                                display: "flex",
                                gap: 6,
                                alignItems: "center",
                            }}
                        >
                            <Btn
                                onClick={resynchroniser}
                                disabled={busy}
                                style={{ fontSize: 10, padding: "4px 10px" }}
                            >
                                {busy ? "…" : "Resynchroniser Stripe"}
                            </Btn>
                            <span
                                style={{
                                    fontSize: 9,
                                    fontFamily: mono,
                                    color: t2,
                                }}
                            >
                                #{f.enrollment_id}
                            </span>
                        </div>
                    )}
                    <SM text={msg} />
                </div>
            )}
        </div>
    )
}

function LienPaiementForm({ niveauDefaut }: { niveauDefaut: Niveau }) {
    const [email, setEmail] = React.useState("")
    const [niv, setNiv] = React.useState<Niveau>(niveauDefaut)
    const [matieres, setMatieres] = React.useState<string[]>([])
    // Cours passés des matières cochées, et ceux que l'élève a suivis
    const [passes, setPasses] = React.useState<Seance[]>([])
    const [suivis, setSuivis] = React.useState<number[]>([])
    const [chargement, setChargement] = React.useState(false)
    const [dejaPaye, setDejaPaye] = React.useState(false)
    const [inscritLe, setInscritLe] = React.useState("")
    const [busy, setBusy] = React.useState(false)
    const [msg, setMsg] = React.useState("")
    const [resultat, setResultat] = React.useState<any>(null)

    // Les 120 derniers jours de cours des matières cochées
    React.useEffect(() => {
        setSuivis([])
        setResultat(null)
        if (!matieres.length) {
            setPasses([])
            return
        }
        const fin = new Date()
        const debut = new Date(fin.getTime() - 120 * 86400000)
        setChargement(true)
        apiFetch(
            `/prepa/adjuris/admin/agenda?niveau=${niv}&debut=${encodeURIComponent(
                debut.toISOString()
            )}&fin=${encodeURIComponent(fin.toISOString())}`
        )
            .then((r) =>
                setPasses(
                    ((r?.items || []) as Seance[]).filter(
                        (s) =>
                            s.matiere_key &&
                            matieres.includes(s.matiere_key) &&
                            s.statut === "prevue" &&
                            s.date_debut &&
                            new Date(s.date_debut) < fin
                    )
                )
            )
            .catch((e) => setMsg(`ERR: ${e.message}`))
            .finally(() => setChargement(false))
    }, [niv, matieres.join(",")])

    // Un même créneau regroupe les matières qui ont cours en même temps
    const creneaux: { date: string; seances: Seance[] }[] = []
    for (const s of passes) {
        const g = creneaux.find((c) => c.date === s.date_debut)
        if (g) g.seances.push(s)
        else creneaux.push({ date: s.date_debut as string, seances: [s] })
    }
    creneaux.sort((a, b) => b.date.localeCompare(a.date))

    function basculer(ids: number[], coche: boolean) {
        setResultat(null)
        setSuivis((l) =>
            coche
                ? Array.from(new Set([...l, ...ids]))
                : l.filter((x) => !ids.includes(x))
        )
    }

    async function envoyer(apercu: boolean) {
        setBusy(true)
        setMsg("")
        setResultat(null)
        try {
            const r = await apiJson(
                `/prepa/adjuris/admin/lien-paiement${apercu ? "?apercu=true" : ""}`,
                "POST",
                {
                    email: email.trim(),
                    matieres,
                    seances_suivies: suivis,
                    inscrit_le:
                        !suivis.length && inscritLe ? `${inscritLe}:00` : null,
                    inscription_deja_payee: dejaPaye,
                }
            )
            setResultat(r)
        } catch (e: any) {
            setMsg(`ERR: ${e.message}`)
        } finally {
            setBusy(false)
        }
    }

    const nbMatieres = matieres.length
    const libelle = (k: string | null) =>
        libelleCourt(
            (MATIERES[niv] || []).find((m) => m.key === k)?.label || ""
        )

    return (
        <div
            style={{
                padding: "10px 14px",
                display: "flex",
                flexDirection: "column",
                gap: 8,
                borderBottom: `1px solid ${b0}`,
            }}
        >
            <div
                style={{
                    fontSize: 10,
                    fontFamily: mono,
                    color: t1,
                    lineHeight: 1.5,
                }}
            >
                Élève qui s'inscrit en cours de route : coche les cours qu'il a
                déjà suivis. Il les règle au paiement (20 € chacun), à la place
                des frais d'inscription, sans les doubler ; tous les cours à
                venir sont prélevés en fin de mois. Sans cours coché :
                inscription normale (20 € pour la prochaine séance).
            </div>
            <div
                style={{
                    display: "grid",
                    gridTemplateColumns: "1.5fr 0.5fr",
                    gap: 6,
                }}
            >
                <TI
                    value={email}
                    onChange={(e: any) => {
                        setEmail(e.target.value)
                        setResultat(null)
                    }}
                    placeholder="email de l'élève"
                />
                <SI
                    value={niv}
                    onChange={(e: any) => {
                        setNiv(e.target.value)
                        setMatieres([])
                    }}
                >
                    {NIVEAUX.map((n) => (
                        <option key={n} value={n}>
                            {n}
                        </option>
                    ))}
                </SI>
            </div>
            <div
                style={{ display: "flex", gap: 10, flexWrap: "wrap" as const }}
            >
                {(MATIERES[niv] || []).map((m) => (
                    <label
                        key={m.key}
                        style={{
                            display: "flex",
                            alignItems: "center",
                            gap: 4,
                            fontSize: 10,
                            fontFamily: mono,
                            color: t0,
                        }}
                    >
                        <input
                            type="checkbox"
                            checked={matieres.includes(m.key)}
                            onChange={() =>
                                setMatieres((l) =>
                                    l.includes(m.key)
                                        ? l.filter((k) => k !== m.key)
                                        : [...l, m.key]
                                )
                            }
                        />
                        {m.label}
                    </label>
                ))}
            </div>

            {nbMatieres > 0 && (
                <div>
                    <FL>
                        Cours déjà suivis
                        {suivis.length ? ` (${suivis.length})` : ""}
                    </FL>
                    <div
                        style={{
                            maxHeight: 150,
                            overflowY: "auto",
                            border: `1px solid ${b0}`,
                            padding: "4px 8px",
                            display: "flex",
                            flexDirection: "column",
                            gap: 3,
                        }}
                    >
                        {chargement && <SM text="…" />}
                        {!chargement && !creneaux.length && (
                            <div
                                style={{
                                    fontSize: 10,
                                    fontFamily: mono,
                                    color: t2,
                                }}
                            >
                                Aucun cours passé dans l'agenda pour ces
                                matières.
                            </div>
                        )}
                        {creneaux.map((c) => {
                            const ids = c.seances.map((s) => s.id)
                            const tous = ids.every((x) => suivis.includes(x))
                            return (
                                <div
                                    key={c.date}
                                    style={{
                                        display: "flex",
                                        alignItems: "center",
                                        gap: 8,
                                        flexWrap: "wrap" as const,
                                        fontSize: 10,
                                        fontFamily: mono,
                                        color: t0,
                                    }}
                                >
                                    <label
                                        style={{
                                            display: "flex",
                                            alignItems: "center",
                                            gap: 4,
                                            minWidth: 170,
                                        }}
                                    >
                                        <input
                                            type="checkbox"
                                            checked={tous}
                                            onChange={(e) =>
                                                basculer(ids, e.target.checked)
                                            }
                                        />
                                        {dateLongue(c.date)}
                                    </label>
                                    {nbMatieres > 1 &&
                                        c.seances.map((s) => (
                                            <label
                                                key={s.id}
                                                style={{
                                                    display: "flex",
                                                    alignItems: "center",
                                                    gap: 3,
                                                    color: t1,
                                                }}
                                            >
                                                <input
                                                    type="checkbox"
                                                    checked={suivis.includes(
                                                        s.id
                                                    )}
                                                    onChange={(e) =>
                                                        basculer(
                                                            [s.id],
                                                            e.target.checked
                                                        )
                                                    }
                                                />
                                                {libelle(s.matiere_key)}
                                            </label>
                                        ))}
                                </div>
                            )
                        })}
                    </div>
                </div>
            )}

            <div
                style={{
                    display: "grid",
                    gridTemplateColumns: suivis.length ? "1fr" : "1fr 1fr",
                    gap: 6,
                    alignItems: "end",
                }}
            >
                {!suivis.length && (
                    <div>
                        <FL>Inscrit le (vide = maintenant)</FL>
                        <TI
                            type="datetime-local"
                            value={inscritLe}
                            onChange={(e: any) => {
                                setInscritLe(e.target.value)
                                setResultat(null)
                            }}
                        />
                    </div>
                )}
                <label
                    style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 5,
                        fontSize: 10,
                        fontFamily: mono,
                        color: t0,
                        height: 32,
                    }}
                >
                    <input
                        type="checkbox"
                        checked={dejaPaye}
                        onChange={(e) => {
                            setDejaPaye(e.target.checked)
                            setResultat(null)
                        }}
                    />
                    {suivis.length
                        ? "Ces cours sont déjà réglés (rien à encaisser)"
                        : "20 € d'inscription déjà réglés"}
                </label>
            </div>
            <div style={{ display: "flex", gap: 6 }}>
                <Btn
                    onClick={() => envoyer(true)}
                    disabled={busy || !email.trim() || !matieres.length}
                >
                    {busy ? "…" : "Aperçu"}
                </Btn>
                <Btn
                    primary
                    onClick={() => envoyer(false)}
                    disabled={busy || !email.trim() || !matieres.length}
                >
                    {busy ? "…" : "Générer le lien de paiement"}
                </Btn>
            </div>
            <SM text={msg} />
            {resultat && (
                <div
                    style={{ display: "flex", flexDirection: "column", gap: 6 }}
                >
                    <div
                        style={{
                            fontSize: 11,
                            fontFamily: mono,
                            color: t0,
                        }}
                    >
                        Encaissé au paiement :{" "}
                        {euros(resultat.inscription?.total_cents || 0)}
                        {" · "}puis{" "}
                        {(resultat.prelevements || [])
                            .filter((p: any) => p.montant_cents > 0)
                            .map(
                                (p: any) =>
                                    `${euros(p.montant_cents)} le ${dateCourte(p.date)}`
                            )
                            .join(", ") || "aucun prélèvement"}
                    </div>
                    {resultat.checkout_url && (
                        <div style={{ display: "flex", gap: 6 }}>
                            <TI
                                value={resultat.checkout_url}
                                onChange={() => {}}
                                style={{ fontSize: 10 }}
                            />
                            <Btn
                                onClick={() =>
                                    navigator.clipboard.writeText(
                                        resultat.checkout_url
                                    )
                                }
                                style={{ padding: "4px 10px" }}
                            >
                                Copier
                            </Btn>
                        </div>
                    )}
                    <div
                        style={{
                            fontSize: 10,
                            fontFamily: mono,
                            color: t1,
                            lineHeight: 1.5,
                            whiteSpace: "pre-wrap" as const,
                        }}
                    >
                        {resultat.texte}
                        {resultat.expire_le
                            ? `\n(lien valable jusqu'au ${dateLongue(resultat.expire_le)})`
                            : ""}
                    </div>
                </div>
            )}
        </div>
    )
}

export function FacturationPanel({
    niveau,
    search,
}: {
    niveau: "tous" | Niveau
    search: string
}) {
    const [items, setItems] = React.useState<Facturation[]>([])
    const [aVenir, setAVenir] = React.useState<any[]>([])
    const [alertes, setAlertes] = React.useState<Alerte[]>([])
    const [loading, setLoading] = React.useState(true)
    const [err, setErr] = React.useState("")
    const [lienOuvert, setLienOuvert] = React.useState(false)

    function load() {
        setLoading(true)
        setErr("")
        const qs = niveau === "tous" ? "" : `?niveau=${niveau}`
        Promise.all([
            apiFetch(`/prepa/adjuris/admin/facturation${qs}`),
            apiFetch("/prepa/adjuris/admin/alertes").catch(() => ({
                items: [],
            })),
        ])
            .then(([f, a]) => {
                setItems(f?.items || [])
                setAVenir(f?.a_venir || [])
                setAlertes(a?.items || [])
            })
            .catch((e) => setErr(e.message))
            .finally(() => setLoading(false))
    }
    React.useEffect(() => {
        load()
    }, [niveau])

    const q = search.toLowerCase()
    const filtres = items.filter(
        (f) =>
            !q ||
            [f.email || "", f.matiere_label].some((v) =>
                v.toLowerCase().includes(q)
            )
    )
    const alertesVisibles = alertes.filter(
        (a) =>
            niveau === "tous" ||
            !a.matiere_key ||
            a.matiere_key.startsWith(niveau)
    )
    const couleurAlerte = (n: string) =>
        n === "erreur" ? BAD : n === "alerte" ? "#FFD166" : t1

    return (
        <div style={{ flex: 1, overflowY: "auto" }}>
            {err && (
                <div style={{ padding: "8px 14px" }}>
                    <SM text={`ERR: ${err}`} />
                </div>
            )}

            {alertesVisibles.length > 0 && (
                <div
                    style={{
                        padding: "8px 14px",
                        borderBottom: `1px solid ${b0}`,
                        display: "flex",
                        flexDirection: "column",
                        gap: 4,
                    }}
                >
                    <FL>Alertes ({alertesVisibles.length})</FL>
                    {alertesVisibles.map((a, i) => (
                        <div
                            key={i}
                            style={{
                                fontSize: 10,
                                fontFamily: mono,
                                color: t0,
                                padding: "4px 8px",
                                borderLeft: `2px solid ${couleurAlerte(a.niveau)}`,
                                background: "rgba(255,255,255,0.02)",
                            }}
                        >
                            {a.message}
                        </div>
                    ))}
                </div>
            )}

            <div
                style={{ padding: "8px 14px", borderBottom: `1px solid ${b0}` }}
            >
                <FL>
                    Prélèvements à venir{" "}
                    {niveau === "tous" ? "" : `(${niveau})`}
                </FL>
                {aVenir.length === 0 ? (
                    <div style={{ fontSize: 10, fontFamily: mono, color: t2 }}>
                        Aucun.
                    </div>
                ) : (
                    <div
                        style={{
                            display: "flex",
                            gap: 6,
                            flexWrap: "wrap" as const,
                        }}
                    >
                        {aVenir.map((m) => (
                            <div
                                key={m.mois}
                                style={{
                                    padding: "5px 9px",
                                    border: `1px solid ${b0}`,
                                    borderRadius: 10,
                                }}
                            >
                                <div
                                    style={{
                                        fontSize: 8,
                                        fontFamily: mono,
                                        color: t2,
                                        textTransform: "uppercase" as const,
                                    }}
                                >
                                    {dateCourte(m.date)}
                                </div>
                                <div
                                    style={{
                                        fontSize: 12,
                                        fontFamily: mono,
                                        color: t0,
                                        fontWeight: 700,
                                    }}
                                >
                                    {euros(m.montant_cents)}
                                </div>
                                <div
                                    style={{
                                        fontSize: 8,
                                        fontFamily: mono,
                                        color: t1,
                                    }}
                                >
                                    {m.seances} séance(s)
                                </div>
                            </div>
                        ))}
                    </div>
                )}
            </div>

            <div
                onClick={() => setLienOuvert((v) => !v)}
                style={{
                    padding: "7px 14px",
                    borderBottom: `1px solid ${b0}`,
                    cursor: "pointer",
                    fontSize: 10,
                    fontFamily: mono,
                    color: t1,
                }}
            >
                {lienOuvert ? "▾" : "▸"} Lien de paiement manuel
            </div>
            {lienOuvert && (
                <LienPaiementForm
                    niveauDefaut={niveau === "tous" ? "M1" : niveau}
                />
            )}

            {loading ? (
                <div style={VIDE}>Chargement…</div>
            ) : filtres.length === 0 ? (
                <div style={VIDE}>Aucune inscription en cours.</div>
            ) : (
                filtres.map((f) => (
                    <FacturationRow
                        key={f.enrollment_id}
                        f={f}
                        onChanged={load}
                    />
                ))
            )}
        </div>
    )
}

function AdjurisPromoPanel() {
    const [promos, setPromos] = React.useState<any[]>([])
    const [loading, setLoading] = React.useState(true)
    const [showForm, setShowForm] = React.useState(false)
    const [form, setForm] = React.useState({
        code: "",
        prix: "",
        maxUses: "",
        expiresAt: "",
    })
    const [saving, setSaving] = React.useState(false)
    const [msg, setMsg] = React.useState("")

    function load() {
        setLoading(true)
        apiFetch("/prepa/adjuris/admin/promo")
            .then((d) => setPromos(d?.items || []))
            .catch(() => {})
            .finally(() => setLoading(false))
    }
    React.useEffect(() => {
        load()
    }, [])

    async function createPromo() {
        if (!form.code || !form.prix) return
        setSaving(true)
        setMsg("")
        try {
            await apiFetch("/prepa/adjuris/admin/promo", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    code: form.code.toUpperCase(),
                    prix_euros: Number(form.prix),
                    max_uses: form.maxUses ? Number(form.maxUses) : null,
                    expires_at: form.expiresAt
                        ? new Date(form.expiresAt).toISOString()
                        : null,
                }),
            })
            setMsg("Code créé.")
            setShowForm(false)
            setForm({ code: "", prix: "", maxUses: "", expiresAt: "" })
            load()
        } catch (e: any) {
            setMsg(`Erreur: ${e.message}`)
        } finally {
            setSaving(false)
        }
    }
    async function togglePromo(p: any) {
        try {
            await apiFetch(`/prepa/adjuris/admin/promo/${p.id}`, {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ is_active: !p.is_active }),
            })
            setPromos((prev) =>
                prev.map((x) =>
                    x.id === p.id ? { ...x, is_active: !x.is_active } : x
                )
            )
        } catch {}
    }
    async function deletePromo(p: any) {
        if (!confirm(`Supprimer "${p.code}" ?`)) return
        try {
            await apiFetch(`/prepa/adjuris/admin/promo/${p.id}`, {
                method: "DELETE",
            })
            setPromos((prev) => prev.filter((x) => x.id !== p.id))
        } catch {}
    }

    return (
        <div style={{ flex: 1, overflowY: "auto" }}>
            <div
                style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    padding: "10px 14px",
                    borderBottom: `1px solid ${b0}`,
                }}
            >
                <span style={{ fontSize: 11, fontFamily: mono, color: t1 }}>
                    {promos.length} code(s) — remplace le prix d'inscription
                    (one_time) sur toutes les matières
                </span>
                <Btn
                    onClick={() => setShowForm((v) => !v)}
                    style={{ fontSize: 10, padding: "4px 10px" }}
                >
                    {showForm ? "✕ Annuler" : "+ Nouveau"}
                </Btn>
            </div>
            {showForm && (
                <div
                    style={{
                        padding: "14px",
                        borderBottom: `1px solid ${b0}`,
                        display: "flex",
                        flexDirection: "column",
                        gap: 10,
                    }}
                >
                    <div
                        style={{
                            display: "grid",
                            gridTemplateColumns: "1fr 1fr",
                            gap: 8,
                        }}
                    >
                        <div>
                            <FL>Code</FL>
                            <TI
                                value={form.code}
                                onChange={(e: any) =>
                                    setForm((f) => ({
                                        ...f,
                                        code: e.target.value.toUpperCase(),
                                    }))
                                }
                                placeholder="ADJURIS5"
                            />
                        </div>
                        <div>
                            <FL>Prix d'inscription (€)</FL>
                            <TI
                                type="number"
                                value={form.prix}
                                onChange={(e: any) =>
                                    setForm((f) => ({
                                        ...f,
                                        prix: e.target.value,
                                    }))
                                }
                                placeholder="5"
                            />
                        </div>
                        <div>
                            <FL>Utilisations max</FL>
                            <TI
                                type="number"
                                value={form.maxUses}
                                onChange={(e: any) =>
                                    setForm((f) => ({
                                        ...f,
                                        maxUses: e.target.value,
                                    }))
                                }
                                placeholder="Illimité"
                            />
                        </div>
                        <div>
                            <FL>Expire le</FL>
                            <input
                                type="date"
                                value={form.expiresAt}
                                onChange={(e) =>
                                    setForm((f) => ({
                                        ...f,
                                        expiresAt: e.target.value,
                                    }))
                                }
                                style={{
                                    width: "100%",
                                    height: 32,
                                    padding: "0 10px",
                                    background: FIELD,
                                    border: `1px solid ${b0}`,
                                    borderRadius: 10,
                                    color: t0,
                                    fontSize: 12,
                                    fontFamily: mono,
                                    outline: "none",
                                    boxSizing: "border-box" as const,
                                }}
                            />
                        </div>
                    </div>
                    <SM text={msg} />
                    <Btn
                        onClick={createPromo}
                        disabled={saving || !form.code || !form.prix}
                    >
                        {saving ? "Création…" : "Créer →"}
                    </Btn>
                </div>
            )}
            {loading ? (
                <div
                    style={{
                        padding: "32px 14px",
                        textAlign: "center",
                        fontFamily: mono,
                        fontSize: 12,
                        color: t2,
                    }}
                >
                    Chargement…
                </div>
            ) : promos.length === 0 ? (
                <div
                    style={{
                        padding: "32px 14px",
                        textAlign: "center",
                        fontFamily: mono,
                        fontSize: 12,
                        color: t2,
                    }}
                >
                    Aucun code.
                </div>
            ) : (
                promos.map((p) => (
                    <div
                        key={p.id}
                        style={{
                            display: "flex",
                            alignItems: "center",
                            gap: 10,
                            padding: "10px 14px",
                            borderBottom: `1px solid rgba(255,255,255,0.04)`,
                            opacity: p.is_active ? 1 : 0.4,
                            minWidth: 0,
                        }}
                    >
                        <div style={{ flex: 1, minWidth: 0 }}>
                            <div
                                style={{
                                    display: "flex",
                                    alignItems: "center",
                                    gap: 8,
                                    marginBottom: 3,
                                    flexWrap: "wrap" as const,
                                }}
                            >
                                <span
                                    style={{
                                        fontSize: 13,
                                        fontWeight: 700,
                                        fontFamily: mono,
                                        color: t0,
                                    }}
                                >
                                    {p.code}
                                </span>
                                <span
                                    style={{
                                        fontSize: 10,
                                        fontFamily: mono,
                                        color: t1,
                                        padding: "2px 6px",
                                        border: `1px solid ${b0}`,
                                        borderRadius: 99,
                                    }}
                                >
                                    {p.prix_euros} €
                                </span>
                            </div>
                            <div
                                style={{
                                    fontSize: 10,
                                    fontFamily: mono,
                                    color: t2,
                                }}
                            >
                                {p.uses_count}
                                {p.max_uses ? `/${p.max_uses}` : ""} util.
                                {p.expires_at
                                    ? ` · exp. ${p.expires_at.slice(0, 10)}`
                                    : ""}
                            </div>
                        </div>
                        <div style={{ display: "flex", gap: 6, flexShrink: 0 }}>
                            <Btn
                                onClick={() => togglePromo(p)}
                                style={{ fontSize: 10, padding: "4px 10px" }}
                            >
                                {p.is_active ? "Actif" : "Inactif"}
                            </Btn>
                            <Btn
                                danger
                                onClick={() => deletePromo(p)}
                                style={{ fontSize: 10, padding: "4px 10px" }}
                            >
                                Suppr.
                            </Btn>
                        </div>
                    </div>
                ))
            )}
        </div>
    )
}

export function AdjurisSection() {
    const [onglet, setOnglet] = React.useState<Onglet>("etudiants")
    const [etudiants, setEtudiants] = React.useState<Etudiant[]>([])
    const [inscriptions, setInscriptions] = React.useState<Inscription[]>([])
    const [totalActives, setTotalActives] = React.useState(0)
    const [loading, setLoading] = React.useState(true)
    const [search, setSearch] = React.useState("")
    const [niveau, setNiveau] = React.useState<"tous" | Niveau>("tous")
    const [err, setErr] = React.useState("")
    const [discordSeul, setDiscordSeul] = React.useState(false)

    function load() {
        setLoading(true)
        setErr("")
        Promise.all([
            apiFetch("/prepa/adjuris/admin/etudiants").catch((e) => {
                setErr(e.message)
                return null
            }),
            apiFetch("/prepa/adjuris/admin/inscriptions").catch(() => null),
        ])
            .then(([e, i]) => {
                if (e) {
                    setEtudiants(e.items || [])
                    setTotalActives(e.total_matieres_actives || 0)
                }
                if (i) setInscriptions(i.items || [])
            })
            .finally(() => setLoading(false))
    }
    React.useEffect(() => {
        load()
    }, [])

    const q = search.toLowerCase()
    const etuFiltres = etudiants.filter(
        (e) =>
            (niveau === "tous" || e.niveau === niveau) &&
            (!discordSeul || e.discord_lie) &&
            (!q ||
                [
                    e.email,
                    e.prenom,
                    e.nom,
                    e.username || "",
                    e.telephone || "",
                ].some((v) => (v || "").toLowerCase().includes(q)))
    )
    const nbDiscord = etudiants.filter((e) => e.discord_lie).length
    const insFiltres = inscriptions.filter(
        (i) =>
            (niveau === "tous" || i.niveau === niveau) &&
            (!q ||
                [i.email, i.prenom, i.nom, i.telephone || ""].some((v) =>
                    (v || "").toLowerCase().includes(q)
                ))
    )

    const emailsPayants = new Set(etudiants.map((e) => e.email))
    const prospects = insFiltres.filter((i) => !emailsPayants.has(i.email))

    // Les ressources se gèrent niveau par niveau ; l'agenda et la
    // facturation acceptent aussi « Tous ».
    const gereNiveau = onglet === "ressources"
    const niveauActif: "tous" | Niveau =
        gereNiveau && niveau === "tous" ? "L1" : niveau

    function OngletBtn({ id, label }: { id: Onglet; label: string }) {
        const actif = onglet === id
        return (
            <button
                onClick={() => setOnglet(id)}
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
                }}
            >
                {label}
            </button>
        )
    }

    return (
        <div
            style={{
                display: "flex",
                flexDirection: "column",
                height: "100%",
                maxHeight: "100%",
            }}
        >
            <SBar
                left={
                    <span style={{ display: "flex", gap: 5 }}>
                        <OngletBtn id="etudiants" label="Élèves" />
                        <OngletBtn id="prospects" label="Prospects" />
                        <OngletBtn id="ressources" label="PDF" />
                        <OngletBtn id="agenda" label="Agenda" />
                        <OngletBtn id="facturation" label="Factu." />
                        <OngletBtn id="promo" label="Promo" />
                    </span>
                }
                right={
                    <span
                        style={{
                            display: "flex",
                            gap: 6,
                            alignItems: "center",
                        }}
                    >
                        <input
                            value={search}
                            onChange={(e) => setSearch(e.target.value)}
                            placeholder="Rechercher…"
                            style={{
                                width: 110,
                                height: 28,
                                padding: "0 10px",
                                background: s0,
                                border: `1px solid ${b0}`,
                                borderRadius: 10,
                                color: t0,
                                fontSize: 12,
                                fontFamily: mono,
                                outline: "none",
                                boxSizing: "border-box" as const,
                            }}
                        />
                        <Btn
                            onClick={load}
                            style={{ fontSize: 10, padding: "4px 10px" }}
                        >
                            ↻
                        </Btn>
                    </span>
                }
            />

            <div
                style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 6,
                    padding: "8px 14px",
                    borderBottom: `1px solid ${b0}`,
                    flexShrink: 0,
                }}
            >
                {onglet !== "promo" &&
                    (gereNiveau
                        ? NIVEAUX
                        : (["tous", ...NIVEAUX] as ("tous" | Niveau)[])
                    ).map((n) => (
                        <button
                            key={n}
                            onClick={() => setNiveau(n)}
                            style={{
                                fontSize: 10,
                                fontFamily: mono,
                                padding: "3px 12px",
                                borderRadius: 99,
                                border: `1px solid ${niveauActif === n ? b2 : b0}`,
                                background:
                                    niveauActif === n ? sA : "transparent",
                                color: niveauActif === n ? t0 : t1,
                                cursor: "pointer",
                                fontWeight: niveauActif === n ? 700 : 400,
                            }}
                        >
                            {n === "tous" ? "Tous" : n}
                        </button>
                    ))}

                {onglet === "etudiants" && (
                    <button
                        onClick={() => setDiscordSeul((v) => !v)}
                        title="N'afficher que les étudiants ayant lié leur compte Discord"
                        style={{
                            fontSize: 10,
                            fontFamily: mono,
                            padding: "3px 10px",
                            borderRadius: 99,
                            border: `1px solid ${discordSeul ? "rgba(88,101,242,0.6)" : b0}`,
                            background: discordSeul
                                ? "rgba(88,101,242,0.18)"
                                : "transparent",
                            color: discordSeul ? "#aab4ff" : t1,
                            cursor: "pointer",
                            fontWeight: discordSeul ? 700 : 400,
                        }}
                    >
                        Discord ({nbDiscord})
                    </button>
                )}

                <div style={{ flex: 1 }} />
                <span style={{ fontSize: 10, fontFamily: mono, color: t2 }}>
                    {onglet === "etudiants"
                        ? `${etuFiltres.length} étudiant(s) · ${totalActives} matière(s) active(s)`
                        : onglet === "prospects"
                          ? `${prospects.length} sans paiement`
                          : onglet === "promo"
                            ? "toutes matières"
                            : niveauActif === "tous"
                              ? "tous niveaux"
                              : `niveau ${niveauActif}`}
                </span>
                {(onglet === "etudiants" || onglet === "prospects") && (
                    <Btn
                        onClick={() =>
                            downloadAuth(
                                "/prepa/adjuris/admin/inscriptions.csv",
                                "prepa-adjuris-inscriptions.csv"
                            ).catch(() => {})
                        }
                        style={{ fontSize: 9, padding: "3px 8px" }}
                    >
                        ↓ CSV
                    </Btn>
                )}
            </div>

            {err && (
                <div
                    style={{
                        padding: "10px 14px",
                        fontSize: 11,
                        fontFamily: mono,
                        color: BAD,
                        borderBottom: `1px solid ${b0}`,
                    }}
                >
                    ERR: {err}
                </div>
            )}

            {onglet === "ressources" ? (
                <RessourcesPanel
                    key={niveauActif}
                    niveau={niveauActif as Niveau}
                />
            ) : onglet === "agenda" ? (
                <AgendaPanel niveau={niveauActif} />
            ) : onglet === "facturation" ? (
                <FacturationPanel niveau={niveauActif} search={search} />
            ) : onglet === "promo" ? (
                <AdjurisPromoPanel />
            ) : loading ? (
                <div
                    style={{
                        padding: "32px 14px",
                        textAlign: "center",
                        fontFamily: mono,
                        fontSize: 12,
                        color: t2,
                    }}
                >
                    Chargement…
                </div>
            ) : onglet === "etudiants" ? (
                etuFiltres.length === 0 ? (
                    <div
                        style={{
                            padding: "32px 14px",
                            textAlign: "center",
                            fontFamily: mono,
                            fontSize: 12,
                            color: t2,
                        }}
                    >
                        Aucun étudiant payant.
                    </div>
                ) : (
                    <div style={{ flex: 1, overflowY: "auto" }}>
                        {etuFiltres.map((e) => (
                            <EtudiantRow key={e.email} e={e} />
                        ))}
                    </div>
                )
            ) : prospects.length === 0 ? (
                <div
                    style={{
                        padding: "32px 14px",
                        textAlign: "center",
                        fontFamily: mono,
                        fontSize: 12,
                        color: t2,
                    }}
                >
                    Aucun prospect en attente.
                </div>
            ) : (
                <div style={{ flex: 1, overflowY: "auto" }}>
                    {prospects.map((i) => (
                        <InscriptionRow key={i.id} i={i} />
                    ))}
                </div>
            )}
        </div>
    )
}

// ══════════════════════════════════════════════════════════
// OVERRIDE 1 — AdminConsoleFrame (583×590)
// ══════════════════════════════════════════════════════════

const SECTIONS: { id: Section; label: string }[] = [
    { id: "users", label: "Users" },
    { id: "promos", label: "Promos" },
    { id: "actus", label: "Actus" },
    { id: "prepa", label: "Prepa" },
    { id: "adjuris", label: "AdJuris" },
    { id: "ambassadeurs", label: "Amb." },
    { id: "lib", label: "Lib" },
    { id: "professeurs", label: "Prof." },
    { id: "terminal", label: "Terminal" },
]

function ConsoleUI() {
    const [authed, setAuthed] = React.useState(() => {
        try {
            return sessionStorage.getItem(SESSION_KEY) === "1"
        } catch {
            return false
        }
    })
    const [section, setSection] = React.useState<Section>("users")
    const [selectedUser, setSelectedUser] = React.useState<User | null>(null)

    if (!getToken())
        return (
            <div style={ROOT}>
                <div
                    style={{
                        padding: "32px 14px",
                        textAlign: "center",
                        fontFamily: mono,
                        fontSize: 12,
                        color: t2,
                    }}
                >
                    Non connecté.
                </div>
            </div>
        )

    if (!authed)
        return (
            <div style={ROOT}>
                <AuthGate onSuccess={() => setAuthed(true)} />
            </div>
        )

    return (
        <div style={{ ...ROOT, position: "relative" }}>
            <div
                style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 4,
                    padding: "0 12px",
                    height: 48,
                    borderBottom: `1px solid ${b0}`,
                    flexShrink: 0,
                    // La barre défile plutôt que de déborder si elle manque de place.
                    overflowX: "auto",
                }}
            >
                <span
                    style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 8,
                        marginRight: 6,
                        whiteSpace: "nowrap" as const,
                        flexShrink: 0,
                    }}
                >
                    <span
                        style={{
                            width: 22,
                            height: 22,
                            background: `url("${API_BASE}/assets/logo-navire-app.png") center/contain no-repeat`,
                        }}
                    />
                    <span style={{ fontSize: 13, fontWeight: 800, letterSpacing: 0.5 }}>
                        NAVIRE
                    </span>
                </span>
                {SECTIONS.map((s) => {
                    const active = section === s.id
                    return (
                        <button
                            key={s.id}
                            onClick={() => setSection(s.id)}
                            style={{
                                height: 30,
                                padding: "0 10px",
                                borderRadius: 99,
                                border: `1px solid ${active ? b1 : "transparent"}`,
                                background: active ? sA : "transparent",
                                color: active ? t0 : t1,
                                fontSize: 12,
                                fontFamily: mono,
                                fontWeight: active ? 600 : 500,
                                cursor: "pointer",
                                flexShrink: 0,
                                transition: "background .15s, color .15s",
                            }}
                        >
                            {s.label}
                        </button>
                    )
                })}
            </div>
            <div style={{ flex: 1, overflow: "hidden" }}>
                {section === "users" && (
                    <UsersSection
                        selectedUser={selectedUser}
                        onSelectUser={setSelectedUser}
                    />
                )}
                {section === "promos" && <PromosSection />}
                {section === "actus" && <ActusSection />}
                {section === "prepa" && <PrepaSection />}
                {section === "terminal" && <TerminalSection />}
                {section === "adjuris" && <AdjurisSection />}
                {section === "ambassadeurs" && <AmbassadeursSection />}
                {section === "lib" && <LibSection />}
                {section === "professeurs" && <ProfesseursSection />}
            </div>
        </div>
    )
}

const ROOT: React.CSSProperties = {
    width: "100%",
    height: "100%",
    display: "flex",
    flexDirection: "column",
    fontFamily: "'Inter', system-ui, sans-serif",
    WebkitFontSmoothing: "antialiased",
    color: t0,
    background: "transparent",
    overflow: "hidden",
}

export function AdminConsoleFrame(): Override {
    return { children: <ConsoleUI /> }
}

// ══════════════════════════════════════════════════════════
// OVERRIDE 2 — AdminUserPanel (261×189)
// ══════════════════════════════════════════════════════════

type PanelTab = "info" | "qcm" | "fichiers"

function UserPanelUI() {
    const [user, setUser] = React.useState<User | null>(readSelectedUser)
    const [stats, setStats] = React.useState<UserStats | null>(null)
    const [statsLoading, setStatsLoading] = React.useState(false)
    const [tab, setTab] = React.useState<PanelTab>("info")
    const [planMsg, setPlanMsg] = React.useState("")
    const [planBusy, setPlanBusy] = React.useState(false)
    const [curTier, setCurTier] = React.useState<string | null>(null)
    const [copied, setCopied] = React.useState(false)
    const [prepaAnnee, setPrepaAnnee] = React.useState<string | null>(null)
    const [prepaExpiresAt, setPrepaExpiresAt] = React.useState<string | null>(
        null
    )
    const [prepaLoading, setPrepaLoading] = React.useState(false)
    const [prepaBusy, setPrepaBusy] = React.useState(false)
    const [prepaMsg, setPrepaMsg] = React.useState("")
    const [grantAnnee, setGrantAnnee] = React.useState<"L1" | "L2" | "L3">("L1")
    const [grantDays, setGrantDays] = React.useState(30)
    const [adjurisMatieres, setAdjurisMatieres] = React.useState<MatiereRow[]>(
        []
    )
    const [adjurisLoading, setAdjurisLoading] = React.useState(false)
    const [adjurisBusy, setAdjurisBusy] = React.useState(false)
    const [adjurisMsg, setAdjurisMsg] = React.useState("")
    const [grantMatiereKey, setGrantMatiereKey] = React.useState(
        MATIERES_FLAT[0]?.key || ""
    )
    const [grantJours, setGrantJours] = React.useState(7)
    const [grantAvecGrade, setGrantAvecGrade] = React.useState(true)
    const [planView, setPlanView] = React.useState<PlanView | null>(null)
    const [planViewLoading, setPlanViewLoading] = React.useState(false)

    React.useEffect(() => {
        function onEvent(e: Event) {
            const u = (e as CustomEvent).detail as User | null
            setUser(u)
            setStats(null)
            setTab("info")
            setPlanMsg("")
            setCurTier(u?.subscription_tier ?? null)
            setPrepaAnnee(null)
            setPrepaExpiresAt(null)
            setPrepaMsg("")
            setAdjurisMatieres([])
            setAdjurisMsg("")
            setPlanView(null)
        }
        function onStorage(e: StorageEvent) {
            if (e.key === SELECTED_USER_KEY) {
                try {
                    const u = e.newValue ? JSON.parse(e.newValue) : null
                    setUser(u)
                    setStats(null)
                    setTab("info")
                    setPlanMsg("")
                    setCurTier(u?.subscription_tier ?? null)
                    setPrepaAnnee(null)
                    setPrepaExpiresAt(null)
                    setPrepaMsg("")
                    setAdjurisMatieres([])
                    setAdjurisMsg("")
                    setPlanView(null)
                } catch {}
            }
        }
        window.addEventListener("navire:admin:user_selected", onEvent)
        window.addEventListener("storage", onStorage)
        return () => {
            window.removeEventListener("navire:admin:user_selected", onEvent)
            window.removeEventListener("storage", onStorage)
        }
    }, [])

    React.useEffect(() => {
        if (!user) {
            setStats(null)
            return
        }
        setCurTier(user.subscription_tier)
        setStatsLoading(true)
        apiFetch(
            `/admin/users/${user.id}/stats?tz_offset=${new Date().getTimezoneOffset()}`
        )
            .then(setStats)
            .catch(() => setStats(null))
            .finally(() => setStatsLoading(false))

        setPrepaLoading(true)
        apiFetch(`/admin/users/${user.id}`)
            .then((d) => {
                setPrepaAnnee(d?.prepa_annee ?? null)
                setPrepaExpiresAt(d?.prepa_expires_at ?? null)
            })
            .catch(() => {
                setPrepaAnnee(null)
                setPrepaExpiresAt(null)
            })
            .finally(() => setPrepaLoading(false))

        setAdjurisLoading(true)
        apiFetch(`/prepa/adjuris/admin/etudiants`)
            .then((d) => {
                const rows = (d?.items || []) as Etudiant[]
                const match = rows.find((r) => r.email === user.email)
                setAdjurisMatieres(match?.matieres || [])
            })
            .catch(() => setAdjurisMatieres([]))
            .finally(() => setAdjurisLoading(false))

        setPlanViewLoading(true)
        apiFetch(`/admin/users/${user.id}/plan`)
            .then((d) => setPlanView(d))
            .catch(() => setPlanView(null))
            .finally(() => setPlanViewLoading(false))
    }, [user?.id])

    function refreshPlanView() {
        if (!user) return
        apiFetch(`/admin/users/${user.id}/plan`)
            .then((d) => setPlanView(d))
            .catch(() => {})
    }

    async function setPlan(plan: string) {
        if (!user) return
        setPlanBusy(true)
        setPlanMsg("")
        try {
            await apiFetch(
                `/admin/set-subscription?email=${encodeURIComponent(user.email)}&plan=${encodeURIComponent(plan)}`,
                { method: "POST" }
            )
            setCurTier(plan)
            setPlanMsg(`✓ ${plan.toUpperCase()}`)
            refreshPlanView()
        } catch (e: any) {
            setPlanMsg(`ERR`)
        } finally {
            setPlanBusy(false)
        }
    }

    async function grantPrepa() {
        if (!user) return
        setPrepaBusy(true)
        setPrepaMsg("")
        try {
            const d = await apiFetch(`/admin/users/${user.id}/prepa/grant`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    annee: grantAnnee,
                    duration_days: grantDays,
                }),
            })
            setPrepaAnnee(d.prepa_annee)
            setPrepaExpiresAt(d.prepa_expires_at)
            setCurTier("prepa")
            setPrepaMsg(`✓ PREPA ${grantAnnee} · ${grantDays}j`)
            refreshPlanView()
        } catch (e: any) {
            setPrepaMsg(`ERR: ${e.message}`)
        } finally {
            setPrepaBusy(false)
        }
    }

    async function revokePrepa() {
        if (!user) return
        setPrepaBusy(true)
        setPrepaMsg("")
        try {
            await apiFetch(`/admin/users/${user.id}/prepa/revoke`, {
                method: "POST",
            })
            setPrepaAnnee(null)
            setPrepaExpiresAt(null)
            setCurTier("free")
            setPrepaMsg("✓ Révoqué")
            refreshPlanView()
        } catch (e: any) {
            setPrepaMsg(`ERR: ${e.message}`)
        } finally {
            setPrepaBusy(false)
        }
    }

    async function grantAdjuris() {
        if (!user) return
        setAdjurisBusy(true)
        setAdjurisMsg("")
        try {
            const d = await apiFetch(`/prepa/adjuris/admin/acces`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    email: user.email,
                    matiere_key: grantMatiereKey,
                    jours: grantJours,
                    avec_grade: grantAvecGrade,
                }),
            })
            setAdjurisMsg(
                `✓ ${d.matiere_label} · ${grantJours}j${grantAvecGrade ? " (+ grade)" : ""}`
            )
            if (grantAvecGrade) {
                setCurTier("prepa")
                setPrepaAnnee(grantMatiereKey.split("_")[0])
                setPrepaExpiresAt(d.expires_at)
                refreshPlanView()
            }
            const list = await apiFetch(`/prepa/adjuris/admin/etudiants`)
            const match = ((list?.items || []) as Etudiant[]).find(
                (r) => r.email === user.email
            )
            setAdjurisMatieres(match?.matieres || [])
        } catch (e: any) {
            setAdjurisMsg(`ERR: ${e.message}`)
        } finally {
            setAdjurisBusy(false)
        }
    }

    async function revokeAdjuris(matiereKey: string) {
        if (!user) return
        setAdjurisBusy(true)
        setAdjurisMsg("")
        try {
            await apiFetch(`/prepa/adjuris/admin/acces/revoquer`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    email: user.email,
                    matiere_key: matiereKey,
                }),
            })
            setAdjurisMatieres((prev) =>
                prev.filter((m) => m.matiere_key !== matiereKey)
            )
            setAdjurisMsg("✓ Révoqué")
        } catch (e: any) {
            setAdjurisMsg(`ERR: ${e.message}`)
        } finally {
            setAdjurisBusy(false)
        }
    }

    if (!user) {
        return (
            <div style={PANEL_ROOT}>
                <div
                    style={{
                        flex: 1,
                        display: "flex",
                        flexDirection: "column",
                        alignItems: "center",
                        justifyContent: "center",
                        gap: 6,
                    }}
                >
                    <div
                        style={{
                            width: 28,
                            height: 28,
                            borderRadius: "50%",
                            border: `1px solid ${b0}`,
                            display: "flex",
                            alignItems: "center",
                            justifyContent: "center",
                        }}
                    >
                        <svg
                            width="13"
                            height="13"
                            viewBox="0 0 13 13"
                            fill="none"
                        >
                            <circle
                                cx="6.5"
                                cy="4.5"
                                r="2.5"
                                stroke={t2}
                                strokeWidth="1.2"
                            />
                            <path
                                d="M1.5 11.5c0-2.761 2.239-5 5-5s5 2.239 5 5"
                                stroke={t2}
                                strokeWidth="1.2"
                                strokeLinecap="round"
                            />
                        </svg>
                    </div>
                    <span
                        style={{
                            fontSize: 10,
                            fontFamily: mono,
                            color: t2,
                            letterSpacing: 0.5,
                        }}
                    >
                        Sélectionner un user
                    </span>
                </div>
            </div>
        )
    }

    const TABS: { id: PanelTab; label: string }[] = [
        { id: "info", label: "Info" },
        { id: "qcm", label: "QCM" },
        {
            id: "fichiers",
            label: `PDF${stats ? ` (${stats.files_count})` : ""}`,
        },
    ]

    const TabInfo = () => {
        const prepaActive =
            !!prepaExpiresAt && new Date(prepaExpiresAt) > new Date()

        return (
            <div
                style={{
                    padding: "8px 10px",
                    display: "flex",
                    flexDirection: "column",
                    gap: 6,
                    overflow: "hidden",
                    flex: 1,
                }}
            >
                <div
                    style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 6,
                        justifyContent: "space-between",
                    }}
                >
                    <span
                        style={{
                            fontSize: 12,
                            fontWeight: 600,
                            color: t0,
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            whiteSpace: "nowrap" as const,
                            flex: 1,
                        }}
                    >
                        {user.username}
                    </span>
                    <PlanBadge
                        tier={curTier}
                        manual={user.is_manual_subscription}
                        status={user.subscription_status}
                    />
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
                    <span
                        style={{
                            fontSize: 10,
                            fontFamily: mono,
                            color: t1,
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            whiteSpace: "nowrap" as const,
                            flex: 1,
                        }}
                    >
                        {user.email}
                    </span>
                    <button
                        onClick={() => {
                            navigator.clipboard.writeText(user.email)
                            setCopied(true)
                            setTimeout(() => setCopied(false), 1400)
                        }}
                        style={{
                            fontSize: 9,
                            fontFamily: mono,
                            padding: "1px 5px",
                            borderRadius: 99,
                            border: `1px solid ${b0}`,
                            background: "transparent",
                            color: copied ? OK : t2,
                            cursor: "pointer",
                            flexShrink: 0,
                        }}
                    >
                        {copied ? "✓" : "cp"}
                    </button>
                </div>
                <div
                    style={{
                        display: "grid",
                        gridTemplateColumns: "1fr 1fr 1fr",
                        gap: 4,
                    }}
                >
                    {[
                        { l: "ID", v: `#${user.id}` },
                        { l: "ELO", v: user.elo },
                        { l: "Inscrit", v: fmtDate(user.created_at) },
                    ].map(({ l, v }) => (
                        <div
                            key={l}
                            style={{
                                padding: "5px 6px",
                                border: `1px solid ${b0}`,
                                borderRadius: 10,
                            }}
                        >
                            <div
                                style={{
                                    fontSize: 8,
                                    fontFamily: mono,
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
                                    fontSize: 11,
                                    fontFamily: mono,
                                    color: t0,
                                }}
                            >
                                {v}
                            </div>
                        </div>
                    ))}
                </div>
                <div>
                    <div
                        style={{
                            fontSize: 8,
                            fontFamily: mono,
                            color: t2,
                            textTransform: "uppercase" as const,
                            letterSpacing: 0.8,
                            marginBottom: 4,
                        }}
                    >
                        Plan
                    </div>
                    {planViewLoading ? (
                        <div
                            style={{
                                fontSize: 9,
                                fontFamily: mono,
                                color: t2,
                                marginBottom: 4,
                            }}
                        >
                            …
                        </div>
                    ) : planView ? (
                        <div
                            style={{
                                display: "grid",
                                gridTemplateColumns: "1fr 1fr",
                                gap: 4,
                                marginBottom: 4,
                            }}
                        >
                            {[
                                {
                                    l: "Statut",
                                    v:
                                        planView.status +
                                        (planView.stripe?.cancel_at_period_end
                                            ? " (annulé)"
                                            : ""),
                                },
                                {
                                    l: "Géré par",
                                    v: planView.is_manual
                                        ? "Manuel"
                                        : planView.stripe_subscription_id
                                          ? "Stripe"
                                          : "—",
                                },
                                {
                                    l: "Cycle",
                                    v: planView.billing_cycle || "—",
                                },
                                {
                                    l: "Échéance",
                                    v: fmtDate(planView.current_period_end),
                                },
                            ].map(({ l, v }) => (
                                <div
                                    key={l}
                                    style={{
                                        padding: "4px 6px",
                                        border: `1px solid ${b0}`,
                                        borderRadius: 99,
                                    }}
                                >
                                    <div
                                        style={{
                                            fontSize: 7,
                                            fontFamily: mono,
                                            color: t2,
                                            textTransform: "uppercase" as const,
                                            letterSpacing: 0.6,
                                            marginBottom: 1,
                                        }}
                                    >
                                        {l}
                                    </div>
                                    <div
                                        style={{
                                            fontSize: 9,
                                            fontFamily: mono,
                                            color: t0,
                                        }}
                                    >
                                        {v}
                                    </div>
                                </div>
                            ))}
                        </div>
                    ) : null}
                    <div style={{ display: "flex", gap: 4 }}>
                        {["free", "membre", "membre+"].map((p) => (
                            <button
                                key={p}
                                onClick={() => setPlan(p)}
                                disabled={planBusy || curTier === p}
                                style={{
                                    flex: 1,
                                    height: 24,
                                    borderRadius: 99,
                                    border: `1px solid ${curTier === p ? b2 : b0}`,
                                    background: curTier === p ? sA : s0,
                                    color: curTier === p ? t0 : t2,
                                    fontSize: 9,
                                    fontFamily: mono,
                                    fontWeight: 700,
                                    cursor:
                                        planBusy || curTier === p
                                            ? "not-allowed"
                                            : "pointer",
                                    opacity: planBusy ? 0.5 : 1,
                                }}
                            >
                                {p === "membre+"
                                    ? "M+"
                                    : p === "membre"
                                      ? "M"
                                      : "F"}
                            </button>
                        ))}
                    </div>
                    {planMsg && (
                        <div
                            style={{
                                fontSize: 9,
                                fontFamily: mono,
                                color: planMsg.startsWith("ERR")
                                    ? BAD
                                    : OK,
                                marginTop: 4,
                            }}
                        >
                            {planMsg}
                        </div>
                    )}
                </div>
                <div>
                    <div
                        style={{
                            fontSize: 8,
                            fontFamily: mono,
                            color: t2,
                            textTransform: "uppercase" as const,
                            letterSpacing: 0.8,
                            marginBottom: 4,
                        }}
                    >
                        Prépa temporaire
                    </div>
                    <div
                        style={{
                            fontSize: 9,
                            fontFamily: mono,
                            color: prepaActive ? "#9B6FFF" : t2,
                            marginBottom: 4,
                        }}
                    >
                        {prepaLoading
                            ? "…"
                            : prepaActive
                              ? `Actif · ${prepaAnnee} · exp. ${fmtDate(prepaExpiresAt)}`
                              : "Aucun accès actif"}
                    </div>
                    <div style={{ display: "flex", gap: 4, marginBottom: 4 }}>
                        <select
                            value={grantAnnee}
                            onChange={(e) =>
                                setGrantAnnee(
                                    e.target.value as "L1" | "L2" | "L3"
                                )
                            }
                            disabled={prepaBusy}
                            style={{
                                flex: 1,
                                height: 24,
                                borderRadius: 99,
                                border: `1px solid ${b0}`,
                                background: FIELD,
                                color: t0,
                                fontSize: 9,
                                fontFamily: mono,
                            }}
                        >
                            <option value="L1">L1</option>
                            <option value="L2">L2</option>
                            <option value="L3">L3</option>
                        </select>
                        <input
                            type="number"
                            min={1}
                            max={365}
                            value={grantDays}
                            onChange={(e) =>
                                setGrantDays(Number(e.target.value))
                            }
                            disabled={prepaBusy}
                            style={{
                                width: 46,
                                height: 24,
                                borderRadius: 99,
                                border: `1px solid ${b0}`,
                                background: s0,
                                color: t0,
                                fontSize: 9,
                                fontFamily: mono,
                                textAlign: "center" as const,
                            }}
                        />
                        <span
                            style={{
                                fontSize: 8,
                                fontFamily: mono,
                                color: t2,
                                alignSelf: "center",
                            }}
                        >
                            j
                        </span>
                    </div>
                    <div style={{ display: "flex", gap: 4 }}>
                        <button
                            onClick={grantPrepa}
                            disabled={prepaBusy}
                            style={{
                                flex: 1,
                                height: 24,
                                borderRadius: 99,
                                border: `1px solid ${b1}`,
                                background: s0,
                                color: t0,
                                fontSize: 9,
                                fontFamily: mono,
                                fontWeight: 700,
                                cursor: prepaBusy ? "not-allowed" : "pointer",
                                opacity: prepaBusy ? 0.5 : 1,
                            }}
                        >
                            {prepaActive ? "Renouveler" : "Attribuer"}
                        </button>
                        {prepaActive && (
                            <button
                                onClick={revokePrepa}
                                disabled={prepaBusy}
                                style={{
                                    flex: 1,
                                    height: 24,
                                    borderRadius: 99,
                                    border: "1px solid rgba(255,107,107,0.3)",
                                    background: "rgba(255,107,107,0.08)",
                                    color: BAD,
                                    fontSize: 9,
                                    fontFamily: mono,
                                    fontWeight: 700,
                                    cursor: prepaBusy
                                        ? "not-allowed"
                                        : "pointer",
                                    opacity: prepaBusy ? 0.5 : 1,
                                }}
                            >
                                Révoquer
                            </button>
                        )}
                    </div>
                    {prepaMsg && (
                        <div
                            style={{
                                fontSize: 9,
                                fontFamily: mono,
                                color: prepaMsg.startsWith("ERR")
                                    ? BAD
                                    : OK,
                                marginTop: 4,
                            }}
                        >
                            {prepaMsg}
                        </div>
                    )}
                </div>
                <div>
                    <div
                        style={{
                            fontSize: 8,
                            fontFamily: mono,
                            color: t2,
                            textTransform: "uppercase" as const,
                            letterSpacing: 0.8,
                            marginBottom: 4,
                        }}
                    >
                        Prépa AdJuris
                    </div>
                    {adjurisLoading ? (
                        <div
                            style={{
                                fontSize: 9,
                                fontFamily: mono,
                                color: t2,
                                marginBottom: 4,
                            }}
                        >
                            …
                        </div>
                    ) : adjurisMatieres.length === 0 ? (
                        <div
                            style={{
                                fontSize: 9,
                                fontFamily: mono,
                                color: t2,
                                marginBottom: 4,
                            }}
                        >
                            Aucune matière active
                        </div>
                    ) : (
                        <div
                            style={{
                                display: "flex",
                                flexDirection: "column",
                                gap: 3,
                                marginBottom: 4,
                            }}
                        >
                            {adjurisMatieres.map((m) => (
                                <div
                                    key={m.matiere_key}
                                    style={{
                                        display: "flex",
                                        alignItems: "center",
                                        gap: 4,
                                    }}
                                >
                                    <span
                                        style={{
                                            flex: 1,
                                            fontSize: 9,
                                            fontFamily: mono,
                                            color: t1,
                                            overflow: "hidden",
                                            textOverflow: "ellipsis",
                                            whiteSpace: "nowrap" as const,
                                        }}
                                    >
                                        {m.niveau} · {m.label}
                                    </span>
                                    <StatusTag status={m.status} />
                                    <button
                                        onClick={() =>
                                            revokeAdjuris(m.matiere_key)
                                        }
                                        disabled={adjurisBusy}
                                        style={{
                                            fontSize: 8,
                                            fontFamily: mono,
                                            padding: "1px 5px",
                                            borderRadius: 99,
                                            border: "1px solid rgba(255,107,107,0.3)",
                                            background: "rgba(255,107,107,0.08)",
                                            color: BAD,
                                            cursor: adjurisBusy
                                                ? "not-allowed"
                                                : "pointer",
                                            flexShrink: 0,
                                        }}
                                    >
                                        ✕
                                    </button>
                                </div>
                            ))}
                        </div>
                    )}
                    <select
                        value={grantMatiereKey}
                        onChange={(e) => setGrantMatiereKey(e.target.value)}
                        disabled={adjurisBusy}
                        style={{
                            width: "100%",
                            height: 24,
                            borderRadius: 99,
                            border: `1px solid ${b0}`,
                            background: FIELD,
                            color: t0,
                            fontSize: 9,
                            fontFamily: mono,
                            marginBottom: 4,
                        }}
                    >
                        {MATIERES_FLAT.map((m) => (
                            <option key={m.key} value={m.key}>
                                {m.label}
                            </option>
                        ))}
                    </select>
                    <div
                        style={{
                            display: "flex",
                            gap: 4,
                            alignItems: "center",
                            marginBottom: 4,
                        }}
                    >
                        <input
                            type="number"
                            min={1}
                            max={365}
                            value={grantJours}
                            onChange={(e) =>
                                setGrantJours(Number(e.target.value))
                            }
                            disabled={adjurisBusy}
                            style={{
                                width: 46,
                                height: 24,
                                borderRadius: 99,
                                border: `1px solid ${b0}`,
                                background: s0,
                                color: t0,
                                fontSize: 9,
                                fontFamily: mono,
                                textAlign: "center" as const,
                            }}
                        />
                        <span
                            style={{
                                fontSize: 8,
                                fontFamily: mono,
                                color: t2,
                            }}
                        >
                            j
                        </span>
                        <label
                            style={{
                                display: "flex",
                                alignItems: "center",
                                gap: 3,
                                fontSize: 8,
                                fontFamily: mono,
                                color: t1,
                                flex: 1,
                            }}
                        >
                            <input
                                type="checkbox"
                                checked={grantAvecGrade}
                                onChange={(e) =>
                                    setGrantAvecGrade(e.target.checked)
                                }
                                disabled={adjurisBusy}
                            />
                            + grade
                        </label>
                    </div>
                    <button
                        onClick={grantAdjuris}
                        disabled={adjurisBusy}
                        style={{
                            width: "100%",
                            height: 24,
                            borderRadius: 99,
                            border: `1px solid ${b1}`,
                            background: s0,
                            color: t0,
                            fontSize: 9,
                            fontFamily: mono,
                            fontWeight: 700,
                            cursor: adjurisBusy ? "not-allowed" : "pointer",
                            opacity: adjurisBusy ? 0.5 : 1,
                        }}
                    >
                        Attribuer matière
                    </button>
                    {adjurisMsg && (
                        <div
                            style={{
                                fontSize: 9,
                                fontFamily: mono,
                                color: adjurisMsg.startsWith("ERR")
                                    ? BAD
                                    : OK,
                                marginTop: 4,
                            }}
                        >
                            {adjurisMsg}
                        </div>
                    )}
                </div>
            </div>
        )
    }

    const TabQcm = () => {
        if (statsLoading)
            return (
                <div
                    style={{
                        flex: 1,
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                    }}
                >
                    <span style={{ fontSize: 10, fontFamily: mono, color: t2 }}>
                        …
                    </span>
                </div>
            )
        if (!stats)
            return (
                <div
                    style={{
                        flex: 1,
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                    }}
                >
                    <span style={{ fontSize: 10, fontFamily: mono, color: t2 }}>
                        Aucune donnée.
                    </span>
                </div>
            )

        const pages = [
            [
                { l: "Streak", v: `${stats.current_streak}j` },
                { l: "Record", v: `${stats.longest_streak}j` },
                { l: "Jours", v: stats.total_days },
                { l: "Réussite", v: `${stats.success_rate_pct}%` },
            ],
            [
                { l: "Correct", v: stats.total_correct },
                { l: "Faux", v: stats.total_wrong },
                { l: "Sessions", v: stats.total_sessions },
                { l: "Complet", v: stats.complete_sessions },
            ],
        ]
        const [page, setPage] = React.useState(0)
        const total = pages.length

        return (
            <div
                style={{
                    flex: 1,
                    display: "flex",
                    flexDirection: "column",
                    padding: "8px 10px",
                    gap: 6,
                    overflow: "hidden",
                }}
            >
                <div
                    style={{
                        display: "grid",
                        gridTemplateColumns: "1fr 1fr 1fr 1fr",
                        gap: 4,
                    }}
                >
                    {pages[page].map(({ l, v }) => (
                        <div
                            key={l}
                            style={{
                                padding: "5px 4px",
                                border: `1px solid ${b0}`,
                                borderRadius: 10,
                                textAlign: "center" as const,
                            }}
                        >
                            <div
                                style={{
                                    fontSize: 8,
                                    fontFamily: mono,
                                    color: t2,
                                    textTransform: "uppercase" as const,
                                    letterSpacing: 0.6,
                                    marginBottom: 2,
                                }}
                            >
                                {l}
                            </div>
                            <div
                                style={{
                                    fontSize: 13,
                                    fontWeight: 700,
                                    fontFamily: mono,
                                    color: t0,
                                }}
                            >
                                {v}
                            </div>
                        </div>
                    ))}
                </div>
                {page === 1 && stats.qcm_files?.length > 0 && (
                    <div
                        style={{
                            border: `1px solid ${b0}`,
                            borderRadius: 10,
                            overflow: "hidden",
                        }}
                    >
                        {stats.qcm_files.slice(0, 3).map((f, i) => (
                            <div
                                key={f.file_id}
                                style={{
                                    display: "flex",
                                    alignItems: "center",
                                    gap: 6,
                                    padding: "5px 8px",
                                    borderBottom:
                                        i <
                                        Math.min(2, stats.qcm_files.length - 1)
                                            ? `1px solid rgba(255,255,255,0.04)`
                                            : "none",
                                }}
                            >
                                <div
                                    style={{
                                        flex: 1,
                                        fontSize: 10,
                                        color: t0,
                                        overflow: "hidden",
                                        textOverflow: "ellipsis",
                                        whiteSpace: "nowrap" as const,
                                    }}
                                >
                                    {f.file_name}
                                </div>
                                <span
                                    style={{
                                        fontSize: 10,
                                        fontFamily: mono,
                                        fontWeight: 700,
                                        color: t0,
                                        flexShrink: 0,
                                    }}
                                >
                                    {f.success_rate_pct}%
                                </span>
                            </div>
                        ))}
                    </div>
                )}
                <div
                    style={{
                        display: "flex",
                        justifyContent: "center",
                        alignItems: "center",
                        gap: 8,
                        marginTop: "auto",
                    }}
                >
                    <button
                        onClick={() => setPage((p) => Math.max(0, p - 1))}
                        disabled={page === 0}
                        style={{
                            fontSize: 9,
                            fontFamily: mono,
                            padding: "2px 6px",
                            borderRadius: 99,
                            border: `1px solid ${b0}`,
                            background: "transparent",
                            color: page === 0 ? t2 : t1,
                            cursor: page === 0 ? "not-allowed" : "pointer",
                        }}
                    >
                        ◂
                    </button>
                    <span style={{ fontSize: 9, fontFamily: mono, color: t2 }}>
                        {page + 1}/{total}
                    </span>
                    <button
                        onClick={() =>
                            setPage((p) => Math.min(total - 1, p + 1))
                        }
                        disabled={page === total - 1}
                        style={{
                            fontSize: 9,
                            fontFamily: mono,
                            padding: "2px 6px",
                            borderRadius: 99,
                            border: `1px solid ${b0}`,
                            background: "transparent",
                            color: page === total - 1 ? t2 : t1,
                            cursor:
                                page === total - 1 ? "not-allowed" : "pointer",
                        }}
                    >
                        ▸
                    </button>
                </div>
            </div>
        )
    }

    const TabFichiers = () => {
        if (statsLoading)
            return (
                <div
                    style={{
                        flex: 1,
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                    }}
                >
                    <span style={{ fontSize: 10, fontFamily: mono, color: t2 }}>
                        …
                    </span>
                </div>
            )
        if (!stats || stats.files.length === 0)
            return (
                <div
                    style={{
                        flex: 1,
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                    }}
                >
                    <span style={{ fontSize: 10, fontFamily: mono, color: t2 }}>
                        Aucun fichier.
                    </span>
                </div>
            )

        const PAGE_SIZE = 3
        const [page, setPage] = React.useState(0)
        const total = Math.ceil(stats.files.length / PAGE_SIZE)
        const slice = stats.files.slice(
            page * PAGE_SIZE,
            page * PAGE_SIZE + PAGE_SIZE
        )

        return (
            <div
                style={{
                    flex: 1,
                    display: "flex",
                    flexDirection: "column",
                    padding: "6px 10px",
                    gap: 4,
                }}
            >
                <div
                    style={{
                        flex: 1,
                        border: `1px solid ${b0}`,
                        borderRadius: 10,
                        overflow: "hidden",
                    }}
                >
                    {slice.map((f, i) => (
                        <div
                            key={f.id}
                            style={{
                                display: "flex",
                                alignItems: "center",
                                gap: 6,
                                padding: "6px 8px",
                                borderBottom:
                                    i < slice.length - 1
                                        ? `1px solid rgba(255,255,255,0.04)`
                                        : "none",
                            }}
                        >
                            <div style={{ flex: 1, minWidth: 0 }}>
                                <div
                                    style={{
                                        fontSize: 10,
                                        color: t0,
                                        overflow: "hidden",
                                        textOverflow: "ellipsis",
                                        whiteSpace: "nowrap" as const,
                                    }}
                                >
                                    {f.filename_original.replace(/\.pdf$/i, "")}
                                </div>
                                <div
                                    style={{
                                        fontSize: 9,
                                        fontFamily: mono,
                                        color: t2,
                                    }}
                                >
                                    #{f.id} · {fmtSize(f.size_bytes)}
                                </div>
                            </div>
                            <span
                                style={{
                                    fontSize: 9,
                                    fontFamily: mono,
                                    color: f.expires_at ? t1 : t2,
                                    flexShrink: 0,
                                }}
                            >
                                {f.expires_at
                                    ? `exp. ${fmtDate(f.expires_at)}`
                                    : "∞"}
                            </span>
                        </div>
                    ))}
                </div>
                {total > 1 && (
                    <div
                        style={{
                            display: "flex",
                            justifyContent: "center",
                            alignItems: "center",
                            gap: 8,
                        }}
                    >
                        <button
                            onClick={() => setPage((p) => Math.max(0, p - 1))}
                            disabled={page === 0}
                            style={{
                                fontSize: 9,
                                fontFamily: mono,
                                padding: "2px 6px",
                                borderRadius: 99,
                                border: `1px solid ${b0}`,
                                background: "transparent",
                                color: page === 0 ? t2 : t1,
                                cursor: page === 0 ? "not-allowed" : "pointer",
                            }}
                        >
                            ◂
                        </button>
                        <span
                            style={{ fontSize: 9, fontFamily: mono, color: t2 }}
                        >
                            {page + 1}/{total}
                        </span>
                        <button
                            onClick={() =>
                                setPage((p) => Math.min(total - 1, p + 1))
                            }
                            disabled={page === total - 1}
                            style={{
                                fontSize: 9,
                                fontFamily: mono,
                                padding: "2px 6px",
                                borderRadius: 99,
                                border: `1px solid ${b0}`,
                                background: "transparent",
                                color: page === total - 1 ? t2 : t1,
                                cursor:
                                    page === total - 1
                                        ? "not-allowed"
                                        : "pointer",
                            }}
                        >
                            ▸
                        </button>
                    </div>
                )}
            </div>
        )
    }

    return (
        <div style={PANEL_ROOT}>
            <div
                style={{
                    display: "flex",
                    alignItems: "center",
                    borderBottom: `1px solid ${b0}`,
                    padding: "0 8px",
                    height: 32,
                    gap: 4,
                    flexShrink: 0,
                }}
            >
                {TABS.map((t) => {
                    const active = tab === t.id
                    return (
                        <button
                            key={t.id}
                            onClick={() => setTab(t.id)}
                            style={{
                                fontSize: 10,
                                fontFamily: mono,
                                padding: "3px 8px",
                                borderRadius: 99,
                                border: `1px solid ${active ? b2 : "transparent"}`,
                                background: active ? sA : "transparent",
                                color: active ? t0 : t1,
                                cursor: "pointer",
                                fontWeight: active ? 700 : 400,
                            }}
                        >
                            {t.label}
                        </button>
                    )
                })}
            </div>
            <div
                style={{
                    flex: 1,
                    display: "flex",
                    flexDirection: "column",
                    overflow: tab === "info" ? "auto" : "hidden",
                }}
            >
                {tab === "info" && <TabInfo />}
                {tab === "qcm" && <TabQcm />}
                {tab === "fichiers" && <TabFichiers />}
            </div>
        </div>
    )
}

const PANEL_ROOT: React.CSSProperties = {
    width: "100%",
    height: "100%",
    display: "flex",
    flexDirection: "column",
    fontFamily: "'Inter', system-ui, sans-serif",
    WebkitFontSmoothing: "antialiased",
    color: t0,
    background: "transparent",
    overflow: "hidden",
}

export function AdminUserPanel(): Override {
    return { children: <UserPanelUI /> }
}
