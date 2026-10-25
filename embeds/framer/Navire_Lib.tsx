// Navire_Lib.tsx
// Section « Lib » de la console admin (Navire_AdminConsole.tsx).
//
// Import d'un cours (.docx ou .pdf) → le serveur le découpe en blocs, refait
// le plan, surligne les références et place les points de passage (QCM +
// questions à points). Ici : publier, régler l'Elo demandé et gagné à chaque
// point de passage, corriger les questions, en faire générer par l'IA,
// ajouter un point de passage à la main, déposer une nouvelle version.
//
// Mêmes jeton, design tokens et primitives que la console (charte NAVIRE
// AI) : le fichier est autonome pour ne pas dépendre de l'ordre des exports
// de la console.

import * as React from "react"

const API_BASE = "https://navire-app-api.onrender.com"
const TOKEN_KEY = "navire_token"
const ADMIN_CODE = "THORKISHERE"
// Page Framer qui affiche embeds/lib.html (lien « Ouvrir » d'un cours publié)
const LIB_PAGE = "https://navire-ai.com/lib"

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
    const isForm = typeof FormData !== "undefined" && body instanceof FormData
    const res = await fetch(`${API_BASE}${path}`, {
        method,
        headers: {
            Authorization: `Bearer ${token}`,
            "X-Admin-Code": ADMIN_CODE,
            ...(body && !isForm ? { "Content-Type": "application/json" } : {}),
        },
        body: body ? (isForm ? body : JSON.stringify(body)) : undefined,
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

async function download(path: string, filename: string) {
    const res = await fetch(`${API_BASE}${path}`, {
        headers: {
            Authorization: `Bearer ${getToken()}`,
            "X-Admin-Code": ADMIN_CODE,
        },
    })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const url = URL.createObjectURL(await res.blob())
    const a = document.createElement("a")
    a.href = url
    a.download = filename
    document.body.appendChild(a)
    a.click()
    a.remove()
    setTimeout(() => URL.revokeObjectURL(url), 1500)
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
const VIOLET = "#9B6FFF"
const FIELD = "#101018"

// ── Types ──
type Kind = "cours" | "dissertation" | "fiche" | "cas_pratique"
const KINDS: { id: Kind; label: string }[] = [
    { id: "cours", label: "Cours" },
    { id: "dissertation", label: "Dissertation" },
    { id: "fiche", label: "Fiche" },
    { id: "cas_pratique", label: "Cas pratique" },
]
const NIVEAUX = ["L1", "L2", "L3", "M1", "M2", "CRFPA"]

interface Question {
    id: string
    type: "qcm" | "courte"
    question: string
    choices?: string[]
    answer?: number
    accepted?: string[]
    points: number
    explication?: string
}
interface Checkpoint {
    id: number
    anchor_block: number
    anchor_text: string
    section: string
    titre: string
    elo_requis: number
    seuil_pct: number
    recompense_elo: number
    questions: Question[]
    source: string
    is_active: boolean
    final: boolean
    total_points: number
    tentatives: number
    valides: number
}
interface Doc {
    id: number
    kind: Kind
    niveau: string
    matiere: string
    titre: string
    sous_titre: string
    kicker: string
    maj: string
    description: string
    elo_requis: number
    is_published: boolean
    ordre: number
    source_filename: string
    size_bytes: number
    lecteurs: number
    nb_blocs: number
    nb_etapes: number
    minutes: number | null
    checkpoints: Checkpoint[]
    blocks?: { id: string; t: string; text: string }[]
    detection?: { points_passage: number; words?: number }
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

const INPUT: React.CSSProperties = {
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
    boxSizing: "border-box",
}

function TI({ value, onChange, placeholder, type = "text", style }: any) {
    return (
        <input
            type={type}
            value={value}
            onChange={onChange}
            placeholder={placeholder}
            style={{ ...INPUT, ...style }}
        />
    )
}

function SI({ value, onChange, children, style }: any) {
    return (
        <select
            value={value}
            onChange={onChange}
            style={{ ...INPUT, height: 32, background: FIELD, ...style }}
        >
            {children}
        </select>
    )
}

function TAI({ value, onChange, placeholder, rows = 2 }: any) {
    return (
        <textarea
            value={value}
            onChange={onChange}
            placeholder={placeholder}
            rows={rows}
            style={{
                ...INPUT,
                height: "auto",
                padding: "7px 10px",
                resize: "vertical",
                lineHeight: 1.55,
            }}
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
                padding: "6px 12px",
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
                fontSize: 10,
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
                fontSize: 11,
                fontFamily: mono,
                color: err ? BAD : OK,
                padding: "7px 11px",
                background: err
                    ? "rgba(255,107,107,0.08)"
                    : "rgba(91,227,163,0.08)",
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

function fmtSize(b: number): string {
    if (b < 1024 * 1024) return `${Math.round(b / 1024)} Ko`
    return `${(b / 1024 / 1024).toFixed(1)} Mo`
}

function kindLabel(k: string) {
    return KINDS.find((x) => x.id === k)?.label || k
}

// ══════════════════════════════════════════════════════════
// Import
// ══════════════════════════════════════════════════════════

function ImportForm({ onDone }: { onDone: (d: Doc) => void }) {
    const [file, setFile] = React.useState<File | null>(null)
    const [kind, setKind] = React.useState<Kind>("cours")
    const [niveau, setNiveau] = React.useState("M1")
    const [matiere, setMatiere] = React.useState("")
    const [titre, setTitre] = React.useState("")
    const [description, setDescription] = React.useState("")
    const [auto, setAuto] = React.useState(true)
    const [palier, setPalier] = React.useState("10")
    const [recompense, setRecompense] = React.useState("10")
    const [publier, setPublier] = React.useState(false)
    const [busy, setBusy] = React.useState(false)
    const [msg, setMsg] = React.useState("")
    const ref = React.useRef<HTMLInputElement>(null)

    async function envoyer() {
        if (!file) return
        setBusy(true)
        setMsg("")
        try {
            const fd = new FormData()
            fd.append("file", file)
            fd.append("kind", kind)
            fd.append("niveau", niveau)
            fd.append("matiere", matiere)
            fd.append("titre", titre)
            fd.append("description", description)
            fd.append("auto", String(auto))
            fd.append("elo_palier", palier || "0")
            fd.append("recompense_elo", recompense || "0")
            fd.append("publier", String(publier))
            const d: Doc = await api("/lib/admin/documents", "POST", fd)
            setFile(null)
            setTitre("")
            setDescription("")
            if (ref.current) ref.current.value = ""
            onDone(d)
        } catch (e: any) {
            setMsg(`ERR: ${e.message}`)
        } finally {
            setBusy(false)
        }
    }

    return (
        <div
            style={{
                padding: "12px 14px",
                borderBottom: `1px solid ${b0}`,
                display: "flex",
                flexDirection: "column",
                gap: 8,
                background: "rgba(255,255,255,0.015)",
            }}
        >
            <div
                onClick={() => ref.current?.click()}
                style={{
                    border: `1px dashed ${file ? "rgba(91,227,163,0.45)" : b1}`,
                    borderRadius: 12,
                    padding: "14px 12px",
                    textAlign: "center" as const,
                    cursor: "pointer",
                    fontSize: 11.5,
                    fontFamily: mono,
                    color: file ? OK : t1,
                    background: file ? "rgba(91,227,163,0.05)" : "transparent",
                }}
            >
                {file
                    ? `${file.name} · ${fmtSize(file.size)}`
                    : "Choisir le cours (.docx conseillé, ou .pdf)"}
                <input
                    ref={ref}
                    type="file"
                    accept=".docx,.pdf,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                    onChange={(e) => setFile(e.target.files?.[0] || null)}
                    style={{ display: "none" }}
                />
            </div>
            <div
                style={{
                    display: "grid",
                    gridTemplateColumns: "1fr 0.7fr 1.3fr",
                    gap: 6,
                }}
            >
                <div>
                    <FL>Type</FL>
                    <SI
                        value={kind}
                        onChange={(e: any) => setKind(e.target.value)}
                    >
                        {KINDS.map((k) => (
                            <option key={k.id} value={k.id}>
                                {k.label}
                            </option>
                        ))}
                    </SI>
                </div>
                <div>
                    <FL>Niveau</FL>
                    <SI
                        value={niveau}
                        onChange={(e: any) => setNiveau(e.target.value)}
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
                    <TI
                        value={matiere}
                        onChange={(e: any) => setMatiere(e.target.value)}
                        placeholder="Distribution"
                    />
                </div>
            </div>
            <TI
                value={titre}
                onChange={(e: any) => setTitre(e.target.value)}
                placeholder="Titre (vide = repris du document)"
            />
            <TAI
                value={description}
                onChange={(e: any) => setDescription(e.target.value)}
                placeholder="Description affichée dans la bibliothèque (optionnel)"
            />
            <div
                style={{
                    display: "flex",
                    gap: 10,
                    alignItems: "center",
                    flexWrap: "wrap" as const,
                }}
            >
                <Toggle
                    on={auto}
                    onClick={() => setAuto((v) => !v)}
                    label="Points de passage automatiques"
                />
                <Toggle
                    on={publier}
                    onClick={() => setPublier((v) => !v)}
                    label="Publier tout de suite"
                />
            </div>
            {auto && (
                <div
                    style={{
                        display: "grid",
                        gridTemplateColumns: "1fr 1fr",
                        gap: 6,
                    }}
                >
                    <div>
                        <FL>Elo demandé : + par étape</FL>
                        <TI
                            type="number"
                            value={palier}
                            onChange={(e: any) => setPalier(e.target.value)}
                        />
                    </div>
                    <div>
                        <FL>Elo gagné par étape</FL>
                        <TI
                            type="number"
                            value={recompense}
                            onChange={(e: any) => setRecompense(e.target.value)}
                        />
                    </div>
                </div>
            )}
            <div style={{ fontSize: 9.5, fontFamily: mono, color: t2, lineHeight: 1.5 }}>
                Détection : un point de passage à chaque « POINTS DE PASSAGE »
                (questions tirées du tableau qui suit), sinon à la fin de chaque
                grande partie. Le k-ième demande k × {palier || 0} Elo.
            </div>
            <Msg text={msg} />
            <Btn primary onClick={envoyer} disabled={busy || !file}>
                {busy ? "Import et analyse…" : "Importer →"}
            </Btn>
        </div>
    )
}

// ══════════════════════════════════════════════════════════
// Questions d'un point de passage
// ══════════════════════════════════════════════════════════

function QuestionsEditor({
    cp,
    onSaved,
}: {
    cp: Checkpoint
    onSaved: (c: Checkpoint) => void
}) {
    const [qs, setQs] = React.useState<Question[]>(() =>
        JSON.parse(JSON.stringify(cp.questions || []))
    )
    const [busy, setBusy] = React.useState(false)
    const [msg, setMsg] = React.useState("")

    function maj(i: number, patch: Partial<Question>) {
        setQs((l) => l.map((q, k) => (k === i ? { ...q, ...patch } : q)))
    }
    function ajouter(type: "qcm" | "courte") {
        const id = `q${Date.now().toString(36).slice(-4)}`
        setQs((l) => [
            ...l,
            type === "qcm"
                ? { id, type, question: "", choices: ["", "", "", ""], answer: 0, points: 1, explication: "" }
                : { id, type, question: "", accepted: [""], points: 3, explication: "" },
        ])
    }
    async function enregistrer() {
        setBusy(true)
        setMsg("")
        try {
            const propres = qs.map((q) =>
                q.type === "qcm"
                    ? { ...q, choices: (q.choices || []).filter((c) => c.trim()) }
                    : { ...q, accepted: (q.accepted || []).filter((a) => a.trim()) }
            )
            const c = await api(`/lib/admin/checkpoints/${cp.id}`, "PATCH", {
                questions: propres,
            })
            setMsg("✓ Questions enregistrées")
            onSaved(c)
        } catch (e: any) {
            setMsg(`ERR: ${e.message}`)
        } finally {
            setBusy(false)
        }
    }

    const total = qs.reduce((a, q) => a + (Number(q.points) || 0), 0)

    return (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {qs.map((q, i) => (
                <div
                    key={q.id}
                    style={{
                        border: `1px solid ${b0}`,
                        borderRadius: 12,
                        padding: "8px 10px",
                        display: "flex",
                        flexDirection: "column",
                        gap: 6,
                    }}
                >
                    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                        <Tag
                            color={q.type === "qcm" ? BLUE : VIOLET}
                            border={q.type === "qcm" ? "rgba(122,167,255,0.35)" : "rgba(155,111,255,0.35)"}
                        >
                            {q.type === "qcm" ? "QCM" : "À POINTS"}
                        </Tag>
                        <span style={{ fontSize: 9.5, fontFamily: code, color: t2 }}>
                            {q.id}
                        </span>
                        <div style={{ flex: 1 }} />
                        <span style={{ fontSize: 10, fontFamily: mono, color: t1 }}>
                            pts
                        </span>
                        <TI
                            type="number"
                            value={q.points}
                            onChange={(e: any) => maj(i, { points: Number(e.target.value) || 1 })}
                            style={{ width: 48, height: 24, padding: "0 6px" }}
                        />
                        <button
                            onClick={() => setQs((l) => l.filter((_, k) => k !== i))}
                            style={{ background: "none", border: "none", color: t2, cursor: "pointer", fontSize: 12 }}
                            title="Supprimer la question"
                        >
                            ✕
                        </button>
                    </div>
                    <TAI
                        value={q.question}
                        onChange={(e: any) => maj(i, { question: e.target.value })}
                        placeholder="Énoncé"
                    />
                    {q.type === "qcm" ? (
                        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                            {(q.choices || []).map((c, k) => (
                                <div key={k} style={{ display: "flex", gap: 6, alignItems: "center" }}>
                                    <button
                                        onClick={() => maj(i, { answer: k })}
                                        title="Bonne réponse"
                                        style={{
                                            width: 22,
                                            height: 22,
                                            borderRadius: 7,
                                            border: `1px solid ${q.answer === k ? OK : b1}`,
                                            background: q.answer === k ? OK : "transparent",
                                            color: q.answer === k ? "#0a0a12" : t1,
                                            fontSize: 10,
                                            fontFamily: code,
                                            cursor: "pointer",
                                            flexShrink: 0,
                                        }}
                                    >
                                        {"ABCDEFGH"[k]}
                                    </button>
                                    <TI
                                        value={c}
                                        onChange={(e: any) =>
                                            maj(i, {
                                                choices: (q.choices || []).map((x, j) =>
                                                    j === k ? e.target.value : x
                                                ),
                                            })
                                        }
                                        placeholder={`Choix ${"ABCDEFGH"[k]}`}
                                        style={{ height: 28 }}
                                    />
                                </div>
                            ))}
                            {(q.choices || []).length < 6 && (
                                <span
                                    onClick={() => maj(i, { choices: [...(q.choices || []), ""] })}
                                    style={{ fontSize: 10, fontFamily: mono, color: t1, cursor: "pointer" }}
                                >
                                    + choix
                                </span>
                            )}
                        </div>
                    ) : (
                        <div>
                            <FL>Réponses acceptées (séparées par « ; »)</FL>
                            <TI
                                value={(q.accepted || []).join(" ; ")}
                                onChange={(e: any) =>
                                    maj(i, {
                                        accepted: e.target.value.split(";").map((x: string) => x.trimStart()),
                                    })
                                }
                                placeholder="Loi Doubin ; L. 330-3"
                            />
                        </div>
                    )}
                    <TI
                        value={q.explication || ""}
                        onChange={(e: any) => maj(i, { explication: e.target.value })}
                        placeholder="Explication montrée après correction"
                        style={{ height: 28 }}
                    />
                </div>
            ))}
            <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" as const }}>
                <Btn onClick={() => ajouter("qcm")} style={{ fontSize: 10 }}>
                    + QCM
                </Btn>
                <Btn onClick={() => ajouter("courte")} style={{ fontSize: 10 }}>
                    + Question à points
                </Btn>
                <div style={{ flex: 1 }} />
                <span style={{ fontSize: 10, fontFamily: mono, color: t1 }}>
                    {qs.length} q. · {total} pts
                </span>
                <Btn primary onClick={enregistrer} disabled={busy || !qs.length} style={{ fontSize: 10 }}>
                    {busy ? "…" : "Enregistrer"}
                </Btn>
            </div>
            <Msg text={msg} />
        </div>
    )
}

// ══════════════════════════════════════════════════════════
// Point de passage
// ══════════════════════════════════════════════════════════

function CheckpointRow({
    cp,
    index,
    onChanged,
}: {
    cp: Checkpoint
    index: number
    onChanged: () => void
}) {
    const [open, setOpen] = React.useState(false)
    const [edit, setEdit] = React.useState(false)
    const [elo, setElo] = React.useState(String(cp.elo_requis))
    const [seuil, setSeuil] = React.useState(String(cp.seuil_pct))
    const [gain, setGain] = React.useState(String(cp.recompense_elo))
    const [titre, setTitre] = React.useState(cp.titre)
    const [busy, setBusy] = React.useState(false)
    const [msg, setMsg] = React.useState("")
    const [cur, setCur] = React.useState<Checkpoint>(cp)

    React.useEffect(() => {
        setCur(cp)
        setElo(String(cp.elo_requis))
        setSeuil(String(cp.seuil_pct))
        setGain(String(cp.recompense_elo))
        setTitre(cp.titre)
    }, [cp.id, cp.elo_requis, cp.seuil_pct, cp.recompense_elo, cp.titre, cp.is_active, cp.questions])

    async function patch(body: any, ok: string) {
        setBusy(true)
        setMsg("")
        try {
            const c = await api(`/lib/admin/checkpoints/${cp.id}`, "PATCH", body)
            setCur(c)
            setMsg(`✓ ${ok}`)
            onChanged()
        } catch (e: any) {
            setMsg(`ERR: ${e.message}`)
        } finally {
            setBusy(false)
        }
    }
    async function ia() {
        if (cur.source === "manuel" && !confirm("Remplacer les questions éditées à la main ?")) return
        setBusy(true)
        setMsg("Génération en cours…")
        try {
            const c = await api(`/lib/admin/checkpoints/${cp.id}/generer-ia`, "POST")
            setCur(c)
            setMsg(`✓ ${c.questions.length} questions générées`)
            setEdit(true)
            onChanged()
        } catch (e: any) {
            setMsg(`ERR: ${e.message}`)
        } finally {
            setBusy(false)
        }
    }
    async function supprimer() {
        if (!confirm(`Supprimer « ${cur.titre} » ? La progression des élèves sur ce point est perdue.`)) return
        try {
            await api(`/lib/admin/checkpoints/${cp.id}`, "DELETE")
            onChanged()
        } catch (e: any) {
            setMsg(`ERR: ${e.message}`)
        }
    }

    const srcColor =
        cur.source === "ia" ? VIOLET : cur.source === "manuel" ? WARN : t1

    return (
        <div
            style={{
                borderBottom: `1px solid rgba(255,255,255,0.04)`,
                opacity: cur.is_active ? 1 : 0.45,
            }}
        >
            <div
                onClick={() => setOpen((v) => !v)}
                style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 8,
                    padding: "8px 10px",
                    cursor: "pointer",
                    background: open ? sA : "transparent",
                    borderRadius: 10,
                }}
            >
                <span
                    style={{
                        width: 20,
                        height: 20,
                        borderRadius: "50%",
                        border: `1.5px solid ${cur.final ? VIOLET : BLUE}`,
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                        fontSize: 9,
                        fontFamily: code,
                        color: t0,
                        flexShrink: 0,
                    }}
                >
                    {index + 1}
                </span>
                <div style={{ flex: 1, minWidth: 0 }}>
                    <div
                        style={{
                            fontSize: 11.5,
                            color: t0,
                            fontWeight: 600,
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            whiteSpace: "nowrap" as const,
                        }}
                    >
                        {cur.final ? "Quiz final · " : ""}
                        {cur.titre}
                    </div>
                    <div
                        style={{
                            fontSize: 9.5,
                            fontFamily: mono,
                            color: t2,
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            whiteSpace: "nowrap" as const,
                        }}
                    >
                        {cur.section ? `fin de « ${cur.section} »` : `après « ${cur.anchor_text} »`}
                    </div>
                </div>
                <Tag color={srcColor}>{cur.source.toUpperCase()}</Tag>
                <Tag color={cur.elo_requis ? WARN : t1} border={cur.elo_requis ? "rgba(255,209,102,0.35)" : b0}>
                    {cur.elo_requis} ELO
                </Tag>
                <Tag color={VIOLET} border="rgba(155,111,255,0.35)">
                    +{cur.recompense_elo}
                </Tag>
                <span
                    title="Élèves qui l'ont validé / ayant essayé"
                    style={{ fontSize: 10, fontFamily: code, color: t1, width: 38, textAlign: "right" as const }}
                >
                    {cur.valides}/{cur.tentatives}
                </span>
            </div>
            {open && (
                <div
                    style={{
                        padding: "6px 10px 12px 38px",
                        display: "flex",
                        flexDirection: "column",
                        gap: 8,
                    }}
                >
                    <TI value={titre} onChange={(e: any) => setTitre(e.target.value)} />
                    <div
                        style={{
                            display: "grid",
                            gridTemplateColumns: "1fr 1fr 1fr auto",
                            gap: 6,
                            alignItems: "end",
                        }}
                    >
                        <div>
                            <FL>Elo requis</FL>
                            <TI type="number" value={elo} onChange={(e: any) => setElo(e.target.value)} />
                        </div>
                        <div>
                            <FL>Seuil %</FL>
                            <TI type="number" value={seuil} onChange={(e: any) => setSeuil(e.target.value)} />
                        </div>
                        <div>
                            <FL>Elo gagné</FL>
                            <TI type="number" value={gain} onChange={(e: any) => setGain(e.target.value)} />
                        </div>
                        <Btn
                            primary
                            disabled={busy}
                            onClick={() =>
                                patch(
                                    {
                                        titre,
                                        elo_requis: Number(elo) || 0,
                                        seuil_pct: Number(seuil) || 0,
                                        recompense_elo: Number(gain) || 0,
                                    },
                                    "Réglages enregistrés"
                                )
                            }
                            style={{ height: 32 }}
                        >
                            OK
                        </Btn>
                    </div>
                    <div style={{ display: "flex", gap: 6, flexWrap: "wrap" as const, alignItems: "center" }}>
                        <Btn onClick={() => setEdit((v) => !v)} style={{ fontSize: 10 }}>
                            {edit ? "Fermer les questions" : `Questions (${cur.questions.length} · ${cur.total_points} pts)`}
                        </Btn>
                        <Btn onClick={ia} disabled={busy} style={{ fontSize: 10, color: VIOLET }}>
                            ✦ Générer avec l'IA
                        </Btn>
                        <Btn
                            onClick={() => patch({ is_active: !cur.is_active }, cur.is_active ? "Désactivé" : "Activé")}
                            disabled={busy}
                            style={{ fontSize: 10 }}
                        >
                            {cur.is_active ? "Désactiver" : "Activer"}
                        </Btn>
                        <div style={{ flex: 1 }} />
                        <Btn danger onClick={supprimer} style={{ fontSize: 10 }}>
                            Suppr.
                        </Btn>
                    </div>
                    <Msg text={msg} />
                    {edit && (
                        <QuestionsEditor
                            key={cur.id + ":" + cur.questions.length + ":" + cur.source}
                            cp={cur}
                            onSaved={(c) => {
                                setCur(c)
                                onChanged()
                            }}
                        />
                    )}
                </div>
            )}
        </div>
    )
}

function AddCheckpoint({ doc, onDone }: { doc: Doc; onDone: () => void }) {
    // Positions proposées : juste avant chaque titre (fin de la partie précédente).
    const options = (doc.blocks || [])
        .map((b, i) => ({ b, i }))
        .filter(({ b, i }) => i > 0 && ["h1", "h2", "h3"].includes(b.t))
        .filter(({ i }) => !doc.checkpoints.some((c) => c.anchor_block === i - 1))
    const [pos, setPos] = React.useState(options[0] ? String(options[0].i - 1) : "")
    const [elo, setElo] = React.useState("0")
    const [busy, setBusy] = React.useState(false)
    const [msg, setMsg] = React.useState("")

    async function ajouter() {
        setBusy(true)
        setMsg("")
        try {
            const opt = options.find((o) => String(o.i - 1) === pos)
            await api(`/lib/admin/documents/${doc.id}/checkpoints`, "POST", {
                anchor_block: Number(pos),
                titre: opt ? `Point de passage — avant « ${opt.b.text.slice(0, 60)} »` : "Point de passage",
                elo_requis: Number(elo) || 0,
            })
            onDone()
        } catch (e: any) {
            setMsg(`ERR: ${e.message}`)
        } finally {
            setBusy(false)
        }
    }

    if (!options.length) return null
    return (
        <div style={{ display: "flex", flexDirection: "column", gap: 6, padding: "8px 10px" }}>
            <FL>Ajouter un point de passage</FL>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 64px auto", gap: 6 }}>
                <SI value={pos} onChange={(e: any) => setPos(e.target.value)}>
                    {options.map(({ b, i }) => (
                        <option key={i} value={i - 1}>
                            {b.t === "h1" ? "" : b.t === "h2" ? "· " : "·· "}avant « {b.text.slice(0, 70)} »
                        </option>
                    ))}
                </SI>
                <TI type="number" value={elo} onChange={(e: any) => setElo(e.target.value)} placeholder="Elo" />
                <Btn onClick={ajouter} disabled={busy || !pos}>
                    {busy ? "…" : "+ Ajouter"}
                </Btn>
            </div>
            <div style={{ fontSize: 9.5, fontFamily: mono, color: t2 }}>
                Questions tirées des paragraphes qui précèdent ; à corriger ou à
                générer avec l'IA ensuite.
            </div>
            <Msg text={msg} />
        </div>
    )
}

// ══════════════════════════════════════════════════════════
// Document
// ══════════════════════════════════════════════════════════

function DocDetail({
    id,
    onChanged,
    onDeleted,
}: {
    id: number
    onChanged: () => void
    onDeleted: () => void
}) {
    const [doc, setDoc] = React.useState<Doc | null>(null)
    const [form, setForm] = React.useState<any>(null)
    const [busy, setBusy] = React.useState(false)
    const [msg, setMsg] = React.useState("")
    const [onglet, setOnglet] = React.useState<"points" | "infos" | "plan">("points")
    const fileRef = React.useRef<HTMLInputElement>(null)

    function load() {
        return api(`/lib/admin/documents/${id}`)
            .then((d: Doc) => {
                setDoc(d)
                setForm({
                    titre: d.titre,
                    sous_titre: d.sous_titre,
                    description: d.description,
                    kind: d.kind,
                    niveau: d.niveau,
                    matiere: d.matiere,
                    maj: d.maj,
                    elo_requis: String(d.elo_requis),
                })
            })
            .catch((e) => setMsg(`ERR: ${e.message}`))
    }
    React.useEffect(() => {
        load()
    }, [id])

    async function patch(body: any, ok: string) {
        setBusy(true)
        setMsg("")
        try {
            await api(`/lib/admin/documents/${id}`, "PATCH", body)
            await load()
            setMsg(`✓ ${ok}`)
            onChanged()
        } catch (e: any) {
            setMsg(`ERR: ${e.message}`)
        } finally {
            setBusy(false)
        }
    }
    async function redetecter() {
        if (!confirm("Replacer tous les points de passage ? Les questions éditées et la progression des élèves sont perdues.")) return
        setBusy(true)
        try {
            const d = await api(`/lib/admin/documents/${id}/detecter`, "POST", {})
            await load()
            setMsg(`✓ ${d.detection.points_passage} point(s) de passage placé(s)`)
            onChanged()
        } catch (e: any) {
            setMsg(`ERR: ${e.message}`)
        } finally {
            setBusy(false)
        }
    }
    async function nouvelleVersion(f: File | null) {
        if (!f) return
        setBusy(true)
        setMsg("")
        try {
            const fd = new FormData()
            fd.append("file", f)
            const d = await api(`/lib/admin/documents/${id}/fichier`, "POST", fd)
            await load()
            setMsg(`✓ Nouvelle version · ${d.detection.points_passage} point(s) de passage, progression conservée`)
            onChanged()
        } catch (e: any) {
            setMsg(`ERR: ${e.message}`)
        } finally {
            setBusy(false)
            if (fileRef.current) fileRef.current.value = ""
        }
    }
    async function supprimer() {
        if (!doc || !confirm(`Supprimer « ${doc.titre} » et la progression de ses ${doc.lecteurs} lecteur(s) ?`)) return
        try {
            await api(`/lib/admin/documents/${id}`, "DELETE")
            onDeleted()
        } catch (e: any) {
            setMsg(`ERR: ${e.message}`)
        }
    }

    if (!doc || !form) return <div style={{ ...VIDE, padding: 16 }}>{msg || "Chargement…"}</div>

    const set = (k: string) => (e: any) => setForm((f: any) => ({ ...f, [k]: e.target.value }))
    const plan = (doc.blocks || []).filter((b) => ["h1", "h2"].includes(b.t))

    return (
        <div style={{ padding: "8px 14px 14px", background: "rgba(255,255,255,0.015)", display: "flex", flexDirection: "column", gap: 8 }}>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" as const, alignItems: "center" }}>
                <Btn
                    primary={!doc.is_published}
                    onClick={() => patch({ is_published: !doc.is_published }, doc.is_published ? "Dépublié" : "Publié")}
                    disabled={busy}
                    style={{ fontSize: 10 }}
                >
                    {doc.is_published ? "Dépublier" : "Publier"}
                </Btn>
                {doc.is_published && (
                    <Btn onClick={() => window.open(`${LIB_PAGE}?doc=${doc.id}`, "_blank")} style={{ fontSize: 10 }}>
                        Ouvrir ↗
                    </Btn>
                )}
                <Btn onClick={() => fileRef.current?.click()} disabled={busy} style={{ fontSize: 10 }}
                     title="Remplace le contenu ; points de passage et progression conservés">
                    Nouvelle version
                </Btn>
                <input
                    ref={fileRef}
                    type="file"
                    accept=".docx,.pdf"
                    onChange={(e) => nouvelleVersion(e.target.files?.[0] || null)}
                    style={{ display: "none" }}
                />
                <Btn
                    onClick={() => download(`/lib/admin/documents/${id}/source`, doc.source_filename || "cours").catch(() => {})}
                    style={{ fontSize: 10 }}
                >
                    ↓ Source
                </Btn>
                <div style={{ flex: 1 }} />
                <Btn danger onClick={supprimer} style={{ fontSize: 10 }}>
                    Suppr.
                </Btn>
            </div>
            <Msg text={msg} />

            <div style={{ display: "flex", gap: 5 }}>
                <MiniBtn actif={onglet === "points"} onClick={() => setOnglet("points")}>
                    Points de passage ({doc.checkpoints.length})
                </MiniBtn>
                <MiniBtn actif={onglet === "infos"} onClick={() => setOnglet("infos")}>
                    Infos
                </MiniBtn>
                <MiniBtn actif={onglet === "plan"} onClick={() => setOnglet("plan")}>
                    Plan détecté
                </MiniBtn>
            </div>

            {onglet === "points" && (
                <div style={{ border: `1px solid ${b0}`, borderRadius: 12, padding: 4 }}>
                    {doc.checkpoints.length === 0 && (
                        <div style={{ ...VIDE, padding: 14 }}>Aucun point de passage : le cours se lit d'un bloc.</div>
                    )}
                    {doc.checkpoints.map((c, i) => (
                        <CheckpointRow key={c.id} cp={c} index={i} onChanged={() => { load(); onChanged() }} />
                    ))}
                    <AddCheckpoint doc={doc} onDone={() => { load(); onChanged() }} />
                    <div style={{ padding: "0 10px 8px" }}>
                        <Btn onClick={redetecter} disabled={busy} style={{ fontSize: 10 }}>
                            ↻ Re-détecter
                        </Btn>
                    </div>
                </div>
            )}

            {onglet === "infos" && (
                <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                    <div>
                        <FL>Titre</FL>
                        <TI value={form.titre} onChange={set("titre")} />
                    </div>
                    <div>
                        <FL>Sous-titre</FL>
                        <TI value={form.sous_titre} onChange={set("sous_titre")} />
                    </div>
                    <div>
                        <FL>Description (bibliothèque)</FL>
                        <TAI value={form.description} onChange={set("description")} />
                    </div>
                    <div style={{ display: "grid", gridTemplateColumns: "1fr 0.7fr 1fr", gap: 6 }}>
                        <div>
                            <FL>Type</FL>
                            <SI value={form.kind} onChange={set("kind")}>
                                {KINDS.map((k) => (
                                    <option key={k.id} value={k.id}>
                                        {k.label}
                                    </option>
                                ))}
                            </SI>
                        </div>
                        <div>
                            <FL>Niveau</FL>
                            <TI value={form.niveau} onChange={set("niveau")} />
                        </div>
                        <div>
                            <FL>Matière</FL>
                            <TI value={form.matiere} onChange={set("matiere")} />
                        </div>
                    </div>
                    <div style={{ display: "grid", gridTemplateColumns: "1.4fr 1fr", gap: 6 }}>
                        <div>
                            <FL>Mise à jour</FL>
                            <TI value={form.maj} onChange={set("maj")} placeholder="À jour au …" />
                        </div>
                        <div>
                            <FL>Elo pour ouvrir le document</FL>
                            <TI type="number" value={form.elo_requis} onChange={set("elo_requis")} />
                        </div>
                    </div>
                    <Btn
                        primary
                        disabled={busy}
                        onClick={() => patch({ ...form, elo_requis: Number(form.elo_requis) || 0 }, "Infos enregistrées")}
                    >
                        Enregistrer
                    </Btn>
                </div>
            )}

            {onglet === "plan" && (
                <div style={{ border: `1px solid ${b0}`, borderRadius: 12, maxHeight: 260, overflowY: "auto", padding: "6px 0" }}>
                    {[
                        ...plan.map((b) => ({ i: Number(b.id.slice(1)), b, c: null as Checkpoint | null })),
                        ...doc.checkpoints.map((c) => ({ i: c.anchor_block + 0.5, b: null as any, c })),
                    ]
                        .sort((x, y) => x.i - y.i)
                        .map(({ b, c }) =>
                            c ? (
                                <div
                                    key={`cp${c.id}`}
                                    style={{ fontSize: 9.5, fontFamily: code, color: c.final ? VIOLET : BLUE, padding: "3px 12px" }}
                                >
                                    ◆ {c.final ? "quiz final" : "point de passage"} · {c.elo_requis} Elo · {c.questions.length} q.
                                </div>
                            ) : (
                                <div
                                    key={b.id}
                                    style={{
                                        fontSize: b.t === "h1" ? 11.5 : 10.5,
                                        fontWeight: b.t === "h1" ? 700 : 400,
                                        color: b.t === "h1" ? t0 : t1,
                                        padding: `3px 12px 3px ${b.t === "h1" ? 12 : 26}px`,
                                        fontFamily: mono,
                                    }}
                                >
                                    {b.text}
                                </div>
                            )
                        )}
                    <div style={{ fontSize: 9.5, fontFamily: mono, color: t2, padding: "6px 12px 2px" }}>
                        {doc.nb_blocs} blocs · {doc.minutes ?? "?"} min de lecture
                    </div>
                </div>
            )}
        </div>
    )
}

function DocRow({
    d,
    open,
    onToggle,
    onChanged,
}: {
    d: Doc
    open: boolean
    onToggle: () => void
    onChanged: () => void
}) {
    const [hov, setHov] = React.useState(false)
    return (
        <div style={{ borderBottom: `1px solid rgba(255,255,255,0.04)` }}>
            <div
                onClick={onToggle}
                onMouseEnter={() => setHov(true)}
                onMouseLeave={() => setHov(false)}
                style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 8,
                    padding: "9px 14px",
                    cursor: "pointer",
                    background: open ? sA : hov ? sH : "transparent",
                    borderLeft: `2px solid ${open ? b2 : "transparent"}`,
                    minWidth: 0,
                }}
            >
                <div style={{ flex: 1, minWidth: 0 }}>
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
                        {d.titre}
                    </div>
                    <div style={{ fontSize: 10, fontFamily: mono, color: t2, marginTop: 1 }}>
                        #{d.id} · {kindLabel(d.kind)}
                        {d.niveau ? ` · ${d.niveau}` : ""}
                        {d.matiere ? ` · ${d.matiere}` : ""} · {d.checkpoints.length} pt(s) de passage ·{" "}
                        {d.lecteurs} lecteur(s)
                    </div>
                </div>
                {d.elo_requis > 0 && (
                    <Tag color={WARN} border="rgba(255,209,102,0.35)">
                        {d.elo_requis} ELO
                    </Tag>
                )}
                <Tag color={d.is_published ? OK : t2} border={d.is_published ? "rgba(91,227,163,0.4)" : b0}>
                    {d.is_published ? "PUBLIÉ" : "BROUILLON"}
                </Tag>
                <span style={{ fontSize: 11, color: open ? t0 : t2 }}>›</span>
            </div>
            {open && (
                <DocDetail
                    id={d.id}
                    onChanged={onChanged}
                    onDeleted={() => {
                        onToggle()
                        onChanged()
                    }}
                />
            )}
        </div>
    )
}

// ══════════════════════════════════════════════════════════
// Section
// ══════════════════════════════════════════════════════════

export function LibSection() {
    const [docs, setDocs] = React.useState<Doc[]>([])
    const [loading, setLoading] = React.useState(true)
    const [err, setErr] = React.useState("")
    const [msg, setMsg] = React.useState("")
    const [showImport, setShowImport] = React.useState(false)
    const [openId, setOpenId] = React.useState<number | null>(null)
    const [filtre, setFiltre] = React.useState<"tous" | Kind>("tous")
    const [search, setSearch] = React.useState("")

    function load() {
        return api("/lib/admin/documents")
            .then((d) => {
                setDocs(d?.items || [])
                setErr("")
            })
            .catch((e) => setErr(e.message))
            .finally(() => setLoading(false))
    }
    React.useEffect(() => {
        load()
    }, [])

    const q = search.toLowerCase()
    const liste = docs.filter(
        (d) =>
            (filtre === "tous" || d.kind === filtre) &&
            (!q || [d.titre, d.matiere, d.niveau].some((v) => (v || "").toLowerCase().includes(q)))
    )
    const publies = docs.filter((d) => d.is_published).length

    return (
        <div style={{ display: "flex", flexDirection: "column", height: "100%", maxHeight: "100%" }}>
            <div
                style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    gap: 8,
                    padding: "11px 14px",
                    borderBottom: `1px solid ${b0}`,
                    flexShrink: 0,
                }}
            >
                <span style={{ fontSize: 11, fontFamily: mono, color: t1, whiteSpace: "nowrap" as const }}>
                    {docs.length} document(s) · {publies} publié(s)
                </span>
                <span style={{ display: "flex", gap: 6, alignItems: "center" }}>
                    <TI
                        value={search}
                        onChange={(e: any) => setSearch(e.target.value)}
                        placeholder="Rechercher…"
                        style={{ width: 120, height: 28 }}
                    />
                    <Btn onClick={() => setShowImport((v) => !v)}>{showImport ? "✕ Annuler" : "+ Importer"}</Btn>
                </span>
            </div>
            <div
                style={{
                    display: "flex",
                    gap: 5,
                    padding: "7px 14px",
                    borderBottom: `1px solid ${b0}`,
                    flexShrink: 0,
                    flexWrap: "wrap" as const,
                }}
            >
                <MiniBtn actif={filtre === "tous"} onClick={() => setFiltre("tous")}>
                    Tous
                </MiniBtn>
                {KINDS.map((k) => (
                    <MiniBtn key={k.id} actif={filtre === k.id} onClick={() => setFiltre(k.id)}>
                        {k.label}
                    </MiniBtn>
                ))}
            </div>
            <div style={{ flex: 1, overflowY: "auto" }}>
                {showImport && (
                    <ImportForm
                        onDone={(d) => {
                            setShowImport(false)
                            setMsg(
                                `✓ « ${d.titre} » importé · ${d.detection?.points_passage ?? 0} point(s) de passage · ${d.nb_blocs} blocs`
                            )
                            setOpenId(d.id)
                            load()
                        }}
                    />
                )}
                {(msg || err) && (
                    <div style={{ padding: "8px 14px" }}>
                        <Msg text={err ? `ERR: ${err}` : msg} />
                    </div>
                )}
                {loading ? (
                    <div style={VIDE}>Chargement…</div>
                ) : liste.length === 0 ? (
                    <div style={VIDE}>
                        {docs.length ? "Aucun résultat." : "Aucun document : importe un premier cours."}
                    </div>
                ) : (
                    liste.map((d) => (
                        <DocRow
                            key={d.id}
                            d={d}
                            open={openId === d.id}
                            onToggle={() => setOpenId(openId === d.id ? null : d.id)}
                            onChanged={load}
                        />
                    ))
                )}
            </div>
        </div>
    )
}
