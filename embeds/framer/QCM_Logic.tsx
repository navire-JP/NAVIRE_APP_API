// QCM_Logic.tsx
// ─────────────────────────────────────────────────────────────────
// UN SEUL OVERRIDE : QcmAppRoot
// Brancher sur une Frame Framer (fond transparent ou #090914, largeur libre)
//
// Charte : celle de NAVIRE AI (Inter + IBM Plex Mono, fond transparent,
// panneaux blancs translucides, bouton principal blanc, états
// vert / jaune / rouge #5BE3A3 / #FFD166 / #FF6B6B).
// ─────────────────────────────────────────────────────────────────

import type { Override } from "framer"
import * as React from "react"
import { createPortal } from "react-dom"
import { useEffect, useRef, useState } from "react"

// ─── CONFIG ───────────────────────────────────────────────────────
const API_BASE = "https://navire-app-api.onrender.com"
const QCM_COUNT = 5
const AFK_LIMIT_MS = 120_000
const MAX_AFK_STREAK = 3
const AFK_KEY = "navire_qcm_noanswer_streak"
const NEXT_BAR_MS = 10_000
const LOGO_URL = `${API_BASE}/assets/logo-navire-app.png`
const FONTS_URL =
    "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=IBM+Plex+Mono:wght@400;600&display=swap"

// Estimé ELO par session selon la difficulté (gain max théorique si 5/5).
// Aligné sur le backend (services/elo.py QCM_DELTA) :
//   easy +1 / medium +3 / hard +5 par bonne réponse.
// Sert d'estimation AVANT la première réponse ; ensuite on affiche le gain réel
// cumulé renvoyé par /answer (champ elo_delta).
const ELO_PER_CORRECT: Record<string, number> = { easy: 1, medium: 3, hard: 5 }
function estimatedSessionElo(diff: string): number {
    return (ELO_PER_CORRECT[diff] ?? ELO_PER_CORRECT.medium) * QCM_COUNT
}

// ─── UTILS ────────────────────────────────────────────────────────
const isBrowser = () => typeof window !== "undefined"
const safeGet = (k: string) => {
    try {
        return localStorage.getItem(k) || ""
    } catch {
        return ""
    }
}
const safeSet = (k: string, v: string) => {
    try {
        localStorage.setItem(k, v)
    } catch {}
}
const safeDel = (k: string) => {
    try {
        localStorage.removeItem(k)
    } catch {}
}
const safeJson = (s: string) => {
    try {
        return s ? JSON.parse(s) : null
    } catch {
        return null
    }
}
const isAbort = (e: any) =>
    e?.name === "AbortError" ||
    String(e?.message || "")
        .toLowerCase()
        .includes("aborted")
const authH = (x: Record<string, string> = {}) => {
    const t = safeGet("navire_token")
    return t ? { Authorization: `Bearer ${t}`, ...x } : x
}
async function fetchT(url: string, opts: RequestInit = {}, ms = 20000) {
    const ctrl = new AbortController()
    const tid = setTimeout(() => ctrl.abort(), ms)
    try {
        return await fetch(url, { ...opts, signal: ctrl.signal })
    } finally {
        clearTimeout(tid)
    }
}
const G = () =>
    typeof globalThis !== "undefined" ? (globalThis as any) : ({} as any)

// ─── RESPONSIVE ───────────────────────────────────────────────────
function useIsMobile(): boolean {
    const [mob, setMob] = useState(() =>
        isBrowser() ? window.innerWidth <= 420 : false
    )
    useEffect(() => {
        if (!isBrowser()) return
        const fn = () => setMob(window.innerWidth <= 420)
        window.addEventListener("resize", fn)
        return () => window.removeEventListener("resize", fn)
    }, [])
    return mob
}

// ─── TYPES ────────────────────────────────────────────────────────
type QcmView = "setup" | "generating" | "run" | "continue"
type RunStatus =
    | "idle"
    | "starting"
    | "generating"
    | "ready"
    | "answered"
    | "done"
    | "error"
type Plan = "free" | "membre" | "membre+" | "beta" | ""

type FileItem = {
    id: string
    label: string
    sessions_count: number
    usage_pct: number
    success_rate: number // 0-100, -1 = pas de données
}

type S = {
    view: QcmView
    status: RunStatus
    sessionId: string
    error: string
    loading: boolean
    index: number
    total: number
    question: string
    choices: string[]
    selectedIndex: number
    correctIndex: number
    isCorrect: boolean | null
    explanation: string
    genPct: number
    nextBarPct: number
    nextReady: boolean
    questionReadyAt: number
    afkStreak: number
    afkSecondsLeft: number
    plan: Plan
    planLoaded: boolean
    selectedFileName: string // nom du fichier sélectionné, affiché dans la vue run
    eloGained: number // ELO réel cumulé sur la session (somme des elo_delta)
    results: (boolean | null)[] // résultat par question : true=correct, false=faux, null=pas encore
}

const ZERO: S = {
    view: "setup",
    status: "idle",
    sessionId: "",
    error: "",
    loading: false,
    index: 0,
    total: QCM_COUNT,
    question: "",
    choices: [],
    selectedIndex: -1,
    correctIndex: -1,
    isCorrect: null,
    explanation: "",
    genPct: 0,
    nextBarPct: 0,
    nextReady: false,
    questionReadyAt: 0,
    afkStreak: 0,
    afkSecondsLeft: 120,
    plan: "",
    planLoaded: false,
    selectedFileName: "",
    eloGained: 0,
    results: [],
}

// ─── GLOBAL STATE ─────────────────────────────────────────────────
function gs(): S {
    const g = G()
    if (!g.__QCM)
        g.__QCM = {
            ...ZERO,
            sessionId: safeGet("navire_qcm_session_id"),
            view: (safeGet("navire_qcm_view") || "setup") as QcmView,
            afkStreak: Math.max(0, Number(safeGet(AFK_KEY) || 0)),
        }
    return g.__QCM as S
}
function ss(next: S) {
    G().__QCM = next
    safeSet("navire_qcm_view", next.view)
    if (next.sessionId) safeSet("navire_qcm_session_id", next.sessionId)
    if (isBrowser())
        window.dispatchEvent(new CustomEvent("__qcm", { detail: next }))
}
function patch(p: Partial<S>) {
    ss({ ...gs(), ...p })
}

// ─── LOCKS ────────────────────────────────────────────────────────
let POLL_LOCK = false,
    NEXT_POLL_LOCK = false,
    ANSWER_LOCK = false,
    NEXT_LOCK = false
const resetLocks = () => {
    POLL_LOCK = false
    NEXT_POLL_LOCK = false
    ANSWER_LOCK = false
    NEXT_LOCK = false
}

// ─── TIMERS ───────────────────────────────────────────────────────
let NEXT_BAR_TID: any = null
let AFK_TID: any = null

// ─── API ──────────────────────────────────────────────────────────
async function apiCurrent(sid: string) {
    const r = await fetchT(
        `${API_BASE}/qcm/${sid}/current`,
        { headers: authH() },
        30000
    )
    const t = await r.text().catch(() => "")
    if (!r.ok) throw new Error(`/current ${r.status}`)
    safeSet("navire_qcm_current", t)
    return safeJson(t) || {}
}
async function apiStart() {
    const token = safeGet("navire_token"),
        fileId = safeGet("navire_qcm_file_id")
    if (!token) {
        patch({ view: "setup", status: "error", error: "Connexion requise." })
        return
    }
    if (!fileId) {
        patch({
            view: "setup",
            status: "error",
            error: "Sélectionne un fichier.",
        })
        return
    }
    patch({ status: "starting", loading: true, error: "" })
    try {
        const r = await fetchT(
            `${API_BASE}/qcm/start`,
            {
                method: "POST",
                headers: { "Content-Type": "application/json", ...authH() },
                body: JSON.stringify({
                    file_id: Number(fileId),
                    difficulty: safeGet("navire_qcm_difficulty") || "medium",
                    pages: safeGet("navire_qcm_pages_raw") || "",
                }),
            },
            20000
        )
        const txt = await r.text().catch(() => "")
        if (r.status === 403) {
            const code = safeJson(txt)?.detail?.code || ""
            patch({
                view: "setup",
                status: "error",
                loading: false,
                error:
                    code === "QCM_DAILY_LIMIT_REACHED"
                        ? "Limite quotidienne atteinte. Passe à Membre pour un accès illimité."
                        : "Accès refusé.",
            })
            return
        }
        if (!r.ok) {
            patch({
                view: "setup",
                status: "error",
                loading: false,
                error: `Erreur serveur (${r.status}).`,
            })
            return
        }
        const sid = String(safeJson(txt)?.session_id || "")
        if (!sid) {
            patch({
                view: "setup",
                status: "error",
                loading: false,
                error: "Démarrage échoué.",
            })
            return
        }
        safeSet("navire_qcm_session_id", sid)
        patch({
            status: "generating",
            view: "generating",
            sessionId: sid,
            loading: false,
            genPct: 0,
        })
        poll(sid)
    } catch (e: any) {
        if (isAbort(e)) {
            const sid = safeGet("navire_qcm_session_id")
            if (sid) {
                patch({ status: "generating", loading: false })
                poll(sid)
            } else
                patch({
                    view: "setup",
                    status: "error",
                    loading: false,
                    error: "Timeout réseau.",
                })
            return
        }
        patch({
            view: "setup",
            status: "error",
            loading: false,
            error: e?.message || "Erreur réseau.",
        })
    }
}
async function apiAnswer(sid: string, idx: number) {
    const r = await fetchT(
        `${API_BASE}/qcm/${sid}/answer`,
        {
            method: "POST",
            headers: { "Content-Type": "application/json", ...authH() },
            body: JSON.stringify({ choice_index: idx }),
        },
        20000
    )
    const t = await r.text().catch(() => "")
    if (!r.ok) throw new Error(`/answer ${r.status}`)
    return safeJson(t) || {}
}
async function apiNext(sid: string) {
    const r = await fetchT(
        `${API_BASE}/qcm/${sid}/next`,
        {
            method: "POST",
            headers: { "Content-Type": "application/json", ...authH() },
            body: "{}",
        },
        30000
    )
    const t = await r.text().catch(() => "")
    if (!r.ok) throw new Error(`/next ${r.status}`)
    return safeJson(t) || {}
}
async function apiNextStatus(sid: string) {
    const r = await fetchT(
        `${API_BASE}/qcm/${sid}/next_status`,
        { headers: authH() },
        15000
    )
    const t = await r.text().catch(() => "")
    if (!r.ok) throw new Error(`/next_status ${r.status}`)
    return safeJson(t) || {}
}
async function apiClose(sid: string) {
    try {
        await fetchT(
            `${API_BASE}/qcm/${sid}/close`,
            {
                method: "POST",
                headers: { "Content-Type": "application/json", ...authH() },
                body: "{}",
            },
            10000
        )
    } catch {}
}
async function fetchPlan(): Promise<Plan> {
    if (!safeGet("navire_token")) return "free"
    try {
        const r = await fetchT(
            `${API_BASE}/subscriptions/me`,
            { headers: authH() },
            10000
        )
        if (!r.ok) return "free"
        return ((safeJson(await r.text()) || {}).plan as Plan) || "free"
    } catch {
        return "free"
    }
}

// ─── HYDRATATION AU REFRESH ───────────────────────────────────────
// Anti-triche : si l'utilisateur rafraîchit pendant qu'une question lui a
// déjà été affichée (sans répondre), on saute cette question via /next.
// On détecte ça via le marqueur localStorage navire_qcm_shown_idx.
async function hydrate() {
    const sid = safeGet("navire_qcm_session_id"),
        token = safeGet("navire_token")
    if (!sid || !token) {
        safeDel("navire_qcm_session_id")
        safeDel("navire_qcm_current")
        safeDel("navire_qcm_shown_idx")
        patch({ view: "setup", status: "idle", sessionId: "" })
        return
    }
    try {
        const r = await fetchT(
            `${API_BASE}/qcm/${sid}/current`,
            { headers: authH() },
            10000
        )
        if (r.status === 404 || r.status === 403) {
            safeDel("navire_qcm_session_id")
            safeDel("navire_qcm_current")
            safeDel("navire_qcm_shown_idx")
            patch({ view: "setup", status: "idle", sessionId: "" })
            return
        }
        if (!r.ok) {
            patch({ view: "generating", status: "generating", sessionId: sid })
            return
        }
        const d = safeJson(await r.text()) || {},
            st = String(d.status || "")
        if (st === "done" || st === "closed") {
            safeDel("navire_qcm_session_id")
            safeDel("navire_qcm_current")
            safeDel("navire_qcm_shown_idx")
            patch({ view: "setup", status: "idle", sessionId: "" })
        } else if (st === "ready") {
            const curIdx = Number(d.index || 1)
            const shownIdx = Number(safeGet("navire_qcm_shown_idx") || 0)

            // L'user avait déjà vu CETTE question → refresh = triche → on saute
            if (shownIdx === curIdx) {
                patch({
                    view: "generating",
                    status: "generating",
                    sessionId: sid,
                    error: "",
                    selectedFileName: safeGet("navire_qcm_file_label"),
                })
                try {
                    const nr = await apiNext(sid)
                    if (nr.status === "done") {
                        safeDel("navire_qcm_session_id")
                        safeDel("navire_qcm_shown_idx")
                        patch({
                            view: "continue",
                            status: "done",
                            sessionId: sid,
                        })
                        fetchPlan().then((plan) =>
                            patch({ plan, planLoaded: true })
                        )
                        return
                    }
                    if (nr.status === "ready") {
                        const nIdx = Number(nr.index || curIdx + 1)
                        safeSet("navire_qcm_shown_idx", String(nIdx))
                        patch({
                            view: "run",
                            status: "ready",
                            sessionId: sid,
                            index: nIdx,
                            total: Number(nr.total || QCM_COUNT),
                            question: String(nr.question || ""),
                            choices: Array.isArray(nr.choices)
                                ? nr.choices.map(String)
                                : [],
                            error: "",
                            questionReadyAt: Date.now(),
                            afkSecondsLeft: 120,
                            selectedFileName: safeGet("navire_qcm_file_label"),
                        })
                        return
                    }
                    // generating → poll
                    poll(sid)
                    return
                } catch {
                    poll(sid)
                    return
                }
            }

            // Première fois qu'on voit cette question → on l'affiche normalement
            safeSet("navire_qcm_shown_idx", String(curIdx))
            patch({
                view: "run",
                status: "ready",
                sessionId: sid,
                index: curIdx,
                total: Number(d.total || QCM_COUNT),
                question: String(d.question || ""),
                choices: Array.isArray(d.choices) ? d.choices.map(String) : [],
                error: "",
                questionReadyAt: Date.now(),
                afkSecondsLeft: 120,
                selectedFileName: safeGet("navire_qcm_file_label"),
            })
        } else {
            patch({
                view: "generating",
                status: "generating",
                sessionId: sid,
                error: "",
            })
            poll(sid)
        }
    } catch {
        const s2 = safeGet("navire_qcm_session_id")
        if (s2) {
            patch({ view: "generating", status: "generating", sessionId: s2 })
            poll(s2)
        }
    }
}

// ─── POLLING ──────────────────────────────────────────────────────
async function poll(sidArg?: string) {
    if (!isBrowser() || POLL_LOCK) return
    POLL_LOCK = true
    const INTERVAL = 900,
        MAX_MS = 180_000,
        FAILS_MAX = 8
    const started = Date.now()
    let fails = 0
    const sid = sidArg || safeGet("navire_qcm_session_id")
    if (!sid) {
        POLL_LOCK = false
        return
    }
    try {
        while (true) {
            let cur: any = null
            try {
                cur = await apiCurrent(sid)
                fails = 0
            } catch {
                if (++fails >= FAILS_MAX || Date.now() - started > MAX_MS) {
                    safeDel("navire_qcm_session_id")
                    patch({
                        view: "setup",
                        status: "error",
                        error: "Session perdue. Recommence.",
                    })
                    return
                }
                patch({ error: "Connexion…" })
                await new Promise((r) => setTimeout(r, 1200))
                continue
            }
            const st = String(cur?.status || "")
            if (st === "ready") {
                const rIdx = Number(cur.index || 1)
                safeSet("navire_qcm_shown_idx", String(rIdx))
                patch({
                    view: "run",
                    status: "ready",
                    sessionId: sid,
                    index: rIdx,
                    total: Number(cur.total || QCM_COUNT),
                    question: String(cur.question || ""),
                    choices: Array.isArray(cur.choices)
                        ? cur.choices.map(String)
                        : [],
                    error: "",
                    genPct: 0,
                    questionReadyAt: Date.now(),
                    afkSecondsLeft: 120,
                })
                return
            }
            if (st === "done") {
                safeDel("navire_qcm_session_id")
                patch({ view: "continue", status: "done", sessionId: sid })
                return
            }
            if (st === "error") {
                safeDel("navire_qcm_session_id")
                patch({
                    view: "setup",
                    status: "error",
                    error: String(cur.detail || "Erreur génération."),
                })
                return
            }
            if (st === "generating") {
                if (Date.now() - started > MAX_MS) {
                    safeDel("navire_qcm_session_id")
                    patch({
                        view: "setup",
                        status: "error",
                        error: "Génération trop longue.",
                    })
                    return
                }
                const pct =
                    cur.total > 0
                        ? Math.round(
                              (Number(cur.generated_count || 0) /
                                  Number(cur.total)) *
                                  100
                          )
                        : 0
                patch({ genPct: Math.min(pct, 99) })
            }
            await new Promise((r) => setTimeout(r, INTERVAL))
        }
    } finally {
        POLL_LOCK = false
    }
}
async function pollNextReady(sid: string) {
    if (NEXT_POLL_LOCK) return
    NEXT_POLL_LOCK = true
    const started = Date.now()
    try {
        while (Date.now() - started < 120_000) {
            const cur = gs()
            if (cur.status !== "answered" || cur.nextReady) break
            try {
                const d = await apiNextStatus(sid)
                if (d.status === "ready" || d.status === "done") {
                    patch({ nextReady: true, nextBarPct: 100 })
                    break
                }
            } catch {}
            await new Promise((r) => setTimeout(r, 700))
        }
    } finally {
        NEXT_POLL_LOCK = false
    }
}

// ─── TIMERS ───────────────────────────────────────────────────────
function startNextBar() {
    if (!isBrowser()) return
    if (NEXT_BAR_TID) clearInterval(NEXT_BAR_TID)
    const started = Date.now()
    patch({ nextBarPct: 0 })
    NEXT_BAR_TID = setInterval(() => {
        const cur = gs()
        if (cur.status !== "answered") {
            clearInterval(NEXT_BAR_TID)
            NEXT_BAR_TID = null
            return
        }
        const pct = Math.min(
            100,
            Math.round(((Date.now() - started) / NEXT_BAR_MS) * 100)
        )
        patch({ nextBarPct: pct })
        if (pct >= 100) {
            clearInterval(NEXT_BAR_TID)
            NEXT_BAR_TID = null
        }
    }, 120)
}
function startAfkCountdown() {
    if (!isBrowser()) return
    if (AFK_TID) clearInterval(AFK_TID)
    patch({ afkSecondsLeft: Math.round(AFK_LIMIT_MS / 1000) })
    AFK_TID = setInterval(() => {
        const cur = gs()
        if (cur.status !== "ready") {
            clearInterval(AFK_TID)
            AFK_TID = null
            return
        }
        const left = Math.max(
            0,
            Math.round(
                (AFK_LIMIT_MS - (Date.now() - cur.questionReadyAt)) / 1000
            )
        )
        patch({ afkSecondsLeft: left })
    }, 1000)
}
function resetRun(hard = false) {
    resetLocks()
    if (NEXT_BAR_TID) {
        clearInterval(NEXT_BAR_TID)
        NEXT_BAR_TID = null
    }
    if (AFK_TID) {
        clearInterval(AFK_TID)
        AFK_TID = null
    }
    const cur = gs()
    if (hard) {
        safeDel("navire_qcm_session_id")
        safeDel("navire_qcm_current")
        safeDel(AFK_KEY)
        safeDel("navire_qcm_shown_idx")
    }
    ss({ ...ZERO, view: "setup", plan: cur.plan, planLoaded: cur.planLoaded })
}

// ─── HOOK ─────────────────────────────────────────────────────────
function useS(): S {
    const [st, set] = useState<S>(gs)
    useEffect(() => {
        if (!isBrowser()) return
        set(gs())
        const fn = (e: any) => set({ ...e.detail })
        window.addEventListener("__qcm", fn)
        return () => window.removeEventListener("__qcm", fn)
    }, [])
    return st
}

// ─── TOAST ────────────────────────────────────────────────────────
let __TT: any = null
function toast(msg: string) {
    if (!isBrowser()) return
    const id = "__qcm_toast"
    let el = document.getElementById(id)
    if (!el) {
        el = document.createElement("div")
        el.id = id
        Object.assign(el.style, {
            position: "fixed",
            left: "50%",
            bottom: "24px",
            transform: "translateX(-50%)",
            zIndex: "999999",
            padding: "9px 14px",
            borderRadius: "11px",
            fontSize: "12.5px",
            fontWeight: "500",
            background: "rgba(30,30,44,0.95)",
            border: "1px solid rgba(255,255,255,0.1)",
            boxShadow: "0 10px 30px rgba(0,0,0,0.35)",
            color: "#fff",
            fontFamily: "'Inter',system-ui,sans-serif",
            whiteSpace: "nowrap",
            pointerEvents: "none",
            transition: "opacity .18s",
        })
        document.body.appendChild(el)
    }
    el.textContent = msg
    ;(el as any).style.opacity = "1"
    if (__TT) clearTimeout(__TT)
    __TT = setTimeout(() => {
        const e = document.getElementById(id)
        if (e) {
            ;(e as any).style.opacity = "0"
            setTimeout(() => e.remove(), 200)
        }
    }, 1600)
}

// ─────────────────────────────────────────────────────────────────
//  CHARTE NAVIRE AI
//  Les tokens et les classes vivent dans une feuille de style injectée
//  par le root (et par l'overlay, monté dans <body>).
// ─────────────────────────────────────────────────────────────────
const CSS = `
.qcm-root {
  --panel: rgba(255,255,255,0.035);
  --panel-2: rgba(255,255,255,0.06);
  --panel-3: rgba(255,255,255,0.1);
  --line: rgba(255,255,255,0.07);
  --line-2: rgba(255,255,255,0.14);
  --text: #fff;
  --text-2: rgba(255,255,255,0.72);
  --text-3: rgba(255,255,255,0.45);
  --text-4: rgba(255,255,255,0.28);
  --danger: #FF6B6B;
  --ok: #5BE3A3;
  --warn: #FFD166;
  --blue: #7AA7FF;
  --ink: #0a0a12;
  --ease: cubic-bezier(.2,.8,.2,1);
  font-family: 'Inter', system-ui, sans-serif;
  -webkit-font-smoothing: antialiased;
  color: var(--text);
  font-size: 14px;
  box-sizing: border-box;
}
.qcm-root *, .qcm-root *::before, .qcm-root *::after { box-sizing: border-box; }
:where(.qcm-root) button { font-family: inherit; color: inherit; margin: 0; }
.qcm-root ::selection { background: rgba(255,255,255,0.22); }
.qcm-root svg { flex-shrink: 0; }
.qcm-mono { font-family: 'IBM Plex Mono', ui-monospace, monospace; }

@keyframes qcm-fade { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: none; } }
@keyframes qcm-rise { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: none; } }
@keyframes qcm-in { from { opacity: 0; } to { opacity: 1; } }
@keyframes qcm-pulse { 0%,100% { transform: scale(.94); opacity: .75; } 50% { transform: scale(1.04); opacity: 1; } }
@keyframes qcm-shimmer { from { background-position: 120% 0; } to { background-position: -120% 0; } }

.qcm-view { display: flex; flex-direction: column; animation: qcm-fade .28s var(--ease); }
.qcm-head h2 { margin: 0; font-size: 20px; font-weight: 700; letter-spacing: -.4px; }
.qcm-head p { margin: 4px 0 0; font-size: 13px; color: var(--text-3); line-height: 1.55; }
.qcm-label { display: block; font-size: 12px; font-weight: 500; color: var(--text-3); margin-bottom: 8px; }
.qcm-label em { font-style: normal; color: var(--text-4); }
.qcm-kicker { font-family: 'IBM Plex Mono', ui-monospace, monospace; font-size: 10px; font-weight: 600; letter-spacing: .6px; text-transform: uppercase; color: var(--text-4); }

.qcm-card { padding: 16px; border-radius: 16px; background: var(--panel); border: 1px solid var(--line); animation: qcm-rise .4s var(--ease) both; }
.qcm-card.is-on { border-color: var(--line-2); background: var(--panel-2); }
.qcm-notice { padding: 12px 14px; border-radius: 12px; background: rgba(255,209,102,0.08); border: 1px solid rgba(255,209,102,0.2); font-size: 12.5px; color: #ffe2a3; line-height: 1.5; }
.qcm-notice.is-bad { background: rgba(255,107,107,0.08); border-color: rgba(255,107,107,0.2); color: #ffb3b3; }
.qcm-link-btn { background: none; border: none; padding: 0; color: #fff; font-size: 12.5px; font-weight: 600; cursor: pointer; text-decoration: underline; text-underline-offset: 3px; }

/* Boutons */
.qcm-btn { display: inline-flex; align-items: center; justify-content: center; gap: 7px; height: 38px; padding: 0 18px; border-radius: 99px; border: none; background: #fff; color: var(--ink) !important; font-size: 13px; font-weight: 600; cursor: pointer; text-decoration: none; white-space: nowrap; transition: transform .15s, background .2s; }
.qcm-btn:hover:not(:disabled) { transform: translateY(-1px); }
.qcm-btn:active:not(:disabled) { transform: scale(.98); }
.qcm-btn:disabled { background: var(--panel-3); color: var(--text-4) !important; cursor: default; }
.qcm-btn.is-block { width: 100%; height: 42px; }
.qcm-btn svg { width: 15px; height: 15px; }
.qcm-btn-sub { display: flex; flex-direction: column; height: auto !important; padding: 10px 18px; gap: 1px; }
.qcm-btn-sub small { font-size: 11px; font-weight: 500; opacity: .6; }
.qcm-chip { display: inline-flex; align-items: center; justify-content: center; gap: 6px; height: 38px; padding: 0 16px; border-radius: 99px; border: 1px solid var(--line); background: transparent; font-size: 13px; font-weight: 500; color: var(--text-2); cursor: pointer; text-decoration: none; white-space: nowrap; transition: background .15s, color .15s; }
.qcm-chip:hover { background: var(--panel-2); color: #fff; }
.qcm-icon-btn { width: 34px; height: 34px; display: inline-flex; align-items: center; justify-content: center; border-radius: 10px; border: none; background: transparent; color: var(--text-3); cursor: pointer; transition: background .15s, color .15s; padding: 0; }
.qcm-icon-btn:hover { background: var(--panel-2); color: #fff; }

/* Champs */
.qcm-input { width: 100%; height: 42px; padding: 0 14px; border-radius: 12px; background: var(--panel); border: 1px solid var(--line); color: #fff; font: inherit; font-size: 13.5px; outline: none; transition: border-color .15s, background .15s; }
.qcm-input::placeholder { color: var(--text-4); }
.qcm-input:focus { border-color: rgba(255,255,255,0.22); background: var(--panel-2); }
.qcm-root.is-mobile .qcm-input { font-size: 16px; }
.qcm-search { display: flex; align-items: center; gap: 10px; padding: 0 14px; height: 40px; border-radius: 12px; background: var(--panel); border: 1px solid var(--line); color: var(--text-3); }
.qcm-search input { flex: 1; min-width: 0; background: none; border: none; outline: none; color: #fff; font: inherit; font-size: 13.5px; }
.qcm-search input::placeholder { color: var(--text-4); }
.qcm-root.is-mobile .qcm-search input { font-size: 16px; }

/* Difficulté */
.qcm-seg { display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px; }
.qcm-seg button { display: flex; flex-direction: column; align-items: center; gap: 2px; padding: 9px 6px; border-radius: 12px; border: 1px solid var(--line); background: transparent; cursor: pointer; transition: background .15s, border-color .15s; }
.qcm-seg button:hover { background: var(--panel); }
.qcm-seg b { font-size: 13px; font-weight: 600; color: var(--text-2); transition: color .15s; }
.qcm-seg small { font-family: 'IBM Plex Mono', ui-monospace, monospace; font-size: 10px; color: var(--text-4); }
.qcm-seg button.is-active { background: var(--panel-3); border-color: var(--line-2); }
.qcm-seg button.is-active b { color: #fff; }
.qcm-seg button.is-active small { color: var(--text-3); }

/* Lignes (fichiers) */
.qcm-row { display: flex; align-items: center; gap: 12px; width: 100%; padding: 12px 14px; border-radius: 12px; background: var(--panel); border: 1px solid var(--line); cursor: pointer; text-align: left; transition: background .15s, border-color .15s; }
.qcm-row:hover { background: var(--panel-2); }
.qcm-row.is-on { background: var(--panel-2); border-color: var(--line-2); }
.qcm-row-ico { width: 36px; height: 36px; border-radius: 10px; background: var(--panel-2); display: flex; align-items: center; justify-content: center; color: var(--text-2); flex-shrink: 0; transition: background .15s, color .15s; }
.qcm-row.is-on .qcm-row-ico { background: #fff; color: var(--ink); }
.qcm-row-body { flex: 1; min-width: 0; }
.qcm-row-title { font-size: 13.5px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.qcm-row-meta { font-size: 11.5px; color: var(--text-3); margin-top: 3px; }
.qcm-row-chev { color: var(--text-4); }
.qcm-file { flex-direction: column; align-items: stretch; animation: qcm-rise .35s var(--ease) both; }
.qcm-file-top { display: flex; align-items: center; gap: 12px; }
.qcm-tag { display: inline-flex; align-items: center; gap: 5px; font-family: 'IBM Plex Mono', ui-monospace, monospace; font-size: 10px; font-weight: 600; padding: 2px 8px; border-radius: 99px; background: var(--panel-2); color: var(--text-2); white-space: nowrap; }
.qcm-tag i { width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
.qcm-tag.ok { color: var(--ok); background: rgba(91,227,163,0.1); }
.qcm-tag.warn { color: var(--warn); background: rgba(255,209,102,0.1); }
.qcm-tag.bad { color: var(--danger); background: rgba(255,107,107,0.1); }
.qcm-bar { height: 4px; border-radius: 9px; background: var(--panel-2); overflow: hidden; }
.qcm-bar i { display: block; height: 100%; border-radius: 9px; transition: width .8s var(--ease); }
.qcm-bar-label { display: flex; justify-content: space-between; font-family: 'IBM Plex Mono', ui-monospace, monospace; font-size: 10px; color: var(--text-4); }
.qcm-skel { height: 62px; border-radius: 12px; background: linear-gradient(90deg, var(--panel) 0%, var(--panel-2) 50%, var(--panel) 100%); background-size: 200% 100%; animation: qcm-shimmer 1.4s linear infinite; }
.qcm-empty { text-align: center; padding: 34px 20px; font-size: 13px; color: var(--text-3); line-height: 1.6; }
.qcm-empty b { display: block; color: var(--text-2); font-size: 14px; margin-bottom: 4px; }

/* Progression de la session */
.qcm-segs { display: flex; gap: 4px; flex: 1; }
.qcm-segs i { flex: 1; height: 4px; border-radius: 9px; background: var(--panel-3); transition: background .3s; }
.qcm-segs i.is-past { background: rgba(255,255,255,0.3); }
.qcm-segs i.is-cur { background: #fff; }
.qcm-segs i.is-ok { background: var(--ok); }
.qcm-segs i.is-bad { background: var(--danger); }

/* Minuteur */
.qcm-timer { display: inline-flex; align-items: center; gap: 6px; height: 26px; padding: 0 10px; border-radius: 99px; background: var(--panel); border: 1px solid var(--line); font-family: 'IBM Plex Mono', ui-monospace, monospace; font-size: 11px; font-weight: 600; color: var(--text-3); transition: color .4s, background .4s, border-color .4s; }
.qcm-timer.is-urgent { color: var(--danger); background: rgba(255,107,107,0.08); border-color: rgba(255,107,107,0.25); }

/* Pastille ELO */
.qcm-elo { display: inline-flex; align-items: center; gap: 6px; height: 24px; padding: 0 10px; border-radius: 99px; background: #fff; color: var(--ink); font-family: 'IBM Plex Mono', ui-monospace, monospace; font-size: 11px; font-weight: 600; white-space: nowrap; flex-shrink: 0; }
.qcm-elo small { font-size: 9.5px; letter-spacing: .4px; text-transform: uppercase; opacity: .55; }

/* Choix */
.qcm-choice { display: flex; align-items: flex-start; gap: 12px; width: 100%; padding: 11px 12px; border-radius: 12px; background: var(--panel); border: 1px solid var(--line); text-align: left; font-size: 14px; line-height: 1.5; color: var(--text-2); cursor: default; transition: background .15s, border-color .15s, color .15s, opacity .25s, transform .15s; animation: qcm-rise .35s var(--ease) both; }
.qcm-root.is-mobile .qcm-choice { font-size: 13.5px; padding: 10px 11px; }
.qcm-choice.is-live { cursor: pointer; }
.qcm-choice.is-live:hover { background: var(--panel-2); border-color: var(--line-2); color: #fff; }
.qcm-choice.is-live:active { transform: scale(.99); }
.qcm-choice-key { width: 26px; height: 26px; border-radius: 8px; background: var(--panel-2); display: flex; align-items: center; justify-content: center; flex-shrink: 0; font-family: 'IBM Plex Mono', ui-monospace, monospace; font-size: 11px; font-weight: 600; color: var(--text-3); transition: background .15s, color .15s; }
.qcm-choice-key svg { width: 13px; height: 13px; }
.qcm-choice-text { flex: 1; min-width: 0; padding-top: 2px; }
.qcm-choice.is-pending { background: var(--panel-2); border-color: var(--line-2); color: #fff; }
.qcm-choice.is-pending .qcm-choice-key { background: #fff; color: var(--ink); }
.qcm-choice.is-good { background: rgba(91,227,163,0.08); border-color: rgba(91,227,163,0.45); color: #fff; }
.qcm-choice.is-good .qcm-choice-key { background: var(--ok); color: var(--ink); }
.qcm-choice.is-bad { background: rgba(255,107,107,0.08); border-color: rgba(255,107,107,0.45); color: #fff; }
.qcm-choice.is-bad .qcm-choice-key { background: var(--danger); color: var(--ink); }
.qcm-choice.is-muted { opacity: .45; }

/* Suivant : se remplit pendant la lecture de l'explication, puis devient blanc */
.qcm-next { position: relative; overflow: hidden; display: inline-flex; align-items: center; justify-content: center; height: 42px; min-width: 150px; padding: 0 22px; border-radius: 99px; border: 1px solid var(--line); background: var(--panel); color: var(--text-3); font-size: 13px; font-weight: 600; cursor: default; transition: background .25s, color .25s, border-color .25s, transform .15s; }
.qcm-next .qcm-next-fill { position: absolute; top: 0; bottom: 0; left: 0; background: rgba(255,255,255,0.1); transition: width .12s linear, opacity .25s; }
.qcm-next .qcm-next-lbl { position: relative; display: inline-flex; align-items: center; gap: 7px; }
.qcm-next svg { width: 15px; height: 15px; }
.qcm-next.is-ready { background: #fff; border-color: #fff; color: var(--ink); cursor: pointer; }
.qcm-next.is-ready:hover { transform: translateY(-1px); }
.qcm-next.is-ready .qcm-next-fill { opacity: 0; }

/* Liste d'avantages */
.qcm-perks { display: flex; flex-direction: column; gap: 8px; margin: 0; padding: 0; list-style: none; }
.qcm-perks li { display: flex; align-items: center; gap: 10px; font-size: 13px; color: var(--text-2); }
.qcm-perks svg { width: 15px; height: 15px; color: var(--ok); }

/* Overlay du choix de cours */
.qcm-overlay { position: fixed; inset: 0; z-index: 9999; display: flex; align-items: center; justify-content: center; padding: 24px 16px; background: rgba(0,0,0,0.5); backdrop-filter: blur(14px); -webkit-backdrop-filter: blur(14px); animation: qcm-in .2s ease both; }
.qcm-sheet { width: 100%; max-width: 460px; max-height: 80vh; display: flex; flex-direction: column; overflow: hidden; border-radius: 18px; background: #0c0c14; border: 1px solid rgba(255,255,255,0.1); box-shadow: 0 30px 80px rgba(0,0,0,0.5); animation: qcm-rise .3s var(--ease) both; }
.qcm-sheet-head { display: flex; align-items: center; gap: 12px; padding: 16px 14px 14px 18px; border-bottom: 1px solid var(--line); flex-shrink: 0; }
.qcm-sheet-head b { display: block; font-size: 15px; font-weight: 700; letter-spacing: -.2px; }
.qcm-sheet-head span { display: block; font-size: 11.5px; color: var(--text-3); margin-top: 2px; }
.qcm-scroll { overflow-y: auto; overscroll-behavior: contain; scrollbar-width: thin; scrollbar-color: rgba(255,255,255,0.1) transparent; }
.qcm-scroll::-webkit-scrollbar { width: 6px; }
.qcm-scroll::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.1); border-radius: 9px; }
`

// Feuille de style + polices, rendues dans le root et dans l'overlay
function QcmStyles() {
    return (
        <>
            <link rel="stylesheet" href={FONTS_URL} />
            <style>{CSS}</style>
        </>
    )
}

// ─── ICÔNES ───────────────────────────────────────────────────────
function Ico({
    children,
    size = 16,
    sw = 1.8,
}: {
    children: React.ReactNode
    size?: number
    sw?: number
}) {
    return (
        <svg
            width={size}
            height={size}
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth={sw}
            strokeLinecap="round"
            strokeLinejoin="round"
        >
            {children}
        </svg>
    )
}
const IcoFile = (p: { size?: number }) => (
    <Ico {...p}>
        <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z" />
        <polyline points="14 2 14 8 20 8" />
    </Ico>
)
const IcoChevron = (p: { size?: number }) => (
    <Ico {...p}>
        <path d="M6 9l6 6 6-6" />
    </Ico>
)
const IcoX = (p: { size?: number }) => (
    <Ico {...p} sw={2.2}>
        <path d="M18 6L6 18M6 6l12 12" />
    </Ico>
)
const IcoCheck = (p: { size?: number }) => (
    <Ico {...p} sw={2.4}>
        <path d="M20 6L9 17l-5-5" />
    </Ico>
)
const IcoClock = (p: { size?: number }) => (
    <Ico {...p} sw={2}>
        <circle cx="12" cy="12" r="9" />
        <path d="M12 7v5l3 2" />
    </Ico>
)
const IcoArrow = (p: { size?: number }) => (
    <Ico {...p} sw={2}>
        <path d="M5 12h14M13 6l6 6-6 6" />
    </Ico>
)
const IcoSearch = (p: { size?: number }) => (
    <Ico {...p}>
        <circle cx="11" cy="11" r="7" />
        <path d="M20 20l-3.5-3.5" />
    </Ico>
)

// ─────────────────────────────────────────────────────────────────
//  FILE PICKER OVERLAY
//  • monté dans <body> (portal) : pas rogné par la Frame Framer
//  • lignes scrollables avec barres activité + réussite
// ─────────────────────────────────────────────────────────────────

type FileStat = {
    file_id: number
    file_name: string
    sessions_count: number
    usage_pct: number
    success_rate_pct: number
}

// Couleur de la barre success selon le score
function successColor(pct: number): string {
    if (pct <= 0) return "rgba(255,255,255,0.15)"
    if (pct < 40) return "#FF6B6B"
    if (pct < 65) return "#FFD166"
    return "#5BE3A3"
}
function successTag(pct: number): string {
    if (pct < 40) return "bad"
    if (pct < 65) return "warn"
    return "ok"
}
function successLabel(pct: number): string {
    if (pct < 40) return "À retravailler"
    if (pct < 65) return "Moyen"
    return "Maîtrisé"
}

function FilePickerOverlay({
    files,
    fileStats,
    selectedId,
    mobile,
    onSelect,
    onClose,
}: {
    files: FileItem[]
    fileStats: Record<string, FileStat>
    selectedId: string
    mobile: boolean
    onSelect: (id: string) => void
    onClose: () => void
}) {
    const [q, setQ] = useState("")

    // Ferme avec Échap
    useEffect(() => {
        const fn = (e: KeyboardEvent) => e.key === "Escape" && onClose()
        window.addEventListener("keydown", fn)
        return () => window.removeEventListener("keydown", fn)
    }, [onClose])

    // Ferme sur clic backdrop
    const onBackdrop = (e: React.MouseEvent) => {
        if (e.target === e.currentTarget) onClose()
    }

    const needle = q.trim().toLowerCase()
    const shown = needle
        ? files.filter((f) => f.label.toLowerCase().includes(needle))
        : files

    const sheet = (
        <div className={`qcm-root${mobile ? " is-mobile" : ""}`}>
            <QcmStyles />
            <div className="qcm-overlay" onClick={onBackdrop}>
                <div className="qcm-sheet">
                    {/* Header */}
                    <div className="qcm-sheet-head">
                        <div style={{ flex: 1, minWidth: 0 }}>
                            <b>Choisir un cours</b>
                            <span>
                                {files.length} fichier
                                {files.length > 1 ? "s" : ""} disponible
                                {files.length > 1 ? "s" : ""}
                            </span>
                        </div>
                        <button
                            className="qcm-icon-btn"
                            onClick={onClose}
                            aria-label="Fermer"
                        >
                            <IcoX size={16} />
                        </button>
                    </div>

                    {/* Recherche, utile dès qu'il y a plusieurs cours */}
                    {files.length > 5 && (
                        <div style={{ padding: "12px 14px 0" }}>
                            <label className="qcm-search">
                                <IcoSearch size={16} />
                                <input
                                    value={q}
                                    onChange={(e) => setQ(e.target.value)}
                                    placeholder="Rechercher un cours"
                                />
                            </label>
                        </div>
                    )}

                    {/* Liste */}
                    <div
                        className="qcm-scroll"
                        style={{
                            padding: "12px 14px 16px",
                            display: "flex",
                            flexDirection: "column",
                            gap: 6,
                        }}
                    >
                        {shown.map((f, n) => {
                            const stat = fileStats[f.id] || null
                            const sessions = stat?.sessions_count ?? 0
                            const succPct = stat?.success_rate_pct ?? -1
                            const usagePct = stat?.usage_pct ?? 0
                            const isActive = f.id === selectedId
                            const color = successColor(succPct)

                            return (
                                <button
                                    key={f.id}
                                    className={`qcm-row qcm-file${isActive ? " is-on" : ""}`}
                                    style={{
                                        gap: 10,
                                        animationDelay: `${Math.min(n, 8) * 0.03}s`,
                                    }}
                                    onClick={() => {
                                        onSelect(f.id)
                                        onClose()
                                    }}
                                >
                                    <div className="qcm-file-top">
                                        <div className="qcm-row-ico">
                                            {isActive ? (
                                                <IcoCheck size={15} />
                                            ) : (
                                                <IcoFile size={16} />
                                            )}
                                        </div>
                                        <div className="qcm-row-body">
                                            <div
                                                className="qcm-row-title"
                                                style={{
                                                    whiteSpace: "normal",
                                                    wordBreak: "break-word",
                                                }}
                                            >
                                                {f.label}
                                            </div>
                                            <div className="qcm-row-meta">
                                                {sessions > 0
                                                    ? `${sessions} session${sessions > 1 ? "s" : ""}`
                                                    : "Jamais utilisé"}
                                            </div>
                                        </div>
                                        {succPct >= 0 && (
                                            <span
                                                className={`qcm-tag ${successTag(succPct)}`}
                                            >
                                                <i />
                                                {succPct}%
                                            </span>
                                        )}
                                    </div>

                                    {/* Barres : activité (usage_pct) puis réussite (success_rate_pct) */}
                                    {sessions > 0 && (
                                        <div
                                            style={{
                                                display: "flex",
                                                flexDirection: "column",
                                                gap: 5,
                                                paddingLeft: 48,
                                            }}
                                        >
                                            <div className="qcm-bar-label">
                                                <span>Activité</span>
                                                {succPct >= 0 && (
                                                    <span style={{ color }}>
                                                        {successLabel(succPct)}
                                                    </span>
                                                )}
                                            </div>
                                            <div className="qcm-bar">
                                                <BarAnimate
                                                    pct={usagePct}
                                                    color="rgba(255,255,255,0.3)"
                                                />
                                            </div>
                                            {succPct >= 0 && (
                                                <div className="qcm-bar">
                                                    <BarAnimate
                                                        pct={succPct}
                                                        color={color}
                                                    />
                                                </div>
                                            )}
                                        </div>
                                    )}
                                </button>
                            )
                        })}

                        {files.length > 0 && shown.length === 0 && (
                            <div className="qcm-empty">
                                <b>Aucun résultat</b>
                                Aucun cours ne correspond à « {q.trim()} ».
                            </div>
                        )}

                        {files.length === 0 && (
                            <div className="qcm-empty">
                                <b>Aucun fichier disponible</b>
                                Upload un PDF dans /files pour lancer un QCM.
                            </div>
                        )}
                    </div>
                </div>
            </div>
        </div>
    )

    return isBrowser() ? createPortal(sheet, document.body) : sheet
}

// Barre animée au mount
function BarAnimate({ pct, color }: { pct: number; color: string }) {
    const [w, setW] = useState(0)
    useEffect(() => {
        const t = setTimeout(() => setW(pct), 80)
        return () => clearTimeout(t)
    }, [pct])
    return <i style={{ width: `${w}%`, background: color }} />
}

// ─────────────────────────────────────────────────────────────────
//  VIEW — SETUP
// ─────────────────────────────────────────────────────────────────
const DIFFS = [
    { id: "easy", label: "Facile" },
    { id: "medium", label: "Moyen" },
    { id: "hard", label: "Difficile" },
] as const

function SetupView() {
    const st = useS()
    const mobile = useIsMobile()
    const [fileId, setFileId] = useState(safeGet("navire_qcm_file_id") || "")
    const [diff, setDiff] = useState(
        safeGet("navire_qcm_difficulty") || "medium"
    )
    const [pages, setPages] = useState(safeGet("navire_qcm_pages_raw") || "")
    const [files, setFiles] = useState<FileItem[]>([])
    const [fileStats, setFileStats] = useState<Record<string, FileStat>>({})
    const [loading, setLoading] = useState(false)
    const [fErr, setFErr] = useState("")
    const [overlayOpen, setOverlayOpen] = useState(false)

    useEffect(() => {
        loadAll()
    }, [])

    async function loadAll() {
        if (!safeGet("navire_token")) {
            setFErr("Connecte-toi pour accéder au QCM.")
            return
        }
        setLoading(true)
        setFErr("")
        try {
            const [rFiles, rStats] = await Promise.all([
                fetchT(`${API_BASE}/files`, { headers: authH() }, 20000),
                fetchT(
                    `${API_BASE}/stats/files`,
                    { headers: authH() },
                    20000
                ).catch(() => null),
            ])
            if (rFiles.status === 401) {
                setFErr("Session expirée. Reconnecte-toi.")
                return
            }
            if (!rFiles.ok) {
                setFErr(`Erreur /files (${rFiles.status})`)
                return
            }

            const rawFiles = safeJson(await rFiles.text().catch(() => "")) || []
            const arr = Array.isArray(rawFiles)
                ? rawFiles
                : Array.isArray(rawFiles?.items)
                  ? rawFiles.items
                  : []
            const list: FileItem[] = arr.map((f: any) => ({
                id: String(f.id),
                label: String(
                    f.filename_original ??
                        f.filename ??
                        f.name ??
                        `Fichier ${f.id}`
                ).replace(/\.pdf$/i, ""),
                sessions_count: 0,
                usage_pct: 0,
                success_rate: -1,
            }))
            setFiles(list)
            if (!fileId && list.length) setFileId(list[0].id)
            if (!list.length) setFErr("Aucun fichier. Upload un PDF d'abord.")

            // Stats par fichier
            if (rStats && rStats.ok) {
                const rawStats =
                    safeJson(await rStats.text().catch(() => "")) || []
                if (Array.isArray(rawStats)) {
                    const map: Record<string, FileStat> = {}
                    rawStats.forEach((s: any) => {
                        map[String(s.file_id)] = s
                    })
                    setFileStats(map)
                }
            }
        } catch {
            setFErr("Erreur réseau. Réessaie.")
        } finally {
            setLoading(false)
        }
    }

    function start() {
        if (!fileId) {
            setFErr("Sélectionne un fichier.")
            return
        }
        const fname = files.find((f) => f.id === fileId)?.label || ""
        safeSet("navire_qcm_file_id", fileId)
        safeSet("navire_qcm_difficulty", diff)
        safeSet("navire_qcm_pages_raw", pages)
        safeDel("navire_qcm_session_id")
        safeDel("navire_qcm_current")
        safeDel("navire_qcm_shown_idx")
        resetLocks()
        patch({
            view: "generating",
            status: "starting",
            error: "",
            selectedFileName: fname,
            eloGained: 0,
            results: [],
        })
        apiStart()
    }

    const selectedFile = files.find((f) => f.id === fileId)
    const selectedStat = fileId ? fileStats[fileId] : null
    const errMsg = st.error || fErr

    return (
        <>
            <div className="qcm-view" style={{ gap: mobile ? 22 : 18 }}>
                <div className="qcm-head">
                    <h2>Nouvelle session</h2>
                    <p>
                        {QCM_COUNT} questions générées à partir de ton cours.
                    </p>
                </div>

                {/* Erreur */}
                {errMsg && (
                    <div className="qcm-notice is-bad">
                        {errMsg}
                        {errMsg.includes("Limite") && (
                            <a
                                href="/subscribe"
                                className="qcm-link-btn"
                                style={{ display: "block", marginTop: 6 }}
                            >
                                Voir les offres Membre
                            </a>
                        )}
                    </div>
                )}

                {/* Fichier — ligne qui ouvre l'overlay */}
                <div>
                    <span className="qcm-label">Cours</span>
                    {loading ? (
                        <div className="qcm-skel" />
                    ) : (
                        <button
                            className={`qcm-row${selectedFile ? " is-on" : ""}`}
                            onClick={() => setOverlayOpen(true)}
                        >
                            <div className="qcm-row-ico">
                                <IcoFile size={16} />
                            </div>
                            <div className="qcm-row-body">
                                <div
                                    className="qcm-row-title"
                                    style={{
                                        color: selectedFile
                                            ? undefined
                                            : "var(--text-3)",
                                    }}
                                >
                                    {selectedFile
                                        ? selectedFile.label
                                        : "Choisir un fichier"}
                                </div>
                                {selectedStat &&
                                selectedStat.sessions_count > 0 ? (
                                    <div className="qcm-row-meta">
                                        {selectedStat.sessions_count} session
                                        {selectedStat.sessions_count > 1
                                            ? "s"
                                            : ""}{" "}
                                        · {selectedStat.success_rate_pct}%
                                        de réussite
                                    </div>
                                ) : selectedFile ? (
                                    <div className="qcm-row-meta">
                                        Jamais utilisé
                                    </div>
                                ) : null}
                            </div>
                            <span className="qcm-row-chev">
                                <IcoChevron size={16} />
                            </span>
                        </button>
                    )}
                </div>

                {/* Difficulté */}
                <div>
                    <span className="qcm-label">Difficulté</span>
                    <div className="qcm-seg">
                        {DIFFS.map((d) => (
                            <button
                                key={d.id}
                                className={diff === d.id ? "is-active" : ""}
                                onClick={() => setDiff(d.id)}
                            >
                                <b>{d.label}</b>
                                <small>+{ELO_PER_CORRECT[d.id]} elo</small>
                            </button>
                        ))}
                    </div>
                </div>

                {/* Pages */}
                <label style={{ display: "block" }}>
                    <span className="qcm-label">
                        Pages <em>· optionnel, ex : 1-5, 12</em>
                    </span>
                    <input
                        className="qcm-input"
                        type="text"
                        placeholder="Toutes les pages"
                        value={pages}
                        onChange={(e) => setPages(e.target.value)}
                    />
                </label>

                {/* CTA — mobile : 2/3 largeur aligné à droite (zone du pouce) ; desktop : pleine largeur */}
                <div
                    style={{
                        display: "flex",
                        justifyContent: mobile ? "flex-end" : "stretch",
                        width: "100%",
                    }}
                >
                    <button
                        className="qcm-btn is-block"
                        onClick={start}
                        disabled={loading || !files.length}
                        style={{
                            width: mobile ? "66%" : "100%",
                            minWidth: mobile ? 150 : undefined,
                        }}
                    >
                        Commencer
                        <IcoArrow />
                    </button>
                </div>
            </div>

            {overlayOpen && (
                <FilePickerOverlay
                    files={files}
                    fileStats={fileStats}
                    selectedId={fileId}
                    mobile={mobile}
                    onSelect={(id) => {
                        setFileId(id)
                        safeSet("navire_qcm_file_id", id)
                        const fname =
                            files.find((f) => f.id === id)?.label || ""
                        safeSet("navire_qcm_file_label", fname)
                        patch({ selectedFileName: fname })
                    }}
                    onClose={() => setOverlayOpen(false)}
                />
            )}
        </>
    )
}

// ─────────────────────────────────────────────────────────────────
//  VIEW — GENERATING
// ─────────────────────────────────────────────────────────────────
const GEN_STEPS = [
    "Extraction du cours…",
    "Génération des questions…",
    "Construction des réponses…",
    "Finalisation…",
]

function GeneratingView() {
    const st = useS()
    const mobile = useIsMobile()
    const [step, setStep] = useState(0)
    const logoSize = mobile ? 48 : 60

    useEffect(() => {
        const t = setInterval(
            () => setStep((v) => Math.min(v + 1, GEN_STEPS.length - 1)),
            1800
        )
        return () => clearInterval(t)
    }, [])

    const msg =
        st.error && st.error !== "Connexion…" ? st.error : GEN_STEPS[step]

    return (
        <div
            className="qcm-view"
            style={{
                alignItems: "center",
                justifyContent: "center",
                gap: 18,
                padding: mobile ? "36px 0" : "56px 0",
                minHeight: mobile ? 280 : 360,
                textAlign: "center",
            }}
        >
            {/* Logo NAVIRE pulsé */}
            <div
                style={{
                    width: logoSize,
                    height: logoSize,
                    marginBottom: 4,
                    animation: "qcm-pulse 2.4s ease-in-out infinite",
                    filter: "drop-shadow(0 16px 40px rgba(120,150,255,0.22))",
                }}
            >
                <img
                    src={LOGO_URL}
                    alt="NAVIRE"
                    style={{
                        width: "100%",
                        height: "100%",
                        objectFit: "contain",
                        display: "block",
                        borderRadius: 14,
                    }}
                />
            </div>

            <div>
                <div
                    style={{
                        fontSize: 15,
                        fontWeight: 600,
                        letterSpacing: "-.2px",
                    }}
                >
                    Préparation du QCM
                </div>
                <div
                    key={msg}
                    style={{
                        marginTop: 4,
                        fontSize: 13,
                        color: "var(--text-3)",
                        animation: "qcm-fade .3s var(--ease)",
                    }}
                >
                    {msg}
                </div>
            </div>

            {/* Progression */}
            <div
                style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 10,
                    width: mobile ? 180 : 220,
                }}
            >
                <div className="qcm-bar" style={{ flex: 1 }}>
                    <i
                        style={{
                            width: `${Math.max(st.genPct, 5)}%`,
                            background: "#fff",
                            transition: "width .4s ease",
                        }}
                    />
                </div>
                <span
                    className="qcm-mono"
                    style={{
                        fontSize: 10,
                        color: "var(--text-4)",
                        minWidth: 28,
                        textAlign: "right",
                    }}
                >
                    {st.genPct > 0 ? `${st.genPct}%` : ""}
                </span>
            </div>
        </div>
    )
}

// ─────────────────────────────────────────────────────────────────
//  VIEW — RUN
// ─────────────────────────────────────────────────────────────────
function Segments({
    total,
    index,
    answered,
    results,
}: {
    total: number
    index: number
    answered: boolean
    results: (boolean | null)[]
}) {
    return (
        <div className="qcm-segs">
            {Array.from({ length: total }).map((_, i) => {
                const res = results[i] // true / false / null / undefined
                const isPast = i < index - 1 || (i === index - 1 && answered)
                const isCur = i === index - 1
                let cls = ""
                if (res === true) cls = "is-ok"
                else if (res === false) cls = "is-bad"
                else if (isCur) cls = "is-cur"
                else if (isPast) cls = "is-past"
                return <i key={i} className={cls} />
            })}
        </div>
    )
}

function RunView() {
    const st = useS()
    const mobile = useIsMobile()
    const answered = st.status === "answered"
    const fname = st.selectedFileName || safeGet("navire_qcm_file_label") || ""
    const diff = safeGet("navire_qcm_difficulty") || "medium"
    const total = st.total || QCM_COUNT
    const index = st.index || 1
    // Avant la 1re réponse → estimé ; ensuite → gain réel cumulé
    const eloShown = st.eloGained > 0 ? st.eloGained : estimatedSessionElo(diff)
    const eloIsEstimate = st.eloGained === 0

    return (
        <div className="qcm-view" style={{ gap: mobile ? 12 : 14 }}>
            {/* En-tête : numéro + fichier, minuteur AFK */}
            <div
                style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    gap: 10,
                    minHeight: 26,
                }}
            >
                <div
                    className="qcm-kicker"
                    style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 8,
                        minWidth: 0,
                        flex: 1,
                    }}
                >
                    <span style={{ color: "var(--text-2)" }}>
                        {String(index).padStart(2, "0")}/
                        {String(total).padStart(2, "0")}
                    </span>
                    {fname && (
                        <>
                            <span>·</span>
                            <span
                                style={{
                                    overflow: "hidden",
                                    textOverflow: "ellipsis",
                                    whiteSpace: "nowrap",
                                }}
                            >
                                {fname}
                            </span>
                        </>
                    )}
                </div>
                {!answered && (
                    <AfkTimer
                        secondsLeft={st.afkSecondsLeft}
                        afkStreak={st.afkStreak}
                    />
                )}
            </div>

            <Segments
                total={total}
                index={index}
                answered={answered}
                results={st.results}
            />

            {/* Question */}
            <div
                key={`q${index}`}
                className="qcm-card"
                style={{
                    padding: mobile ? 14 : 16,
                    display: "flex",
                    flexDirection: "column",
                    gap: 10,
                }}
            >
                <div
                    style={{
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "space-between",
                        gap: 8,
                    }}
                >
                    <span className="qcm-kicker">Question</span>
                    <span className="qcm-elo">
                        <small>{eloIsEstimate ? "Estimé" : "Gain"}</small>
                        {eloIsEstimate ? "~" : "+"}
                        {eloShown} elo
                    </span>
                </div>
                <div
                    style={{
                        fontSize: mobile ? 15 : 16,
                        fontWeight: 600,
                        lineHeight: 1.5,
                        letterSpacing: "-.2px",
                    }}
                >
                    {st.question || "Chargement…"}
                </div>
            </div>

            {/* Choix */}
            <div
                key={`c${index}`}
                style={{ display: "flex", flexDirection: "column", gap: 6 }}
            >
                {(st.choices || []).map((c, i) => (
                    <ChoiceBtn key={i} i={i} c={c} />
                ))}
            </div>

            {/* Explication */}
            {answered && st.explanation && (
                <div
                    className="qcm-card"
                    style={{
                        padding: mobile ? 14 : 16,
                        display: "flex",
                        flexDirection: "column",
                        gap: 8,
                    }}
                >
                    <span
                        className={`qcm-tag ${st.isCorrect ? "ok" : "bad"}`}
                        style={{ width: "fit-content" }}
                    >
                        <i />
                        {st.isCorrect ? "Correct" : "Incorrect"}
                    </span>
                    <div
                        style={{
                            fontSize: 13,
                            color: "var(--text-2)",
                            lineHeight: 1.6,
                        }}
                    >
                        {st.explanation}
                    </div>
                </div>
            )}

            {answered && <NextBtn />}
        </div>
    )
}

function AfkTimer({
    secondsLeft,
    afkStreak,
}: {
    secondsLeft: number
    afkStreak: number
}) {
    const urgent = secondsLeft <= 30
    const mm = String(Math.floor(secondsLeft / 60)).padStart(2, "0")
    const ss_ = String(secondsLeft % 60).padStart(2, "0")
    return (
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            {afkStreak > 0 && (
                <span className="qcm-tag warn" title="Questions sautées">
                    {afkStreak}/{MAX_AFK_STREAK} skip
                </span>
            )}
            <span className={`qcm-timer${urgent ? " is-urgent" : ""}`}>
                <IcoClock size={12} />
                {mm}:{ss_}
            </span>
        </div>
    )
}

function ChoiceBtn({ i, c }: { i: number; c: string }) {
    const st = useS()
    const answered = st.status === "answered"
    const isChosen = st.selectedIndex === i
    const isGood = st.correctIndex === i
    const canClick = !answered && st.status === "ready"

    let cls = "qcm-choice"
    if (answered) {
        if (isGood) cls += " is-good"
        else if (isChosen) cls += " is-bad"
        else cls += " is-muted"
    } else if (isChosen && st.loading) cls += " is-pending"
    else if (canClick) cls += " is-live"

    const go = async () => {
        if (!canClick || ANSWER_LOCK) return
        ANSWER_LOCK = true
        if (AFK_TID) {
            clearInterval(AFK_TID)
            AFK_TID = null
        }
        const cur = gs()
        try {
            patch({ loading: true, selectedIndex: i })
            const ans = await apiAnswer(cur.sessionId, i)
            if (Number(ans.index) !== gs().index) {
                patch({ loading: false })
                return
            }
            const correct = ans.is_correct ?? null
            const newResults = [...gs().results]
            newResults[gs().index - 1] = correct
            patch({
                status: "answered",
                loading: false,
                selectedIndex: i,
                correctIndex: Number(ans.correct_index ?? -1),
                isCorrect: correct,
                explanation: String(ans.explanation || ""),
                nextReady: false,
                nextBarPct: 0,
                afkStreak: 0,
                eloGained: gs().eloGained + Number(ans.elo_delta || 0),
                results: newResults,
            })
            safeSet(AFK_KEY, "0")
            startNextBar()
            pollNextReady(cur.sessionId)
        } catch {
            patch({ loading: false })
        } finally {
            ANSWER_LOCK = false
        }
    }
    return (
        <button
            className={cls}
            onClick={go}
            style={{ animationDelay: `${i * 0.04}s` }}
        >
            <span className="qcm-choice-key">
                {answered && isGood ? (
                    <IcoCheck />
                ) : answered && isChosen ? (
                    <IcoX />
                ) : (
                    ["A", "B", "C", "D"][i]
                )}
            </span>
            <span className="qcm-choice-text">{c}</span>
        </button>
    )
}

function NextBtn() {
    const st = useS()
    const isLast = (st.index || 0) >= (st.total || QCM_COUNT)
    const canGo = st.nextBarPct >= 100 && (st.nextReady || isLast)
    const go = async () => {
        const cur = gs()
        if (!cur.sessionId) return
        if (!canGo) {
            toast("Lis l'explication d'abord")
            return
        }
        if (NEXT_LOCK) return
        NEXT_LOCK = true
        try {
            patch({ loading: true })
            const r = await apiNext(cur.sessionId)
            if (r.status === "done") {
                safeDel("navire_qcm_session_id")
                patch({
                    view: "continue",
                    status: "done",
                    sessionId: cur.sessionId,
                    loading: false,
                })
                fetchPlan().then((plan) => patch({ plan, planLoaded: true }))
                return
            }
            if (r.status === "ready") {
                const nIdx = Number(r.index || 1)
                safeSet("navire_qcm_shown_idx", String(nIdx))
                patch({
                    status: "ready",
                    loading: false,
                    index: nIdx,
                    total: Number(r.total || QCM_COUNT),
                    question: String(r.question || ""),
                    choices: Array.isArray(r.choices)
                        ? r.choices.map(String)
                        : [],
                    explanation: "",
                    isCorrect: null,
                    selectedIndex: -1,
                    correctIndex: -1,
                    nextBarPct: 0,
                    nextReady: false,
                    questionReadyAt: Date.now(),
                    afkStreak: 0,
                    afkSecondsLeft: 120,
                })
                safeSet(AFK_KEY, "0")
                startAfkCountdown()
                return
            }
            patch({ status: "generating", view: "generating", loading: false })
            poll(cur.sessionId)
        } catch (e: any) {
            patch({ loading: false })
            if (!isAbort(e)) toast("Erreur réseau, réessaie")
        } finally {
            NEXT_LOCK = false
        }
    }
    return (
        <div style={{ display: "flex", justifyContent: "flex-end" }}>
            <button
                className={`qcm-next${canGo ? " is-ready" : ""}`}
                onClick={go}
            >
                <span
                    className="qcm-next-fill"
                    style={{ width: `${st.nextBarPct}%` }}
                />
                <span className="qcm-next-lbl">
                    {isLast ? "Résultats" : "Suivant"}
                    <IcoArrow />
                </span>
            </button>
        </div>
    )
}

// ─────────────────────────────────────────────────────────────────
//  VIEW — CONTINUE
// ─────────────────────────────────────────────────────────────────
const PERKS = [
    "Sessions QCM illimitées",
    "Jusqu'à 7 fichiers hébergés",
    "500 flashcards",
    "Classement ELO complet",
]

function ContinueView() {
    const st = useS()
    const isFree = !st.planLoaded || st.plan === "free" || st.plan === ""
    const total = st.total || QCM_COUNT
    // Les résultats ne survivent pas à un refresh : on n'affiche le score que s'il est connu
    const hasResults = st.results.some((r) => r === true || r === false)
    const correct = st.results.filter((r) => r === true).length

    const restart = async () => {
        const sid = safeGet("navire_qcm_session_id")
        if (sid) await apiClose(sid)
        resetRun(true)
        patch({
            view: "generating",
            status: "starting",
            eloGained: 0,
            results: [],
        })
        apiStart()
    }
    const goSetup = async () => {
        const sid = safeGet("navire_qcm_session_id")
        if (sid) await apiClose(sid)
        resetRun(true)
    }
    return (
        <div className="qcm-view" style={{ gap: 16 }}>
            {/* Bilan */}
            <div
                style={{
                    display: "flex",
                    flexDirection: "column",
                    alignItems: "center",
                    textAlign: "center",
                    gap: 6,
                    paddingTop: 6,
                }}
            >
                <div
                    style={{
                        width: 52,
                        height: 52,
                        borderRadius: 99,
                        background: "#fff",
                        color: "var(--ink)",
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                        marginBottom: 8,
                        boxShadow: "0 20px 60px rgba(120,150,255,0.18)",
                        animation: "qcm-rise .4s var(--ease) both",
                    }}
                >
                    <IcoCheck size={22} />
                </div>
                <div
                    style={{
                        fontSize: 20,
                        fontWeight: 700,
                        letterSpacing: "-.4px",
                    }}
                >
                    Session terminée
                </div>
                {hasResults && (
                    <div style={{ fontSize: 13, color: "var(--text-3)" }}>
                        {correct}/{total} bonne{correct > 1 ? "s" : ""}{" "}
                        réponse{correct > 1 ? "s" : ""}
                    </div>
                )}
            </div>

            {hasResults && (
                <div
                    className="qcm-card"
                    style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 14,
                        padding: "14px 16px",
                    }}
                >
                    <Segments
                        total={total}
                        index={total + 1}
                        answered
                        results={st.results}
                    />
                    <span
                        className="qcm-mono"
                        style={{
                            fontSize: 13,
                            fontWeight: 600,
                            color:
                                st.eloGained > 0 ? "var(--ok)" : "var(--text-3)",
                            whiteSpace: "nowrap",
                        }}
                    >
                        +{st.eloGained} elo
                    </span>
                </div>
            )}

            {isFree && (
                <div
                    className="qcm-card is-on"
                    style={{
                        display: "flex",
                        flexDirection: "column",
                        gap: 14,
                        animationDelay: ".06s",
                    }}
                >
                    <div
                        style={{
                            display: "flex",
                            alignItems: "center",
                            gap: 8,
                        }}
                    >
                        <span className="qcm-kicker">Plan gratuit</span>
                        <span className="qcm-tag warn">Offre bêta</span>
                    </div>
                    <div
                        style={{
                            fontSize: 13,
                            color: "var(--text-2)",
                            lineHeight: 1.6,
                        }}
                    >
                        Tu as utilisé ta session du jour. Avec{" "}
                        <b style={{ color: "#fff" }}>Membre</b>, débloque les
                        sessions illimitées, plus de fichiers et l'accès
                        complet au réseau NAVIRE.
                    </div>
                    <ul className="qcm-perks">
                        {PERKS.map((txt) => (
                            <li key={txt}>
                                <IcoCheck />
                                {txt}
                            </li>
                        ))}
                    </ul>
                    {/* Prix bêta : 3€/mois bloqué pendant 1 an */}
                    <a
                        href="/subscribe"
                        className="qcm-btn is-block qcm-btn-sub"
                    >
                        <span>Profiter de l'offre bêta</span>
                        <small>3€/mois pendant 1 an</small>
                    </a>
                </div>
            )}

            <div style={{ display: "flex", gap: 8 }}>
                <button
                    className="qcm-chip"
                    onClick={goSetup}
                    style={{ flex: 1 }}
                >
                    Changer de fichier
                </button>
                {!isFree && (
                    <button
                        className="qcm-btn"
                        onClick={restart}
                        style={{ flex: 1 }}
                    >
                        Rejouer
                    </button>
                )}
            </div>
        </div>
    )
}

// ─────────────────────────────────────────────────────────────────
//  ROOT
// ─────────────────────────────────────────────────────────────────
function QcmApp() {
    const st = useS()
    const mobile = useIsMobile()
    const booted = useRef(false)

    // Dimensions cibles :
    //   Desktop : 590 × 583  → contenu centré, 4/5 de la largeur
    //   Mobile  : 390 × 680  → pleine largeur, gouttière de 16px
    // La Frame Framer gère la taille extérieure ; le div root remplit 100%.
    const px = mobile ? 16 : 18
    const py = mobile ? 16 : 20

    useEffect(() => {
        if (!isBrowser() || booted.current) return
        booted.current = true
        fetchPlan().then((plan) => patch({ plan, planLoaded: true }))
        hydrate()
    }, [])

    // Watchdog generating
    useEffect(() => {
        if (!isBrowser()) return
        const t = setInterval(() => {
            const cur = gs()
            if (cur.view !== "generating") return
            const sid = safeGet("navire_qcm_session_id")
            if (sid && !POLL_LOCK) poll(sid)
            else if (!sid && !POLL_LOCK) apiStart()
        }, 1400)
        return () => clearInterval(t)
    }, [])

    // AFK watchdog
    useEffect(() => {
        if (!isBrowser()) return
        const t = setInterval(() => {
            const cur = gs()
            if (cur.status !== "ready" || !cur.sessionId) return
            const elapsed = Date.now() - (cur.questionReadyAt || 0)
            if (elapsed < AFK_LIMIT_MS) return
            const streak = (cur.afkStreak || 0) + 1
            safeSet(AFK_KEY, String(streak))
            patch({ questionReadyAt: Date.now(), afkStreak: streak })
            if (streak >= MAX_AFK_STREAK) {
                apiClose(cur.sessionId).finally(() => {
                    resetRun(true)
                    toast("Session terminée (inactivité)")
                })
                return
            }
            toast(`Inactivité, question suivante (${streak}/${MAX_AFK_STREAK})`)
            // Marque la question sautée comme ratée pour la progression
            const skipResults = [...gs().results]
            skipResults[cur.index - 1] = false
            patch({ loading: true, results: skipResults })
            apiNext(cur.sessionId)
                .then((r) => {
                    if (r.status === "done") {
                        safeDel("navire_qcm_session_id")
                        safeDel("navire_qcm_shown_idx")
                        patch({
                            view: "continue",
                            status: "done",
                            loading: false,
                        })
                        fetchPlan().then((plan) =>
                            patch({ plan, planLoaded: true })
                        )
                    } else if (r.status === "ready") {
                        const nIdx = Number(r.index || 1)
                        safeSet("navire_qcm_shown_idx", String(nIdx))
                        patch({
                            status: "ready",
                            loading: false,
                            index: nIdx,
                            total: Number(r.total || QCM_COUNT),
                            question: String(r.question || ""),
                            choices: Array.isArray(r.choices)
                                ? r.choices.map(String)
                                : [],
                            explanation: "",
                            isCorrect: null,
                            selectedIndex: -1,
                            correctIndex: -1,
                            nextBarPct: 0,
                            nextReady: false,
                            questionReadyAt: Date.now(),
                            afkSecondsLeft: 120,
                        })
                        safeSet(AFK_KEY, String(streak))
                        startAfkCountdown()
                    } else {
                        patch({
                            status: "generating",
                            view: "generating",
                            loading: false,
                        })
                        poll(cur.sessionId)
                    }
                })
                .catch(() => patch({ loading: false }))
        }, 1000)
        return () => clearInterval(t)
    }, [])

    // Démarre le compte à rebours quand une question devient ready
    useEffect(() => {
        if (st.status === "ready") startAfkCountdown()
    }, [st.questionReadyAt])

    const views: Record<QcmView, React.ReactNode> = {
        setup: <SetupView />,
        generating: <GeneratingView />,
        run: <RunView />,
        continue: <ContinueView />,
    }

    return (
        <div
            className={`qcm-root${mobile ? " is-mobile" : ""}`}
            style={{
                width: "100%",
                height: "auto",
                background: "transparent",
                padding: `${py}px ${px}px`,
                display: "flex",
                justifyContent: "center",
            }}
        >
            <QcmStyles />
            {/* Desktop : contenu contraint à 4/5 de la largeur, centré */}
            <div
                key={st.view}
                style={{
                    width: mobile ? "100%" : "80%",
                    maxWidth: 480,
                }}
            >
                {views[st.view] || <SetupView />}
            </div>
        </div>
    )
}

// ─────────────────────────────────────────────────────────────────
//  EXPORT UNIQUE
// ─────────────────────────────────────────────────────────────────
export function QcmAppRoot(): Override {
    return { children: React.createElement(QcmApp) } as any
}
