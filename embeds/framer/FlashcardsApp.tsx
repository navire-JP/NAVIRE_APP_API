// FlashcardsApp.tsx : NAVIRE Flashcards
// Charte NAVIRE (comme NAVIRE Lib) : bleu et blanc uniquement, Inter + IBM Plex
// Mono, fond transparent, panneaux blancs translucides, bouton principal blanc.
// Le composant remplit son cadre Framer (100 % × 100 %) ; tailles de texte et
// de boutons lisibles, cibles tactiles de 34 px et plus.

import { useState, useEffect, useCallback } from "react"
import { motion, AnimatePresence } from "framer-motion"
import { addPropertyControls, ControlType } from "framer"

// ═══════════════════════════════════════════════════════════════
// TYPES
// ═══════════════════════════════════════════════════════════════
interface Deck {
    id: number
    title: string
    description: string
    cards_count: number
}
interface Card {
    id: number
    deck_id: number
    front: string
    back: string
    tags: string
    source_type: string
    source_file_id: number | null
    source_pages: string
}
interface UserFile {
    id: number
    filename_original: string
    size_bytes: number
    expires_at: string | null
}
interface StudyStats {
    correct: number
    incorrect: number
    total: number
    history: ("correct" | "incorrect")[]
}
type View =
    | "decks"
    | "cards"
    | "study"
    | "create-deck"
    | "edit-deck"
    | "create-card"
    | "edit-card"
    | "generate-pdf"
    | "stats"

// ═══════════════════════════════════════════════════════════════
// DESIGN TOKENS (charte NAVIRE : bleu et blanc)
// ═══════════════════════════════════════════════════════════════
const API_BASE = "https://navire-app-api.onrender.com"
const C = {
    blue: "#7AA7FF",
    blueHi: "#B5CDFF",
    blueDeep: "#4F7DFF",
    blueSoft: "rgba(122,167,255,0.1)",
    blueLine: "rgba(122,167,255,0.35)",
    panel: "rgba(255,255,255,0.035)",
    panel2: "rgba(255,255,255,0.06)",
    panel3: "rgba(255,255,255,0.1)",
    line: "rgba(255,255,255,0.07)",
    line2: "rgba(255,255,255,0.14)",
    text: "#fff",
    text2: "rgba(255,255,255,0.72)",
    text3: "rgba(255,255,255,0.45)",
    text4: "rgba(255,255,255,0.28)",
    ink: "#0a0a12",
}
const MONO = "'IBM Plex Mono', monospace"
const SANS = "'Inter', system-ui, sans-serif"
const FONTS =
    "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=IBM+Plex+Mono:wght@400;600&display=swap"

const spring = { type: "spring", stiffness: 420, damping: 34 }
const springGent = { type: "spring", stiffness: 200, damping: 28 }

function useIsDesktop() {
    const [isDesktop, setIsDesktop] = useState(
        typeof window !== "undefined" ? window.innerWidth >= 768 : true
    )
    useEffect(() => {
        const fn = () => setIsDesktop(window.innerWidth >= 768)
        window.addEventListener("resize", fn)
        return () => window.removeEventListener("resize", fn)
    }, [])
    return isDesktop
}

// ═══════════════════════════════════════════════════════════════
// ICÔNES
// ═══════════════════════════════════════════════════════════════
function Icon({ d, size = 16, width = 2 }: { d: string; size?: number; width?: number }) {
    return (
        <svg
            width={size}
            height={size}
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth={width}
            strokeLinecap="round"
            strokeLinejoin="round"
            style={{ flexShrink: 0 }}
        >
            <path d={d} />
        </svg>
    )
}
const I = {
    back: "m15 18-6-6 6-6",
    next: "m9 18 6-6-6-6",
    plus: "M12 5v14M5 12h14",
    play: "M7 4v16l13-8z",
    edit: "M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z",
    trash: "M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6",
    stats: "M3 3v18h18M7 15l4-4 3 3 6-6",
    file: "M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8zM14 2v6h6",
    tick: "M20 6 9 17l-5-5",
    x: "M18 6 6 18M6 6l12 12",
    flip: "M21 12a9 9 0 1 1-3-6.7L21 8M21 3v5h-5",
    cards: "M4 7h13a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V9a2 2 0 0 1 2-2zM7 3h13a2 2 0 0 1 2 2v11",
    cap: "M22 10 12 5 2 10l10 5zM6 12v5c3 2 9 2 12 0v-5",
}

// ═══════════════════════════════════════════════════════════════
// MINI COMPOSANTS
// ═══════════════════════════════════════════════════════════════

function Pill({
    tone = "blue",
    children,
}: {
    tone?: "blue" | "solid" | "white" | "plain"
    children: React.ReactNode
}) {
    const tones = {
        blue: { background: C.blueSoft, color: C.blue },
        solid: { background: C.blue, color: C.ink },
        white: { background: "#fff", color: C.ink },
        plain: { background: C.panel2, color: C.text2 },
    }
    return (
        <span
            style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 5,
                borderRadius: 99,
                padding: "3px 9px",
                fontSize: 11,
                fontWeight: 600,
                fontFamily: MONO,
                whiteSpace: "nowrap" as const,
                ...tones[tone],
            }}
        >
            {children}
        </span>
    )
}

function Spinner({ size = 20, color = C.blue }: { size?: number; color?: string }) {
    return (
        <motion.div
            animate={{ rotate: 360 }}
            transition={{ duration: 0.9, repeat: Infinity, ease: "linear" }}
            style={{
                width: size,
                height: size,
                borderRadius: "50%",
                border: `2px solid ${color}30`,
                borderTopColor: color,
            }}
        />
    )
}

function Label({ children, color = C.text3 }: { children: React.ReactNode; color?: string }) {
    return (
        <div
            style={{
                fontSize: 10.5,
                fontWeight: 600,
                color,
                fontFamily: MONO,
                letterSpacing: "0.08em",
                textTransform: "uppercase" as const,
                marginBottom: 6,
            }}
        >
            {children}
        </div>
    )
}

// Barres d'activité
function SparkBars({ data }: { data: number[] }) {
    const max = Math.max(...data, 1)
    return (
        <div style={{ display: "flex", alignItems: "flex-end", gap: 4, height: 36 }}>
            {data.map((v, i) => (
                <motion.div
                    key={i}
                    initial={{ scaleY: 0 }}
                    animate={{ scaleY: 1 }}
                    transition={{ delay: i * 0.04, ...springGent }}
                    style={{
                        flex: 1,
                        minWidth: 6,
                        borderRadius: 3,
                        height: `${Math.max(4, (v / max) * 36)}px`,
                        background: i === data.length - 1 ? C.blue : "rgba(122,167,255,0.35)",
                        transformOrigin: "bottom",
                    }}
                />
            ))}
        </div>
    )
}

// Anneau de réussite
function DonutChart({ correct, total, size = 56 }: { correct: number; total: number; size?: number }) {
    const pct = total > 0 ? correct / total : 0
    const r = (size - 8) / 2
    const circ = 2 * Math.PI * r
    return (
        <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} style={{ flexShrink: 0 }}>
            <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={C.panel3} strokeWidth={5} />
            <motion.circle
                cx={size / 2}
                cy={size / 2}
                r={r}
                fill="none"
                stroke={C.blue}
                strokeWidth={5}
                strokeLinecap="round"
                strokeDasharray={`${circ}`}
                initial={{ strokeDashoffset: circ }}
                animate={{ strokeDashoffset: circ - pct * circ }}
                transition={{ duration: 0.8, ease: "easeOut" }}
                style={{ transform: "rotate(-90deg)", transformOrigin: "center" }}
            />
            <text
                x={size / 2}
                y={size / 2 + 1}
                textAnchor="middle"
                dominantBaseline="middle"
                fill="#fff"
                fontSize={Math.max(11, size / 5)}
                fontWeight={700}
                fontFamily={SANS}
            >
                {Math.round(pct * 100)}%
            </text>
        </svg>
    )
}

// Dernières réponses : bleu = juste, contour blanc = à revoir
function HistoryDots({ history }: { history: ("correct" | "incorrect")[] }) {
    const last = history.slice(-12)
    return (
        <div style={{ display: "flex", gap: 4, flexWrap: "wrap" as const }}>
            {last.map((h, i) => (
                <div
                    key={i}
                    style={{
                        width: 9,
                        height: 9,
                        borderRadius: "50%",
                        boxSizing: "border-box" as const,
                        background: h === "correct" ? C.blue : "transparent",
                        border: h === "correct" ? "none" : `1.5px solid ${C.text3}`,
                    }}
                />
            ))}
        </div>
    )
}

function GlassBtn({
    children,
    onClick,
    variant = "glass",
    size = "md",
    disabled = false,
    fullWidth = false,
    title,
    style = {},
}: {
    children: React.ReactNode
    onClick?: () => void
    variant?: "glass" | "primary" | "blue" | "soft" | "ghost"
    size?: "xs" | "sm" | "md" | "lg"
    disabled?: boolean
    fullWidth?: boolean
    title?: string
    style?: React.CSSProperties
}) {
    const sizes = {
        xs: { height: 32, minWidth: 32, padding: "0 9px", fontSize: 12 },
        sm: { height: 34, minWidth: 34, padding: "0 13px", fontSize: 12.5 },
        md: { height: 38, minWidth: 38, padding: "0 18px", fontSize: 13 },
        lg: { height: 44, minWidth: 44, padding: "0 22px", fontSize: 14 },
    }
    const variants: any = {
        glass: { background: "transparent", border: `1px solid ${C.line2}`, color: C.text2 },
        primary: { background: "#fff", border: "none", color: C.ink },
        blue: { background: C.blue, border: "none", color: C.ink },
        soft: { background: C.blueSoft, border: `1px solid ${C.blueLine}`, color: C.blue },
        ghost: { background: "transparent", border: "1px solid transparent", color: C.text3 },
    }
    return (
        <motion.button
            onClick={onClick}
            disabled={disabled}
            title={title}
            whileHover={{ y: disabled ? 0 : -1 }}
            whileTap={{ scale: disabled ? 1 : 0.97 }}
            transition={spring}
            style={{
                ...sizes[size],
                ...variants[variant],
                borderRadius: 99,
                cursor: disabled ? "default" : "pointer",
                opacity: disabled ? 0.4 : 1,
                fontWeight: 600,
                display: "inline-flex",
                alignItems: "center",
                justifyContent: "center",
                gap: 7,
                width: fullWidth ? "100%" : "auto",
                outline: "none",
                fontFamily: SANS,
                whiteSpace: "nowrap" as const,
                boxSizing: "border-box" as const,
                ...style,
            }}
        >
            {children}
        </motion.button>
    )
}

function GlassInput({
    value,
    onChange,
    placeholder,
    multiline = false,
    rows = 3,
    type = "text",
}: {
    value: string
    onChange: (v: string) => void
    placeholder?: string
    multiline?: boolean
    rows?: number
    type?: string
}) {
    const base: React.CSSProperties = {
        width: "100%",
        minHeight: multiline ? undefined : 42,
        padding: multiline ? "11px 14px" : "0 14px",
        background: C.panel,
        border: `1px solid ${C.line}`,
        borderRadius: 12,
        color: C.text,
        fontSize: 14,
        lineHeight: 1.5,
        fontFamily: SANS,
        outline: "none",
        resize: multiline ? "vertical" : ("none" as any),
        boxSizing: "border-box" as const,
        transition: "border-color 0.15s, background 0.15s",
    }
    const Tag: any = multiline ? "textarea" : "input"
    return (
        <Tag
            value={value}
            onChange={(e: any) => onChange(e.target.value)}
            placeholder={placeholder}
            rows={multiline ? rows : undefined}
            type={!multiline ? type : undefined}
            style={base}
            onFocus={(e: any) => {
                e.target.style.borderColor = C.blueLine
                e.target.style.background = C.panel2
            }}
            onBlur={(e: any) => {
                e.target.style.borderColor = C.line
                e.target.style.background = C.panel
            }}
        />
    )
}

function Card({
    children,
    onClick,
    style = {},
}: {
    children: React.ReactNode
    onClick?: () => void
    style?: React.CSSProperties
}) {
    return (
        <motion.div
            onClick={onClick}
            whileHover={onClick ? { y: -2, backgroundColor: C.panel2 } : {}}
            whileTap={onClick ? { scale: 0.995 } : {}}
            transition={spring}
            style={{
                background: C.panel,
                border: `1px solid ${C.line}`,
                borderRadius: 16,
                padding: 16,
                cursor: onClick ? "pointer" : "default",
                boxSizing: "border-box" as const,
                overflow: "hidden",
                ...style,
            }}
        >
            {children}
        </motion.div>
    )
}

function SectionHeader({ title, sub, right }: { title: string; sub?: string; right?: React.ReactNode }) {
    return (
        <div
            style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                gap: 12,
                flexWrap: "wrap" as const,
            }}
        >
            <div style={{ minWidth: 0 }}>
                <h2
                    style={{
                        margin: 0,
                        fontSize: 22,
                        fontWeight: 700,
                        color: C.text,
                        fontFamily: SANS,
                        letterSpacing: "-0.4px",
                    }}
                >
                    {title}
                </h2>
                {sub && <p style={{ margin: "3px 0 0", fontSize: 13, color: C.text3 }}>{sub}</p>}
            </div>
            {right}
        </div>
    )
}

function BackHeader({ title, sub, onBack, right }: { title: string; sub?: string; onBack: () => void; right?: React.ReactNode }) {
    return (
        <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" as const }}>
            <GlassBtn variant="ghost" size="sm" onClick={onBack} title="Retour" style={{ padding: 0 }}>
                <Icon d={I.back} size={18} />
            </GlassBtn>
            <div style={{ flex: 1, minWidth: 120 }}>
                <h2
                    style={{
                        margin: 0,
                        fontSize: 18,
                        fontWeight: 700,
                        color: C.text,
                        fontFamily: SANS,
                        letterSpacing: "-0.3px",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap" as const,
                    }}
                >
                    {title}
                </h2>
                {sub && <p style={{ margin: "2px 0 0", fontSize: 12.5, color: C.text3 }}>{sub}</p>}
            </div>
            {right}
        </div>
    )
}

function EmptyState({ icon, title, text }: { icon: string; title: string; text: string }) {
    return (
        <Card style={{ padding: "34px 20px", textAlign: "center" }}>
            <div
                style={{
                    width: 48,
                    height: 48,
                    margin: "0 auto 14px",
                    borderRadius: 14,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    background: C.blueSoft,
                    border: `1px solid ${C.blueLine}`,
                    color: C.blue,
                }}
            >
                <Icon d={icon} size={22} />
            </div>
            <div style={{ fontSize: 15, fontWeight: 600, color: C.text, marginBottom: 4 }}>{title}</div>
            <div style={{ fontSize: 13, color: C.text3, lineHeight: 1.55 }}>{text}</div>
        </Card>
    )
}

// ═══════════════════════════════════════════════════════════════
// STATISTIQUES
// ═══════════════════════════════════════════════════════════════
function StatsView({ decks, studyStats, onBack }: { decks: Deck[]; studyStats: StudyStats; onBack: () => void }) {
    const totalCards = decks.reduce((s, d) => s + d.cards_count, 0)
    const { correct, incorrect, total, history } = studyStats
    // Activité de la semaine, tirée de l'historique de la session
    const weekData = [0, 1, 2, 3, 4, 5, 6].map(
        (i) => history.filter((_, j) => j % 7 === i && history[j] === "correct").length + 1
    )

    return (
        <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} style={{ display: "flex", flexDirection: "column", gap: 14 }}>
            <BackHeader title="Statistiques" onBack={onBack} />

            <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 8 }}>
                {[
                    { label: "Decks", val: decks.length },
                    { label: "Cartes", val: totalCards },
                    { label: "Révisées", val: total },
                ].map(({ label, val }) => (
                    <Card key={label} style={{ padding: 14, minWidth: 0 }}>
                        <Label color={C.text4}>{label}</Label>
                        <div style={{ fontSize: 22, fontWeight: 700, color: C.text, letterSpacing: "-0.5px" }}>{val}</div>
                    </Card>
                ))}
            </div>

            <Card style={{ display: "flex", alignItems: "center", gap: 16, padding: 18 }}>
                <DonutChart correct={correct} total={total} size={72} />
                <div style={{ flex: 1, minWidth: 0 }}>
                    <Label>Taux de réussite</Label>
                    <div style={{ display: "flex", gap: 16, marginBottom: 10, flexWrap: "wrap" as const }}>
                        <div style={{ fontSize: 13, color: C.text3 }}>
                            <span style={{ fontSize: 16, fontWeight: 700, color: C.blue, marginRight: 5 }}>{correct}</span>
                            justes
                        </div>
                        <div style={{ fontSize: 13, color: C.text3 }}>
                            <span style={{ fontSize: 16, fontWeight: 700, color: C.text, marginRight: 5 }}>{incorrect}</span>
                            à revoir
                        </div>
                    </div>
                    <HistoryDots history={history} />
                </div>
            </Card>

            <Card style={{ padding: "16px 18px" }}>
                <Label>Activité</Label>
                <SparkBars data={weekData} />
                <div style={{ display: "flex", gap: 4, marginTop: 6 }}>
                    {["L", "M", "M", "J", "V", "S", "D"].map((d, i) => (
                        <span key={i} style={{ flex: 1, minWidth: 6, fontSize: 10.5, color: C.text4, fontFamily: MONO, textAlign: "center" }}>
                            {d}
                        </span>
                    ))}
                </div>
            </Card>

            <Card style={{ padding: "16px 18px" }}>
                <Label>Decks</Label>
                <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                    {decks.map((d, i) => {
                        const pct = totalCards > 0 ? (d.cards_count / totalCards) * 100 : 0
                        return (
                            <div key={d.id} style={{ display: "flex", alignItems: "center", gap: 10 }}>
                                <div
                                    style={{
                                        flex: 1,
                                        fontSize: 13,
                                        color: C.text2,
                                        overflow: "hidden",
                                        textOverflow: "ellipsis",
                                        whiteSpace: "nowrap" as const,
                                    }}
                                >
                                    {d.title}
                                </div>
                                <div style={{ width: 90, height: 4, background: C.panel2, borderRadius: 99, overflow: "hidden", flexShrink: 0 }}>
                                    <motion.div
                                        initial={{ width: 0 }}
                                        animate={{ width: `${pct}%` }}
                                        transition={{ duration: 0.6, delay: i * 0.08 }}
                                        style={{ height: "100%", background: `linear-gradient(90deg, ${C.blueDeep}, ${C.blueHi})`, borderRadius: 99 }}
                                    />
                                </div>
                                <div style={{ fontSize: 12, color: C.text3, fontFamily: MONO, flexShrink: 0, width: 26, textAlign: "right" }}>
                                    {d.cards_count}
                                </div>
                            </div>
                        )
                    })}
                </div>
            </Card>
        </motion.div>
    )
}

// ═══════════════════════════════════════════════════════════════
// DECKS
// ═══════════════════════════════════════════════════════════════
function DecksView({
    decks,
    loading,
    studyStats,
    onSelectDeck,
    onCreateDeck,
    onEditDeck,
    onDeleteDeck,
    onStudyDeck,
    onStats,
}: {
    decks: Deck[]
    loading: boolean
    studyStats: StudyStats
    onSelectDeck: (d: Deck) => void
    onCreateDeck: () => void
    onEditDeck: (d: Deck) => void
    onDeleteDeck: (d: Deck) => void
    onStudyDeck: (d: Deck) => void
    onStats: () => void
}) {
    const totalCards = decks.reduce((s, d) => s + d.cards_count, 0)
    const { correct, total } = studyStats
    const pct = total > 0 ? Math.round((correct / total) * 100) : 0

    return (
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
            <SectionHeader
                title="Flashcards"
                sub={`${decks.length} deck${decks.length !== 1 ? "s" : ""} · ${totalCards} carte${totalCards !== 1 ? "s" : ""}`}
                right={
                    <div style={{ display: "flex", gap: 8 }}>
                        <GlassBtn variant="glass" size="sm" onClick={onStats} title="Statistiques" style={{ padding: 0 }}>
                            <Icon d={I.stats} size={16} />
                        </GlassBtn>
                        <GlassBtn variant="primary" size="sm" onClick={onCreateDeck}>
                            <Icon d={I.plus} size={15} width={2.5} />
                            Nouveau deck
                        </GlassBtn>
                    </div>
                }
            />

            {total > 0 && (
                <Card style={{ padding: "14px 16px", background: C.blueSoft, border: `1px solid ${C.blueLine}` }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
                        <DonutChart correct={correct} total={total} size={52} />
                        <div style={{ flex: 1, minWidth: 0 }}>
                            <div style={{ fontSize: 14, fontWeight: 600, color: C.text, marginBottom: 6 }}>
                                {pct} % de réussite
                                <span style={{ fontWeight: 400, color: C.text3 }}>
                                    {" "}
                                    · {correct} juste{correct !== 1 ? "s" : ""}, {total - correct} à revoir
                                </span>
                            </div>
                            <HistoryDots history={studyStats.history.slice(-8)} />
                        </div>
                    </div>
                </Card>
            )}

            {loading ? (
                <div style={{ display: "flex", justifyContent: "center", padding: 40 }}>
                    <Spinner />
                </div>
            ) : decks.length === 0 ? (
                <EmptyState icon={I.cards} title="Aucun deck" text="Crée ton premier deck pour commencer à réviser." />
            ) : (
                <AnimatePresence mode="popLayout">
                    {decks.map((deck, i) => (
                        <motion.div
                            key={deck.id}
                            initial={{ opacity: 0, y: 12 }}
                            animate={{ opacity: 1, y: 0 }}
                            exit={{ opacity: 0, scale: 0.97 }}
                            transition={{ delay: i * 0.04, ...springGent }}
                        >
                            <Card onClick={() => onSelectDeck(deck)}>
                                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10 }}>
                                    <div style={{ flex: 1, minWidth: 0 }}>
                                        <div
                                            style={{
                                                fontSize: 16,
                                                fontWeight: 700,
                                                color: C.text,
                                                letterSpacing: "-0.3px",
                                                overflow: "hidden",
                                                textOverflow: "ellipsis",
                                                whiteSpace: "nowrap" as const,
                                                marginBottom: 4,
                                            }}
                                        >
                                            {deck.title}
                                        </div>
                                        {deck.description && (
                                            <p
                                                style={{
                                                    margin: "0 0 8px",
                                                    fontSize: 12.5,
                                                    color: C.text3,
                                                    lineHeight: 1.5,
                                                    overflow: "hidden",
                                                    textOverflow: "ellipsis",
                                                    whiteSpace: "nowrap" as const,
                                                }}
                                            >
                                                {deck.description}
                                            </p>
                                        )}
                                        <Pill tone="blue">
                                            {deck.cards_count} carte{deck.cards_count !== 1 ? "s" : ""}
                                        </Pill>
                                    </div>
                                    <div style={{ display: "flex", gap: 4, flexShrink: 0 }} onClick={(e) => e.stopPropagation()}>
                                        {deck.cards_count > 0 && (
                                            <GlassBtn variant="blue" size="xs" onClick={() => onStudyDeck(deck)} title="Réviser" style={{ padding: 0 }}>
                                                <Icon d={I.play} size={13} />
                                            </GlassBtn>
                                        )}
                                        <GlassBtn variant="ghost" size="xs" onClick={() => onEditDeck(deck)} title="Modifier" style={{ padding: 0 }}>
                                            <Icon d={I.edit} size={15} />
                                        </GlassBtn>
                                        <GlassBtn variant="ghost" size="xs" onClick={() => onDeleteDeck(deck)} title="Supprimer" style={{ padding: 0 }}>
                                            <Icon d={I.trash} size={15} />
                                        </GlassBtn>
                                    </div>
                                </div>
                            </Card>
                        </motion.div>
                    ))}
                </AnimatePresence>
            )}
        </div>
    )
}

// ═══════════════════════════════════════════════════════════════
// CARTES D'UN DECK
// ═══════════════════════════════════════════════════════════════
const CARDS_PER_PAGE = 3

function CardsView({
    deck,
    cards,
    loading,
    onBack,
    onStudy,
    onCreateCard,
    onEditCard,
    onDeleteCard,
    onGeneratePdf,
}: {
    deck: Deck
    cards: Card[]
    loading: boolean
    onBack: () => void
    onStudy: () => void
    onCreateCard: () => void
    onEditCard: (c: Card) => void
    onDeleteCard: (c: Card) => void
    onGeneratePdf: () => void
}) {
    const isDesktop = useIsDesktop()
    const [page, setPage] = useState(0)
    const totalPages = Math.max(1, Math.ceil(cards.length / CARDS_PER_PAGE))
    const pageCards = isDesktop ? cards.slice(page * CARDS_PER_PAGE, (page + 1) * CARDS_PER_PAGE) : cards

    useEffect(() => {
        setPage(0)
    }, [cards.length])

    return (
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <BackHeader
                title={deck.title}
                sub={`${cards.length} carte${cards.length !== 1 ? "s" : ""}`}
                onBack={onBack}
                right={
                    <div style={{ display: "flex", gap: 6 }}>
                        {cards.length > 0 && (
                            <GlassBtn variant="primary" size="sm" onClick={onStudy}>
                                <Icon d={I.play} size={12} />
                                Réviser
                            </GlassBtn>
                        )}
                        <GlassBtn variant="glass" size="sm" onClick={onCreateCard} title="Nouvelle carte" style={{ padding: 0 }}>
                            <Icon d={I.plus} size={16} width={2.5} />
                        </GlassBtn>
                        <GlassBtn variant="soft" size="sm" onClick={onGeneratePdf}>
                            <Icon d={I.file} size={14} />
                            PDF
                        </GlassBtn>
                    </div>
                }
            />

            {loading ? (
                <div style={{ display: "flex", justifyContent: "center", padding: 40 }}>
                    <Spinner />
                </div>
            ) : cards.length === 0 ? (
                <EmptyState icon={I.cards} title="Deck vide" text="Ajoute des cartes, ou génère-les depuis un PDF." />
            ) : (
                <>
                    <AnimatePresence mode="popLayout">
                        {pageCards.map((card, i) => (
                            <motion.div
                                key={card.id}
                                initial={{ opacity: 0, y: 10 }}
                                animate={{ opacity: 1, y: 0 }}
                                exit={{ opacity: 0, x: -60 }}
                                transition={{ delay: i * 0.03, ...springGent }}
                            >
                                <Card>
                                    <div style={{ display: "flex", gap: 12 }}>
                                        <div style={{ flex: 1, minWidth: 0 }}>
                                            <Label color={C.blue}>Recto</Label>
                                            <div
                                                style={
                                                    {
                                                        fontSize: 14,
                                                        fontWeight: 500,
                                                        color: C.text,
                                                        lineHeight: 1.5,
                                                        marginBottom: 12,
                                                        overflow: "hidden",
                                                        display: "-webkit-box",
                                                        WebkitLineClamp: 2,
                                                        WebkitBoxOrient: "vertical",
                                                    } as any
                                                }
                                            >
                                                {card.front}
                                            </div>
                                            <Label color={C.text4}>Verso</Label>
                                            <div
                                                style={
                                                    {
                                                        fontSize: 13,
                                                        color: C.text2,
                                                        lineHeight: 1.5,
                                                        overflow: "hidden",
                                                        display: "-webkit-box",
                                                        WebkitLineClamp: 2,
                                                        WebkitBoxOrient: "vertical",
                                                    } as any
                                                }
                                            >
                                                {card.back}
                                            </div>
                                            {card.tags && (
                                                <div style={{ marginTop: 10, display: "flex", gap: 5, flexWrap: "wrap" as const }}>
                                                    {card.tags
                                                        .split(",")
                                                        .slice(0, 3)
                                                        .map((t, j) => (
                                                            <Pill key={j} tone="plain">
                                                                {t.trim()}
                                                            </Pill>
                                                        ))}
                                                </div>
                                            )}
                                        </div>
                                        <div style={{ display: "flex", flexDirection: "column" as const, gap: 4, flexShrink: 0 }}>
                                            <GlassBtn variant="ghost" size="xs" onClick={() => onEditCard(card)} title="Modifier" style={{ padding: 0 }}>
                                                <Icon d={I.edit} size={15} />
                                            </GlassBtn>
                                            <GlassBtn variant="ghost" size="xs" onClick={() => onDeleteCard(card)} title="Supprimer" style={{ padding: 0 }}>
                                                <Icon d={I.trash} size={15} />
                                            </GlassBtn>
                                        </div>
                                    </div>
                                </Card>
                            </motion.div>
                        ))}
                    </AnimatePresence>

                    {/* Pagination : ordinateur uniquement */}
                    {isDesktop && totalPages > 1 && (
                        <motion.div
                            initial={{ opacity: 0 }}
                            animate={{ opacity: 1 }}
                            style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 10, paddingTop: 4 }}
                        >
                            <GlassBtn variant="glass" size="xs" onClick={() => setPage((p) => Math.max(0, p - 1))} disabled={page === 0} style={{ padding: 0 }}>
                                <Icon d={I.back} size={15} />
                            </GlassBtn>
                            <span style={{ fontSize: 12, color: C.text2, fontFamily: MONO }}>
                                {page + 1} <span style={{ color: C.text4 }}>/ {totalPages}</span>
                            </span>
                            <GlassBtn
                                variant="glass"
                                size="xs"
                                onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
                                disabled={page === totalPages - 1}
                                style={{ padding: 0 }}
                            >
                                <Icon d={I.next} size={15} />
                            </GlassBtn>
                        </motion.div>
                    )}
                </>
            )}
        </div>
    )
}

// ═══════════════════════════════════════════════════════════════
// RÉVISION
// ═══════════════════════════════════════════════════════════════
function StudyView({
    deck,
    studyCards,
    currentIndex,
    total,
    studyStats,
    onBack,
    onCorrect,
    onIncorrect,
}: {
    deck: Deck
    studyCards: Card[]
    currentIndex: number
    total: number
    studyStats: StudyStats
    onBack: () => void
    onCorrect: () => void
    onIncorrect: () => void
}) {
    const [flipped, setFlipped] = useState(false)
    const card = studyCards[currentIndex]
    const done = currentIndex >= total

    useEffect(() => {
        setFlipped(false)
    }, [currentIndex])

    if (done) {
        return (
            <motion.div
                initial={{ opacity: 0, scale: 0.95 }}
                animate={{ opacity: 1, scale: 1 }}
                transition={springGent}
                style={{ display: "flex", flexDirection: "column", alignItems: "center", padding: "24px 0" }}
            >
                <Card style={{ textAlign: "center", padding: 28, maxWidth: 360, width: "100%", background: C.blueSoft, border: `1px solid ${C.blueLine}` }}>
                    <motion.div
                        initial={{ scale: 0 }}
                        animate={{ scale: 1 }}
                        transition={{ delay: 0.15, ...spring }}
                        style={{
                            width: 54,
                            height: 54,
                            margin: "0 auto 14px",
                            borderRadius: 16,
                            display: "flex",
                            alignItems: "center",
                            justifyContent: "center",
                            background: C.blue,
                            color: C.ink,
                        }}
                    >
                        <Icon d={I.cap} size={26} />
                    </motion.div>
                    <h2 style={{ margin: "0 0 4px", fontSize: 20, fontWeight: 700, color: C.text, letterSpacing: "-0.3px" }}>Session terminée</h2>
                    <div style={{ margin: "0 0 18px", fontSize: 13, color: C.text3 }}>
                        {total} carte{total !== 1 ? "s" : ""} révisée{total !== 1 ? "s" : ""}
                    </div>
                    <div style={{ display: "flex", justifyContent: "center" }}>
                        <DonutChart correct={studyStats.correct} total={studyStats.total} size={84} />
                    </div>
                    <div style={{ marginTop: 14, display: "flex", gap: 8, justifyContent: "center" }}>
                        <Pill tone="solid">
                            <Icon d={I.tick} size={11} width={3} /> {studyStats.correct} juste{studyStats.correct !== 1 ? "s" : ""}
                        </Pill>
                        <Pill tone="white">{studyStats.incorrect} à revoir</Pill>
                    </div>
                    <div style={{ marginTop: 22 }}>
                        <GlassBtn variant="primary" size="lg" fullWidth onClick={onBack}>
                            Retour aux decks
                        </GlassBtn>
                    </div>
                </Card>
            </motion.div>
        )
    }

    if (!card)
        return (
            <div style={{ display: "flex", justifyContent: "center", padding: 40 }}>
                <Spinner />
            </div>
        )

    const progress = total > 0 ? (currentIndex / total) * 100 : 0

    return (
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                <GlassBtn variant="ghost" size="sm" onClick={onBack} title="Retour" style={{ padding: 0 }}>
                    <Icon d={I.back} size={18} />
                </GlassBtn>
                <div style={{ flex: 1, minWidth: 0 }}>
                    <div
                        style={{
                            fontSize: 13,
                            color: C.text2,
                            marginBottom: 6,
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            whiteSpace: "nowrap" as const,
                        }}
                    >
                        {deck.title} <span style={{ color: C.text4, fontFamily: MONO }}>· {currentIndex + 1}/{total}</span>
                    </div>
                    <div style={{ width: "100%", height: 4, background: C.panel2, borderRadius: 99, overflow: "hidden" }}>
                        <motion.div
                            animate={{ width: `${progress}%` }}
                            transition={{ duration: 0.4 }}
                            style={{ height: "100%", background: `linear-gradient(90deg, ${C.blueDeep}, ${C.blueHi})`, borderRadius: 99 }}
                        />
                    </div>
                </div>
                <div style={{ display: "flex", gap: 4 }}>
                    <Pill tone="blue">
                        <Icon d={I.tick} size={11} width={3} />
                        {studyStats.correct}
                    </Pill>
                    <Pill tone="plain">
                        <Icon d={I.x} size={11} width={3} />
                        {studyStats.incorrect}
                    </Pill>
                </div>
            </div>

            <AnimatePresence mode="wait">
                <motion.div
                    key={currentIndex + "-" + flipped}
                    initial={{ opacity: 0, rotateY: flipped ? -15 : 15 }}
                    animate={{ opacity: 1, rotateY: 0 }}
                    exit={{ opacity: 0 }}
                    transition={{ duration: 0.2 }}
                >
                    <Card
                        onClick={() => setFlipped(!flipped)}
                        style={{
                            minHeight: 220,
                            display: "flex",
                            flexDirection: "column" as const,
                            alignItems: "center",
                            justifyContent: "center",
                            background: flipped ? "rgba(255,255,255,0.06)" : C.blueSoft,
                            border: `1px solid ${flipped ? C.line2 : C.blueLine}`,
                            padding: 26,
                            borderRadius: 20,
                        }}
                    >
                        <Label color={flipped ? C.text3 : C.blue}>{flipped ? "Réponse" : "Question"}</Label>
                        <div
                            style={{
                                fontSize: 18,
                                fontWeight: 500,
                                color: C.text,
                                textAlign: "center",
                                lineHeight: 1.55,
                                width: "100%",
                                marginTop: 4,
                            }}
                        >
                            {flipped ? card.back : card.front}
                        </div>
                        <div style={{ marginTop: 18, fontSize: 12, color: C.text4, display: "flex", alignItems: "center", gap: 6 }}>
                            <Icon d={I.flip} size={13} />
                            Touche la carte pour la retourner
                        </div>
                    </Card>
                </motion.div>
            </AnimatePresence>

            {flipped ? (
                <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
                    <GlassBtn variant="glass" size="lg" fullWidth onClick={onIncorrect}>
                        <Icon d={I.x} size={15} width={2.5} />À revoir
                    </GlassBtn>
                    <GlassBtn variant="blue" size="lg" fullWidth onClick={onCorrect}>
                        <Icon d={I.tick} size={15} width={2.5} />
                        Je savais
                    </GlassBtn>
                </motion.div>
            ) : (
                <div style={{ textAlign: "center", fontSize: 12.5, color: C.text4 }}>Retourne la carte pour évaluer ta réponse</div>
            )}
        </div>
    )
}

// ═══════════════════════════════════════════════════════════════
// FORMULAIRES
// ═══════════════════════════════════════════════════════════════
function FormActions({
    onBack,
    onSave,
    saving,
    disabled,
    label,
}: {
    onBack: () => void
    onSave: () => void
    saving: boolean
    disabled: boolean
    label: string
}) {
    return (
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 4 }}>
            <GlassBtn variant="glass" onClick={onBack} disabled={saving}>
                Annuler
            </GlassBtn>
            <GlassBtn variant="primary" onClick={onSave} disabled={disabled || saving} style={{ minWidth: 110 }}>
                {saving ? <Spinner size={14} color={C.ink} /> : label}
            </GlassBtn>
        </div>
    )
}

function DeckForm({
    deck,
    onBack,
    onSave,
    saving,
}: {
    deck: Deck | null
    onBack: () => void
    onSave: (t: string, d: string) => void
    saving: boolean
}) {
    const [title, setTitle] = useState(deck?.title || "")
    const [desc, setDesc] = useState(deck?.description || "")
    return (
        <motion.div initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }} style={{ display: "flex", flexDirection: "column", gap: 14 }}>
            <BackHeader title={deck ? "Modifier le deck" : "Nouveau deck"} onBack={onBack} />
            <Card style={{ padding: 18 }}>
                <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
                    <div>
                        <Label>Titre *</Label>
                        <GlassInput value={title} onChange={setTitle} placeholder="Droit des obligations" />
                    </div>
                    <div>
                        <Label>Description</Label>
                        <GlassInput value={desc} onChange={setDesc} placeholder="Description…" multiline rows={2} />
                    </div>
                    <FormActions
                        onBack={onBack}
                        onSave={() => title.trim() && !saving && onSave(title.trim(), desc.trim())}
                        saving={saving}
                        disabled={!title.trim()}
                        label={deck ? "Enregistrer" : "Créer"}
                    />
                </div>
            </Card>
        </motion.div>
    )
}

function CardForm({
    card,
    onBack,
    onSave,
    saving,
}: {
    card: Card | null
    onBack: () => void
    onSave: (f: string, b: string, t: string) => void
    saving: boolean
}) {
    const [front, setFront] = useState(card?.front || "")
    const [back, setBack] = useState(card?.back || "")
    const [tags, setTags] = useState(card?.tags || "")
    return (
        <motion.div initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }} style={{ display: "flex", flexDirection: "column", gap: 14 }}>
            <BackHeader title={card ? "Modifier la carte" : "Nouvelle carte"} onBack={onBack} />
            <Card style={{ padding: 18 }}>
                <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
                    <div>
                        <Label color={C.blue}>Recto *</Label>
                        <GlassInput value={front} onChange={setFront} placeholder="Question…" multiline rows={2} />
                    </div>
                    <div>
                        <Label>Verso *</Label>
                        <GlassInput value={back} onChange={setBack} placeholder="Réponse…" multiline rows={3} />
                    </div>
                    <div>
                        <Label>Tags</Label>
                        <GlassInput value={tags} onChange={setTags} placeholder="contrat, 1103" />
                    </div>
                    <FormActions
                        onBack={onBack}
                        onSave={() => front.trim() && back.trim() && !saving && onSave(front.trim(), back.trim(), tags.trim())}
                        saving={saving}
                        disabled={!front.trim() || !back.trim()}
                        label={card ? "Enregistrer" : "Créer"}
                    />
                </div>
            </Card>
        </motion.div>
    )
}

function GeneratePdfView({
    deck,
    onBack,
    onGenerate,
    generating,
    files,
    loadingFiles,
}: {
    deck: Deck
    onBack: () => void
    onGenerate: (id: number, pages: string, count: number) => void
    generating: boolean
    files: UserFile[]
    loadingFiles: boolean
}) {
    const [fileId, setFileId] = useState<number | null>(null)
    const [pages, setPages] = useState("")
    const [count, setCount] = useState("10")
    return (
        <motion.div initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }} style={{ display: "flex", flexDirection: "column", gap: 14 }}>
            <BackHeader title="Générer depuis un PDF" sub={deck.title} onBack={onBack} />
            <Card style={{ padding: 18 }}>
                <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
                    <div
                        style={{
                            padding: "12px 14px",
                            background: C.blueSoft,
                            border: `1px solid ${C.blueLine}`,
                            borderRadius: 12,
                            fontSize: 13,
                            color: "#d6e4ff",
                            lineHeight: 1.55,
                        }}
                    >
                        L'IA rédige des flashcards à partir de ton PDF. Choisis le fichier, et si tu veux, les pages.
                    </div>
                    <div>
                        <Label>Fichier *</Label>
                        {loadingFiles ? (
                            <div style={{ padding: 16, display: "flex", justifyContent: "center" }}>
                                <Spinner size={18} />
                            </div>
                        ) : files.length === 0 ? (
                            <div style={{ padding: 14, fontSize: 13, color: C.text3, textAlign: "center" }}>Aucun fichier importé</div>
                        ) : (
                            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                                {files.map((f) => {
                                    const on = fileId === f.id
                                    return (
                                        <div
                                            key={f.id}
                                            onClick={() => setFileId(f.id)}
                                            style={{
                                                minHeight: 44,
                                                padding: "0 14px",
                                                background: on ? C.blueSoft : C.panel,
                                                border: `1px solid ${on ? C.blue : C.line}`,
                                                borderRadius: 12,
                                                cursor: "pointer",
                                                display: "flex",
                                                alignItems: "center",
                                                gap: 10,
                                                fontSize: 13.5,
                                                color: on ? C.text : C.text2,
                                                overflow: "hidden",
                                                transition: "background 0.15s, border-color 0.15s",
                                            }}
                                        >
                                            <span style={{ color: on ? C.blue : C.text3, display: "flex" }}>
                                                <Icon d={I.file} size={15} />
                                            </span>
                                            <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                                {f.filename_original}
                                            </span>
                                            {on && (
                                                <span style={{ color: C.blue, display: "flex" }}>
                                                    <Icon d={I.tick} size={15} width={3} />
                                                </span>
                                            )}
                                        </div>
                                    )
                                })}
                            </div>
                        )}
                    </div>
                    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
                        <div>
                            <Label>Pages</Label>
                            <GlassInput value={pages} onChange={setPages} placeholder="1-5, ou vide" />
                        </div>
                        <div>
                            <Label>Nombre de cartes</Label>
                            <GlassInput value={count} onChange={setCount} placeholder="10" type="number" />
                        </div>
                    </div>
                    <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 4 }}>
                        <GlassBtn variant="glass" onClick={onBack} disabled={generating}>
                            Annuler
                        </GlassBtn>
                        <GlassBtn
                            variant="primary"
                            onClick={() => fileId && !generating && onGenerate(fileId, pages, parseInt(count) || 10)}
                            disabled={!fileId || generating}
                        >
                            {generating && <Spinner size={14} color={C.ink} />}
                            {generating ? "Génération…" : "Générer"}
                        </GlassBtn>
                    </div>
                </div>
            </Card>
        </motion.div>
    )
}

// ═══════════════════════════════════════════════════════════════
// MAIN
// ═══════════════════════════════════════════════════════════════
export default function FlashcardsApp({ apiBase = API_BASE }: { apiBase?: string }) {
    const [view, setView] = useState<View>("decks")
    const [token, setToken] = useState<string | null>(null)
    const [decks, setDecks] = useState<Deck[]>([])
    const [cards, setCards] = useState<Card[]>([])
    const [files, setFiles] = useState<UserFile[]>([])
    const [selectedDeck, setSelectedDeck] = useState<Deck | null>(null)
    const [selectedCard, setSelectedCard] = useState<Card | null>(null)
    const [studyCards, setStudyCards] = useState<Card[]>([])
    const [studyIndex, setStudyIndex] = useState(0)
    const [studyStats, setStudyStats] = useState<StudyStats>({ correct: 0, incorrect: 0, total: 0, history: [] })
    const [loading, setLoading] = useState(false)
    const [loadingFiles, setLoadingFiles] = useState(false)
    const [saving, setSaving] = useState(false)
    const [generating, setGenerating] = useState(false)
    const [error, setError] = useState<string | null>(null)

    useEffect(() => {
        const t = typeof window !== "undefined" ? localStorage.getItem("navire_token") : null
        if (t) setToken(t)
    }, [])

    const api = useCallback(
        async (path: string, opts: RequestInit = {}) => {
            const res = await fetch(`${apiBase}${path}`, {
                ...opts,
                headers: {
                    "Content-Type": "application/json",
                    ...(token ? { Authorization: `Bearer ${token}` } : {}),
                    ...((opts.headers as any) || {}),
                },
            })
            if (!res.ok) {
                const err = await res.json().catch(() => ({ detail: "Erreur" }))
                throw new Error(typeof err.detail === "string" ? err.detail : err.detail?.message || `Erreur ${res.status}`)
            }
            return res.json()
        },
        [apiBase, token]
    )

    const loadDecks = useCallback(async () => {
        if (!token) return
        setLoading(true)
        setError(null)
        try {
            const d = await api("/flash/decks")
            setDecks(d.items || [])
        } catch (e: any) {
            setError(e.message)
        } finally {
            setLoading(false)
        }
    }, [api, token])

    const loadCards = useCallback(
        async (id: number) => {
            if (!token) return
            setLoading(true)
            setError(null)
            try {
                const d = await api(`/flash/decks/${id}/cards`)
                setCards(d.items || [])
            } catch (e: any) {
                setError(e.message)
            } finally {
                setLoading(false)
            }
        },
        [api, token]
    )

    const loadFiles = useCallback(async () => {
        if (!token) return
        setLoadingFiles(true)
        try {
            const d = await api("/files")
            setFiles(d.items || [])
        } catch {
            setFiles([])
        } finally {
            setLoadingFiles(false)
        }
    }, [api, token])

    useEffect(() => {
        if (token) loadDecks()
    }, [token, loadDecks])

    const handleSelectDeck = (d: Deck) => {
        setSelectedDeck(d)
        loadCards(d.id)
        setView("cards")
    }
    const handleStudy = async (d: Deck) => {
        setSelectedDeck(d)
        setLoading(true)
        setError(null)
        try {
            const data = await api(`/flash/decks/${d.id}/cards`)
            const all: Card[] = data.items || []
            if (!all.length) {
                setError("Deck vide.")
                setLoading(false)
                return
            }
            setStudyCards([...all].sort(() => Math.random() - 0.5).slice(0, Math.min(10, all.length)))
            setStudyIndex(0)
            setStudyStats({ correct: 0, incorrect: 0, total: 0, history: [] })
            setView("study")
        } catch (e: any) {
            setError(e.message)
        } finally {
            setLoading(false)
        }
    }
    const handleCorrect = () => {
        setStudyStats((s) => ({ ...s, correct: s.correct + 1, total: s.total + 1, history: [...s.history, "correct"] }))
        setStudyIndex((i) => i + 1)
    }
    const handleIncorrect = () => {
        setStudyStats((s) => ({ ...s, incorrect: s.incorrect + 1, total: s.total + 1, history: [...s.history, "incorrect"] }))
        setStudyIndex((i) => i + 1)
    }

    const handleSaveDeck = async (title: string, desc: string) => {
        setSaving(true)
        setError(null)
        try {
            if (selectedDeck)
                await api(`/flash/decks/${selectedDeck.id}`, { method: "PATCH", body: JSON.stringify({ title, description: desc }) })
            else await api("/flash/decks", { method: "POST", body: JSON.stringify({ title, description: desc }) })
            await loadDecks()
            setView("decks")
        } catch (e: any) {
            setError(e.message)
        } finally {
            setSaving(false)
        }
    }
    const handleSaveCard = async (front: string, back: string, tags: string) => {
        if (!selectedDeck) return
        setSaving(true)
        setError(null)
        try {
            if (selectedCard)
                await api(`/flash/decks/${selectedDeck.id}/cards/${selectedCard.id}`, {
                    method: "PATCH",
                    body: JSON.stringify({ front, back, tags }),
                })
            else
                await api(`/flash/decks/${selectedDeck.id}/cards`, {
                    method: "POST",
                    body: JSON.stringify({ front, back, tags }),
                })
            await loadCards(selectedDeck.id)
            setView("cards")
        } catch (e: any) {
            setError(e.message)
        } finally {
            setSaving(false)
        }
    }
    const handleDeleteDeck = async (d: Deck) => {
        if (!confirm(`Supprimer « ${d.title} » ?`)) return
        try {
            await api(`/flash/decks/${d.id}`, { method: "DELETE" })
            loadDecks()
        } catch (e: any) {
            setError(e.message)
        }
    }
    const handleDeleteCard = async (c: Card) => {
        if (!selectedDeck || !confirm("Supprimer cette carte ?")) return
        try {
            await api(`/flash/decks/${selectedDeck.id}/cards/${c.id}`, { method: "DELETE" })
            loadCards(selectedDeck.id)
        } catch (e: any) {
            setError(e.message)
        }
    }
    const handleGenerate = async (fileId: number, pages: string, count: number) => {
        if (!selectedDeck) return
        setGenerating(true)
        setError(null)
        try {
            await api(`/flash/decks/${selectedDeck.id}/generate-from-pdf`, {
                method: "POST",
                body: JSON.stringify({ file_id: fileId, pages, count }),
            })
            await loadCards(selectedDeck.id)
            setView("cards")
        } catch (e: any) {
            setError(e.message)
        } finally {
            setGenerating(false)
        }
    }
    const handleBack = () => {
        setSelectedDeck(null)
        setSelectedCard(null)
        setStudyCards([])
        setStudyIndex(0)
        setCards([])
        setView("decks")
        loadDecks()
    }

    return (
        <div
            className="nv-flash"
            style={
                {
                    width: "100%",
                    height: "100%",
                    padding: 16,
                    fontFamily: SANS,
                    fontSize: 14,
                    WebkitFontSmoothing: "antialiased",
                    background: "transparent",
                    color: C.text,
                    boxSizing: "border-box",
                    overflowY: "auto",
                    overflowX: "hidden",
                    scrollbarWidth: "none",
                    msOverflowStyle: "none",
                    position: "relative",
                } as any
            }
        >
            <link href={FONTS} rel="stylesheet" />
            <style>{`
                .nv-flash::-webkit-scrollbar { display: none; }
                .nv-flash ::selection { background: rgba(122,167,255,0.3); }
                .nv-flash input::placeholder, .nv-flash textarea::placeholder { color: ${C.text4}; }
            `}</style>
            <div style={{ maxWidth: 760, margin: "0 auto" }}>
                <AnimatePresence>
                    {error && (
                        <motion.div
                            initial={{ opacity: 0, y: -12 }}
                            animate={{ opacity: 1, y: 0 }}
                            exit={{ opacity: 0 }}
                            onClick={() => setError(null)}
                            style={{
                                position: "sticky",
                                top: 0,
                                zIndex: 1000,
                                marginBottom: 12,
                                background: "rgba(30,32,50,0.95)",
                                border: `1px solid ${C.blueLine}`,
                                color: C.blueHi,
                                padding: "11px 14px",
                                borderRadius: 12,
                                fontSize: 13,
                                textAlign: "center",
                                cursor: "pointer",
                                backdropFilter: "blur(8px)",
                                boxShadow: "0 10px 30px rgba(0,0,0,0.35)",
                            }}
                        >
                            {error}
                        </motion.div>
                    )}
                </AnimatePresence>

                {!token ? (
                    <EmptyState icon={I.cards} title="Connecte-toi" text="Pour accéder à tes flashcards." />
                ) : (
                    <AnimatePresence mode="wait">
                        <motion.div
                            key={view}
                            initial={{ opacity: 0, y: 6 }}
                            animate={{ opacity: 1, y: 0 }}
                            exit={{ opacity: 0 }}
                            transition={{ duration: 0.18 }}
                        >
                            {view === "decks" && (
                                <DecksView
                                    decks={decks}
                                    loading={loading}
                                    studyStats={studyStats}
                                    onSelectDeck={handleSelectDeck}
                                    onCreateDeck={() => {
                                        setSelectedDeck(null)
                                        setView("create-deck")
                                    }}
                                    onEditDeck={(d) => {
                                        setSelectedDeck(d)
                                        setView("edit-deck")
                                    }}
                                    onDeleteDeck={handleDeleteDeck}
                                    onStudyDeck={handleStudy}
                                    onStats={() => setView("stats")}
                                />
                            )}
                            {view === "stats" && <StatsView decks={decks} studyStats={studyStats} onBack={() => setView("decks")} />}
                            {view === "cards" && selectedDeck && (
                                <CardsView
                                    deck={selectedDeck}
                                    cards={cards}
                                    loading={loading}
                                    onBack={handleBack}
                                    onStudy={() => handleStudy(selectedDeck)}
                                    onCreateCard={() => {
                                        setSelectedCard(null)
                                        setView("create-card")
                                    }}
                                    onEditCard={(c) => {
                                        setSelectedCard(c)
                                        setView("edit-card")
                                    }}
                                    onDeleteCard={handleDeleteCard}
                                    onGeneratePdf={() => {
                                        loadFiles()
                                        setView("generate-pdf")
                                    }}
                                />
                            )}
                            {view === "study" && selectedDeck && (
                                <StudyView
                                    deck={selectedDeck}
                                    studyCards={studyCards}
                                    currentIndex={studyIndex}
                                    total={studyCards.length}
                                    studyStats={studyStats}
                                    onBack={handleBack}
                                    onCorrect={handleCorrect}
                                    onIncorrect={handleIncorrect}
                                />
                            )}
                            {(view === "create-deck" || view === "edit-deck") && (
                                <DeckForm deck={view === "edit-deck" ? selectedDeck : null} onBack={() => setView("decks")} onSave={handleSaveDeck} saving={saving} />
                            )}
                            {(view === "create-card" || view === "edit-card") && selectedDeck && (
                                <CardForm card={view === "edit-card" ? selectedCard : null} onBack={() => setView("cards")} onSave={handleSaveCard} saving={saving} />
                            )}
                            {view === "generate-pdf" && selectedDeck && (
                                <GeneratePdfView
                                    deck={selectedDeck}
                                    onBack={() => setView("cards")}
                                    onGenerate={handleGenerate}
                                    generating={generating}
                                    files={files}
                                    loadingFiles={loadingFiles}
                                />
                            )}
                        </motion.div>
                    </AnimatePresence>
                )}
            </div>
        </div>
    )
}

addPropertyControls(FlashcardsApp, {
    apiBase: {
        type: ControlType.String,
        title: "API Base URL",
        defaultValue: API_BASE,
    },
})
