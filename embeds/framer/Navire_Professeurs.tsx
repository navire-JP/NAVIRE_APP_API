// Navire_Professeurs.tsx
// Section « Professeurs » de la console admin (Navire_AdminConsole.tsx).
//
// Rémunérations : tableau de pilotage de tous les professeurs, mois par mois
// (montant calculé, montant fixé à la main, versé). Aucun lien avec Stripe :
// le virement se fait à la main, puis « Marquer versé » fige le montant.
//
// Professeurs : attacher un compte NAVIRE (grade « ChargéTD » par défaut), lui
// donner un ou plusieurs groupes avec sa rémunération (par séance et par
// étudiant), suspendre, détacher. Groupes : un ou plusieurs par matière, avec
// le salon Discord et des liens ; étudiants du groupe et changement de groupe.
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

// ── Design tokens (identiques à la console) ──
const mono = "'IBM Plex Mono', monospace"
const b0 = "rgba(255,255,255,0.07)"
const b1 = "rgba(255,255,255,0.14)"
const b2 = "rgba(255,255,255,0.28)"
const t0 = "#f0f0f0"
const t1 = "#777"
const t2 = "#333"
const s0 = "rgba(255,255,255,0.03)"
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
        bgH = "#ddd"
        clr = "#000"
        bdr = "#fff"
    } else if (danger) {
        bg = "rgba(120,0,0,0.08)"
        bgH = "rgba(150,0,0,0.14)"
        clr = "#c55"
        bdr = "rgba(120,0,0,0.3)"
    }
    return (
        <button
            onClick={onClick}
            disabled={disabled}
            onMouseEnter={() => setH(true)}
            onMouseLeave={() => setH(false)}
            style={{
                padding: "5px 10px",
                borderRadius: 4,
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
                borderRadius: 3,
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
                fontSize: 9,
                fontFamily: mono,
                letterSpacing: 0.6,
                padding: "2px 7px",
                borderRadius: 3,
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

const VERT = { color: "#4ECDC4", border: "rgba(78,205,196,0.4)" }
const JAUNE = { color: "#FFD166", border: "rgba(255,209,102,0.35)" }
const ROUGE = { color: "#c55", border: "rgba(120,0,0,0.4)" }
const GRIS = { color: t1, border: b0 }

function Msg({ text }: { text: string }) {
    if (!text) return null
    const err = text.startsWith("ERR")
    return (
        <div
            style={{
                fontSize: 10,
                fontFamily: mono,
                color: err ? "#c55" : "#5a5",
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
                borderRadius: 4,
                minWidth: 0,
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
                    fontSize: 8,
                    fontFamily: mono,
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
                    borderRadius: 4,
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
    borderRadius: 4,
    color: t0,
    fontSize: 10,
    fontFamily: mono,
    outline: "none",
    boxSizing: "border-box",
}


// ── Types ──
type Vue = "professeurs" | "groupes" | "remunerations"

interface LigneRemu {
    affectation_id: number
    professeur_id: number
    professeur: string
    email: string | null
    professeur_status: string
    classe: string
    matiere_key: string
    nb_etudiants: number
    mois: string
    seances: number
    calcule_cents: number
    fixe_cents: number | null
    montant_cents: number
    note: string
    verse_le: string | null
    reference: string | null
}

interface Lien {
    label: string
    url: string
}
interface Groupe {
    id: number
    matiere_key: string
    matiere_label: string
    niveau: string
    nom: string
    discord_url: string
    liens: Lien[]
    is_active: boolean
    nb_etudiants?: number
    professeurs?: { affectation_id: number; professeur_id: number; nom: string }[]
}
interface Previsionnel {
    remu_seance_cents: number
    remu_etudiant_cents: number
    par_seance_cents: number
    seances_passees: number
    seances_a_venir: number
    realise_cents: number
    a_venir_cents: number
    total_cents: number
}
interface Affectation {
    id: number
    classe: Groupe
    nb_etudiants: number
    previsionnel: Previsionnel
}
interface Professeur {
    id: number
    user_id: number
    username: string | null
    email: string | null
    prenom: string
    nom: string
    grade: string
    status: "active" | "suspendu"
    created_at: string | null
    affectations: Affectation[]
}
interface Etudiant {
    enrollment_id: number
    user_id: number | null
    username: string | null
    email: string | null
    university: string | null
    discord_lie: boolean
    status: string
    inscrit_le: string | null
}

const MATIERES = Object.keys(MATIERE_LABELS).filter((k) => !k.startsWith("M1"))

function versCentimes(v: string): number {
    const n = parseFloat(String(v).replace(",", "."))
    return isFinite(n) && n >= 0 ? Math.round(n * 100) : 0
}
function versEuros(c: number): string {
    return ((c || 0) / 100).toFixed(2).replace(".", ",")
}

function Champ({ l, children, style }: { l: string; children: React.ReactNode; style?: any }) {
    return (
        <label style={{ display: "flex", flexDirection: "column", gap: 3, minWidth: 0, ...style }}>
            <span
                style={{
                    fontSize: 8,
                    fontFamily: mono,
                    color: t2,
                    textTransform: "uppercase" as const,
                    letterSpacing: 0.8,
                }}
            >
                {l}
            </span>
            {children}
        </label>
    )
}

// ── Professeur ──

function ProfesseurRow({
    p,
    groupes,
    onChanged,
    setMsg,
}: {
    p: Professeur
    groupes: Groupe[]
    onChanged: () => void
    setMsg: (m: string) => void
}) {
    const [open, setOpen] = React.useState(false)
    const [hov, setHov] = React.useState(false)
    const [busy, setBusy] = React.useState(false)
    const [prenom, setPrenom] = React.useState(p.prenom)
    const [nom, setNom] = React.useState(p.nom)
    const [grade, setGrade] = React.useState(p.grade)
    const [ajout, setAjout] = React.useState("")
    const [remu, setRemu] = React.useState<Record<number, { s: string; e: string }>>(() =>
        Object.fromEntries(
            p.affectations.map((a) => [
                a.id,
                {
                    s: versEuros(a.previsionnel.remu_seance_cents),
                    e: versEuros(a.previsionnel.remu_etudiant_cents),
                },
            ])
        )
    )

    async function appel(chemin: string, method: string, corps: any, ok: string) {
        setBusy(true)
        try {
            await api(chemin, method, corps)
            setMsg(`✓ ${ok}`)
            onChanged()
        } catch (e: any) {
            setMsg(`ERR: ${e.message}`)
        } finally {
            setBusy(false)
        }
    }

    const affiche = `${p.prenom} ${p.nom}`.trim() || p.username || "—"
    const total = p.affectations.reduce((t, a) => t + a.previsionnel.total_cents, 0)
    const dejaPris = new Set(p.affectations.map((a) => a.classe.id))
    const disponibles = groupes.filter((g) => g.is_active && !dejaPris.has(g.id))

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
                        {affiche}
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
                        {p.email} ·{" "}
                        {p.affectations.map((a) => MATIERE_LABELS[a.classe.matiere_key] || a.classe.nom).join(", ") ||
                            "aucun groupe"}
                    </div>
                </div>
                <span style={{ fontSize: 10, fontFamily: mono, color: t1, flexShrink: 0 }} title="Prévisionnel total">
                    {euros(total)}
                </span>
                <Tag color="#7AA7FF" border="rgba(122,167,255,0.35)">
                    {p.grade}
                </Tag>
                {p.status === "suspendu" && (
                    <Tag color={ROUGE.color} border={ROUGE.border}>
                        SUSPENDU
                    </Tag>
                )}
                <span style={{ fontSize: 11, color: open ? t0 : t2, flexShrink: 0 }}>›</span>
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
                    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr auto", gap: 6, alignItems: "end" }}>
                        <Champ l="Prénom">
                            <input value={prenom} onChange={(e) => setPrenom(e.target.value)} style={INPUT} />
                        </Champ>
                        <Champ l="Nom">
                            <input value={nom} onChange={(e) => setNom(e.target.value)} style={INPUT} />
                        </Champ>
                        <Champ l="Grade">
                            <input value={grade} onChange={(e) => setGrade(e.target.value)} style={INPUT} />
                        </Champ>
                        <Btn
                            disabled={busy}
                            onClick={() =>
                                appel(`/professeurs/admin/${p.id}`, "PATCH", { prenom, nom, grade }, `${affiche} enregistré`)
                            }
                        >
                            Enregistrer
                        </Btn>
                    </div>

                    <div
                        style={{
                            fontSize: 8,
                            fontFamily: mono,
                            color: t2,
                            textTransform: "uppercase" as const,
                            letterSpacing: 0.8,
                            marginTop: 4,
                        }}
                    >
                        Groupes et taux du prévisionnel (€ par séance + € par étudiant et par séance) · montants du mois ajustables dans « Rémunérations »
                    </div>
                    {p.affectations.length === 0 && (
                        <div style={{ fontSize: 10, fontFamily: mono, color: t1 }}>Aucun groupe.</div>
                    )}
                    {p.affectations.map((a) => {
                        const pr = a.previsionnel
                        const r = remu[a.id] || {
                            s: versEuros(pr.remu_seance_cents),
                            e: versEuros(pr.remu_etudiant_cents),
                        }
                        return (
                            <div
                                key={a.id}
                                style={{
                                    display: "flex",
                                    alignItems: "center",
                                    gap: 6,
                                    padding: "6px 8px",
                                    border: `1px solid ${b0}`,
                                    borderRadius: 4,
                                    flexWrap: "wrap" as const,
                                }}
                            >
                                <div style={{ flex: 1, minWidth: 120 }}>
                                    <div style={{ fontSize: 11, color: t0 }}>{a.classe.nom}</div>
                                    <div style={{ fontSize: 9, fontFamily: mono, color: t1 }}>
                                        {a.nb_etudiants} étud. · {pr.seances_passees}+{pr.seances_a_venir} séances · fait{" "}
                                        {euros(pr.realise_cents)} · à venir {euros(pr.a_venir_cents)}
                                        {a.classe.is_active ? "" : " · groupe désactivé"}
                                    </div>
                                </div>
                                <input
                                    value={r.s}
                                    title="€ par séance"
                                    onChange={(e) => setRemu({ ...remu, [a.id]: { ...r, s: e.target.value } })}
                                    style={{ ...INPUT, width: 56 }}
                                />
                                <span style={{ fontSize: 10, color: t1 }}>+</span>
                                <input
                                    value={r.e}
                                    title="€ par étudiant et par séance"
                                    onChange={(e) => setRemu({ ...remu, [a.id]: { ...r, e: e.target.value } })}
                                    style={{ ...INPUT, width: 56 }}
                                />
                                <span style={{ fontSize: 9, fontFamily: mono, color: t1 }}>/étud.</span>
                                <Btn
                                    disabled={busy}
                                    onClick={() =>
                                        appel(
                                            `/professeurs/admin/affectations/${a.id}`,
                                            "PATCH",
                                            {
                                                remu_seance_cents: versCentimes(r.s),
                                                remu_etudiant_cents: versCentimes(r.e),
                                            },
                                            `Rémunération · ${a.classe.nom}`
                                        )
                                    }
                                >
                                    OK
                                </Btn>
                                <Btn
                                    danger
                                    disabled={busy}
                                    onClick={() =>
                                        confirm(`Retirer ${affiche} du groupe ${a.classe.nom} ?`) &&
                                        appel(`/professeurs/admin/affectations/${a.id}`, "DELETE", undefined, `Retiré de ${a.classe.nom}`)
                                    }
                                >
                                    ✕
                                </Btn>
                            </div>
                        )
                    })}
                    <div style={{ display: "flex", gap: 6 }}>
                        <select value={ajout} onChange={(e) => setAjout(e.target.value)} style={{ ...INPUT, flex: 1 }}>
                            <option value="">+ Ajouter un groupe…</option>
                            {disponibles.map((g) => (
                                <option key={g.id} value={g.id}>
                                    {g.nom}
                                </option>
                            ))}
                        </select>
                        <Btn
                            disabled={busy || !ajout}
                            onClick={() => {
                                appel(`/professeurs/admin/${p.id}/affectations`, "POST", { classe_id: Number(ajout) }, "Groupe ajouté")
                                setAjout("")
                            }}
                        >
                            Ajouter
                        </Btn>
                    </div>

                    <div style={{ display: "flex", gap: 6, marginTop: 4 }}>
                        {p.status === "active" ? (
                            <Btn
                                disabled={busy}
                                onClick={() =>
                                    appel(`/professeurs/admin/${p.id}`, "PATCH", { status: "suspendu" }, `${affiche} suspendu`)
                                }
                            >
                                Suspendre l'accès
                            </Btn>
                        ) : (
                            <Btn
                                primary
                                disabled={busy}
                                onClick={() =>
                                    appel(`/professeurs/admin/${p.id}`, "PATCH", { status: "active" }, `${affiche} réactivé`)
                                }
                            >
                                Réactiver
                            </Btn>
                        )}
                        <Btn
                            danger
                            disabled={busy}
                            onClick={() =>
                                confirm(`Détacher ${affiche} ? Ses documents restent aux étudiants.`) &&
                                appel(`/professeurs/admin/${p.id}`, "DELETE", undefined, `${affiche} détaché`)
                            }
                        >
                            Détacher
                        </Btn>
                    </div>
                </div>
            )}
        </div>
    )
}

function NouveauProfesseur({
    groupes,
    onCreated,
    setMsg,
}: {
    groupes: Groupe[]
    onCreated: () => void
    setMsg: (m: string) => void
}) {
    const [email, setEmail] = React.useState("")
    const [prenom, setPrenom] = React.useState("")
    const [nom, setNom] = React.useState("")
    const [grade, setGrade] = React.useState("ChargéTD")
    const [sel, setSel] = React.useState<number[]>([])
    const [busy, setBusy] = React.useState(false)

    async function creer() {
        setBusy(true)
        try {
            await api("/professeurs/admin", "POST", { email, prenom, nom, grade, classe_ids: sel })
            setMsg(`✓ ${email} attaché comme ${grade}`)
            setEmail("")
            setPrenom("")
            setNom("")
            setSel([])
            onCreated()
        } catch (e: any) {
            setMsg(`ERR: ${e.message}`)
        } finally {
            setBusy(false)
        }
    }

    return (
        <div
            style={{
                padding: "10px 14px",
                borderBottom: `1px solid ${b0}`,
                display: "flex",
                flexDirection: "column",
                gap: 6,
                flexShrink: 0,
            }}
        >
            <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr 1fr 1fr", gap: 6 }}>
                <Champ l="Email du compte">
                    <input value={email} onChange={(e) => setEmail(e.target.value)} placeholder="prof@…" style={INPUT} />
                </Champ>
                <Champ l="Prénom">
                    <input value={prenom} onChange={(e) => setPrenom(e.target.value)} style={INPUT} />
                </Champ>
                <Champ l="Nom">
                    <input value={nom} onChange={(e) => setNom(e.target.value)} style={INPUT} />
                </Champ>
                <Champ l="Grade">
                    <input value={grade} onChange={(e) => setGrade(e.target.value)} style={INPUT} />
                </Champ>
            </div>
            <div style={{ display: "flex", flexWrap: "wrap" as const, gap: 4 }}>
                {groupes
                    .filter((g) => g.is_active)
                    .map((g) => (
                        <MiniBtn
                            key={g.id}
                            actif={sel.includes(g.id)}
                            onClick={() => setSel(sel.includes(g.id) ? sel.filter((x) => x !== g.id) : [...sel, g.id])}
                        >
                            {g.nom}
                        </MiniBtn>
                    ))}
            </div>
            <div>
                <Btn primary disabled={busy || !email.includes("@")} onClick={creer}>
                    Attacher le professeur
                </Btn>
            </div>
        </div>
    )
}

// ── Groupe ──

function GroupeRow({
    g,
    onChanged,
    setMsg,
}: {
    g: Groupe
    onChanged: () => void
    setMsg: (m: string) => void
}) {
    const [open, setOpen] = React.useState(false)
    const [hov, setHov] = React.useState(false)
    const [busy, setBusy] = React.useState(false)
    const [nom, setNom] = React.useState(g.nom)
    const [discord, setDiscord] = React.useState(g.discord_url)
    const [liens, setLiens] = React.useState<Lien[]>(g.liens || [])
    const [etudiants, setEtudiants] = React.useState<Etudiant[] | null>(null)
    const [autres, setAutres] = React.useState<{ id: number; nom: string }[]>([])

    function chargerEtudiants() {
        api(`/professeurs/admin/classes/${g.id}/etudiants`)
            .then((d) => {
                setEtudiants(d?.items || [])
                setAutres(d?.groupes || [])
            })
            .catch(() => setEtudiants([]))
    }
    React.useEffect(() => {
        if (open && etudiants === null) chargerEtudiants()
    }, [open])

    async function appel(chemin: string, method: string, corps: any, ok: string) {
        setBusy(true)
        try {
            await api(chemin, method, corps)
            setMsg(`✓ ${ok}`)
            onChanged()
        } catch (e: any) {
            setMsg(`ERR: ${e.message}`)
        } finally {
            setBusy(false)
        }
    }

    async function deplacer(e: Etudiant, classeId: number) {
        try {
            await api(`/professeurs/admin/inscriptions/${e.enrollment_id}`, "PATCH", { classe_id: classeId })
            setMsg(`✓ ${e.username || e.email} changé de groupe`)
            chargerEtudiants()
            onChanged()
        } catch (x: any) {
            setMsg(`ERR: ${x.message}`)
        }
    }

    return (
        <div style={{ borderBottom: `1px solid rgba(255,255,255,0.04)`, opacity: g.is_active ? 1 : 0.55 }}>
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
                        {g.nom}
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
                        {(g.professeurs || []).map((p) => p.nom).join(", ") || "aucun professeur"}
                        {g.discord_url ? " · Discord ✓" : " · pas de Discord"}
                    </div>
                </div>
                <span style={{ fontSize: 10, fontFamily: mono, color: t1, flexShrink: 0 }}>
                    {g.nb_etudiants ?? 0} étud.
                </span>
                <Tag color={t1} border={b0}>
                    {MATIERE_LABELS[g.matiere_key] || g.matiere_key}
                </Tag>
                {!g.is_active && (
                    <Tag color={GRIS.color} border={GRIS.border}>
                        INACTIF
                    </Tag>
                )}
                <span style={{ fontSize: 11, color: open ? t0 : t2, flexShrink: 0 }}>›</span>
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
                    <div style={{ display: "grid", gridTemplateColumns: "1fr 2fr", gap: 6 }}>
                        <Champ l="Nom du groupe">
                            <input value={nom} onChange={(e) => setNom(e.target.value)} style={INPUT} />
                        </Champ>
                        <Champ l="Salon Discord">
                            <input
                                value={discord}
                                onChange={(e) => setDiscord(e.target.value)}
                                placeholder="https://discord.com/channels/…"
                                style={INPUT}
                            />
                        </Champ>
                    </div>
                    <Champ l="Liens">
                        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                            {liens.map((l, i) => (
                                <div key={i} style={{ display: "flex", gap: 4 }}>
                                    <input
                                        value={l.label}
                                        placeholder="Libellé"
                                        onChange={(e) =>
                                            setLiens(liens.map((x, j) => (j === i ? { ...x, label: e.target.value } : x)))
                                        }
                                        style={{ ...INPUT, width: 110 }}
                                    />
                                    <input
                                        value={l.url}
                                        placeholder="https://…"
                                        onChange={(e) =>
                                            setLiens(liens.map((x, j) => (j === i ? { ...x, url: e.target.value } : x)))
                                        }
                                        style={{ ...INPUT, flex: 1 }}
                                    />
                                    <Btn danger onClick={() => setLiens(liens.filter((_, j) => j !== i))}>
                                        ✕
                                    </Btn>
                                </div>
                            ))}
                            <div>
                                <Btn onClick={() => setLiens([...liens, { label: "", url: "" }])}>+ Lien</Btn>
                            </div>
                        </div>
                    </Champ>
                    <div style={{ display: "flex", gap: 6 }}>
                        <Btn
                            primary
                            disabled={busy}
                            onClick={() =>
                                appel(
                                    `/professeurs/admin/classes/${g.id}`,
                                    "PATCH",
                                    {
                                        nom,
                                        discord_url: discord,
                                        liens: liens.filter((l) => l.label.trim() && l.url.trim()),
                                    },
                                    `${nom} enregistré`
                                )
                            }
                        >
                            Enregistrer
                        </Btn>
                        <Btn
                            disabled={busy}
                            onClick={() =>
                                appel(
                                    `/professeurs/admin/classes/${g.id}`,
                                    "PATCH",
                                    { is_active: !g.is_active },
                                    g.is_active ? `${g.nom} désactivé` : `${g.nom} réactivé`
                                )
                            }
                        >
                            {g.is_active ? "Désactiver" : "Réactiver"}
                        </Btn>
                        <Btn
                            danger
                            disabled={busy}
                            onClick={() =>
                                confirm(`Supprimer le groupe ${g.nom} ?`) &&
                                appel(`/professeurs/admin/classes/${g.id}`, "DELETE", undefined, `${g.nom} supprimé`)
                            }
                        >
                            Supprimer
                        </Btn>
                    </div>

                    <div
                        style={{
                            fontSize: 8,
                            fontFamily: mono,
                            color: t2,
                            textTransform: "uppercase" as const,
                            letterSpacing: 0.8,
                            marginTop: 4,
                        }}
                    >
                        Étudiants {etudiants ? `(${etudiants.length})` : ""}
                    </div>
                    {etudiants === null ? (
                        <div style={{ fontSize: 10, fontFamily: mono, color: t1 }}>Chargement…</div>
                    ) : etudiants.length === 0 ? (
                        <div style={{ fontSize: 10, fontFamily: mono, color: t1 }}>
                            {g.is_active ? "Aucun étudiant." : "Groupe inactif : ses étudiants sont dans le premier groupe actif."}
                        </div>
                    ) : (
                        etudiants.map((e) => (
                            <div
                                key={e.enrollment_id}
                                style={{
                                    display: "flex",
                                    alignItems: "center",
                                    gap: 6,
                                    fontSize: 10,
                                    fontFamily: mono,
                                    color: t0,
                                }}
                            >
                                <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" as const }}>
                                    {e.username || "—"} · <span style={{ color: t1 }}>{e.email}</span>
                                </span>
                                {e.status === "payment_failed" && (
                                    <Tag color={JAUNE.color} border={JAUNE.border}>
                                        IMPAYÉ
                                    </Tag>
                                )}
                                {autres.length > 1 && (
                                    <select
                                        value={g.id}
                                        onChange={(x) => deplacer(e, Number(x.target.value))}
                                        style={{ ...INPUT, height: 22, width: 130 }}
                                    >
                                        {autres.map((o) => (
                                            <option key={o.id} value={o.id}>
                                                {o.nom}
                                            </option>
                                        ))}
                                    </select>
                                )}
                            </div>
                        ))
                    )}
                </div>
            )}
        </div>
    )
}

function NouveauGroupe({ onCreated, setMsg }: { onCreated: () => void; setMsg: (m: string) => void }) {
    const [matiere, setMatiere] = React.useState(MATIERES[0])
    const [nom, setNom] = React.useState("")
    const [discord, setDiscord] = React.useState("")
    const [busy, setBusy] = React.useState(false)

    async function creer() {
        setBusy(true)
        try {
            await api("/professeurs/admin/classes", "POST", { matiere_key: matiere, nom, discord_url: discord })
            setMsg(`✓ Groupe ${nom} créé`)
            setNom("")
            setDiscord("")
            onCreated()
        } catch (e: any) {
            setMsg(`ERR: ${e.message}`)
        } finally {
            setBusy(false)
        }
    }

    return (
        <div
            style={{
                padding: "10px 14px",
                borderBottom: `1px solid ${b0}`,
                display: "grid",
                gridTemplateColumns: "1fr 1fr 2fr auto",
                gap: 6,
                alignItems: "end",
                flexShrink: 0,
            }}
        >
            <Champ l="Matière">
                <select value={matiere} onChange={(e) => setMatiere(e.target.value)} style={INPUT}>
                    {MATIERES.map((k) => (
                        <option key={k} value={k}>
                            {MATIERE_LABELS[k]}
                        </option>
                    ))}
                </select>
            </Champ>
            <Champ l="Nom">
                <input value={nom} onChange={(e) => setNom(e.target.value)} placeholder="L2 Pénal — gr. 2" style={INPUT} />
            </Champ>
            <Champ l="Salon Discord">
                <input
                    value={discord}
                    onChange={(e) => setDiscord(e.target.value)}
                    placeholder="https://discord.com/channels/…"
                    style={INPUT}
                />
            </Champ>
            <Btn primary disabled={busy || nom.trim().length < 2} onClick={creer}>
                Créer
            </Btn>
        </div>
    )
}

// ── Rémunérations (pilotage, virements manuels) ──

function moisFr(cle: string): string {
    const [a, m] = cle.split("-").map(Number)
    return new Date(a, m - 1, 1).toLocaleDateString("fr-FR", { month: "short", year: "2-digit" })
}
function moisCourant(): string {
    const d = new Date()
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`
}

function LigneRemuRow({
    l,
    coche,
    onCoche,
    onChanged,
    setMsg,
}: {
    l: LigneRemu
    coche: boolean
    onCoche: () => void
    onChanged: () => void
    setMsg: (m: string) => void
}) {
    const [montant, setMontant] = React.useState(l.fixe_cents === null ? "" : versEuros(l.fixe_cents))
    const [note, setNote] = React.useState(l.note)
    const [busy, setBusy] = React.useState(false)
    React.useEffect(() => {
        setMontant(l.fixe_cents === null ? "" : versEuros(l.fixe_cents))
        setNote(l.note)
    }, [l.fixe_cents, l.note])

    const modifie = montant !== (l.fixe_cents === null ? "" : versEuros(l.fixe_cents)) || note !== l.note

    async function appel(chemin: string, method: string, corps: any, ok: string) {
        setBusy(true)
        try {
            await api(chemin, method, corps)
            setMsg(`✓ ${ok}`)
            onChanged()
        } catch (e: any) {
            setMsg(`ERR: ${e.message}`)
        } finally {
            setBusy(false)
        }
    }

    const qui = `${l.professeur} · ${l.classe} · ${moisFr(l.mois)}`
    return (
        <div
            style={{
                display: "grid",
                gridTemplateColumns: "16px 1fr 62px 70px 62px 56px",
                gap: 6,
                alignItems: "center",
                padding: "6px 14px",
                borderBottom: `1px solid rgba(255,255,255,0.04)`,
                fontSize: 10,
                fontFamily: mono,
                color: t0,
            }}
        >
            <input type="checkbox" checked={coche} disabled={!!l.verse_le} onChange={onCoche} />
            <div style={{ minWidth: 0 }}>
                <div style={{ fontSize: 11, fontFamily: "inherit", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" as const }}>
                    {l.professeur}
                    <span style={{ color: t1 }}> · {MATIERE_LABELS[l.matiere_key] || l.classe}</span>
                </div>
                <div style={{ display: "flex", gap: 4, alignItems: "center", marginTop: 2 }}>
                    <span style={{ color: t1, flexShrink: 0 }}>
                        {moisFr(l.mois)} · {l.seances} séance(s) · {l.nb_etudiants} étud.
                    </span>
                    <input
                        value={note}
                        disabled={busy}
                        onChange={(e) => setNote(e.target.value)}
                        placeholder="note…"
                        style={{ ...INPUT, height: 20, flex: 1, minWidth: 40, fontSize: 9 }}
                    />
                </div>
            </div>
            <span style={{ color: t1, textAlign: "right" as const }} title="Montant calculé">
                {euros(l.calcule_cents)}
            </span>
            <input
                value={montant}
                disabled={busy || !!l.verse_le}
                onChange={(e) => setMontant(e.target.value)}
                placeholder="calculé"
                title="Montant fixé (vide = montant calculé)"
                style={{ ...INPUT, width: "100%", textAlign: "right" as const, borderColor: l.fixe_cents !== null ? b2 : b0 }}
            />
            <span
                style={{ textAlign: "right" as const, fontWeight: 700, color: l.verse_le ? VERT.color : t0 }}
                title={l.verse_le ? `Versé le ${fmtDate(l.verse_le)}${l.reference ? ` · ${l.reference}` : ""}` : "Montant retenu"}
            >
                {euros(l.montant_cents)}
            </span>
            {modifie ? (
                <Btn
                    primary
                    disabled={busy}
                    onClick={() =>
                        appel(
                            "/professeurs/admin/remunerations",
                            "PUT",
                            {
                                affectation_id: l.affectation_id,
                                mois: l.mois,
                                montant_cents: montant.trim() === "" ? null : versCentimes(montant),
                                note,
                            },
                            `Montant enregistré · ${qui}`
                        )
                    }
                >
                    OK
                </Btn>
            ) : l.verse_le ? (
                <Btn
                    disabled={busy}
                    onClick={() =>
                        confirm(`Annuler le versement · ${qui} ?`) &&
                        appel(
                            "/professeurs/admin/remunerations/annuler-versement",
                            "POST",
                            { affectation_id: l.affectation_id, mois: l.mois },
                            `Versement annulé · ${qui}`
                        )
                    }
                    style={{ color: VERT.color }}
                >
                    VERSÉ
                </Btn>
            ) : (
                <Tag color={l.fixe_cents !== null ? JAUNE.color : t1} border={l.fixe_cents !== null ? JAUNE.border : b0}>
                    {l.fixe_cents !== null ? "FIXÉ" : "CALC."}
                </Tag>
            )}
        </div>
    )
}

function RemunerationsPanel({ tick, setMsg }: { tick: number; setMsg: (m: string) => void }) {
    const [mois, setMois] = React.useState<string>(moisCourant())
    const [liste, setListe] = React.useState<string[]>([])
    const [items, setItems] = React.useState<LigneRemu[]>([])
    const [totaux, setTotaux] = React.useState<any>({})
    const [loading, setLoading] = React.useState(true)
    const [sel, setSel] = React.useState<string[]>([])
    const [reference, setReference] = React.useState("")
    const [busy, setBusy] = React.useState(false)
    const [version, setVersion] = React.useState(0)

    React.useEffect(() => {
        setLoading(true)
        setSel([])
        api(`/professeurs/admin/remunerations${mois ? `?mois=${mois}` : ""}`)
            .then((d) => {
                setListe(d?.mois || [])
                setItems(d?.items || [])
                setTotaux(d?.totaux || {})
            })
            .catch((e) => setMsg(`ERR: ${e.message}`))
            .finally(() => setLoading(false))
    }, [mois, tick, version])

    const recharger = () => setVersion((v) => v + 1)
    const cle = (l: LigneRemu) => `${l.affectation_id}|${l.mois}`
    const aVerser = items.filter((l) => !l.verse_le && l.montant_cents > 0)
    const totalSel = items.filter((l) => sel.includes(cle(l))).reduce((t, l) => t + l.montant_cents, 0)
    const moisAffiches = Array.from(new Set([...liste, moisCourant()])).sort()

    async function verser() {
        if (!confirm(`Confirmer le virement de ${sel.length} ligne(s) · ${euros(totalSel)} ?`)) return
        setBusy(true)
        try {
            const r = await api("/professeurs/admin/remunerations/verser", "POST", {
                lignes: sel.map((k) => {
                    const [a, m] = k.split("|")
                    return { affectation_id: Number(a), mois: m }
                }),
                reference: reference.trim() || null,
            })
            setMsg(`✓ ${r.verses.length} ligne(s) marquée(s) versée(s) · ${euros(r.total_cents)}`)
            setReference("")
            recharger()
        } catch (e: any) {
            setMsg(`ERR: ${e.message}`)
        } finally {
            setBusy(false)
        }
    }

    function exporter() {
        const lignes = [["Mois", "Professeur", "Email", "Groupe", "Séances", "Étudiants", "Calculé", "Fixé", "Retenu", "Versé le", "Référence", "Note"]]
        items.forEach((l) =>
            lignes.push([
                l.mois, l.professeur, l.email || "", l.classe, String(l.seances), String(l.nb_etudiants),
                versEuros(l.calcule_cents), l.fixe_cents === null ? "" : versEuros(l.fixe_cents),
                versEuros(l.montant_cents), l.verse_le ? fmtDate(l.verse_le) : "", l.reference || "", l.note,
            ])
        )
        const csv = lignes.map((r) => r.map((v) => `"${String(v).replace(/"/g, '""')}"`).join(";")).join("\n")
        const a = document.createElement("a")
        a.href = URL.createObjectURL(new Blob(["\ufeff" + csv], { type: "text/csv;charset=utf-8" }))
        a.download = `remunerations-professeurs-${mois || "tout"}.csv`
        a.click()
    }

    return (
        <div style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0 }}>
            <div
                style={{
                    display: "flex",
                    gap: 4,
                    padding: "6px 14px",
                    borderBottom: `1px solid ${b0}`,
                    overflowX: "auto" as const,
                    flexShrink: 0,
                }}
            >
                {moisAffiches.map((m) => (
                    <MiniBtn key={m} actif={mois === m} onClick={() => setMois(m)}>
                        {moisFr(m)}
                    </MiniBtn>
                ))}
                <MiniBtn actif={mois === ""} onClick={() => setMois("")}>
                    Tout
                </MiniBtn>
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
                    alignItems: "center",
                }}
            >
                <span>calculé {euros(totaux.calcule_cents || 0)}</span>
                <span style={{ color: t0 }}>retenu {euros(totaux.montant_cents || 0)}</span>
                <span style={{ color: (totaux.a_verser_cents || 0) > 0 ? "#FFD166" : t1 }}>
                    à verser {euros(totaux.a_verser_cents || 0)}
                </span>
                <span style={{ color: VERT.color }}>versé {euros(totaux.verse_cents || 0)}</span>
                <span style={{ flex: 1 }} />
                <Btn onClick={exporter} disabled={!items.length}>
                    CSV
                </Btn>
            </div>
            <div
                style={{
                    display: "grid",
                    gridTemplateColumns: "16px 1fr 62px 70px 62px 56px",
                    gap: 6,
                    padding: "5px 14px",
                    borderBottom: `1px solid ${b0}`,
                    fontSize: 8,
                    fontFamily: mono,
                    color: t2,
                    textTransform: "uppercase" as const,
                    letterSpacing: 0.8,
                    flexShrink: 0,
                }}
            >
                <input
                    type="checkbox"
                    checked={aVerser.length > 0 && sel.length === aVerser.length}
                    onChange={() => setSel(sel.length === aVerser.length ? [] : aVerser.map(cle))}
                />
                <span>Professeur · groupe</span>
                <span style={{ textAlign: "right" as const }}>Calculé</span>
                <span style={{ textAlign: "right" as const }}>Fixé €</span>
                <span style={{ textAlign: "right" as const }}>Retenu</span>
                <span />
            </div>
            {loading ? (
                <div style={VIDE}>Chargement…</div>
            ) : items.length === 0 ? (
                <div style={VIDE}>Rien à rémunérer sur ce mois.</div>
            ) : (
                <div style={{ flex: 1, overflowY: "auto" }}>
                    {items.map((l) => (
                        <LigneRemuRow
                            key={cle(l)}
                            l={l}
                            coche={sel.includes(cle(l))}
                            onCoche={() => setSel(sel.includes(cle(l)) ? sel.filter((x) => x !== cle(l)) : [...sel, cle(l)])}
                            onChanged={recharger}
                            setMsg={setMsg}
                        />
                    ))}
                </div>
            )}
            {sel.length > 0 && (
                <div
                    style={{
                        display: "flex",
                        gap: 6,
                        padding: "8px 14px",
                        borderTop: `1px solid ${b0}`,
                        alignItems: "center",
                        flexShrink: 0,
                    }}
                >
                    <span style={{ fontSize: 10, fontFamily: mono, color: t0 }}>
                        {sel.length} ligne(s) · {euros(totalSel)}
                    </span>
                    <input
                        value={reference}
                        onChange={(e) => setReference(e.target.value)}
                        placeholder="Référence du virement"
                        style={{ ...INPUT, flex: 1 }}
                    />
                    <Btn primary disabled={busy} onClick={verser}>
                        Marquer versé
                    </Btn>
                </div>
            )}
        </div>
    )
}

// ── Section ──

export function ProfesseursSection() {
    const [vue, setVue] = React.useState<Vue>("professeurs")
    const [profs, setProfs] = React.useState<Professeur[]>([])
    const [groupes, setGroupes] = React.useState<Groupe[]>([])
    const [loading, setLoading] = React.useState(true)
    const [search, setSearch] = React.useState("")
    const [ajout, setAjout] = React.useState(false)
    const [msg, setMsg] = React.useState("")
    const [tick, setTick] = React.useState(0)

    React.useEffect(() => {
        setLoading(true)
        Promise.all([
            api("/professeurs/admin/list").then((d) => setProfs(d?.items || [])),
            api("/professeurs/admin/classes").then((d) => setGroupes(d?.items || [])),
        ])
            .catch((e) => setMsg(`ERR: ${e.message}`))
            .finally(() => setLoading(false))
    }, [tick])

    const recharger = () => setTick((t) => t + 1)
    const q = search.toLowerCase()
    const profsVisibles = profs.filter(
        (p) => !q || [p.prenom, p.nom, p.email || "", p.username || "", p.grade].some((v) => v.toLowerCase().includes(q))
    )
    const groupesVisibles = groupes.filter(
        (g) => !q || [g.nom, g.matiere_label, MATIERE_LABELS[g.matiere_key] || ""].some((v) => v.toLowerCase().includes(q))
    )
    const totalPrevu = profs.reduce(
        (t, p) => t + p.affectations.reduce((s, a) => s + a.previsionnel.total_cents, 0),
        0
    )

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
                    <MiniBtn actif={vue === "professeurs"} onClick={() => setVue("professeurs")}>
                        Professeurs ({profs.length})
                    </MiniBtn>
                    <MiniBtn actif={vue === "groupes"} onClick={() => setVue("groupes")}>
                        Groupes ({groupes.length})
                    </MiniBtn>
                    <MiniBtn actif={vue === "remunerations"} onClick={() => setVue("remunerations")}>
                        Rémunérations
                    </MiniBtn>
                </span>
                <span style={{ display: "flex", gap: 6, alignItems: "center" }}>
                    {vue !== "remunerations" && (
                        <input
                            value={search}
                            onChange={(e) => setSearch(e.target.value)}
                            placeholder="Rechercher…"
                            style={{ ...INPUT, width: 110, height: 28, fontSize: 12 }}
                        />
                    )}
                    {vue !== "remunerations" && (
                        <Btn onClick={() => setAjout((v) => !v)} style={{ fontSize: 10, padding: "4px 10px" }}>
                            {ajout ? "×" : "+"}
                        </Btn>
                    )}
                    <Btn onClick={recharger} style={{ fontSize: 10, padding: "4px 10px" }}>
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
                <span>{profs.filter((p) => p.status === "active").length} actif(s)</span>
                <span>{groupes.filter((g) => g.is_active).length} groupe(s) actif(s)</span>
                <span>{groupes.reduce((t, g) => t + (g.nb_etudiants || 0), 0)} étudiant(s)</span>
                <span>prévu {euros(totalPrevu)}</span>
            </div>

            <Msg text={msg} />

            {vue === "remunerations" ? (
                <RemunerationsPanel tick={tick} setMsg={setMsg} />
            ) : ajout &&
                (vue === "professeurs" ? (
                    <NouveauProfesseur groupes={groupes} onCreated={recharger} setMsg={setMsg} />
                ) : (
                    <NouveauGroupe onCreated={recharger} setMsg={setMsg} />
                ))}

            {vue === "remunerations" ? null : loading ? (
                <div style={VIDE}>Chargement…</div>
            ) : vue === "professeurs" ? (
                profsVisibles.length === 0 ? (
                    <div style={VIDE}>Aucun professeur. « + » pour en attacher un.</div>
                ) : (
                    <div style={{ flex: 1, overflowY: "auto" }}>
                        {profsVisibles.map((p) => (
                            <ProfesseurRow
                                key={p.id}
                                p={p}
                                groupes={groupes}
                                setMsg={setMsg}
                                onChanged={recharger}
                            />
                        ))}
                    </div>
                )
            ) : groupesVisibles.length === 0 ? (
                <div style={VIDE}>Aucun groupe.</div>
            ) : (
                <div style={{ flex: 1, overflowY: "auto" }}>
                    {groupesVisibles.map((g) => (
                        <GroupeRow key={g.id} g={g} setMsg={setMsg} onChanged={recharger} />
                    ))}
                </div>
            )}
        </div>
    )
}
