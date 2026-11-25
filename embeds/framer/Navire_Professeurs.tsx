// Navire_Professeurs.tsx
// Section « Prof. » de la console admin (Navire_AdminConsole.tsx).
//
// Professeurs : attacher un compte NAVIRE (grade « ChargéTD » par défaut), lui
// donner un ou plusieurs groupes avec ses taux de prévisionnel (€ par séance +
// € par étudiant et par séance), suspendre, détacher.
// Groupes : un ou plusieurs par matière, avec le salon Discord et des liens ;
// étudiants du groupe et changement de groupe.
// Cours : les cours du mois (lancés depuis Discord ou ajoutés ici), la
// présence relevée dans le salon vocal du groupe, corrigeable sans limite de
// temps (sauf mois versé) ; « + Cours oublié » crée un cours passé.
// Fiches : fiches publiques de la page Professeurs (/professeurs#slug). Le
// professeur la remplit et la soumet ; ici on compare version en ligne et
// brouillon, on publie ou on refuse avec un motif, on pose le badge
// « Vérifié », on masque, on règle l'ordre du catalogue.
// Rémunérations : tableau de pilotage, mois par mois (montant calculé, montant
// fixé à la main, versé), et « Détail » : séance par séance, élèves, extras,
// encaissé, part professeur et part NAVIRE au centime. Aucun lien avec Stripe : le virement se fait à la
// main, puis « Marquer versé » fige le montant.
//
// Mêmes jeton, design tokens et primitives que la console (FL, TI, SI, Btn,
// SM, SBar, Tag, MiniBtn) : le fichier est autonome pour ne pas dépendre de
// l'ordre des exports de la console.

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
            ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
        },
        body: body !== undefined ? JSON.stringify(body) : undefined,
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
            (Array.isArray(d) ? d.map((x: any) => String(x.msg || "").replace(/^Value error, /, "")).join(" · ") : d) ||
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
const OK = "#5BE3A3"
const WARN = "#FFD166"
const BAD = "#FF6B6B"
const BLUE = "#7AA7FF"
const FIELD = "#101018"

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
const MATIERES = Object.keys(MATIERE_LABELS).filter((k) => !k.startsWith("M1"))

// ── Formats ──
// 128500 → « 1 285,00 € » (espaces insécables).
function euros(c: number): string {
    const [e, d] = (Math.abs(c || 0) / 100).toFixed(2).split(".")
    return `${c < 0 ? "−" : ""}${e.replace(/\B(?=(\d{3})+(?!\d))/g, "\u202f")},${d}\u00a0€`
}
function heure(s: string | null): string {
    return s ? new Date(s).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" }) : "—"
}
function jour(s: string): string {
    return new Date(s).toLocaleDateString("fr-FR", { weekday: "short", day: "2-digit", month: "short" })
}
function versEuros(c: number): string {
    return ((c || 0) / 100).toFixed(2).replace(".", ",")
}
function versCentimes(v: string): number {
    const n = parseFloat(String(v).replace(",", "."))
    return isFinite(n) && n >= 0 ? Math.round(n * 100) : 0
}
function fmtDate(s: string | null): string {
    if (!s) return "—"
    return new Date(s).toLocaleDateString("fr-FR", { day: "2-digit", month: "short", year: "2-digit" })
}
function moisFr(cle: string): string {
    const [a, m] = cle.split("-").map(Number)
    return new Date(a, m - 1, 1).toLocaleDateString("fr-FR", { month: "long", year: "numeric" })
}
function moisCourt(cle: string): string {
    const [a, m] = cle.split("-").map(Number)
    return new Date(a, m - 1, 1).toLocaleDateString("fr-FR", { month: "short", year: "2-digit" })
}
function moisCourant(): string {
    const d = new Date()
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`
}

// ── Primitives (copies de la console) ──

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

function TI({ value, onChange, placeholder, type = "text", style, disabled, title }: any) {
    return (
        <input
            type={type}
            value={value}
            onChange={onChange}
            placeholder={placeholder}
            disabled={disabled}
            title={title}
            style={{
                width: "100%",
                height: 32,
                padding: "0 10px",
                background: s0,
                border: `1px solid ${b0}`,
                borderRadius: 10,
                color: t0,
                fontSize: 12,
                fontFamily: mono,
                outline: "none",
                boxSizing: "border-box" as const,
                opacity: disabled ? 0.55 : 1,
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

function SBar({ left, right }: { left: React.ReactNode; right?: React.ReactNode }) {
    return (
        <div
            style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                padding: "11px 14px",
                borderBottom: `1px solid ${b0}`,
                flexShrink: 0,
                gap: 8,
            }}
        >
            <span style={{ fontSize: 11, fontFamily: mono, color: t1 }}>{left}</span>
            {right}
        </div>
    )
}

function Tag({ children, color, border }: { children: React.ReactNode; color: string; border: string }) {
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

// Case d'information (même rendu que la fiche élève de la console).
function Case({ l, v, color }: { l: string; v: React.ReactNode; color?: string }) {
    return (
        <div style={{ padding: "6px 8px", border: `1px solid ${b0}`, borderRadius: 10, minWidth: 0 }}>
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
                    fontSize: 12,
                    fontFamily: mono,
                    fontWeight: 600,
                    color: color || t0,
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap" as const,
                }}
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

const TITRE: React.CSSProperties = {
    fontSize: 13,
    fontWeight: 500,
    color: t0,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
}
const SOUS_TITRE: React.CSSProperties = {
    fontSize: 10,
    fontFamily: mono,
    color: t1,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
    marginTop: 1,
}
const DEPLIE: React.CSSProperties = {
    padding: "10px 14px 14px",
    background: "rgba(255,255,255,0.015)",
    display: "flex",
    flexDirection: "column",
    gap: 10,
}

// Ligne cliquable qui se déplie (même rendu que les élèves AdJuris).
function Ligne({
    open,
    onToggle,
    children,
    faded,
}: {
    open: boolean
    onToggle: () => void
    children: React.ReactNode
    faded?: boolean
}) {
    const [hov, setHov] = React.useState(false)
    return (
        <div
            onClick={onToggle}
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
                opacity: faded ? 0.5 : 1,
            }}
        >
            {children}
            <span style={{ fontSize: 11, color: open ? t0 : t2, flexShrink: 0 }}>›</span>
        </div>
    )
}

// ── Types ──
type Vue = "professeurs" | "groupes" | "cours" | "fiches" | "remunerations"

interface CoursRow {
    id: number
    classe_id: number
    groupe: string
    matiere_key: string
    matiere_label: string
    professeur_id: number
    professeur: string
    debut: string
    fin: string
    statut: string
    duree_comptee_minutes: number
    cree_par_admin: boolean
    vocal: boolean
    releve: boolean
    nb_presents: number
}

interface PresenceRow {
    presence_id: number | null
    enrollment_id: number | null
    user_id: number | null
    nom: string
    minutes: number
    present: boolean
    source: string | null
    discord_relie: boolean
    non_relie?: boolean
    hors_groupe?: boolean
}

interface ContenuFiche {
    nom_complet_public: boolean
    photo_url: string | null
    statut_pro: string
    poste: string
    experience_annees: number | null
    formation: { diplome: string; etablissement: string; annee: string }[]
    description: string
    linkedin_url: string | null
}

interface Fiche {
    id: number
    professeur_id: number
    prenom: string
    nom: string
    email: string | null
    professeur_status: string | null
    etat: "brouillon" | "en_attente" | "publie" | "refuse"
    motif_refus: string
    soumis_le: string | null
    publie_le: string | null
    verifie_le: string | null
    masque: boolean
    ordre: number
    slug: string | null
    visible: boolean
    lien: string | null
    publie: ContenuFiche | null
    brouillon: ContenuFiche | null
    manques: string[]
    nom_affiche: string
    photo_url: string
    matieres: { key: string; niveau: string; nom: string }[]
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
    discord_vocal_id?: string
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
    verse_cents: number
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
    prevu_cents: number
    acquis_cents: number
    a_verser_cents: number
    clos: boolean
}

type Etat = "encaisse" | "a_venir" | "impaye" | "estime" | "offert" | "non_facture"
interface Seaux {
    encaisse: number
    a_venir: number
    impaye: number
    estime: number
}
// Encaissement d'un élève : échéancier + impayés signalés par Stripe (webhooks).
const ETATS: Record<Etat, [string, string]> = {
    encaisse: ["ENCAISSÉ", OK],
    a_venir: ["À VENIR", t1],
    impaye: ["IMPAYÉ", BAD],
    estime: ["HISTORIQUE · À VÉRIFIER", WARN],
    offert: ["OFFERT", t2],
    non_facture: ["NON FACTURÉ", t2],
}

interface SeanceDetail {
    date: string
    passee: boolean
    hors_agenda: boolean
    eleves: { enrollment_id: number; nom: string; payant: boolean; encaissement: Etat; montant_cents: number }[]
    nb_eleves: number
    part_prof_fixe_cents: number
    part_prof_eleves_cents: number
    part_prof_cents: number
    part_prof_calculee_cents: number
    reparti: boolean
    encaissement: Seaux
    encaisse_cents: number
    attendu_cents: number
    part_navire_cents: number
    cours: { id: number; debut: string; fin: string | null; duree_comptee_minutes: number; cloture_auto: boolean } | null
    extra: {
        ajoute_a: string
        eleves: { nom: string; reste: boolean; facture_cents: number; facturation: string | null; encaissement: Etat }[]
        nb_factures: number
        part_prof_cents: number
        encaissement: Seaux
    } | null
}

// Appel d'écriture : message de résultat, puis rechargement.
function useAppel(setMsg: (m: string) => void, onChanged: () => void) {
    const [busy, setBusy] = React.useState(false)
    async function appel(chemin: string, method: string, corps: any, ok: string) {
        setBusy(true)
        try {
            await api(chemin, method, corps)
            setMsg(`✓ ${ok}`)
            onChanged()
            return true
        } catch (e: any) {
            setMsg(`Erreur : ${e.message}`)
            return false
        } finally {
            setBusy(false)
        }
    }
    return { busy, appel }
}

// ══════════════════════════════════════════════════════════
// Professeurs
// ══════════════════════════════════════════════════════════

function AffectationLigne({
    a,
    nomProf,
    setMsg,
    onChanged,
}: {
    a: Affectation
    nomProf: string
    setMsg: (m: string) => void
    onChanged: () => void
}) {
    const pr = a.previsionnel
    const [seance, setSeance] = React.useState(versEuros(pr.remu_seance_cents))
    const [etudiant, setEtudiant] = React.useState(versEuros(pr.remu_etudiant_cents))
    const { busy, appel } = useAppel(setMsg, onChanged)
    const modifie =
        versCentimes(seance) !== pr.remu_seance_cents || versCentimes(etudiant) !== pr.remu_etudiant_cents

    return (
        <div
            style={{
                border: `1px solid ${b0}`,
                borderRadius: 10,
                padding: "10px 12px",
                display: "flex",
                flexDirection: "column",
                gap: 8,
            }}
        >
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ ...TITRE, fontSize: 12, fontWeight: 600 }}>{a.classe.nom}</div>
                    <div style={SOUS_TITRE}>
                        {a.nb_etudiants} étudiant(s) · {pr.seances_passees} séance(s) faite(s) ·{" "}
                        {pr.seances_a_venir} à venir
                        {a.classe.is_active ? "" : " · groupe désactivé"}
                    </div>
                </div>
                <Btn
                    danger
                    disabled={busy}
                    onClick={() =>
                        confirm(`Retirer ${nomProf} du groupe ${a.classe.nom} ?`) &&
                        appel(`/professeurs/admin/affectations/${a.id}`, "DELETE", undefined, `Retiré de ${a.classe.nom}`)
                    }
                    style={{ fontSize: 10, padding: "4px 10px" }}
                >
                    Retirer
                </Btn>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 6 }}>
                <Case l="Déjà gagné" v={euros(pr.realise_cents)} color={OK} />
                <Case l="À venir" v={euros(pr.a_venir_cents)} />
                <Case l="Versé" v={euros(pr.verse_cents || 0)} />
            </div>
            <div style={{ display: "flex", gap: 8, alignItems: "flex-end" }}>
                <div style={{ flex: 1 }}>
                    <FL>€ par séance</FL>
                    <TI value={seance} onChange={(e: any) => setSeance(e.target.value)} />
                </div>
                <div style={{ flex: 1 }}>
                    <FL>€ par étudiant / séance</FL>
                    <TI value={etudiant} onChange={(e: any) => setEtudiant(e.target.value)} />
                </div>
                <Btn
                    primary={modifie}
                    disabled={busy || !modifie}
                    onClick={() =>
                        appel(
                            `/professeurs/admin/affectations/${a.id}`,
                            "PATCH",
                            { remu_seance_cents: versCentimes(seance), remu_etudiant_cents: versCentimes(etudiant) },
                            `Taux enregistrés · ${a.classe.nom}`
                        )
                    }
                    style={{ height: 32 }}
                >
                    Enregistrer
                </Btn>
            </div>
        </div>
    )
}

function ProfesseurRow({
    p,
    groupes,
    onChanged,
}: {
    p: Professeur
    groupes: Groupe[]
    onChanged: () => void
}) {
    const [open, setOpen] = React.useState(false)
    const [msg, setMsg] = React.useState("")
    const [prenom, setPrenom] = React.useState(p.prenom)
    const [nom, setNom] = React.useState(p.nom)
    const [grade, setGrade] = React.useState(p.grade)
    const [ajout, setAjout] = React.useState("")
    const { busy, appel } = useAppel(setMsg, onChanged)

    const affiche = `${p.prenom} ${p.nom}`.trim() || p.username || "—"
    const total = p.affectations.reduce((t, a) => t + a.previsionnel.total_cents, 0)
    const aVenir = p.affectations.reduce((t, a) => t + a.previsionnel.a_venir_cents, 0)
    const dejaPris = new Set(p.affectations.map((a) => a.classe.id))
    const disponibles = groupes.filter((g) => g.is_active && !dejaPris.has(g.id))
    const nbEtudiants = p.affectations.reduce((t, a) => t + a.nb_etudiants, 0)

    return (
        <div style={{ borderBottom: `1px solid rgba(255,255,255,0.04)` }}>
            <Ligne open={open} onToggle={() => setOpen((v) => !v)}>
                <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={TITRE}>{affiche}</div>
                    <div style={SOUS_TITRE}>
                        {p.email} ·{" "}
                        {p.affectations.map((a) => MATIERE_LABELS[a.classe.matiere_key] || a.classe.nom).join(", ") ||
                            "aucun groupe"}
                    </div>
                </div>
                {p.status === "suspendu" && (
                    <Tag color={BAD} border="rgba(255,107,107,0.4)">
                        SUSPENDU
                    </Tag>
                )}
                <Tag color={BLUE} border="rgba(122,167,255,0.35)">
                    {p.grade}
                </Tag>
                <span style={{ fontSize: 11, fontFamily: mono, color: t0, flexShrink: 0 }} title="Prévisionnel total">
                    {euros(total)}
                </span>
            </Ligne>

            {open && (
                <div style={DEPLIE}>
                    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 6 }}>
                        <Case l="Étudiants" v={nbEtudiants} />
                        <Case l="À venir" v={euros(aVenir)} />
                        <Case l="Rattaché le" v={fmtDate(p.created_at)} />
                    </div>

                    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 8 }}>
                        <div>
                            <FL>Prénom</FL>
                            <TI value={prenom} onChange={(e: any) => setPrenom(e.target.value)} />
                        </div>
                        <div>
                            <FL>Nom</FL>
                            <TI value={nom} onChange={(e: any) => setNom(e.target.value)} />
                        </div>
                        <div>
                            <FL>Grade</FL>
                            <TI value={grade} onChange={(e: any) => setGrade(e.target.value)} />
                        </div>
                    </div>
                    <div>
                        <Btn
                            disabled={busy}
                            onClick={() =>
                                appel(`/professeurs/admin/${p.id}`, "PATCH", { prenom, nom, grade }, `${affiche} enregistré`)
                            }
                        >
                            Enregistrer la fiche
                        </Btn>
                    </div>

                    <div>
                        <FL>Groupes ({p.affectations.length})</FL>
                        <div style={{ fontSize: 10, fontFamily: mono, color: t2, marginBottom: 6, lineHeight: 1.5 }}>
                            Les taux servent au prévisionnel. Le montant d'un mois se fixe dans l'onglet
                            « Rémunérations ».
                        </div>
                        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                            {p.affectations.length === 0 && (
                                <div style={{ fontSize: 11, fontFamily: mono, color: t2 }}>Aucun groupe.</div>
                            )}
                            {p.affectations.map((a) => (
                                <AffectationLigne
                                    key={a.id}
                                    a={a}
                                    nomProf={affiche}
                                    setMsg={setMsg}
                                    onChanged={onChanged}
                                />
                            ))}
                        </div>
                    </div>

                    <div style={{ display: "flex", gap: 8 }}>
                        <SI value={ajout} onChange={(e: any) => setAjout(e.target.value)} style={{ flex: 1 }}>
                            <option value="">Ajouter un groupe…</option>
                            {disponibles.map((g) => (
                                <option key={g.id} value={g.id}>
                                    {g.nom}
                                </option>
                            ))}
                        </SI>
                        <Btn
                            disabled={busy || !ajout}
                            onClick={async () => {
                                if (
                                    await appel(
                                        `/professeurs/admin/${p.id}/affectations`,
                                        "POST",
                                        { classe_id: Number(ajout) },
                                        "Groupe ajouté"
                                    )
                                )
                                    setAjout("")
                            }}
                        >
                            + Ajouter
                        </Btn>
                    </div>

                    <SM text={msg} />

                    <div style={{ display: "flex", gap: 8 }}>
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
                        <div style={{ flex: 1 }} />
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

function NouveauProfesseur({ groupes, onCreated }: { groupes: Groupe[]; onCreated: () => void }) {
    const [email, setEmail] = React.useState("")
    const [prenom, setPrenom] = React.useState("")
    const [nom, setNom] = React.useState("")
    const [grade, setGrade] = React.useState("ChargéTD")
    const [sel, setSel] = React.useState<number[]>([])
    const [msg, setMsg] = React.useState("")
    const { busy, appel } = useAppel(setMsg, onCreated)

    async function creer() {
        const ok = await appel(
            "/professeurs/admin",
            "POST",
            { email: email.trim(), prenom, nom, grade, classe_ids: sel },
            `${email.trim()} attaché comme ${grade}`
        )
        if (ok) {
            setEmail("")
            setPrenom("")
            setNom("")
            setSel([])
        }
    }

    return (
        <div
            style={{
                padding: 14,
                borderBottom: `1px solid ${b0}`,
                display: "flex",
                flexDirection: "column",
                gap: 10,
                flexShrink: 0,
            }}
        >
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
                <div style={{ gridColumn: "1/-1" }}>
                    <FL>Email du compte NAVIRE</FL>
                    <TI value={email} onChange={(e: any) => setEmail(e.target.value)} placeholder="prof@…" />
                </div>
                <div>
                    <FL>Prénom</FL>
                    <TI value={prenom} onChange={(e: any) => setPrenom(e.target.value)} />
                </div>
                <div>
                    <FL>Nom</FL>
                    <TI value={nom} onChange={(e: any) => setNom(e.target.value)} />
                </div>
                <div>
                    <FL>Grade</FL>
                    <TI value={grade} onChange={(e: any) => setGrade(e.target.value)} />
                </div>
            </div>
            <div>
                <FL>Groupes</FL>
                <div style={{ display: "flex", flexWrap: "wrap" as const, gap: 6 }}>
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
            </div>
            <SM text={msg} />
            <Btn primary disabled={busy || !email.includes("@")} onClick={creer}>
                {busy ? "…" : "Attacher le professeur →"}
            </Btn>
        </div>
    )
}

// ══════════════════════════════════════════════════════════
// Groupes
// ══════════════════════════════════════════════════════════

function GroupeRow({ g, onChanged }: { g: Groupe; onChanged: () => void }) {
    const [open, setOpen] = React.useState(false)
    const [msg, setMsg] = React.useState("")
    const [nom, setNom] = React.useState(g.nom)
    const [discord, setDiscord] = React.useState(g.discord_url)
    const [vocal, setVocal] = React.useState(g.discord_vocal_id || "")
    const [liens, setLiens] = React.useState<Lien[]>(g.liens || [])
    const [etudiants, setEtudiants] = React.useState<Etudiant[] | null>(null)
    const [autres, setAutres] = React.useState<{ id: number; nom: string }[]>([])
    const { busy, appel } = useAppel(setMsg, onChanged)

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

    async function deplacer(e: Etudiant, classeId: number) {
        try {
            await api(`/professeurs/admin/inscriptions/${e.enrollment_id}`, "PATCH", { classe_id: classeId })
            setMsg(`✓ ${e.username || e.email} changé de groupe`)
            chargerEtudiants()
            onChanged()
        } catch (x: any) {
            setMsg(`Erreur : ${x.message}`)
        }
    }

    const profs = (g.professeurs || []).map((p) => p.nom).join(", ")

    return (
        <div style={{ borderBottom: `1px solid rgba(255,255,255,0.04)` }}>
            <Ligne open={open} onToggle={() => setOpen((v) => !v)} faded={!g.is_active}>
                <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={TITRE}>{g.nom}</div>
                    <div style={SOUS_TITRE}>
                        {profs || "aucun professeur"} · {g.discord_url ? "Discord ✓" : "pas de Discord"} ·{" "}
                        {g.discord_vocal_id ? "présence relevée ✓" : "pas de salon vocal"}
                    </div>
                </div>
                {!g.is_active && (
                    <Tag color={t2} border={b0}>
                        INACTIF
                    </Tag>
                )}
                <Tag color={t1} border={b0}>
                    {MATIERE_LABELS[g.matiere_key] || g.matiere_key}
                </Tag>
                <span style={{ fontSize: 11, fontFamily: mono, color: t0, flexShrink: 0 }}>
                    {g.nb_etudiants ?? 0}
                </span>
            </Ligne>

            {open && (
                <div style={DEPLIE}>
                    <div>
                        <FL>Nom du groupe</FL>
                        <TI value={nom} onChange={(e: any) => setNom(e.target.value)} />
                    </div>
                    <div>
                        <FL>Salon Discord</FL>
                        <TI
                            value={discord}
                            onChange={(e: any) => setDiscord(e.target.value)}
                            placeholder="https://discord.com/channels/…"
                        />
                    </div>
                    <div>
                        <FL>Salon vocal (présence)</FL>
                        <TI
                            value={vocal}
                            onChange={(e: any) => setVocal(e.target.value)}
                            placeholder="Identifiant du salon vocal, ou son lien"
                        />
                        <div style={{ fontSize: 10, fontFamily: mono, color: t2, marginTop: 4 }}>
                            Discord : clic droit sur le salon vocal → Copier l'identifiant. Pendant un cours lancé depuis #cours-start,
                            le bot y relève les présents (5 minutes minimum).
                        </div>
                    </div>
                    <div>
                        <FL>Liens ({liens.length})</FL>
                        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                            {liens.map((l, i) => (
                                <div key={i} style={{ display: "flex", gap: 6 }}>
                                    <TI
                                        value={l.label}
                                        placeholder="Libellé (ex. Drive)"
                                        onChange={(e: any) =>
                                            setLiens(liens.map((x, j) => (j === i ? { ...x, label: e.target.value } : x)))
                                        }
                                        style={{ width: 140, flexShrink: 0 }}
                                    />
                                    <TI
                                        value={l.url}
                                        placeholder="https://…"
                                        onChange={(e: any) =>
                                            setLiens(liens.map((x, j) => (j === i ? { ...x, url: e.target.value } : x)))
                                        }
                                    />
                                    <Btn
                                        danger
                                        onClick={() => setLiens(liens.filter((_, j) => j !== i))}
                                        style={{ padding: "4px 10px" }}
                                    >
                                        ✕
                                    </Btn>
                                </div>
                            ))}
                            <div>
                                <Btn
                                    onClick={() => setLiens([...liens, { label: "", url: "" }])}
                                    style={{ fontSize: 10, padding: "4px 10px" }}
                                >
                                    + Lien
                                </Btn>
                            </div>
                        </div>
                    </div>

                    <SM text={msg} />

                    <div style={{ display: "flex", gap: 8 }}>
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
                                        discord_vocal_id: vocal,
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
                        <div style={{ flex: 1 }} />
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

                    <div>
                        <FL>Étudiants {etudiants ? `(${etudiants.length})` : ""}</FL>
                        {etudiants === null ? (
                            <div style={{ fontSize: 11, fontFamily: mono, color: t2 }}>Chargement…</div>
                        ) : etudiants.length === 0 ? (
                            <div style={{ fontSize: 11, fontFamily: mono, color: t2 }}>
                                {g.is_active
                                    ? "Aucun étudiant."
                                    : "Groupe inactif : ses étudiants sont dans le premier groupe actif de la matière."}
                            </div>
                        ) : (
                            <div style={{ border: `1px solid ${b0}`, borderRadius: 10, overflow: "hidden" }}>
                                {etudiants.map((e, i) => (
                                    <div
                                        key={e.enrollment_id}
                                        style={{
                                            display: "flex",
                                            alignItems: "center",
                                            gap: 8,
                                            padding: "7px 10px",
                                            borderBottom:
                                                i < etudiants.length - 1 ? `1px solid rgba(255,255,255,0.04)` : "none",
                                        }}
                                    >
                                        <div style={{ flex: 1, minWidth: 0 }}>
                                            <div style={{ ...TITRE, fontSize: 12 }}>{e.username || "—"}</div>
                                            <div style={SOUS_TITRE}>{e.email}</div>
                                        </div>
                                        {e.status === "payment_failed" && (
                                            <Tag color={BAD} border="rgba(255,107,107,0.4)">
                                                IMPAYÉ
                                            </Tag>
                                        )}
                                        {e.discord_lie && (
                                            <Tag color="#aab4ff" border="rgba(88,101,242,0.6)">
                                                DISCORD
                                            </Tag>
                                        )}
                                        {autres.length > 1 && (
                                            <SI
                                                value={g.id}
                                                onChange={(x: any) => deplacer(e, Number(x.target.value))}
                                                style={{ width: 150, height: 30, flexShrink: 0 }}
                                            >
                                                {autres.map((o) => (
                                                    <option key={o.id} value={o.id}>
                                                        {o.nom}
                                                    </option>
                                                ))}
                                            </SI>
                                        )}
                                    </div>
                                ))}
                            </div>
                        )}
                    </div>
                </div>
            )}
        </div>
    )
}

function NouveauGroupe({ onCreated }: { onCreated: () => void }) {
    const [matiere, setMatiere] = React.useState(MATIERES[0])
    const [nom, setNom] = React.useState("")
    const [discord, setDiscord] = React.useState("")
    const [msg, setMsg] = React.useState("")
    const { busy, appel } = useAppel(setMsg, onCreated)

    async function creer() {
        const ok = await appel(
            "/professeurs/admin/classes",
            "POST",
            { matiere_key: matiere, nom, discord_url: discord },
            `Groupe ${nom} créé`
        )
        if (ok) {
            setNom("")
            setDiscord("")
        }
    }

    return (
        <div
            style={{
                padding: 14,
                borderBottom: `1px solid ${b0}`,
                display: "flex",
                flexDirection: "column",
                gap: 10,
                flexShrink: 0,
            }}
        >
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
                <div>
                    <FL>Matière</FL>
                    <SI value={matiere} onChange={(e: any) => setMatiere(e.target.value)}>
                        {MATIERES.map((k) => (
                            <option key={k} value={k}>
                                {MATIERE_LABELS[k]}
                            </option>
                        ))}
                    </SI>
                </div>
                <div>
                    <FL>Nom</FL>
                    <TI value={nom} onChange={(e: any) => setNom(e.target.value)} placeholder="L2 Pénal — groupe 2" />
                </div>
                <div style={{ gridColumn: "1/-1" }}>
                    <FL>Salon Discord</FL>
                    <TI
                        value={discord}
                        onChange={(e: any) => setDiscord(e.target.value)}
                        placeholder="https://discord.com/channels/…"
                    />
                </div>
            </div>
            <SM text={msg} />
            <Btn primary disabled={busy || nom.trim().length < 2} onClick={creer}>
                {busy ? "…" : "Créer le groupe →"}
            </Btn>
        </div>
    )
}

// ══════════════════════════════════════════════════════════
// Rémunérations (pilotage, virements manuels)
// ══════════════════════════════════════════════════════════

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
    const fixeTexte = l.fixe_cents === null ? "" : versEuros(l.fixe_cents)
    const [montant, setMontant] = React.useState(fixeTexte)
    const [note, setNote] = React.useState(l.note)
    const { busy, appel } = useAppel(setMsg, onChanged)
    const [detail, setDetail] = React.useState(false)
    React.useEffect(() => {
        setMontant(fixeTexte)
        setNote(l.note)
    }, [l.fixe_cents, l.note])

    const modifie = montant !== fixeTexte || note !== l.note
    const verse = !!l.verse_le
    const qui = `${l.professeur} · ${l.classe} · ${moisFr(l.mois)}`

    return (
        <div
            style={{
                padding: "10px 14px",
                borderBottom: `1px solid rgba(255,255,255,0.04)`,
                display: "flex",
                flexDirection: "column",
                gap: 8,
                background: coche ? sA : "transparent",
            }}
        >
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                <input
                    type="checkbox"
                    checked={coche}
                    disabled={verse || l.montant_cents <= 0}
                    title={l.clos ? undefined : "Mois pas encore clos : séances à venir ou élèves pas encore prélevés"}
                    onChange={onCoche}
                    style={{ flexShrink: 0 }}
                />
                <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={TITRE}>
                        {l.professeur}
                        <span style={{ color: t1, fontSize: 11 }}> · {l.classe}</span>
                    </div>
                    <div style={SOUS_TITRE}>
                        {moisFr(l.mois)} · {l.seances} séance(s) · {l.nb_etudiants} étudiant(s) · calculé{" "}
                        {euros(l.calcule_cents)} · acquis {euros(l.acquis_cents)}
                        {l.clos ? "" : " · mois en cours ou à venir"}
                    </div>
                </div>
                {verse ? (
                    <Tag color={OK} border="rgba(91,227,163,0.4)">
                        VERSÉ {fmtDate(l.verse_le)}
                    </Tag>
                ) : !l.clos ? (
                    <Tag color={BLUE} border="rgba(122,167,255,0.35)">
                        {l.fixe_cents !== null ? "FIXÉ · PRÉVU" : "PRÉVU"}
                    </Tag>
                ) : l.fixe_cents !== null ? (
                    <Tag color={WARN} border="rgba(255,209,102,0.35)">
                        FIXÉ
                    </Tag>
                ) : (
                    <Tag color={t2} border={b0}>
                        CALCULÉ
                    </Tag>
                )}
                <span
                    style={{
                        fontSize: 14,
                        fontWeight: 700,
                        fontFamily: mono,
                        color: verse ? OK : t0,
                        flexShrink: 0,
                        minWidth: 84,
                        textAlign: "right" as const,
                    }}
                >
                    {euros(l.montant_cents)}
                </span>
            </div>
            <div style={{ display: "flex", gap: 8, alignItems: "center", paddingLeft: 24 }}>
                <TI
                    value={montant}
                    disabled={busy || verse}
                    onChange={(e: any) => setMontant(e.target.value)}
                    placeholder={`Fixer (calculé ${versEuros(l.calcule_cents)})`}
                    title="Montant fixé en € (vide = montant calculé)"
                    style={{ width: 170, flexShrink: 0, borderColor: l.fixe_cents !== null ? b2 : b0 }}
                />
                <TI
                    value={note}
                    disabled={busy}
                    onChange={(e: any) => setNote(e.target.value)}
                    placeholder="Note (facultatif)"
                />
                <Btn onClick={() => setDetail(!detail)} style={{ flexShrink: 0 }}>
                    {detail ? "Masquer" : "Détail"}
                </Btn>
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
                                    ...(verse
                                        ? {}
                                        : { montant_cents: montant.trim() === "" ? null : versCentimes(montant) }),
                                    note,
                                },
                                `Enregistré · ${qui}`
                            )
                        }
                    >
                        OK
                    </Btn>
                ) : verse ? (
                    <Btn
                        disabled={busy}
                        title={l.reference ? `Référence : ${l.reference}` : undefined}
                        onClick={() =>
                            confirm(`Annuler le versement · ${qui} ?`) &&
                            appel(
                                "/professeurs/admin/remunerations/annuler-versement",
                                "POST",
                                { affectation_id: l.affectation_id, mois: l.mois },
                                `Versement annulé · ${qui}`
                            )
                        }
                    >
                        Annuler
                    </Btn>
                ) : null}
            </div>
            {detail && <DetailRemu l={l} />}
        </div>
    )
}

const NUM: React.CSSProperties = {
    fontFamily: mono,
    fontVariantNumeric: "tabular-nums",
    textAlign: "right",
    whiteSpace: "nowrap",
}
const FACTURATION: Record<string, [string, string]> = {
    facture: ["FACTURÉ", OK],
    a_facturer: ["À FACTURER", WARN],
    erreur: ["ERREUR STRIPE", BAD],
    sans_paiement: ["SANS PAIEMENT", t2],
}

// Détail au centime d'une ligne du pilotage : séance par séance, élèves,
// extras, encaissé, part professeur et part NAVIRE.
function DetailRemu({ l }: { l: LigneRemu }) {
    const [d, setD] = React.useState<any>(null)
    const [err, setErr] = React.useState("")
    const [ouverte, setOuverte] = React.useState<number | null>(null)
    React.useEffect(() => {
        api(`/professeurs/admin/remunerations/detail?affectation_id=${l.affectation_id}&mois=${l.mois}`)
            .then(setD)
            .catch((e) => setErr(`Erreur : ${e.message}`))
    }, [l.affectation_id, l.mois, l.montant_cents])

    if (err) return <SM text={err} />
    if (!d) return <div style={{ ...VIDE, padding: 12 }}>Chargement du détail…</div>
    const t = d.totaux
    const b = d.bareme
    const seances: SeanceDetail[] = d.seances
    const tb: Seaux = t.encaissement
    const marge = t.attendu_cents ? Math.round((t.part_navire_cents / t.attendu_cents) * 1000) / 10 : 0
    const grille = "minmax(110px,1.3fr) 56px 56px 86px 86px 86px 86px"
    const entete: React.CSSProperties = {
        fontSize: 8,
        fontFamily: mono,
        color: t2,
        textTransform: "uppercase",
        letterSpacing: 0.8,
    }

    function exporter() {
        const lignes = [["Date", "Heure", "Type", "Élève", "Encaissement", "Montant", "Encaissé", "Attendu", "Part prof", "Part NAVIRE"]]
        seances.forEach((s) => {
            const type = s.hors_agenda ? "Extra hors agenda" : "Séance"
            lignes.push([jour(s.date), heure(s.date), type, `${s.nb_eleves} élève(s)`, "", "", "", "", "", ""])
            s.eleves.forEach((e) =>
                lignes.push(["", "", "", e.nom, ETATS[e.encaissement][0], versEuros(e.montant_cents), "", "", "", ""])
            )
            if (s.extra) {
                lignes.push([jour(s.date), heure(s.extra.ajoute_a), "Extra", `${s.extra.nb_factures} facturé(s)`, "", "", "", "", "", ""])
                s.extra.eleves.forEach((e) =>
                    lignes.push(["", "", "", e.nom, e.reste ? ETATS[e.encaissement][0] : "PARTI", versEuros(e.reste ? e.facture_cents : 0), "", "", "", ""])
                )
            }
            lignes.push(["", "", "Total séance", "", "", "", versEuros(s.encaisse_cents), versEuros(s.attendu_cents),
                versEuros(s.part_prof_cents), versEuros(s.part_navire_cents)])
        })
        if (t.ajustement_cents) lignes.push(["", "", "Ajustement", t.note || "", "", "", "", "", versEuros(t.ajustement_cents), versEuros(-t.ajustement_cents)])
        lignes.push(["", "", "TOTAL DU MOIS", "", "", "", versEuros(t.encaisse_cents), versEuros(t.attendu_cents),
            versEuros(t.part_prof_cents), versEuros(t.part_navire_cents)])
        lignes.push(["", "", "Impayés", "", "", "", versEuros(tb.impaye), "", "", ""])
        lignes.push(["", "", "Historiques (estimé, à vérifier dans Stripe)", "", "", "", versEuros(tb.estime), "", "", ""])
        const csv = lignes.map((r) => r.map((v) => `"${String(v).replace(/"/g, '""')}"`).join(";")).join("\n")
        const a = document.createElement("a")
        a.href = URL.createObjectURL(new Blob(["\ufeff" + csv], { type: "text/csv;charset=utf-8" }))
        a.download = `detail-${d.professeur || "prof"}-${d.classe}-${d.mois}.csv`.replace(/[^\w.-]+/g, "_")
        a.click()
    }

    return (
        <div style={{ display: "flex", flexDirection: "column", gap: 8, paddingLeft: 24 }}>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 6 }}>
                <Case l="Encaissé" v={euros(tb.encaisse)} color={OK} />
                <Case l="À venir" v={euros(tb.a_venir)} color={t1} />
                <Case l="Impayé" v={euros(tb.impaye)} color={tb.impaye ? BAD : t2} />
                <Case l="Historiques (à vérifier)" v={euros(tb.estime)} color={tb.estime ? WARN : t2} />
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 6 }}>
                <Case l="Attendu (hors impayés)" v={euros(t.attendu_cents)} />
                <Case l="Part prof" v={euros(t.part_prof_cents)} />
                <Case l="Part NAVIRE attendue" v={euros(t.part_navire_cents)} color={t.part_navire_cents < 0 ? BAD : OK} />
                <Case l="Marge NAVIRE" v={`${String(marge).replace(".", ",")} %`} color={t1} />
            </div>
            <div style={{ fontSize: 10, fontFamily: mono, color: t2 }}>
                Barème : {euros(b.remu_seance_cents)} / séance + {euros(b.remu_etudiant_cents)} / élève · extra{" "}
                {euros(b.remu_extra_etudiant_cents)} / élève resté · élève payant {euros(b.prix_seance_cents)} / séance ·
                avant frais Stripe. Encaissé = prélèvement passé sans impayé signalé par Stripe (ou réglé à
                l'inscription) ; historiques = inscriptions sans échéancier, gérées directement dans Stripe.
            </div>
            {t.fixe_cents !== null && (
                <div style={{ fontSize: 10, fontFamily: mono, color: WARN }}>
                    Montant fixé {euros(t.fixe_cents)} (calculé au barème {euros(t.calcule_cents)}) · réparti{" "}
                    {t.repartition === "prorata"
                        ? "au prorata du barème"
                        : t.repartition === "par_eleve"
                        ? "selon les élèves de chaque séance"
                        : t.repartition === "par_seance"
                        ? "à parts égales entre les séances"
                        : "— aucune séance ce mois-ci"}
                    {t.note ? ` · ${t.note}` : ""}
                </div>
            )}
            <div style={{ border: `1px solid ${b0}`, borderRadius: 10, overflow: "hidden" }}>
                <div style={{ display: "grid", gridTemplateColumns: grille, gap: 8, padding: "6px 10px", borderBottom: `1px solid ${b0}` }}>
                    <span style={entete}>Séance</span>
                    <span style={{ ...entete, textAlign: "right" }}>Élèves</span>
                    <span style={{ ...entete, textAlign: "right" }}>Extra</span>
                    <span style={{ ...entete, textAlign: "right" }}>Encaissé</span>
                    <span style={{ ...entete, textAlign: "right" }}>Attendu</span>
                    <span style={{ ...entete, textAlign: "right" }}>Prof</span>
                    <span style={{ ...entete, textAlign: "right" }}>NAVIRE</span>
                </div>
                {seances.length === 0 && <div style={{ ...VIDE, padding: 12 }}>Aucune séance ce mois-ci.</div>}
                {seances.map((s, i) => {
                    const open = ouverte === i
                    const payants = s.eleves.filter((e) => e.payant).length
                    const alerte = s.encaissement.impaye > 0 ? BAD : s.encaissement.estime > 0 ? WARN : t0
                    return (
                        <div key={i} style={{ borderTop: i ? `1px solid rgba(255,255,255,0.04)` : "none" }}>
                            <div
                                onClick={() => setOuverte(open ? null : i)}
                                style={{
                                    display: "grid",
                                    gridTemplateColumns: grille,
                                    gap: 8,
                                    padding: "7px 10px",
                                    cursor: "pointer",
                                    background: open ? sA : "transparent",
                                    fontSize: 11.5,
                                    color: s.passee ? t0 : t1,
                                    alignItems: "center",
                                }}
                            >
                                <span style={{ fontFamily: mono, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                                    {open ? "▾" : "▸"} {jour(s.date)} · {heure(s.date)}
                                    {s.hors_agenda ? " · hors agenda" : s.passee ? "" : " · à venir"}
                                </span>
                                <span style={NUM} title={`${payants} payant(s)`}>
                                    {s.hors_agenda ? "—" : `${s.nb_eleves}${payants !== s.nb_eleves ? ` (${payants}€)` : ""}`}
                                </span>
                                <span style={{ ...NUM, color: s.extra ? WARN : t2 }}>{s.extra ? `${s.extra.nb_factures} él.` : "—"}</span>
                                <span style={{ ...NUM, color: s.encaisse_cents ? OK : t2 }}>{euros(s.encaisse_cents)}</span>
                                <span style={{ ...NUM, color: alerte }}>{euros(s.attendu_cents)}</span>
                                <span style={NUM}>{euros(s.part_prof_cents)}</span>
                                <span style={{ ...NUM, color: s.part_navire_cents < 0 ? BAD : t0 }}>{euros(s.part_navire_cents)}</span>
                            </div>
                            {open && (
                                <div style={{ padding: "6px 10px 10px 22px", fontSize: 11, fontFamily: mono, color: t1, display: "flex", flexDirection: "column", gap: 6 }}>
                                    {!s.hors_agenda && (
                                        <div>
                                            Prof : {euros(s.part_prof_fixe_cents)} séance + {s.nb_eleves} × {euros(b.remu_etudiant_cents)} ={" "}
                                            {euros(s.part_prof_fixe_cents + s.part_prof_eleves_cents)}
                                            {s.extra && ` · extra ${s.extra.nb_factures} × ${euros(b.remu_extra_etudiant_cents)} = ${euros(s.extra.part_prof_cents)}`}
                                            {s.reparti && ` · part du montant fixé : ${euros(s.part_prof_cents)}`}
                                        </div>
                                    )}
                                    {s.cours && (
                                        <div>
                                            Cours Discord : {heure(s.cours.debut)} → {heure(s.cours.fin)} · compté{" "}
                                            {s.cours.duree_comptee_minutes} min{s.cours.cloture_auto ? " · clos automatiquement" : ""}
                                        </div>
                                    )}
                                    {s.eleves.length > 0 && (
                                        <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                                            {s.eleves.map((e) => (
                                                <Tag key={e.enrollment_id} color={ETATS[e.encaissement][1]} border={b0}>
                                                    {e.nom} · {ETATS[e.encaissement][0]}
                                                    {e.montant_cents ? ` ${euros(e.montant_cents)}` : ""}
                                                </Tag>
                                            ))}
                                        </div>
                                    )}
                                    {s.extra && (
                                        <>
                                            <div style={{ color: WARN }}>
                                                Extra ajouté à {heure(s.extra.ajoute_a)} · encaissé{" "}
                                                {euros(s.extra.encaissement.encaisse)} · à venir {euros(s.extra.encaissement.a_venir)}
                                                {s.extra.encaissement.impaye ? ` · impayé ${euros(s.extra.encaissement.impaye)}` : ""}
                                            </div>
                                            <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                                                {s.extra.eleves.map((e, j) => {
                                                    const [lib, col] = !e.reste
                                                        ? ["PARTI", t2]
                                                        : e.facturation === "facture"
                                                        ? ETATS[e.encaissement]
                                                        : FACTURATION[e.facturation || ""] || ["—", t2]
                                                    return (
                                                        <Tag key={j} color={col} border={b0}>
                                                            {e.nom} · {lib}
                                                            {e.reste && e.facture_cents ? ` ${euros(e.facture_cents)}` : ""}
                                                        </Tag>
                                                    )
                                                })}
                                            </div>
                                        </>
                                    )}
                                </div>
                            )}
                        </div>
                    )
                })}
                {t.ajustement_cents !== 0 && (
                    <div style={{ display: "grid", gridTemplateColumns: grille, gap: 8, padding: "7px 10px", borderTop: `1px solid ${b0}`, fontSize: 11.5, color: WARN }}>
                        <span style={{ fontFamily: mono }}>Ajustement{t.note ? ` · ${t.note}` : ""}</span>
                        <span />
                        <span />
                        <span />
                        <span />
                        <span style={NUM}>{euros(t.ajustement_cents)}</span>
                        <span style={NUM}>{euros(-t.ajustement_cents)}</span>
                    </div>
                )}
                <div style={{ display: "grid", gridTemplateColumns: grille, gap: 8, padding: "8px 10px", borderTop: `1px solid ${b1}`, fontSize: 12, fontWeight: 700, color: t0 }}>
                    <span style={{ fontFamily: mono }}>Total · {t.seances} séance(s){t.extras ? ` · ${t.extras} extra(s)` : ""}</span>
                    <span />
                    <span />
                    <span style={{ ...NUM, color: OK }}>{euros(t.encaisse_cents)}</span>
                    <span style={NUM}>{euros(t.attendu_cents)}</span>
                    <span style={NUM}>{euros(t.part_prof_cents)}</span>
                    <span style={{ ...NUM, color: t.part_navire_cents < 0 ? BAD : OK }}>{euros(t.part_navire_cents)}</span>
                </div>
            </div>
            <div>
                <Btn onClick={exporter} style={{ fontSize: 10, padding: "4px 10px" }}>
                    ↓ CSV du détail
                </Btn>
            </div>
        </div>
    )
}

function RemunerationsPanel({ tick }: { tick: number }) {
    const [mois, setMois] = React.useState<string>(moisCourant())
    const [liste, setListe] = React.useState<string[]>([])
    const [items, setItems] = React.useState<LigneRemu[]>([])
    const [totaux, setTotaux] = React.useState<any>({})
    const [loading, setLoading] = React.useState(true)
    const [sel, setSel] = React.useState<string[]>([])
    const [reference, setReference] = React.useState("")
    const [msg, setMsg] = React.useState("")
    const [version, setVersion] = React.useState(0)
    const recharger = () => setVersion((v) => v + 1)
    const { busy, appel } = useAppel(setMsg, recharger)

    React.useEffect(() => {
        setLoading(true)
        setSel([])
        api(`/professeurs/admin/remunerations${mois ? `?mois=${mois}` : ""}`)
            .then((d) => {
                setListe(d?.mois || [])
                setItems(d?.items || [])
                setTotaux(d?.totaux || {})
            })
            .catch((e) => setMsg(`Erreur : ${e.message}`))
            .finally(() => setLoading(false))
    }, [mois, tick, version])

    const cle = (l: LigneRemu) => `${l.affectation_id}|${l.mois}`
    // À verser : mois clos (élèves prélevés), séances faites, pas encore versé.
    const aVerser = items.filter((l) => !l.verse_le && l.a_verser_cents > 0)
    const totalSel = items.filter((l) => sel.includes(cle(l))).reduce((t, l) => t + l.montant_cents, 0)
    const moisAffiches = Array.from(new Set([...liste, moisCourant()])).sort()

    async function verser() {
        if (!confirm(`Confirmer le virement de ${sel.length} ligne(s) · ${euros(totalSel)} ?`)) return
        const ok = await appel(
            "/professeurs/admin/remunerations/verser",
            "POST",
            {
                lignes: sel.map((k) => {
                    const [a, m] = k.split("|")
                    return { affectation_id: Number(a), mois: m }
                }),
                reference: reference.trim() || null,
            },
            `${sel.length} ligne(s) marquée(s) versée(s) · ${euros(totalSel)}`
        )
        if (ok) setReference("")
    }

    function exporter() {
        const lignes = [
            ["Mois", "Professeur", "Email", "Groupe", "Séances", "Étudiants", "Calculé", "Fixé", "Prévu", "Acquis", "À verser", "Versé le", "Référence", "Note"],
        ]
        items.forEach((l) =>
            lignes.push([
                l.mois,
                l.professeur,
                l.email || "",
                l.classe,
                String(l.seances),
                String(l.nb_etudiants),
                versEuros(l.calcule_cents),
                l.fixe_cents === null ? "" : versEuros(l.fixe_cents),
                versEuros(l.prevu_cents),
                versEuros(l.acquis_cents),
                versEuros(l.a_verser_cents),
                l.verse_le ? fmtDate(l.verse_le) : "",
                l.reference || "",
                l.note,
            ])
        )
        const csv = lignes.map((r) => r.map((v) => `"${String(v).replace(/"/g, '""')}"`).join(";")).join("\n")
        const a = document.createElement("a")
        a.href = URL.createObjectURL(new Blob(["﻿" + csv], { type: "text/csv;charset=utf-8" }))
        a.download = `remunerations-professeurs-${mois || "tout"}.csv`
        a.click()
    }

    return (
        <div style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0 }}>
            <div
                style={{
                    display: "flex",
                    gap: 6,
                    padding: "8px 14px",
                    borderBottom: `1px solid ${b0}`,
                    overflowX: "auto" as const,
                    flexShrink: 0,
                }}
            >
                {moisAffiches.map((m) => (
                    <MiniBtn key={m} actif={mois === m} onClick={() => setMois(m)}>
                        {moisCourt(m)}
                    </MiniBtn>
                ))}
                <MiniBtn actif={mois === ""} onClick={() => setMois("")}>
                    Tout
                </MiniBtn>
            </div>

            <div style={{ padding: "10px 14px", borderBottom: `1px solid ${b0}`, flexShrink: 0 }}>
                <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 6 }}>
                    <Case l="Prévu" v={euros(totaux.prevu_cents || 0)} color={t1} />
                    <Case l="Acquis (séances faites)" v={euros(totaux.acquis_cents || 0)} />
                    <Case
                        l="À verser (mois clos)"
                        v={euros(totaux.a_verser_cents || 0)}
                        color={(totaux.a_verser_cents || 0) > 0 ? WARN : t1}
                    />
                    <Case l="Versé" v={euros(totaux.verse_cents || 0)} color={OK} />
                </div>
            </div>

            <SBar
                left={
                    <label style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer" }}>
                        <input
                            type="checkbox"
                            checked={aVerser.length > 0 && sel.length === aVerser.length}
                            onChange={() => setSel(sel.length === aVerser.length ? [] : aVerser.map(cle))}
                        />
                        {items.length} ligne(s) · tout cocher
                    </label>
                }
                right={
                    <Btn onClick={exporter} disabled={!items.length} style={{ fontSize: 10, padding: "4px 10px" }}>
                        ↓ CSV
                    </Btn>
                }
            />

            {msg && (
                <div style={{ padding: "8px 14px", flexShrink: 0 }}>
                    <SM text={msg} />
                </div>
            )}

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
                        gap: 8,
                        padding: "10px 14px",
                        borderTop: `1px solid ${b0}`,
                        alignItems: "center",
                        flexShrink: 0,
                    }}
                >
                    <span style={{ fontSize: 12, fontFamily: mono, fontWeight: 600, color: t0, flexShrink: 0 }}>
                        {sel.length} · {euros(totalSel)}
                    </span>
                    <TI
                        value={reference}
                        onChange={(e: any) => setReference(e.target.value)}
                        placeholder="Référence du virement"
                    />
                    <Btn primary disabled={busy} onClick={verser}>
                        Marquer versé
                    </Btn>
                </div>
            )}
        </div>
    )
}

// ══════════════════════════════════════════════════════════
// Section
// ══════════════════════════════════════════════════════════

// ══════════════════════════════════════════════════════════
// Cours et présences
// ══════════════════════════════════════════════════════════

function moisVoisin(cle: string, delta: number): string {
    const [a, m] = cle.split("-").map(Number)
    const d = new Date(a, m - 1 + delta, 1)
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`
}

function PresencesCours({ c, onChanged }: { c: CoursRow; onChanged: () => void }) {
    const [d, setD] = React.useState<any>(null)
    const [msg, setMsg] = React.useState("")
    const [busy, setBusy] = React.useState(false)
    React.useEffect(() => {
        api(`/professeurs/admin/cours/${c.id}/presences`)
            .then(setD)
            .catch((e) => setMsg(`Erreur : ${e.message}`))
    }, [c.id])

    async function basculer(e: PresenceRow) {
        setBusy(true)
        try {
            const body: any = { present: !e.present }
            if (e.presence_id) body.presence_id = e.presence_id
            else body.enrollment_id = e.enrollment_id
            setD(await api(`/professeurs/admin/cours/${c.id}/presences`, "PATCH", body))
            setMsg(`✓ ${e.nom} ${e.present ? "absent" : "présent"}`)
            onChanged()
        } catch (x: any) {
            setMsg(`Erreur : ${x.message}`)
        } finally {
            setBusy(false)
        }
    }

    if (!d) return <div style={{ ...DEPLIE, color: t2, fontSize: 11 }}>{msg || "Chargement…"}</div>
    return (
        <div style={DEPLIE}>
            {!d.modifiable && <SM text={`Erreur : ${d.raison}`} />}
            {!c.vocal && !c.cree_par_admin && (
                <div style={{ fontSize: 11, fontFamily: mono, color: WARN }}>
                    Groupe sans salon vocal : aucun relevé automatique. Renseigne-le dans Groupes.
                </div>
            )}
            <div style={{ display: "flex", flexWrap: "wrap" as const, gap: 6 }}>
                {(d.eleves as PresenceRow[]).map((e) => (
                    <button
                        key={`${e.enrollment_id}-${e.presence_id}`}
                        disabled={busy || !d.modifiable}
                        onClick={() => basculer(e)}
                        title={e.source && e.source !== "discord" ? `corrigé (${e.source})` : "relevé Discord"}
                        style={{
                            display: "flex",
                            alignItems: "center",
                            gap: 6,
                            padding: "5px 10px",
                            borderRadius: 99,
                            border: `1px solid ${e.present ? "rgba(91,227,163,0.45)" : b0}`,
                            background: e.present ? "rgba(91,227,163,0.08)" : "transparent",
                            color: e.present ? OK : t1,
                            fontSize: 11,
                            fontFamily: mono,
                            cursor: busy || !d.modifiable ? "default" : "pointer",
                        }}
                    >
                        {e.present ? "✓" : "○"} {e.nom}
                        <span style={{ color: t2 }}>
                            {e.minutes ? `${e.minutes} min` : e.discord_relie ? "—" : "non relié"}
                            {e.source && e.source !== "discord" ? " · corrigé" : ""}
                        </span>
                    </button>
                ))}
            </div>
            {d.autres.length > 0 && (
                <div style={{ fontSize: 10.5, fontFamily: mono, color: t2 }}>
                    Aussi dans le vocal :{" "}
                    {(d.autres as PresenceRow[])
                        .map((a) => `${a.nom || "membre Discord"} (${a.minutes} min, ${a.non_relie ? "non relié" : "hors groupe"})`)
                        .join(" · ")}
                </div>
            )}
            <SM text={msg} />
        </div>
    )
}

function CoursOublie({ groupes, profs, onCreated }: { groupes: Groupe[]; profs: Professeur[]; onCreated: () => void }) {
    const [classeId, setClasseId] = React.useState<number>(0)
    const [profId, setProfId] = React.useState<number>(0)
    const [debut, setDebut] = React.useState("")
    const [duree, setDuree] = React.useState("60")
    const [msg, setMsg] = React.useState("")
    const { busy, appel } = useAppel(setMsg, onCreated)
    const g = groupes.find((x) => x.id === classeId)
    const profsDuGroupe = profs.filter((p) => (g?.professeurs || []).some((x) => x.professeur_id === p.id))
    return (
        <div style={{ ...DEPLIE, borderBottom: `1px solid ${b0}` }}>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" as const, alignItems: "flex-end" }}>
                <div style={{ flex: 1, minWidth: 160 }}>
                    <FL>Groupe</FL>
                    <SI value={classeId} onChange={(e: any) => { setClasseId(+e.target.value); setProfId(0) }}>
                        <option value={0}>Choisir…</option>
                        {groupes.filter((x) => x.is_active).map((x) => (
                            <option key={x.id} value={x.id}>
                                {x.nom}
                            </option>
                        ))}
                    </SI>
                </div>
                <div style={{ flex: 1, minWidth: 140 }}>
                    <FL>Professeur</FL>
                    <SI value={profId} onChange={(e: any) => setProfId(+e.target.value)}>
                        <option value={0}>Choisir…</option>
                        {profsDuGroupe.map((p) => (
                            <option key={p.id} value={p.id}>
                                {`${p.prenom} ${p.nom}`.trim() || p.email}
                            </option>
                        ))}
                    </SI>
                </div>
                <div style={{ width: 190 }}>
                    <FL>Début</FL>
                    <TI type="datetime-local" value={debut} onChange={(e: any) => setDebut(e.target.value)} />
                </div>
                <div style={{ width: 80 }}>
                    <FL>Durée (min)</FL>
                    <TI type="number" value={duree} onChange={(e: any) => setDuree(e.target.value)} />
                </div>
                <Btn
                    primary
                    disabled={busy || !classeId || !profId || !debut}
                    onClick={() =>
                        appel(
                            "/professeurs/admin/cours",
                            "POST",
                            {
                                classe_id: classeId,
                                professeur_id: profId,
                                debut: new Date(debut).toISOString(),
                                duree_minutes: parseInt(duree, 10) || 60,
                            },
                            "Cours ajouté : coche maintenant les présents"
                        )
                    }
                >
                    Ajouter le cours
                </Btn>
            </div>
            <SM text={msg} />
        </div>
    )
}

function CoursPanel({ tick, groupes, profs }: { tick: number; groupes: Groupe[]; profs: Professeur[] }) {
    const [mois, setMois] = React.useState<string>(moisCourant())
    const [items, setItems] = React.useState<CoursRow[]>([])
    const [loading, setLoading] = React.useState(true)
    const [err, setErr] = React.useState("")
    const [open, setOpen] = React.useState<number | null>(null)
    const [ajout, setAjout] = React.useState(false)
    const [t, setT] = React.useState(0)

    React.useEffect(() => {
        setLoading(true)
        api(`/professeurs/admin/cours?mois=${mois}`)
            .then((d) => {
                setItems(d?.items || [])
                setErr("")
            })
            .catch((e) => setErr(e.message))
            .finally(() => setLoading(false))
    }, [mois, tick, t])

    return (
        <>
            <div style={{ display: "flex", gap: 6, alignItems: "center", padding: "8px 14px", borderBottom: `1px solid ${b0}`, flexShrink: 0 }}>
                <MiniBtn onClick={() => setMois(moisVoisin(mois, -1))}>‹</MiniBtn>
                <span style={{ fontSize: 11, fontFamily: mono, color: t0, minWidth: 110, textAlign: "center" as const }}>{moisFr(mois)}</span>
                <MiniBtn onClick={() => setMois(moisVoisin(mois, 1))}>›</MiniBtn>
                <span style={{ fontSize: 11, fontFamily: mono, color: t1, marginLeft: 8 }}>
                    {items.length} cours · {items.reduce((s, c) => s + c.nb_presents, 0)} présences
                </span>
                <span style={{ flex: 1 }} />
                <Btn onClick={() => setAjout((v) => !v)} style={{ fontSize: 10, padding: "4px 10px" }}>
                    {ajout ? "✕" : "+ Cours oublié"}
                </Btn>
            </div>
            {ajout && <CoursOublie groupes={groupes} profs={profs} onCreated={() => { setAjout(false); setT((x) => x + 1) }} />}
            {err && (
                <div style={{ padding: "8px 14px" }}>
                    <SM text={`Erreur : ${err}`} />
                </div>
            )}
            {loading ? (
                <div style={VIDE}>Chargement…</div>
            ) : items.length === 0 ? (
                <div style={VIDE}>Aucun cours ce mois-ci. Les professeurs les lancent depuis #cours-start sur Discord.</div>
            ) : (
                <div style={{ flex: 1, overflowY: "auto" }}>
                    {items.map((c) => (
                        <div key={c.id} style={{ borderBottom: `1px solid rgba(255,255,255,0.04)` }}>
                            <Ligne open={open === c.id} onToggle={() => setOpen(open === c.id ? null : c.id)}>
                                <div style={{ flex: 1, minWidth: 0 }}>
                                    <div style={TITRE}>
                                        {jour(c.debut)} · {heure(c.debut)} — {c.groupe}
                                    </div>
                                    <div style={SOUS_TITRE}>
                                        {c.professeur} · {c.matiere_label} · {c.duree_comptee_minutes} min
                                    </div>
                                </div>
                                {c.statut === "en_cours" && (
                                    <Tag color={OK} border="rgba(91,227,163,0.4)">
                                        EN COURS
                                    </Tag>
                                )}
                                {c.cree_par_admin && (
                                    <Tag color={t1} border={b1}>
                                        AJOUTÉ ICI
                                    </Tag>
                                )}
                                {!c.vocal && !c.cree_par_admin && (
                                    <Tag color={WARN} border="rgba(255,209,102,0.4)">
                                        SANS VOCAL
                                    </Tag>
                                )}
                                <span style={{ fontSize: 11, fontFamily: mono, color: t0, flexShrink: 0 }}>{c.nb_presents} prés.</span>
                            </Ligne>
                            {open === c.id && <PresencesCours c={c} onChanged={() => setT((x) => x + 1)} />}
                        </div>
                    ))}
                </div>
            )}
        </>
    )
}

// ══════════════════════════════════════════════════════════
// Fiches publiques
// ══════════════════════════════════════════════════════════

const STATUTS_PRO: Record<string, string> = { juriste: "Juriste", charge_td: "Chargé de TD", avocat: "Avocat" }

const ETATS_FICHE: Record<Fiche["etat"], [string, string]> = {
    brouillon: ["BROUILLON", t2],
    en_attente: ["À VALIDER", WARN],
    publie: ["PUBLIÉE", OK],
    refuse: ["REFUSÉE", BAD],
}

function ContenuFicheVue({ titre, c, nomAffiche }: { titre: string; c: ContenuFiche; nomAffiche?: string }) {
    const ligne = (l: string, v: React.ReactNode) => (
        <div style={{ display: "flex", gap: 8, fontSize: 11.5, fontFamily: mono, lineHeight: 1.5 }}>
            <span style={{ color: t2, minWidth: 92, flexShrink: 0 }}>{l}</span>
            <span style={{ color: t0, minWidth: 0, wordBreak: "break-word" as const }}>{v || <span style={{ color: t2 }}>—</span>}</span>
        </div>
    )
    return (
        <div style={{ flex: 1, minWidth: 240, border: `1px solid ${b0}`, borderRadius: 12, padding: 12, display: "flex", flexDirection: "column", gap: 5 }}>
            <FL>{titre}</FL>
            <div style={{ display: "flex", gap: 10, alignItems: "center", marginBottom: 4 }}>
                {c.photo_url ? (
                    <img src={c.photo_url} alt="" style={{ width: 52, height: 52, borderRadius: "50%", objectFit: "cover" as const }} />
                ) : (
                    <div style={{ width: 52, height: 52, borderRadius: "50%", background: sA, display: "grid", placeItems: "center", fontSize: 10, color: t2 }}>
                        photo
                    </div>
                )}
                <div style={{ fontSize: 13, fontWeight: 600, color: t0 }}>{nomAffiche}</div>
            </div>
            {ligne("Nom affiché", c.nom_complet_public ? "Nom complet" : "Prénom + initiale")}
            {ligne("Statut", STATUTS_PRO[c.statut_pro] || c.statut_pro)}
            {ligne("Poste", c.poste)}
            {ligne("Expérience", c.experience_annees ? `${c.experience_annees} an(s)` : "")}
            {ligne(
                "Formation",
                c.formation.length ? (
                    <span>
                        {c.formation.map((f, i) => (
                            <div key={i}>
                                {[f.diplome, f.etablissement, f.annee].filter(Boolean).join(" · ")}
                            </div>
                        ))}
                    </span>
                ) : (
                    ""
                )
            )}
            {ligne(
                "LinkedIn",
                c.linkedin_url ? (
                    <a href={c.linkedin_url} target="_blank" rel="noopener" style={{ color: BLUE }}>
                        {c.linkedin_url}
                    </a>
                ) : (
                    ""
                )
            )}
            <div style={{ fontSize: 11.5, fontFamily: mono, color: t1, whiteSpace: "pre-line" as const, lineHeight: 1.55, marginTop: 4 }}>
                {c.description || "—"}
            </div>
        </div>
    )
}

function FicheRow({ f, onChanged }: { f: Fiche; onChanged: () => void }) {
    const [open, setOpen] = React.useState(f.etat === "en_attente")
    const [msg, setMsg] = React.useState("")
    const [motif, setMotif] = React.useState("")
    const [ordre, setOrdre] = React.useState(String(f.ordre))
    const { busy, appel } = useAppel(setMsg, onChanged)
    const base = `/professeurs/admin/profils/${f.professeur_id}`
    const [lib, clr] = ETATS_FICHE[f.etat]
    const aPublier = !!f.brouillon && f.manques.length === 0
    const nom = `${f.prenom} ${f.nom}`.trim() || f.email || `Professeur ${f.professeur_id}`

    return (
        <div style={{ borderBottom: `1px solid rgba(255,255,255,0.04)` }}>
            <Ligne open={open} onToggle={() => setOpen((v) => !v)} faded={f.professeur_status !== "active"}>
                <img src={f.photo_url} alt="" style={{ width: 28, height: 28, borderRadius: "50%", objectFit: "cover" as const, flexShrink: 0 }} />
                <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={TITRE}>{nom}</div>
                    <div style={SOUS_TITRE}>
                        {f.visible ? `en ligne · #${f.slug}` : f.publie_le ? (f.masque ? "masquée" : "hors ligne (professeur suspendu)") : "jamais publiée"}
                        {f.soumis_le ? ` · soumise le ${fmtDate(f.soumis_le)}` : ""}
                    </div>
                </div>
                {f.verifie_le && (
                    <Tag color={OK} border="rgba(91,227,163,0.4)">
                        VÉRIFIÉ
                    </Tag>
                )}
                {f.masque && (
                    <Tag color={t1} border={b1}>
                        MASQUÉE
                    </Tag>
                )}
                <Tag color={clr} border={clr === t2 ? b1 : clr}>
                    {f.publie_le && f.etat === "brouillon" && f.brouillon ? "MODIFS NON SOUMISES" : lib}
                </Tag>
            </Ligne>
            {open && (
                <div style={DEPLIE}>
                    {f.etat === "refuse" && f.motif_refus && <SM text={`Erreur : refusée — ${f.motif_refus}`} />}
                    {f.brouillon && f.manques.length > 0 && (
                        <div style={{ fontSize: 11, fontFamily: mono, color: WARN }}>Incomplète : il manque {f.manques.join(", ")}.</div>
                    )}
                    <div style={{ display: "flex", gap: 10, flexWrap: "wrap" as const }}>
                        {f.publie && <ContenuFicheVue titre="En ligne" c={f.publie} nomAffiche={f.nom_affiche} />}
                        {f.brouillon && (
                            <ContenuFicheVue titre={f.publie ? "Nouvelle version (brouillon)" : "Brouillon"} c={f.brouillon} nomAffiche={f.nom_affiche} />
                        )}
                    </div>
                    <div style={{ fontSize: 11, fontFamily: mono, color: t1 }}>
                        Matières (groupes actifs) : {f.matieres.map((m) => `${m.niveau} ${m.nom}`).join(", ") || "aucune"}
                        {f.lien && (
                            <>
                                {" · "}
                                <a href={f.lien} target="_blank" rel="noopener" style={{ color: BLUE }}>
                                    voir la fiche
                                </a>
                            </>
                        )}
                    </div>
                    <div style={{ display: "flex", gap: 6, flexWrap: "wrap" as const, alignItems: "center" }}>
                        <Btn primary disabled={busy || !aPublier} onClick={() => appel(`${base}/publier`, "POST", undefined, "Fiche publiée, le professeur est prévenu par email")}>
                            Publier
                        </Btn>
                        <TI value={motif} onChange={(e: any) => setMotif(e.target.value)} placeholder="Motif du refus (envoyé au professeur)" style={{ width: 260 }} />
                        <Btn danger disabled={busy || !motif.trim() || !f.brouillon} onClick={() => appel(`${base}/refuser`, "POST", { motif }, "Fiche refusée, le professeur est prévenu par email")}>
                            Refuser
                        </Btn>
                    </div>
                    <div style={{ display: "flex", gap: 6, flexWrap: "wrap" as const, alignItems: "center" }}>
                        <Btn disabled={busy} onClick={() => appel(`${base}/verifier`, "POST", { verifie: !f.verifie_le }, f.verifie_le ? "Badge retiré" : "Badge « Vérifié » posé")}>
                            {f.verifie_le ? "Retirer « Vérifié »" : "Marquer « Vérifié »"}
                        </Btn>
                        <Btn disabled={busy || !f.publie_le} onClick={() => appel(`${base}/masquer`, "POST", { masque: !f.masque }, f.masque ? "Fiche réaffichée" : "Fiche masquée")}>
                            {f.masque ? "Réafficher" : "Masquer"}
                        </Btn>
                        <span style={{ fontSize: 10, fontFamily: mono, color: t2, marginLeft: 6 }}>Ordre</span>
                        <TI value={ordre} onChange={(e: any) => setOrdre(e.target.value)} type="number" style={{ width: 64 }} />
                        <Btn disabled={busy || String(f.ordre) === ordre} onClick={() => appel(`${base}/ordre`, "POST", { ordre: parseInt(ordre, 10) || 0 }, "Ordre enregistré")}>
                            OK
                        </Btn>
                    </div>
                    <SM text={msg} />
                </div>
            )}
        </div>
    )
}

function FichesPanel({ tick, onCount }: { tick: number; onCount: (n: number) => void }) {
    const [items, setItems] = React.useState<Fiche[]>([])
    const [loading, setLoading] = React.useState(true)
    const [err, setErr] = React.useState("")
    const [filtre, setFiltre] = React.useState<"" | Fiche["etat"]>("")
    const [t, setT] = React.useState(0)

    React.useEffect(() => {
        setLoading(true)
        api("/professeurs/admin/profils")
            .then((d) => {
                setItems(d?.items || [])
                onCount(d?.a_valider || 0)
                setErr("")
            })
            .catch((e) => setErr(e.message))
            .finally(() => setLoading(false))
    }, [tick, t])

    const visibles = items.filter((f) => !filtre || f.etat === filtre)
    return (
        <>
            <div style={{ display: "flex", gap: 5, padding: "8px 14px", borderBottom: `1px solid ${b0}`, flexShrink: 0, flexWrap: "wrap" as const }}>
                {([["", "Toutes"], ["en_attente", "À valider"], ["publie", "Publiées"], ["refuse", "Refusées"], ["brouillon", "Brouillons"]] as const).map(([k, l]) => (
                    <MiniBtn key={k} actif={filtre === k} onClick={() => setFiltre(k as any)}>
                        {l} ({k ? items.filter((f) => f.etat === k).length : items.length})
                    </MiniBtn>
                ))}
            </div>
            {err && (
                <div style={{ padding: "8px 14px" }}>
                    <SM text={`Erreur : ${err}`} />
                </div>
            )}
            {loading ? (
                <div style={VIDE}>Chargement…</div>
            ) : visibles.length === 0 ? (
                <div style={VIDE}>Aucune fiche. Chaque professeur remplit la sienne dans son espace (rubrique « Profil public »).</div>
            ) : (
                <div style={{ flex: 1, overflowY: "auto" }}>
                    {visibles.map((f) => (
                        <FicheRow key={f.id} f={f} onChanged={() => setT((x) => x + 1)} />
                    ))}
                </div>
            )}
        </>
    )
}

export function ProfesseursSection() {
    const [vue, setVue] = React.useState<Vue>("professeurs")
    const [profs, setProfs] = React.useState<Professeur[]>([])
    const [groupes, setGroupes] = React.useState<Groupe[]>([])
    const [loading, setLoading] = React.useState(true)
    const [search, setSearch] = React.useState("")
    const [ajout, setAjout] = React.useState(false)
    const [err, setErr] = React.useState("")
    const [tick, setTick] = React.useState(0)
    const [aValider, setAValider] = React.useState(0)

    React.useEffect(() => {
        setLoading(true)
        setErr("")
        api("/professeurs/admin/profils")
            .then((d) => setAValider(d?.a_valider || 0))
            .catch(() => {})
        Promise.all([
            api("/professeurs/admin/list").then((d) => setProfs(d?.items || [])),
            api("/professeurs/admin/classes").then((d) => setGroupes(d?.items || [])),
        ])
            .catch((e) => setErr(e.message))
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
    const totalPrevu = profs.reduce((t, p) => t + p.affectations.reduce((s, a) => s + a.previsionnel.total_cents, 0), 0)
    const nbEtudiants = groupes.reduce((t, g) => t + (g.nb_etudiants || 0), 0)

    return (
        <div style={{ display: "flex", flexDirection: "column", height: "100%", maxHeight: "100%" }}>
            <SBar
                left={
                    <span style={{ display: "flex", gap: 5 }}>
                        <MiniBtn actif={vue === "professeurs"} onClick={() => setVue("professeurs")}>
                            Professeurs ({profs.length})
                        </MiniBtn>
                        <MiniBtn actif={vue === "groupes"} onClick={() => setVue("groupes")}>
                            Groupes ({groupes.length})
                        </MiniBtn>
                        <MiniBtn actif={vue === "cours"} onClick={() => setVue("cours")}>
                            Cours
                        </MiniBtn>
                        <MiniBtn actif={vue === "fiches"} onClick={() => setVue("fiches")}>
                            Fiches{aValider ? ` · ${aValider} à valider` : ""}
                        </MiniBtn>
                        <MiniBtn actif={vue === "remunerations"} onClick={() => setVue("remunerations")}>
                            Rémunérations
                        </MiniBtn>
                    </span>
                }
                right={
                    <span style={{ display: "flex", gap: 6, alignItems: "center" }}>
                        {vue !== "remunerations" && vue !== "fiches" && vue !== "cours" && (
                            <>
                                <TI
                                    value={search}
                                    onChange={(e: any) => setSearch(e.target.value)}
                                    placeholder="Rechercher…"
                                    style={{ width: 110, height: 28 }}
                                />
                                <Btn onClick={() => setAjout((v) => !v)} style={{ fontSize: 10, padding: "4px 10px" }}>
                                    {ajout ? "✕" : "+ Nouveau"}
                                </Btn>
                            </>
                        )}
                        <Btn onClick={recharger} style={{ fontSize: 10, padding: "4px 10px" }}>
                            ↻
                        </Btn>
                    </span>
                }
            />

            {vue !== "remunerations" && vue !== "fiches" && vue !== "cours" && (
                <div
                    style={{
                        display: "flex",
                        gap: 14,
                        padding: "8px 14px",
                        borderBottom: `1px solid ${b0}`,
                        fontSize: 11,
                        fontFamily: mono,
                        color: t1,
                        flexShrink: 0,
                        flexWrap: "wrap" as const,
                    }}
                >
                    <span>{profs.filter((p) => p.status === "active").length} professeur(s) actif(s)</span>
                    <span>{groupes.filter((g) => g.is_active).length} groupe(s)</span>
                    <span>{nbEtudiants} étudiant(s)</span>
                    <span style={{ color: t0 }}>prévu {euros(totalPrevu)}</span>
                </div>
            )}

            {err && (
                <div style={{ padding: "8px 14px", flexShrink: 0 }}>
                    <SM text={`Erreur : ${err}`} />
                </div>
            )}

            {vue === "remunerations" ? (
                <RemunerationsPanel tick={tick} />
            ) : vue === "cours" ? (
                <CoursPanel tick={tick} groupes={groupes} profs={profs} />
            ) : vue === "fiches" ? (
                <FichesPanel tick={tick} onCount={setAValider} />
            ) : (
                <>
                    {ajout &&
                        (vue === "professeurs" ? (
                            <NouveauProfesseur groupes={groupes} onCreated={recharger} />
                        ) : (
                            <NouveauGroupe onCreated={recharger} />
                        ))}
                    {loading ? (
                        <div style={VIDE}>Chargement…</div>
                    ) : vue === "professeurs" ? (
                        profsVisibles.length === 0 ? (
                            <div style={VIDE}>Aucun professeur. « + Nouveau » pour en attacher un.</div>
                        ) : (
                            <div style={{ flex: 1, overflowY: "auto" }}>
                                {profsVisibles.map((p) => (
                                    <ProfesseurRow key={p.id} p={p} groupes={groupes} onChanged={recharger} />
                                ))}
                            </div>
                        )
                    ) : groupesVisibles.length === 0 ? (
                        <div style={VIDE}>Aucun groupe.</div>
                    ) : (
                        <div style={{ flex: 1, overflowY: "auto" }}>
                            {groupesVisibles.map((g) => (
                                <GroupeRow key={g.id} g={g} onChanged={recharger} />
                            ))}
                        </div>
                    )}
                </>
            )}
        </div>
    )
}
