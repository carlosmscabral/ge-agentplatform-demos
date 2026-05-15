import os

from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.apps import App
from google.adk.tools.preload_memory_tool import PreloadMemoryTool

from app.tools import (
    check_ticket_status,
    create_ticket,
    get_preferences,
    lookup_account,
    update_preference,
)


async def generate_memories_callback(callback_context: CallbackContext):
    """Sends the session's events to Memory Bank for memory generation."""
    await callback_context.add_session_to_memory()
    return None


root_agent = Agent(
    name="customer_support_agent",
    model=os.environ.get("GEMINI_MODEL", "gemini-3-flash-preview"),
    instruction=(
        "Você é um agente de suporte ao cliente da Acme Cloud Services. "
        "Responda sempre em português brasileiro.\n\n"
        "No INÍCIO de cada conversa, chame get_preferences para verificar se o "
        "cliente é recorrente. Se encontrar preferências, cumprimente-o pelo nome e "
        "mencione as configurações conhecidas.\n\n"
        "Você ajuda clientes com:\n"
        "- Consultas de conta e cobrança (use lookup_account)\n"
        "- Criação e consulta de tickets de suporte (use create_ticket, check_ticket_status)\n"
        "- Gerenciamento de preferências:\n"
        "  - Use update_preference para SALVAR configurações estáticas (preferred_name, "
        "customer_id, notification_channel, timezone) — persistem entre sessões\n"
        "  - Use get_preferences para LER preferências salvas anteriormente\n\n"
        "IMPORTANTE — propriedade dos dados:\n"
        "- Session state (get_preferences/update_preference): é dono da identidade e "
        "configurações do usuário (nome, customer ID, canal de notificação, timezone). "
        "Sempre use essas tools para preferências estruturadas.\n"
        "- Memory Bank (automático): é dono dos insights do histórico de conversas — "
        "problemas anteriores, resultados de tickets, tópicos discutidos e instruções "
        "explícitas do usuário. NÃO dependa de memórias para preferências que já "
        "estão no session state.\n\n"
        "Quando um cliente mencionar uma preferência, nome ou customer ID, "
        "salve proativamente usando update_preference.\n"
        "Se houver memórias de conversas anteriores (injetadas automaticamente), "
        "use-as para referenciar problemas, tickets e contexto passados.\n"
        "Seja sempre profissional, empático e focado em soluções.\n"
        "Ao criar tickets, confirme os detalhes com o cliente antes de submeter."
    ),
    tools=[
        lookup_account,
        check_ticket_status,
        create_ticket,
        get_preferences,
        update_preference,
        PreloadMemoryTool(),
    ],
    after_agent_callback=generate_memories_callback,
)

app = App(
    root_agent=root_agent,
    name="app",
)
