import os
import logging
from typing import List, Optional

from app.models.chat import ChatMessage, ChatResponse

logger = logging.getLogger(__name__)

# OpenAI client optionnel (si OPENAI_API_KEY est défini)
_USE_OPENAI = bool(os.getenv("OPENAI_API_KEY"))
_CLIENT = None
if _USE_OPENAI:
    try:
        from openai import OpenAI  # openai>=1.0
        _CLIENT = OpenAI()
    except Exception as e:
        logger.warning("OpenAI client indisponible (%s). Fallback local.", e)
        _USE_OPENAI = False


class ChatService:
    """
    Service de chat minimal.
    - Si OPENAI_API_KEY est présent et client OK : appelle OpenAI.
    - Sinon : fallback local (echo amélioré).
    """

    def __init__(self, model: str = "gpt-4o-mini"):
        self.model = model

    def reply(
        self,
        messages: List[ChatMessage],
        file_context: Optional[List[str]] = None,
        max_tokens: Optional[int] = None,
    ) -> ChatResponse:
        # 1) Tentative OpenAI
        if _USE_OPENAI and _CLIENT is not None:
            try:
                # Convertir nos messages en format OpenAI
                conv = [{"role": m.role.value, "content": m.text} for m in messages]
                # Contexte fichiers basique (en système)
                if file_context:
                    ctx = (
                        "Contextualise ta réponse aux fichiers: "
                        + ", ".join(file_context)
                        + ". Réponds de façon concise et pédagogique."
                    )
                    conv.insert(0, {"role": "system", "content": ctx})

                comp = _CLIENT.chat.completions.create(
                    model=self.model,
                    messages=conv,
                    max_tokens=max_tokens or 400,
                    temperature=0.2,
                )
                text = comp.choices[0].message.content.strip()
                return ChatResponse(reply=text)
            except Exception as e:
                logger.warning("OpenAI error: %s. Fallback local.", e)

        # 2) Fallback local (simple, mais utile en dev)
        last_user = next((m for m in reversed(messages) if m.role.value == "user"), None)
        if not last_user:
            return ChatResponse(reply="Bonjour. Pose-moi une question, et je te répondrai.")

        hint = ""
        if file_context:
            hint = f"\n(Note: contexte fichiers: {', '.join(file_context)})"

        # Réponse “rule-based” ultra simple
        user_text = last_user.text.strip()
        if len(user_text) < 8:
            baseline = "Peux-tu préciser ta question ?"
        else:
            baseline = (
                "Voici une réponse rapide basée sur mes règles locales (mode développement). "
                "Quand l'API OpenAI est activée, je fournirai une réponse plus détaillée."
            )

        return ChatResponse(
            reply=f"{baseline}\n\nTa question: « {user_text} »{hint}"
        )
