"""Mocked financial news + sentiment scores."""

NEWS: dict[str, list[dict]] = {
    "PETR4": [
        {"date": "2026-05-12", "headline": "Petrobras anuncia novo plano de investimentos em águas profundas.", "source": "Valor"},
        {"date": "2026-05-09", "headline": "Lucro da Petrobras supera expectativas no 1T26.", "source": "InfoMoney"},
        {"date": "2026-05-05", "headline": "Petrobras eleva preço da gasolina nas refinarias.", "source": "Reuters"},
    ],
    "VALE3": [
        {"date": "2026-05-14", "headline": "Vale fecha contrato bilionário de minério com siderúrgica chinesa.", "source": "Bloomberg"},
        {"date": "2026-05-10", "headline": "Produção de minério da Vale cresce 8% no trimestre.", "source": "Valor"},
    ],
    "AAPL": [
        {"date": "2026-05-15", "headline": "Apple unveils new Vision Pro 2 with breakthrough display tech.", "source": "TechCrunch"},
        {"date": "2026-05-13", "headline": "Apple beats Q2 earnings, services revenue at all-time high.", "source": "Bloomberg"},
        {"date": "2026-05-10", "headline": "Apple expands AI features across iPhone, iPad, and Mac.", "source": "The Verge"},
    ],
    "GOOGL": [
        {"date": "2026-05-14", "headline": "Google Cloud crosses 20% YoY growth, beats AWS in select regions.", "source": "Reuters"},
        {"date": "2026-05-08", "headline": "Alphabet announces buyback program of $80B.", "source": "Bloomberg"},
    ],
    "MSFT": [
        {"date": "2026-05-15", "headline": "Microsoft Copilot adoption hits 200M monthly users.", "source": "WSJ"},
    ],
    "ITUB4": [
        {"date": "2026-05-12", "headline": "Itaú reporta ROE de 22% e supera consenso.", "source": "Valor"},
    ],
}

# Sentiment scores in [-1.0, 1.0]: negative=bearish, positive=bullish.
SENTIMENT: dict[str, dict] = {
    "PETR4": {"score": 0.42, "label": "positive", "n_articles": 3},
    "VALE3": {"score": 0.65, "label": "positive", "n_articles": 2},
    "AAPL": {"score": 0.78, "label": "very_positive", "n_articles": 3},
    "GOOGL": {"score": 0.55, "label": "positive", "n_articles": 2},
    "MSFT": {"score": 0.72, "label": "very_positive", "n_articles": 1},
    "ITUB4": {"score": 0.38, "label": "positive", "n_articles": 1},
}
