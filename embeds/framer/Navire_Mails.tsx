// Navire_Mails.tsx
// Section « Mails » de la console admin (Navire_AdminConsole.tsx).
//
// Rédiger un mail, choisir les destinataires par catégorie (tous les comptes,
// ambassadeurs, professeurs, Prép'AdJuris par niveau, par matière ou par
// groupe…), joindre des fichiers, voir l'aperçu exact (gabarit NAVIRE +
// signature : photo de profil et pseudo de l'admin), s'envoyer un test, puis
// envoyer. L'envoi part de NAVIRE par Brevo, comme les mails automatiques.
// Brouillons et historique des envois.
//
// Mêmes jeton, design tokens et primitives que la console (FL, TI, Btn, SM,
// SBar, Tag, MiniBtn) : le fichier est autonome pour ne pas dépendre de
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

// ── Primitives (copies de la console) ──

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

// ════════════════════════════════════════════════════════════════════════
// Section Mails
// ════════════════════════════════════════════════════════════════════════

type Segment = { key: string; label: string; groupe: string; count: number }
type PJ = { name: string; size: number; content_b64?: string }
type Campagne = {
    id: number
    subject: string
    body?: string
    segments: { key: string; label: string }[]
    attachments: { name: string; size: number }[]
    status: "draft" | "sending" | "sent" | "partial" | "failed"
    recipients_count: number
    sent_count: number
    failed_count: number
    error: string | null
    author: string | null
    sent_at: string | null
    created_at: string | null
    updated_at: string | null
}
type Limits = { max_attachments: number; max_attachments_bytes: number; extensions: string[] }

const STATUTS: Record<Campagne["status"], { label: string; color: string; border: string }> = {
    draft: { label: "BROUILLON", color: t1, border: b1 },
    sending: { label: "ENVOI…", color: BLUE, border: "rgba(122,167,255,0.35)" },
    sent: { label: "ENVOYÉ", color: OK, border: "rgba(91,227,163,0.35)" },
    partial: { label: "PARTIEL", color: WARN, border: "rgba(255,209,102,0.35)" },
    failed: { label: "ÉCHEC", color: BAD, border: "rgba(255,107,107,0.35)" },
}

const ORDRE_GROUPES = ["Général", "Prép'AdJuris", "Matières", "Groupes"]

function taille(o: number): string {
    if (o < 1024) return `${o} o`
    if (o < 1048576) return `${Math.round(o / 1024)} Ko`
    return `${(o / 1048576).toFixed(1).replace(".", ",")} Mo`
}

function fmtDateHeure(s: string | null): string {
    if (!s) return "—"
    return new Date(s).toLocaleString("fr-FR", {
        day: "2-digit",
        month: "short",
        hour: "2-digit",
        minute: "2-digit",
    })
}

function lireBase64(f: File): Promise<string> {
    return new Promise((ok, ko) => {
        const r = new FileReader()
        r.onload = () => ok(String(r.result || "").split(",", 2)[1] || "")
        r.onerror = () => ko(new Error(`Lecture impossible : ${f.name}`))
        r.readAsDataURL(f)
    })
}

const AIDE = [
    ["Ligne vide", "nouveau paragraphe"],
    ["## Titre", "intertitre"],
    ["- élément", "liste à puces"],
    ["**gras**  *italique*", "mise en valeur"],
    ["[texte](https://…)", "lien"],
    ["[bouton: Texte](https://…)", "bouton NAVIRE"],
    ["{prenom}", "prénom du destinataire"],
]

// ── Éditeur ─────────────────────────────────────────────────────────────

function Editeur({
    initial,
    segments,
    limits,
    onSaved,
    onClose,
}: {
    initial: Campagne | null
    segments: Segment[]
    limits: Limits | null
    onSaved: (c: Campagne) => void
    onClose: () => void
}) {
    const [id, setId] = React.useState<number | null>(initial?.id ?? null)
    const [subject, setSubject] = React.useState(initial?.subject || "")
    const [body, setBody] = React.useState(initial?.body || "")
    const [choix, setChoix] = React.useState<string[]>(initial?.segments.map((s) => s.key) || [])
    const [pjs, setPjs] = React.useState<PJ[]>(initial?.attachments.map((a) => ({ ...a })) || [])
    const [dest, setDest] = React.useState<{ count: number; items: { email: string; prenom: string }[] } | null>(
        null
    )
    const [voirDest, setVoirDest] = React.useState(false)
    const [apercu, setApercu] = React.useState<{ subject: string; html: string } | null>(null)
    const [voirApercu, setVoirApercu] = React.useState(true)
    const [busy, setBusy] = React.useState("")
    const [msg, setMsg] = React.useState("")
    const [aide, setAide] = React.useState(false)
    const [sale, setSale] = React.useState(false)
    const zone = React.useRef<HTMLTextAreaElement | null>(null)
    const fichier = React.useRef<HTMLInputElement | null>(null)

    // Destinataires effectifs (union, sans doublons) à chaque changement.
    React.useEffect(() => {
        if (!choix.length) {
            setDest({ count: 0, items: [] })
            return
        }
        let vivant = true
        api("/admin/mailing/recipients", "POST", { segments: choix })
            .then((d) => vivant && setDest(d))
            .catch((e) => vivant && setMsg(`Erreur : ${e.message}`))
        return () => {
            vivant = false
        }
    }, [choix.join("|")])

    // Aperçu exact (gabarit + signature) avec un léger délai de frappe.
    React.useEffect(() => {
        if (!voirApercu) return
        const t = setTimeout(() => {
            api("/admin/mailing/preview", "POST", { subject, body })
                .then(setApercu)
                .catch(() => {})
        }, 500)
        return () => clearTimeout(t)
    }, [subject, body, voirApercu])

    const modif = <T,>(f: (v: T) => void) => (v: T) => {
        f(v)
        setSale(true)
    }
    const setSubjectM = modif(setSubject)
    const setBodyM = modif(setBody)

    const basculer = (k: string) => {
        setChoix((c) => (c.includes(k) ? c.filter((x) => x !== k) : [...c, k]))
        setSale(true)
    }

    const inserer = (avant: string, apres = "", defaut = "") => {
        const el = zone.current
        if (!el) return
        const d = el.selectionStart ?? body.length
        const f = el.selectionEnd ?? body.length
        const sel = body.slice(d, f) || defaut
        const neuf = body.slice(0, d) + avant + sel + apres + body.slice(f)
        setBodyM(neuf)
        requestAnimationFrame(() => {
            el.focus()
            el.setSelectionRange(d + avant.length, d + avant.length + sel.length)
        })
    }

    const ajouterFichiers = async (files: FileList | null) => {
        if (!files || !files.length) return
        setMsg("")
        try {
            const neufs: PJ[] = []
            for (const f of Array.from(files)) {
                const ext = (f.name.split(".").pop() || "").toLowerCase()
                if (limits && !limits.extensions.includes(ext))
                    throw new Error(`« ${f.name} » : type non accepté (${limits.extensions.join(", ")}).`)
                neufs.push({ name: f.name, size: f.size, content_b64: await lireBase64(f) })
            }
            const tous = [...pjs.filter((p) => !neufs.some((n) => n.name === p.name)), ...neufs]
            const total = tous.reduce((t, p) => t + p.size, 0)
            if (limits && tous.length > limits.max_attachments)
                throw new Error(`${limits.max_attachments} pièces jointes au maximum.`)
            if (limits && total > limits.max_attachments_bytes)
                throw new Error(`Pièces jointes trop lourdes : ${taille(total)} (max ${taille(limits.max_attachments_bytes)}).`)
            setPjs(tous)
            setSale(true)
        } catch (e: any) {
            setMsg(`Erreur : ${e.message}`)
        } finally {
            if (fichier.current) fichier.current.value = ""
        }
    }

    const enregistrer = async (silencieux = false): Promise<number | null> => {
        setBusy("save")
        if (!silencieux) setMsg("")
        try {
            const payload = {
                subject,
                body,
                segments: choix,
                attachments: pjs.map((p) => (p.content_b64 ? { name: p.name, content_b64: p.content_b64 } : { name: p.name })),
            }
            const c: Campagne = id
                ? await api(`/admin/mailing/campaigns/${id}`, "PUT", payload)
                : await api("/admin/mailing/campaigns", "POST", payload)
            setId(c.id)
            // Les PJ sont désormais en base : inutile de renvoyer leur contenu.
            setPjs(c.attachments.map((a) => ({ ...a })))
            setSale(false)
            onSaved(c)
            if (!silencieux) setMsg("Brouillon enregistré.")
            return c.id
        } catch (e: any) {
            setMsg(`Erreur : ${e.message}`)
            return null
        } finally {
            setBusy("")
        }
    }

    const tester = async () => {
        const cid = await enregistrer(true)
        if (!cid) return
        setBusy("test")
        setMsg("")
        try {
            const r = await api(`/admin/mailing/campaigns/${cid}/test`, "POST")
            setMsg(`Test envoyé à ${r.to}.`)
        } catch (e: any) {
            setMsg(`Erreur : ${e.message}`)
        } finally {
            setBusy("")
        }
    }

    const envoyer = async () => {
        const n = dest?.count || 0
        if (!n) return setMsg("Erreur : aucun destinataire sélectionné.")
        if (!subject.trim() || !body.trim()) return setMsg("Erreur : objet et message obligatoires.")
        const libelles = segments.filter((s) => choix.includes(s.key)).map((s) => s.label)
        if (
            !window.confirm(
                `Envoyer « ${subject} » à ${n} destinataire${n > 1 ? "s" : ""} ?\n\n` +
                    `${libelles.join(" · ")}\n` +
                    (pjs.length ? `${pjs.length} pièce(s) jointe(s)\n` : "") +
                    `\nL'envoi est définitif.`
            )
        )
            return
        const cid = await enregistrer(true)
        if (!cid) return
        setBusy("send")
        setMsg("")
        try {
            const c: Campagne = await api(`/admin/mailing/campaigns/${cid}/send`, "POST")
            onSaved(c)
            if (c.status === "sent") setMsg(`Envoyé à ${c.sent_count} destinataire${c.sent_count > 1 ? "s" : ""}.`)
            else setMsg(`Erreur : ${c.sent_count} envoyés, ${c.failed_count} en échec.\n${c.error || ""}`)
            if (c.status !== "failed") setTimeout(onClose, 1200)
        } catch (e: any) {
            setMsg(`Erreur : ${e.message}`)
        } finally {
            setBusy("")
        }
    }

    const groupes = ORDRE_GROUPES.map((g) => ({ g, items: segments.filter((s) => s.groupe === g) })).filter(
        (x) => x.items.length
    )
    const totalPj = pjs.reduce((t, p) => t + p.size, 0)

    return (
        <div style={{ flex: 1, overflowY: "auto", padding: 14, display: "flex", flexDirection: "column", gap: 14 }}>
            {/* Objet */}
            <div>
                <FL>Objet</FL>
                <TI
                    value={subject}
                    onChange={(e: any) => setSubjectM(e.target.value)}
                    placeholder="Ex : Rentrée Prép'AdJuris — ton planning"
                    style={{ height: 36, fontSize: 13 }}
                />
            </div>

            {/* Destinataires */}
            <div>
                <FL>Destinataires</FL>
                <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                    {groupes.map(({ g, items }) => (
                        <div key={g}>
                            <div style={{ fontSize: 9.5, fontFamily: code, color: t2, marginBottom: 4 }}>{g}</div>
                            <div style={{ display: "flex", flexWrap: "wrap" as const, gap: 5 }}>
                                {items.map((s) => (
                                    <MiniBtn key={s.key} actif={choix.includes(s.key)} onClick={() => basculer(s.key)}>
                                        {choix.includes(s.key) ? "✓ " : ""}
                                        {s.label} <span style={{ color: t2 }}>· {s.count}</span>
                                    </MiniBtn>
                                ))}
                            </div>
                        </div>
                    ))}
                </div>
                <div
                    style={{
                        marginTop: 8,
                        display: "flex",
                        alignItems: "center",
                        gap: 8,
                        fontSize: 11,
                        fontFamily: mono,
                        color: dest?.count ? t0 : t2,
                    }}
                >
                    <span style={{ fontWeight: 700 }}>
                        {dest ? `${dest.count} destinataire${dest.count > 1 ? "s" : ""}` : "…"}
                    </span>
                    <span style={{ color: t2 }}>une seule fois par adresse</span>
                    {!!dest?.count && (
                        <MiniBtn actif={voirDest} onClick={() => setVoirDest((v) => !v)}>
                            {voirDest ? "Masquer" : "Voir la liste"}
                        </MiniBtn>
                    )}
                </div>
                {voirDest && dest && (
                    <div
                        style={{
                            marginTop: 6,
                            maxHeight: 160,
                            overflowY: "auto",
                            border: `1px solid ${b0}`,
                            borderRadius: 10,
                            padding: "6px 10px",
                            fontSize: 11,
                            fontFamily: code,
                            color: t1,
                        }}
                    >
                        {dest.items.map((d) => (
                            <div key={d.email} style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
                                <span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{d.email}</span>
                                <span style={{ color: t2, flexShrink: 0 }}>{d.prenom}</span>
                            </div>
                        ))}
                    </div>
                )}
            </div>

            {/* Message */}
            <div>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 6 }}>
                    <FL>Message</FL>
                    <span style={{ display: "flex", gap: 4, flexWrap: "wrap" as const, marginBottom: 4 }}>
                        <MiniBtn onClick={() => inserer("**", "**", "gras")}>B</MiniBtn>
                        <MiniBtn onClick={() => inserer("*", "*", "italique")}>I</MiniBtn>
                        <MiniBtn onClick={() => inserer("\n## ", "", "Intertitre")}>Titre</MiniBtn>
                        <MiniBtn onClick={() => inserer("\n- ", "", "élément")}>Liste</MiniBtn>
                        <MiniBtn onClick={() => inserer("[", "](https://)", "texte du lien")}>Lien</MiniBtn>
                        <MiniBtn onClick={() => inserer("\n\n[bouton: ", "](https://navire.fr)\n\n", "Texte du bouton")}>
                            Bouton
                        </MiniBtn>
                        <MiniBtn onClick={() => inserer("{prenom}")}>{"{prenom}"}</MiniBtn>
                        <MiniBtn actif={aide} onClick={() => setAide((v) => !v)}>
                            ?
                        </MiniBtn>
                    </span>
                </div>
                {aide && (
                    <div
                        style={{
                            marginBottom: 6,
                            padding: "8px 10px",
                            border: `1px solid ${b0}`,
                            borderRadius: 10,
                            display: "grid",
                            gridTemplateColumns: "auto 1fr",
                            gap: "3px 12px",
                            fontSize: 10.5,
                        }}
                    >
                        {AIDE.map(([a, b]) => (
                            <React.Fragment key={a}>
                                <span style={{ fontFamily: code, color: t0 }}>{a}</span>
                                <span style={{ fontFamily: mono, color: t1 }}>{b}</span>
                            </React.Fragment>
                        ))}
                    </div>
                )}
                <textarea
                    ref={zone}
                    value={body}
                    onChange={(e) => setBodyM(e.target.value)}
                    placeholder={"Bonjour {prenom},\n\nVoici les infos de la semaine…"}
                    style={{
                        width: "100%",
                        minHeight: 220,
                        padding: 10,
                        background: s0,
                        border: `1px solid ${b0}`,
                        borderRadius: 10,
                        color: t0,
                        fontSize: 12.5,
                        lineHeight: 1.55,
                        fontFamily: mono,
                        outline: "none",
                        resize: "vertical" as const,
                        boxSizing: "border-box" as const,
                    }}
                />
                <div style={{ fontSize: 10, fontFamily: mono, color: t2, marginTop: 4 }}>
                    Signé automatiquement avec ta photo de profil et ton pseudo.
                </div>
            </div>

            {/* Pièces jointes */}
            <div>
                <FL>Pièces jointes</FL>
                <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
                    {pjs.map((p) => (
                        <div
                            key={p.name}
                            style={{
                                display: "flex",
                                alignItems: "center",
                                gap: 8,
                                padding: "6px 10px",
                                border: `1px solid ${b0}`,
                                borderRadius: 10,
                                fontSize: 11.5,
                                fontFamily: mono,
                            }}
                        >
                            <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" as const }}>
                                📎 {p.name}
                            </span>
                            <span style={{ color: t2, fontSize: 10.5 }}>{taille(p.size)}</span>
                            <button
                                onClick={() => {
                                    setPjs((l) => l.filter((x) => x.name !== p.name))
                                    setSale(true)
                                }}
                                style={{ background: "none", border: "none", color: BAD, cursor: "pointer", fontSize: 12 }}
                                title="Retirer"
                            >
                                ✕
                            </button>
                        </div>
                    ))}
                    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                        <Btn onClick={() => fichier.current?.click()} style={{ fontSize: 10.5, padding: "5px 12px" }}>
                            + Ajouter un fichier
                        </Btn>
                        <span style={{ fontSize: 10, fontFamily: mono, color: t2 }}>
                            {pjs.length ? `${taille(totalPj)} · ` : ""}max {limits ? taille(limits.max_attachments_bytes) : "8 Mo"} au total
                        </span>
                        <input
                            ref={fichier}
                            type="file"
                            multiple
                            accept={limits ? limits.extensions.map((e) => `.${e}`).join(",") : undefined}
                            onChange={(e) => ajouterFichiers(e.target.files)}
                            style={{ display: "none" }}
                        />
                    </div>
                </div>
            </div>

            {/* Aperçu */}
            <div>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                    <FL>Aperçu (tel que reçu, {"{prenom}"} = ton pseudo)</FL>
                    <MiniBtn actif={voirApercu} onClick={() => setVoirApercu((v) => !v)}>
                        {voirApercu ? "Masquer" : "Afficher"}
                    </MiniBtn>
                </div>
                {voirApercu && apercu && (
                    <div style={{ border: `1px solid ${b0}`, borderRadius: 12, overflow: "hidden", marginTop: 4 }}>
                        <div
                            style={{
                                padding: "8px 12px",
                                fontSize: 12,
                                fontFamily: mono,
                                borderBottom: `1px solid ${b0}`,
                                background: s0,
                            }}
                        >
                            <span style={{ color: t2 }}>Objet : </span>
                            {apercu.subject || <span style={{ color: t2 }}>(vide)</span>}
                        </div>
                        <iframe
                            title="Aperçu du mail"
                            srcDoc={apercu.html}
                            sandbox=""
                            style={{ width: "100%", height: 520, border: "none", background: "#0a0a0a", display: "block" }}
                        />
                    </div>
                )}
            </div>

            <SM text={msg} />

            {/* Actions */}
            <div
                style={{
                    display: "flex",
                    gap: 8,
                    flexWrap: "wrap" as const,
                    position: "sticky" as const,
                    bottom: -14,
                    padding: "10px 0 14px",
                    background: "linear-gradient(transparent, rgba(10,10,18,0.92) 30%)",
                }}
            >
                <Btn onClick={onClose}>← Retour</Btn>
                <span style={{ flex: 1 }} />
                <Btn onClick={() => enregistrer()} disabled={!!busy}>
                    {busy === "save" ? "…" : sale || !id ? "Enregistrer le brouillon" : "Enregistré ✓"}
                </Btn>
                <Btn onClick={tester} disabled={!!busy || !subject.trim() || !body.trim()}>
                    {busy === "test" ? "Envoi…" : "M'envoyer un test"}
                </Btn>
                <Btn primary onClick={envoyer} disabled={!!busy || !dest?.count || !subject.trim() || !body.trim()}>
                    {busy === "send" ? "Envoi en cours…" : `Envoyer${dest?.count ? ` à ${dest.count}` : ""}`}
                </Btn>
            </div>
        </div>
    )
}

// ── Fiche d'un mail envoyé (lecture seule) ──────────────────────────────

function Envoye({
    c,
    onDuplicate,
    onDelete,
    onClose,
}: {
    c: Campagne
    onDuplicate: () => void
    onDelete: () => void
    onClose: () => void
}) {
    const [apercu, setApercu] = React.useState<string>("")
    React.useEffect(() => {
        api(`/admin/mailing/campaigns/${c.id}`)
            .then((full) => api("/admin/mailing/preview", "POST", { subject: full.subject, body: full.body || "" }))
            .then((p) => setApercu(p.html))
            .catch(() => {})
    }, [c.id])
    const st = STATUTS[c.status]
    return (
        <div style={{ flex: 1, overflowY: "auto", padding: 14, display: "flex", flexDirection: "column", gap: 12 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <Tag color={st.color} border={st.border}>
                    {st.label}
                </Tag>
                <span style={{ ...TITRE, fontSize: 14 }}>{c.subject}</span>
            </div>
            <div style={{ fontSize: 11, fontFamily: mono, color: t1, lineHeight: 1.7 }}>
                Envoyé le {fmtDateHeure(c.sent_at)} par {c.author || "—"}
                <br />
                {c.sent_count} / {c.recipients_count} destinataires
                {c.failed_count ? ` · ${c.failed_count} en échec` : ""}
                <br />
                {c.segments.map((s) => s.label).join(" · ")}
                {!!c.attachments.length && (
                    <>
                        <br />
                        📎 {c.attachments.map((a) => a.name).join(", ")}
                    </>
                )}
            </div>
            {c.error && <SM text={`Erreur : ${c.error}`} />}
            {apercu && (
                <iframe
                    title="Mail envoyé"
                    srcDoc={apercu}
                    sandbox=""
                    style={{ width: "100%", height: 480, border: `1px solid ${b0}`, borderRadius: 12, background: "#0a0a0a" }}
                />
            )}
            <div style={{ display: "flex", gap: 8 }}>
                <Btn onClick={onClose}>← Retour</Btn>
                <span style={{ flex: 1 }} />
                <Btn danger onClick={onDelete}>
                    Supprimer de l'historique
                </Btn>
                <Btn primary onClick={onDuplicate}>
                    Dupliquer
                </Btn>
            </div>
        </div>
    )
}

// ── Section ─────────────────────────────────────────────────────────────

const TITRE: React.CSSProperties = {
    fontSize: 13,
    fontWeight: 500,
    color: t0,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
}

export function MailsSection() {
    const [segments, setSegments] = React.useState<Segment[]>([])
    const [limits, setLimits] = React.useState<Limits | null>(null)
    const [campagnes, setCampagnes] = React.useState<Campagne[]>([])
    const [ouvert, setOuvert] = React.useState<{ mode: "edit" | "view"; c: Campagne | null } | null>(null)
    const [loading, setLoading] = React.useState(true)
    const [err, setErr] = React.useState("")
    const [tick, setTick] = React.useState(0)

    React.useEffect(() => {
        setLoading(true)
        setErr("")
        Promise.all([
            api("/admin/mailing/segments").then((d) => {
                setSegments(d?.items || [])
                setLimits(d?.limits || null)
            }),
            api("/admin/mailing/campaigns").then((d) => setCampagnes(d?.items || [])),
        ])
            .catch((e) => setErr(e.message))
            .finally(() => setLoading(false))
    }, [tick])

    const recharger = () => setTick((t) => t + 1)

    const ouvrir = async (c: Campagne) => {
        try {
            const full: Campagne = await api(`/admin/mailing/campaigns/${c.id}`)
            setOuvert({ mode: full.status === "draft" ? "edit" : "view", c: full })
        } catch (e: any) {
            setErr(e.message)
        }
    }

    const dupliquer = async (c: Campagne) => {
        try {
            const d: Campagne = await api(`/admin/mailing/campaigns/${c.id}/duplicate`, "POST")
            setOuvert({ mode: "edit", c: d })
            recharger()
        } catch (e: any) {
            setErr(e.message)
        }
    }

    const supprimer = async (c: Campagne) => {
        if (!window.confirm(`Supprimer « ${c.subject || "sans objet"} » ?`)) return
        try {
            await api(`/admin/mailing/campaigns/${c.id}`, "DELETE")
            setOuvert(null)
            recharger()
        } catch (e: any) {
            setErr(e.message)
        }
    }

    const brouillons = campagnes.filter((c) => c.status === "draft")
    const envoyes = campagnes.filter((c) => c.status !== "draft")

    return (
        <div style={{ display: "flex", flexDirection: "column", height: "100%", maxHeight: "100%" }}>
            <SBar
                left={
                    ouvert ? (
                        ouvert.mode === "edit" ? (
                            ouvert.c ? "Modifier le brouillon" : "Nouveau mail"
                        ) : (
                            "Mail envoyé"
                        )
                    ) : (
                        `Mails · ${brouillons.length} brouillon${brouillons.length > 1 ? "s" : ""} · ${envoyes.length} envoyé${envoyes.length > 1 ? "s" : ""}`
                    )
                }
                right={
                    <span style={{ display: "flex", gap: 6 }}>
                        {!ouvert && (
                            <Btn primary onClick={() => setOuvert({ mode: "edit", c: null })} style={{ fontSize: 10, padding: "4px 12px" }}>
                                + Nouveau mail
                            </Btn>
                        )}
                        <Btn onClick={recharger} style={{ fontSize: 10, padding: "4px 10px" }}>
                            ↻
                        </Btn>
                    </span>
                }
            />

            {err && (
                <div style={{ padding: "8px 14px" }}>
                    <SM text={`Erreur : ${err}`} />
                </div>
            )}

            {ouvert?.mode === "edit" && (
                <Editeur
                    key={ouvert.c?.id ?? "nouveau"}
                    initial={ouvert.c}
                    segments={segments}
                    limits={limits}
                    onSaved={() => recharger()}
                    onClose={() => {
                        setOuvert(null)
                        recharger()
                    }}
                />
            )}

            {ouvert?.mode === "view" && ouvert.c && (
                <Envoye
                    c={ouvert.c}
                    onDuplicate={() => dupliquer(ouvert.c!)}
                    onDelete={() => supprimer(ouvert.c!)}
                    onClose={() => setOuvert(null)}
                />
            )}

            {!ouvert && (
                <div style={{ flex: 1, overflowY: "auto" }}>
                    {loading && !campagnes.length ? (
                        <div style={VIDE}>Chargement…</div>
                    ) : !campagnes.length ? (
                        <div style={VIDE}>
                            Aucun mail pour l'instant.
                            <br />
                            « + Nouveau mail » pour rédiger, choisir les destinataires et joindre des fichiers.
                        </div>
                    ) : (
                        [
                            { titre: "Brouillons", items: brouillons },
                            { titre: "Envoyés", items: envoyes },
                        ]
                            .filter((b) => b.items.length)
                            .map((b) => (
                                <div key={b.titre}>
                                    <div
                                        style={{
                                            padding: "10px 14px 4px",
                                            fontSize: 10,
                                            fontFamily: code,
                                            color: t2,
                                            textTransform: "uppercase" as const,
                                            letterSpacing: 1.1,
                                        }}
                                    >
                                        {b.titre}
                                    </div>
                                    {b.items.map((c) => (
                                        <LigneMail key={c.id} c={c} onClick={() => ouvrir(c)} />
                                    ))}
                                </div>
                            ))
                    )}
                </div>
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
    lineHeight: 1.7,
}

function LigneMail({ c, onClick }: { c: Campagne; onClick: () => void }) {
    const [hov, setHov] = React.useState(false)
    const st = STATUTS[c.status]
    const quand = c.status === "draft" ? `modifié ${fmtDateHeure(c.updated_at)}` : fmtDateHeure(c.sent_at)
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
                background: hov ? sH : "transparent",
                cursor: "pointer",
                minWidth: 0,
            }}
        >
            <div style={{ flex: 1, minWidth: 0 }}>
                <div style={TITRE}>{c.subject || <span style={{ color: t2 }}>(sans objet)</span>}</div>
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
                    {c.segments.map((s) => s.label).join(" · ") || "aucun destinataire"}
                    {c.attachments.length ? ` · 📎 ${c.attachments.length}` : ""}
                </div>
            </div>
            <span style={{ fontSize: 10, fontFamily: mono, color: t2, flexShrink: 0, textAlign: "right" as const }}>
                {c.status !== "draft" && (
                    <>
                        {c.sent_count}/{c.recipients_count}
                        <br />
                    </>
                )}
                {quand}
            </span>
            <Tag color={st.color} border={st.border}>
                {st.label}
            </Tag>
            <span style={{ fontSize: 11, color: t2, flexShrink: 0 }}>›</span>
        </div>
    )
}
