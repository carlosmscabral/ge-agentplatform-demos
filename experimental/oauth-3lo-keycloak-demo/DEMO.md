# Roteiro de demonstração — OAuth 3LO + Keycloak

Este guia te leva pelo fluxo OAuth 3-Legged end-to-end. Dois caminhos intercambiáveis:

- **Caminho A — Console Playground** — zero UI extra; a Console implementa o handshake `adk_request_credential` nativamente
- **Caminho B — Frontend Cloud Run** — mostra o handshake explícito (popup, /validateUserId, /resume), útil pra explicar o que acontece por baixo

## Pré-requisitos

- `./deploy.sh` rodou com sucesso — o banner final imprimiu o recurso do agente, a URL do frontend e o link do Console Playground
- Existe um usuário de teste no realm Keycloak autorizado a autenticar com o client configurado
- Browser moderno (popup permitido, cookies habilitados)

## Cheat sheet

| Coisa | Onde encontrar |
|-------|----------------|
| Agent resource | `agent/deployment_metadata.json` → `remote_agent_runtime_id` |
| Frontend URL (canônico) | banner final do `deploy.sh`, ou: `gcloud run services describe oauth-3lo-frontend --region=$REGION --format='value(status.url)'` |
| MCP URL | banner final do `deploy.sh` |
| MCP registry name | `gcloud alpha agent-registry mcp-servers list --location=$REGION --filter="displayName='oauth-3lo-mcp'"` |
| Binding | `gcloud alpha agent-registry bindings describe oauth-3lo-agent-binding --location=$REGION` |
| Console Playground link | banner final do `deploy.sh` |
| MCP logs | `gcloud run services logs read oauth-3lo-mcp --region=$REGION` |
| Frontend logs | `gcloud run services logs read oauth-3lo-frontend --region=$REGION` |
| Agent logs | `gcloud logging read 'resource.type="aiplatform.googleapis.com/ReasoningEngine"' --limit=20 --freshness=10m` |

---

## Caminho A — Console Playground

1. Abra o link do Console impresso pelo `deploy.sh`. Aponta pra página do Reasoning Engine em `console.cloud.google.com/vertex-ai/agents/agent-engines/...`
2. Vá na aba **Playground**
3. Envie o prompt: **"Qual é o meu perfil no sistema?"**
4. **Observe**: aparece o prompt de consent diretamente no Playground (Console implementa o handshake `adk_request_credential` nativamente). Clica para logar no Keycloak
5. **Observe** a resposta do agente: lista `sub`, `username`, `email`, e os roles do realm Keycloak — prova de que o seu token Keycloak chegou ao MCP server e foi validado
6. Mande o follow-up: **"echo 'olá com minha identidade'"**. Agente chama a tool `echo` — sem segundo consent (o vault ainda tem seu token)

### Variação

- Abra uma janela anônima/incognito, abra o mesmo Playground, consente como outro usuário Keycloak. Resposta reflete os claims **daquele** usuário — código do agente não muda

---

## Caminho B — Frontend Cloud Run

1. Abra o **URL canônico** do frontend (ex.: `https://oauth-3lo-frontend-yozowz6hla-uc.a.run.app`). Se você abrir o outro hostname (com project number), middleware redireciona automaticamente
2. **Observe** o cabeçalho: project, region, auth provider name. Se aparecer "AGENT_ENGINE_ID is empty", o frontend ainda não tem o agent — espere o `deploy.sh` terminar o segundo redeploy e recarregue
3. Digite: **"Qual é o meu perfil no sistema?"** e clique *Enviar*
4. **Observe** o log de chat:
   - `user:` — seu prompt
   - `system: Consent required — opening Keycloak in a popup…`
   - Popup abre no `auth_uri` do Keycloak
5. Faça login no Keycloak (e dê consent se for sua primeira vez)
6. **Observe** o popup mostrando brevemente "Consent recorded ✔" e fechando automaticamente
7. **Observe** o log de chat continuando:
   - `system: Consent recorded, resuming conversation…`
   - `agent:` — perfil em prosa PT-BR com os claims do MCP
8. Abra DevTools (Network) e reenvie o prompt — **não** abre popup; connector serve o token do vault

### Inspecionar o que rolou

```bash
# Frontend: cookie setado, finalize feito
gcloud run services logs read oauth-3lo-frontend --region=$REGION --limit=30 \
  | grep -E "requested user credential|Finalized credential"

# MCP: JWT validado, claims extraídos
gcloud run services logs read oauth-3lo-mcp --region=$REGION --limit=30 \
  | grep "Authenticated"

# Agent: tool resolvida via binding
gcloud logging read 'resource.type="aiplatform.googleapis.com/ReasoningEngine"' \
  --limit=20 --freshness=10m \
  | grep "Loaded MCP toolset"
```

### Inspecionar via Console

- **Agent**: `console.cloud.google.com/gemini-enterprise-agent-platform/scale/agentRegistry/locations/$REGION/agents/<agent_uuid>?project=$PROJECT_ID` — aba **Identity** mostra Auth Providers (connector `oauth-3lo-keycloak`) E Bindings (binding `oauth-3lo-agent-binding`)
- **Connector standalone**: não há página standalone; sempre via tab Identity do agente

---

## Edge cases que valem demonstrar

| Cenário | Comportamento esperado |
|---------|-----------------------|
| Prompt que não precisa MCP (ex.: "qual a capital do Brasil?") | Agente responde direto, sem consent, sem chamada MCP |
| Revogar a sessão no Keycloak Admin (Sessions → revoke) e re-prompt | Agente re-emite `adk_request_credential`; popup reabre |
| Parar o servidor Keycloak e prompt | MCP request falha na validação (JWKS inalcançável); 403 visível em logs do MCP |
| Setar `KEYCLOAK_VERIFY_AUDIENCE=false`, redeploy MCP, prompt | Validação passa mesmo se o `aud` não bater (usar só pra diagnosticar audience mapper) |

## Cleanup entre runs

- **Esquecer o consent do usuário sem redeploy** — revogue a sessão offline do usuário no Keycloak (Admin → Sessions → User), e adicionalmente limpe a credencial do vault deletando e recriando o connector:
  ```bash
  gcloud alpha agent-identity connectors delete oauth-3lo-keycloak \
      --location=us-central1 --project=$PROJECT_ID --quiet
  ./deploy.sh --no-confirm
  ```
- **Teardown completo**: `./undeploy.sh`
