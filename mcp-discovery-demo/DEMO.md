# DEMO.md — mcp-discovery-demo walkthrough

Step-by-step demo script for showing **dynamic MCP discovery via Agent Registry**
to an audience. Total runtime ~10 minutes after the agents are deployed.

## Prerequisites

```bash
./deploy.sh    # ~10-15 min — produces the orchestrator URL printed at the end
export ORCH_URL="https://us-central1-aiplatform.googleapis.com/v1beta1/projects/.../reasoningEngines/..."
cd orchestrator-agent
```

## Access methods

| Method | Command / link |
|---|---|
| CLI | `agents-cli run --url "${ORCH_URL}" --mode adk "<prompt>"` |
| Console Playground | `https://console.cloud.google.com/vertex-ai/agents/locations/us-central1/agent-engines/<RE_ID>/playground?project=<PROJECT_ID>` |
| Cloud Trace | `https://console.cloud.google.com/traces/list?project=<PROJECT_ID>` |

Replace `<RE_ID>` and `<PROJECT_ID>` with the values from `deploy.sh` output.

---

## Act 1 — Discovery by keyword/intent

**What this shows**: the agent uses `discover_tools_by_intent` to query the
Agent Registry with a free-text keyword extracted from the user's question,
then picks the right MCP based on the substring match.

**Prompt**:
```
Qual o sentimento atual do mercado sobre a Apple? Use suas ferramentas de descoberta.
```

**What to observe** (Cloud Trace, expand spans):
1. **Turn 1** — `discover_tools_by_intent` com `{intent: "sentiment"}` ou similar.
   A resposta inclui o entry `news-sentiment` com `matched_in` apontando para
   o tool ou displayName que casou.
2. **Turn 2** — `invoke_mcp_tool` com:
   - `mcp_server_name`: o resource path completo retornado pela discovery
   - `tool_name`: `get_sentiment_score`
   - `arguments`: `{"ticker": "AAPL"}`
3. Dentro do `invoke_mcp_tool` span, GET ao Registry (cache miss) + chamada MCP
4. Resposta final cita o resource path do `news-sentiment` MCP

Expected answer (paraphrased):
> O sentimento agregado sobre a Apple (AAPL) está **muito positivo** (score 0.78),
> com base em 3 artigos recentes. Fonte: news-sentiment MCP.

---

## Act 2 — Discovery by tag/category

**What this shows**: the agent uses `discover_tools_by_category` to filter
servers by an `attributes.tag=...` value set at registration time.

**Prompt**:
```
Liste todas as ferramentas disponíveis na categoria "portfolio". Não invente — descubra.
```

**What to observe**:
1. Trace shows `discover_tools_by_category` with `{tag: "portfolio"}`
2. Response contains exactly 1 server (`portfolio`) with 3 tools
3. The agent summarizes the 3 tools (`get_portfolio_holdings`, `get_position_pnl`,
   `get_portfolio_allocation`) without calling them

Expected answer (paraphrased):
> Na categoria portfolio encontrei o servidor MCP **portfolio** (via Agent Registry),
> com 3 ferramentas: ...

---

## Act 3 — Multi-server orchestration

**What this shows**: a single prompt fans out to **all 3 MCP servers**. The
agent composes a report.

**Prompt**:
```
Faça uma análise completa da minha posição em PETR4 na conta account-001:
PnL atual, cotação de mercado e manchete recente mais relevante.
```

**What to observe**:
1. **Turn 1** — `discover_tools_by_category(tag="...")` ou múltiplos
   `discover_tools_by_intent` para localizar os 3 MCPs
2. **Turns subsequentes** — múltiplos `invoke_mcp_tool`, cada um com o
   `mcp_server_name` do MCP apropriado:
   * `invoke_mcp_tool(name=<portfolio>, tool="get_position_pnl", args={"account_id": "account-001", "ticker": "PETR4"})`
   * `invoke_mcp_tool(name=<market>, tool="get_stock_quote", args={"ticker": "PETR4"})`
   * `invoke_mcp_tool(name=<news>, tool="get_company_news", args={"ticker": "PETR4", "limit": 1})`
3. Resposta final cita os 3 MCPs pelo resource path completo

Expected shape:
> **Posição PETR4 (account-001)** — 500 ações @ avg cost R$ 32.10, mark R$ 38.42 →
> PnL +R$ 3.160 (+19.7%) *via portfolio MCP*.
> Cotação atual: R$ 38.42 (+1.23% hoje) *via market-data MCP*.
> Manchete: "Petrobras anuncia novo plano de investimentos..." *via news-sentiment MCP*.

---

## Act 4 — Resilience under MCP outage

**What this shows**: the `_LazyToolset` defers MCP toolset materialization to
first use, so the agent stays healthy even if a downstream MCP is unreachable.
Discovery still works (Agent Registry is the source of truth), and the agent
reports the failure gracefully.

**Setup** (temporarily break one MCP — example: `portfolio`):
```bash
# Scale portfolio MCP to 0 instances (no longer serves requests, registry entry intact)
gcloud run services update fintoolkit-portfolio-mcp \
    --max-instances=0 --min-instances=0 --region=us-central1 --quiet
```

**Prompt**:
```
Liste tools por categoria portfolio, depois tente buscar holdings da conta account-001. Se falhar, explique o que aconteceu.
```

**What to observe**:
1. `discover_tools_by_category(tag="portfolio")` returns the server (Registry is the catalog — works even if MCP is down)
2. Tool invocation fails (Cloud Run returns 503 — no instances to serve)
3. Agent reports the outage gracefully and may suggest alternatives

**Restore**:
```bash
gcloud run services update fintoolkit-portfolio-mcp \
    --max-instances=3 --min-instances=0 --region=us-central1 --quiet
```

> **Note on SPIFFE + Cloud Run IAM**: an earlier version of this act tested
> revoking `roles/run.invoker` from the SPIFFE principal. That doesn't apply
> here because Cloud Run is deployed with `--allow-unauthenticated`. See
> [`ARCHITECTURE.md` §2c](./ARCHITECTURE.md) for the rationale — Cloud Run
> doesn't support SPIFFE today, and Agent Runtime can't mint OIDC ID tokens
> without a metadata server. The SPIFFE identity story remains valid on the
> Agent Registry side (the agent uses SPIFFE to query Registry).

---

## Verification checklist

After running the demo, you should be able to confirm:

- [ ] All 3 MCP servers are visible in `gcloud alpha agent-registry mcp-servers list --location=us-central1`
- [ ] Each entry has `attributes.tag` and `attributes.domain`
- [ ] Cloud Run logs for each MCP service show the incoming requests with valid auth
- [ ] Cloud Trace shows the full discovery → tool-call chain for each act
- [ ] The orchestrator's effectiveIdentity matches `principal://agents.global.…/reasoningEngines/<id>`

## Cleanup (without full undeploy)

The demo has no sessions or persistent state — each `agents-cli run` is
stateless. To reset for a fresh demo, simply re-run prompts.

To fully tear down:
```bash
./undeploy.sh
```
