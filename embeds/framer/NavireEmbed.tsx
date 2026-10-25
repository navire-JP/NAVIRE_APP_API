// NAVIRE — composant Framer pour les pages /rejoindre, /ambassadeur et /lib
// ====================================================================
// À coller dans Framer : Assets → Code → New Code File → « NavireEmbed ».
// Puis glisser le composant sur la page et choisir « Page » dans le panneau.
//
// Pourquoi un composant plutôt qu'un Embed HTML : l'Embed est une iframe
// cloisonnée qui ne voit pas l'adresse de la page, donc pas le ?ref=CODE du
// lien de l'ambassadeur. Ce composant, lui, tourne dans la page : il lit le
// code et le transmet à l'embed, servi par l'API. La hauteur suit le contenu.

import { useEffect, useState } from "react"
import { addPropertyControls, ControlType } from "framer"

const API = "https://navire-app-api.onrender.com"

/**
 * @framerSupportedLayoutWidth any
 * @framerSupportedLayoutHeight auto
 */
export default function NavireEmbed(props: { page: "rejoindre" | "ambassadeur" | "lib" }) {
    const [src, setSrc] = useState("")
    const [height, setHeight] = useState(900)

    useEffect(() => {
        const params = new URLSearchParams(window.location.search)
        if (props.page === "lib") {
            // /lib?doc=12 ouvre directement un cours
            const doc = params.get("doc")
            setSrc(`${API}/lib/embed${doc ? `?doc=${encodeURIComponent(doc)}` : ""}`)
        } else {
            const ref = params.get("ref")
            const query = ref ? `?ref=${encodeURIComponent(ref)}` : ""
            setSrc(`${API}/ambassadors/embed/${props.page}${query}`)
        }

        const onMessage = (event: MessageEvent) => {
            if (event.origin !== API) return
            const data = event.data || {}
            if (data.type === "NAVIRE_EMBED_HEIGHT" && data.height > 0) setHeight(data.height)
        }
        window.addEventListener("message", onMessage)
        return () => window.removeEventListener("message", onMessage)
    }, [props.page])

    return (
        <iframe
            src={src || undefined}
            title="NAVIRE"
            allow="clipboard-write"
            style={{ width: "100%", height, border: 0, display: "block", background: "transparent", colorScheme: "normal" }}
        />
    )
}

NavireEmbed.defaultProps = { page: "rejoindre" }

addPropertyControls(NavireEmbed, {
    page: {
        type: ControlType.Enum,
        title: "Page",
        options: ["rejoindre", "ambassadeur", "lib"],
        optionTitles: ["Rejoindre (lien ambassadeur)", "Espace ambassadeur", "NAVIRE Lib"],
    },
})
