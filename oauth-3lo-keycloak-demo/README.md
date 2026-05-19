# OAuth 3LO + Keycloak Demo

Demonstra **Agent Identity 3-Legged OAuth** ([docs](https://docs.cloud.google.com/iam/docs/auth-with-3lo), Preview) end-to-end: um agente ADK em Agent Runtime autentica num servidor MCP protegido por **Keycloak** *em nome do usuário final*. O token do usuário fica num cofre gerenciado pelo Google — o código do agente nunca toca o segredo. Auth provider e MCP são amarrados via **Agent Registry Binding**, então não há nada de OAuth no código do agente.

📐 **Arquitetura detalhada (PT-BR)**: [ARCHITECTURE.md](ARCHITECTURE.md)
🎬 **Roteiro de demonstração**: [DEMO.md](DEMO.md)
📚 **Jornada e bugs vencidos**: [LESSONS.md](LESSONS.md)

## O que esta demo demonstra

1. **Agent Identity Connector** — connector 3LO gerenciado pelo Google (`gcloud alpha agent-identity connectors`) media o OAuth contra qualquer IdP OIDC (Keycloak aqui, vale Auth0/Okta/Cognito/etc.)
2. **Agent Registry Binding** — `(agente → MCP → auth_provider)` armazenado no Registry. Agente chama `get_mcp_toolset(mcp_server_name)` **sem** `auth_scheme` inline; Registry resolve em runtime. Toolset envolvido em **`_LazyMcpToolset`** (padrão `mcp-discovery-demo`) para deferir resolução até a primeira chamada — evita race condition entre criação do agente e criação do binding
3. **Handshake `adk_request_credential`** — agente emite pedido de credencial, frontend abre popup Keycloak, finaliza via `iamconnectorcredentials.credentials:finalize`, retoma a conversa
4. **SPIFFE identity** — agente roda em Agent Runtime com `--agent-identity`; IAM via principal set + per-connector
5. **FastMCP + JWT middleware** — servidor MCP em Cloud Run valida JWTs Keycloak via JWKS

## Arquitetura 1-pager

```
USUÁRIO ──▶ Frontend (Cloud Run) ──▶ Agente (Agent Runtime, SPIFFE)
              │ /chat                   │
              │ /validateUserId         │ get_mcp_toolset(mcp_server_name, continue_uri)
              │ /resume                 │
              │                         ▼
              │            Agent Registry (resolve Binding)
              │                         │
              │                         ▼
              │              Connector (vault de tokens)
              │                         │
              │                         │ Bearer <user_keycloak_token>
              │                         ▼
              │              MCP server (Cloud Run, FastMCP + JWT middleware)
              │                         │ JWKS fetch
              │                         ▼
              │                    Keycloak (IdP externo)
```

Detalhes completos (sequência, IAM, endpoints, vantagens managed vs DIY): [ARCHITECTURE.md](ARCHITECTURE.md)

## Pré-requisitos

- Keycloak rodando com um client confidencial (Standard Flow ON). O redirect URL é fornecido pelo `deploy.sh` para você registrar no client
- `gcloud` autenticado, com `Owner` ou equivalente no projeto GCP (preciso pra IAM em principal set + APIs `gcloud alpha agent-identity` / `gcloud alpha agent-registry`)
- `uv`, `agents-cli`, Python 3.11+

## Quick start

```bash
cp .env.template .env
# preenche PROJECT_ID, KEYCLOAK_URL, KEYCLOAK_REALM, KEYCLOAK_CLIENT_ID, KEYCLOAK_CLIENT_SECRET
./deploy.sh
```

`deploy.sh` pausa uma única vez para mostrar o redirect URL — registre como *Valid Redirect URI* no client Keycloak, dê Enter. Use `./deploy.sh --no-confirm` se já tiver registrado.

Depois do deploy, dois caminhos de demonstração — ver [DEMO.md](DEMO.md):

- **Console Playground** — implementa o handshake nativamente, zero código extra
- **Frontend Cloud Run** (URL impressa no final do deploy) — mostra o handshake explícito (popup, /validateUserId, /resume)

## Configuração (`.env.template`)

| Variável | Default | Descrição |
|----------|---------|-----------|
| `PROJECT_ID` | auto-detectado | Projeto GCP |
| `REGION` | `us-central1` | Região para Cloud Run + Agent Runtime |
| `ORG_ID` | auto-detectado | Org ID (usado pra montar SPIFFE principal set) |
| `GEMINI_MODEL` | `gemini-3-flash-preview` | Modelo Gemini do agente |
| `AGENT_DISPLAY_NAME` | `oauth-3lo-agent` | Nome do agente (Regra #11 — único entre demos) |
| `AUTH_PROVIDER_NAME` | `oauth-3lo-keycloak` | Nome do connector (lowercase, dígitos, hifens) |
| `AUTH_PROVIDER_LOCATION` | `us-central1` | Localização do connector |
| `ALLOWED_SCOPES` | `openid,profile,email` | Scopes OAuth. **Obrigatório** — sem isso Keycloak rejeita com `invalid_scope` |
| `KEYCLOAK_URL` | obrigatório | URL base do Keycloak (sem trailing slash) |
| `KEYCLOAK_REALM` | obrigatório | Nome do realm |
| `KEYCLOAK_CLIENT_ID` | obrigatório | Client ID confidencial |
| `KEYCLOAK_CLIENT_SECRET` | obrigatório | Client secret |
| `KEYCLOAK_AUDIENCE` | `account` | Claim `aud` esperado nos JWTs |
| `KEYCLOAK_VERIFY_AUDIENCE` | `true` (`false` só para debug) | Se `true`, MCP valida `aud` estritamente |
| `MCP_SERVICE_NAME` | `oauth-3lo-mcp` | Nome do serviço Cloud Run do MCP |
| `MCP_REGISTRY_DISPLAY_NAME` | `oauth-3lo-mcp` | Display name no Agent Registry |
| `FRONTEND_SERVICE_NAME` | `oauth-3lo-frontend` | Nome do serviço Cloud Run do frontend |
| `STAGING_BUCKET` | `${PROJECT_ID}-oauth-3lo-staging` | Bucket GCS de staging |
| `GEMINI_ENTERPRISE_APP_ID` | unset | Opcional — registra agente num GE App (Regra #10) |

## Setup do client Keycloak (checklist)

No Keycloak Admin Console do seu realm:

1. **Clients → Create client** — Client type `OpenID Connect`, Client ID = `KEYCLOAK_CLIENT_ID`
2. **Capability config** → Client authentication: **ON** (confidencial), Standard Flow: **ON**, Direct access grants: OFF
3. **Login settings → Valid Redirect URIs** — *deixe vazio agora*; `deploy.sh` vai imprimir o URL exato para colar
4. **Credentials → Client Secret** — copia para `KEYCLOAK_CLIENT_SECRET` no `.env`
5. **Client scopes → `<client>-dedicated` → Add mapper → By configuration → Audience** — *Included Client Audience* = `KEYCLOAK_CLIENT_ID`, marca *Add to access token*. Sem isso, tokens carregam `aud=["account"]` e o MCP server falha com `audience_mismatch`. Alternativa: deixa `KEYCLOAK_AUDIENCE=account` no `.env`

## De onde vem o redirect URL?

O `deploy.sh` gera o URL automaticamente quando cria o connector (passo 6). Formato:

```
https://iamconnectorcredentials.googleapis.com/v1/projects/<PROJECT_ID>/locations/<LOCATION>/connectors/<AUTH_PROVIDER_NAME>/oauthcallback
```

Para o `.env.template` default, fica:

```
https://iamconnectorcredentials.googleapis.com/v1/projects/<PROJECT_ID>/locations/us-central1/connectors/oauth-3lo-keycloak/oauthcallback
```

Para buscar o URL depois (se perdeu o prompt do deploy):

```bash
gcloud alpha agent-identity connectors describe oauth-3lo-keycloak \
    --location=us-central1 --project=$PROJECT_ID \
    --format='value(connectorTypeParams.threeLeggedOauth.redirectUrl)'
```

Cole **exatamente** (sem trailing slash) em **Keycloak → Clients → seu client → Settings → Valid Redirect URIs → Save**.

## Validação rápida pós-deploy

```bash
# 1. MCP responde e valida JWT
TOKEN=$(curl -sk -X POST "$KEYCLOAK_URL/realms/$KEYCLOAK_REALM/protocol/openid-connect/token" \
  -d "grant_type=client_credentials&client_id=$KEYCLOAK_CLIENT_ID&client_secret=$KEYCLOAK_CLIENT_SECRET" \
  | jq -r .access_token)
curl -X POST "$MCP_URL/mcp" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
# Esperado: HTTP 200 com lista de tools

# 2. Binding existe e aponta para o connector
gcloud alpha agent-registry bindings list --location=us-central1 --project=$PROJECT_ID

# 3. Frontend dispara consent corretamente
curl -X POST "$FRONTEND_URL/chat" -H "Content-Type: application/json" \
  -d '{"message":"olá","session_id":"v1","user_id":"v1"}' | jq
# Esperado: {"needs_auth": true, "auth_uri": "https://<keycloak>/...?scope=openid+profile+email&..."}
```

Para checar via Console:
```
https://console.cloud.google.com/gemini-enterprise-agent-platform/scale/agentRegistry/locations/us-central1/agents/<agent_uuid>?project=$PROJECT_ID
```
Aba **Identity** → tanto **Auth Providers** quanto **Bindings** devem aparecer.

## Cleanup

```bash
./undeploy.sh
```

Remove frontend, agente, MCP (Cloud Run + Agent Registry), Binding, connector e bucket de staging.

## Solução de problemas

Veja a tabela completa de erros conhecidos em [LESSONS.md](LESSONS.md). Os mais comuns:

| Sintoma | Causa provável |
|---------|----------------|
| Popup → Keycloak → `oauthcallback?error=invalid_scope` | Connector criado sem `--allowed-scopes`. `deploy.sh` cobre via env `ALLOWED_SCOPES` |
| Keycloak: `Invalid parameter: redirect_uri` | Redirect URL não registrado no client Keycloak ou tem trailing slash |
| `/validateUserId` → `Missing user_id cookie` | Browser bloqueando cookies cross-site, ou abriu /chat em hostname diferente do `CANONICAL_URL` (middleware deveria redirecionar — verifique se a `CANONICAL_URL` env var está setada no frontend Cloud Run) |
| `Finalize failed (400) "User ID is required"` | Cookies não chegando ao `/validateUserId`. Idem acima |
| Chat: `(empty response)` + agente log `RuntimeError: Failed to retrieve credential` | Telemetria reativada sem `sys.modules["urllib3.contrib.pyopenssl"]=None` block — pyOpenSSL race. Veja `agent/app/agent_runtime_app.py` |
| Chat: `(empty response)` + agente log `Tool 'X' not found` | LLM alucinou nome de tool. Instruction do agente já lista nomes exatos — se persistir, renomear a tool no MCP |
| Agente startup: `httpx.HTTPStatusError: 401` para `agentregistry.googleapis.com` | Falta `roles/agentregistry.viewer` no principal set, OU falta `GOOGLE_API_PREVENT_AGENT_TOKEN_SHARING_FOR_GCP_SERVICES=False`. `deploy.sh` cobre ambos |
| `Tool 'X' not found` ou tool call com `<call:...>` em texto na resposta | Toolset não materializou (race condition com Binding). Aguarde 5s e tente de novo — `_LazyMcpToolset` retenta no próximo request |
| `Failed to create MCP session` no log do agente, primeira chamada | Cold-start do MCP server (Cloud Run) + materialização do toolset. Próxima chamada funciona |
| MCP rejeitando `initialize` ou `tools/list` com 401 | Middleware não está liberando discovery methods. Verifique `mcp-server/app/middleware.py` lista `_PUBLIC_RPC_METHODS` |
| `connectors update --allowed-scopes` retorna `NOT_FOUND` mas `describe` retorna o connector | Connector está em estado soft-deleted (30-day retention). Solução: `gcloud alpha agent-identity connectors undelete <name> --location=<L>`. `deploy.sh` cobre via passo `undelete` antes do create |
| `services create` succeed mas `mcp-servers list` vazio | Recurso espelho propaga assincronamente. `deploy.sh` faz retry até 50s |
| Console: Tab Identity mostra Binding mas Auth Provider vazio | Falta IAM `roles/iamconnectors.user` no connector com o **principal individual** do agente. `deploy.sh` cobre via `setIamPolicy` direto |

Para um troubleshooting profundo com root-cause e fix, ver [LESSONS.md](LESSONS.md).
