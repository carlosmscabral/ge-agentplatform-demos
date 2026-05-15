import os

import google.auth
from google.adk.agents import Agent
from google.adk.apps import App

_, project_id = google.auth.default()
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", project_id or "")
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", "global")
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "True")

EXCHANGE_RATES = {
    ("USD", "BRL"): 5.25,
    ("BRL", "USD"): 0.19,
    ("USD", "EUR"): 0.92,
    ("EUR", "USD"): 1.09,
    ("BRL", "EUR"): 0.175,
    ("EUR", "BRL"): 5.71,
    ("USD", "GBP"): 0.79,
    ("GBP", "USD"): 1.27,
    ("BRL", "GBP"): 0.15,
    ("GBP", "BRL"): 6.65,
}


def get_exchange_rate(from_currency: str, to_currency: str) -> dict:
    """Consulta a taxa de câmbio entre duas moedas.

    Args:
        from_currency: Código da moeda de origem (ex: USD, BRL, EUR).
        to_currency: Código da moeda de destino (ex: USD, BRL, EUR).
    """
    key = (from_currency.upper(), to_currency.upper())
    if key in EXCHANGE_RATES:
        return {
            "from": key[0],
            "to": key[1],
            "rate": EXCHANGE_RATES[key],
            "source": "mock_data",
        }
    return {"error": f"Taxa não encontrada para {key[0]} → {key[1]}"}


def convert_currency(amount: float, from_currency: str, to_currency: str) -> dict:
    """Converte um valor de uma moeda para outra.

    Args:
        amount: Valor a ser convertido.
        from_currency: Código da moeda de origem (ex: USD, BRL, EUR).
        to_currency: Código da moeda de destino (ex: USD, BRL, EUR).
    """
    key = (from_currency.upper(), to_currency.upper())
    if key not in EXCHANGE_RATES:
        return {"error": f"Conversão não suportada: {key[0]} → {key[1]}"}
    rate = EXCHANGE_RATES[key]
    converted = round(amount * rate, 2)
    return {
        "original_amount": amount,
        "from": key[0],
        "to": key[1],
        "rate": rate,
        "converted_amount": converted,
    }


root_agent = Agent(
    name="currency_specialist",
    model=os.environ.get("GEMINI_MODEL", "gemini-3-flash-preview"),
    description="Agente especialista em câmbio: consulta taxas e converte valores entre moedas.",
    instruction=(
        "Você é um especialista em câmbio de moedas. Responda sempre em português brasileiro.\n\n"
        "Você pode:\n"
        "- Consultar taxas de câmbio entre moedas (use get_exchange_rate)\n"
        "- Converter valores de uma moeda para outra (use convert_currency)\n\n"
        "Moedas suportadas: USD, BRL, EUR, GBP.\n"
        "Sempre informe a taxa utilizada na conversão.\n"
        "Seja preciso e conciso nas respostas."
    ),
    tools=[get_exchange_rate, convert_currency],
)

app = App(root_agent=root_agent, name="app")
