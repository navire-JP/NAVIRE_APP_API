from fastapi import APIRouter
from app.models.chat import ChatRequest, ChatResponse
from app.services.chat_service import ChatService

router = APIRouter(prefix="/v1/chat", tags=["chat"])

_service = ChatService()  # modèle par défaut défini dans le service

@router.post("", response_model=ChatResponse)
def chat(body: ChatRequest):
    return _service.reply(
        messages=body.messages,
        file_context=body.fileContext,
        max_tokens=body.maxTokens,
    )
