# Arquitetura — OAuth 3LO + Keycloak Demo

Guia didático e completo do estado atual da implementação. Para o histórico de bugs, decisões revertidas e aprendizados do caminho até aqui, veja [LESSONS.md](LESSONS.md).

> **Idioma**: este documento está em PT-BR. Comentários inline no código também em PT-BR ou EN conforme o contexto.

---

## Visão geral

Um agente ADK rodando em **Agent Runtime** (com identidade SPIFFE) chama um servidor MCP protegido por **Keycloak** *em nome do usuário final*. O agente **não conhece** nada sobre OAuth — ele apenas pede o toolset ao Agent Registry e o Registry resolve o auth automaticamente via um **Binding** `(agente → MCP → auth_provider)`. O token Keycloak do usuário fica num cofre gerenciado pelo Google e nunca toca o código do agente.

Três componentes Cloud Run + uma cadeia de recursos de plataforma:

```
                          USUÁRIO
                             │
                             ▼
   ┌─────────────────────────────────────────────────────┐
   │  Frontend (Cloud Run, FastAPI)                       │
   │  - /            → UI de chat                         │
   │  - /chat        → proxy stream para o agente         │
   │  - /validateUserId → finaliza o consent OAuth        │
   │  - /resume      → envia function_response ao agente  │
   └────────────────┬────────────────────────────────────┘
                    │ stream_query
                    ▼
   ┌─────────────────────────────────────────────────────┐
   │  Agente ADK (Agent Runtime, SPIFFE identity)         │
   │  - sem auth_scheme inline                            │
   │  - get_mcp_toolset(mcp_server_name, continue_uri)    │
   │  - Registry resolve o Binding e injeta o Bearer      │
   └────┬──────────────┬─────────────────────────────────┘
        │              │
        │ retrieveCreds│ ler binding
        │              │
        ▼              ▼
   ┌─────────────┐    ┌────────────────────────────────────┐
   │  Connector  │    │  Agent Registry                    │
   │  (Agent     │    │  - Agent  (urn:agent:…)            │
   │  Identity)  │    │  - MCP    (urn:mcp:…)              │
   │             │    │  - Binding (agent → MCP → conn)    │
   │  3LO config │    │             + continue_uri + scopes│
   │  + vault    │    └────────────────────────────────────┘
   └──────┬──────┘
          │ guarda/serve tokens
          │
          │  Bearer <user_keycloak_token>
          ▼
   ┌─────────────────────────────────────────────────────┐
   │  MCP server (Cloud Run, FastMCP)                     │
   │  - middleware Starlette → PyJWT/JWKS valida JWT      │
   │  - tools: get_my_profile, echo                       │
   │  - claims → ContextVar → tools                       │
   └──────────────────┬──────────────────────────────────┘
                      │ JWKS fetch (cached 1h)
                      ▼
            ┌──────────────────────────┐
            │  Keycloak (IdP externo)  │
            │  /protocol/openid-       │
            │   connect/auth, /token,  │
            │   /certs (JWKS)          │
            │  + Audience Mapper       │
            └──────────────────────────┘
```

---

## Recursos no GCP

A demo cria quatro recursos de plataforma (além dos três serviços Cloud Run):

| Recurso | Resource name | Criado por | Função |
|---------|---------------|------------|--------|
| **Connector** (Agent Identity) | `projects/<P>/locations/<L>/connectors/<name>` | `gcloud alpha agent-identity connectors create` | Guarda configuração OAuth: `client_id`, `client_secret`, `authorization_url`, `token_url`, `allowed_scopes`. Um por IdP. Project-scoped. |
| **MCP Server** (Agent Registry) | `projects/<P>/locations/<L>/mcpServers/agentregistry-<UUID>` | `gcloud alpha agent-registry services create` (auto-vira mcpServer) | Catálogo do MCP no Registry. Contém toolspec + endpoint URL. Lido por `AgentRegistry.get_mcp_toolset`. |
| **Agent** (Agent Registry) | `projects/<P>/locations/<L>/agents/agentregistry-<UUID>` | Auto-registrado quando `agents-cli deploy --agent-identity` provisiona o Reasoning Engine | Catálogo do agente no Registry. Inclui `RuntimeIdentity.principal` (SPIFFE). |
| **Binding** (Agent Registry) | `projects/<P>/locations/<L>/bindings/<name>` | `gcloud alpha agent-registry bindings create` | A tripla `(source: agente, target: MCP, auth_provider: connector) + continue_uri + scopes`. Permite que o código do agente não saiba qual connector usar — o Registry resolve em runtime. |

Os três Cloud Run services (frontend, MCP, agent runtime no Vertex AI) são os "executáveis"; os quatro recursos acima são metadata/control-plane que orquestram quem fala com quem e como.

---

## Endpoints

### Frontend (`https://oauth-3lo-frontend-<hash>.<region>.run.app`)

| Método + path | Quem chama | O que faz |
|---------------|------------|-----------|
| `GET /` | Usuário (browser) | Serve a UI de chat (HTML + JS inline). Se `AGENT_ENGINE_ID` não estiver configurado, mostra banner de aviso |
| `GET /health` | Cloud Run probe | Retorna `{status: "ok", agent_configured: bool}` |
| `POST /chat` | UI JS | Body: `{message, session_id, user_id}`. Cria sessão no agente se ainda não existir, faz `stream_query`, inspeciona eventos. Se aparecer `adk_request_credential`, **seta cookies** `user_id` e `consent_nonce` e retorna `{needs_auth: true, auth_uri, function_call_id, auth_config, consent_nonce}` |
| `GET /validateUserId` | Google (redirect após Keycloak login) | Query params `user_id_validation_state` + `connector_name`. Lê `user_id` e `consent_nonce` dos cookies. Faz `POST iamconnectorcredentials.googleapis.com/v1alpha/<connector>/credentials:finalize` para amarrar o token Keycloak ao `user_id` do agente. Retorna HTML que fecha o popup |
| `POST /resume` | UI JS (depois que popup fecha) | Body: `{session_id, user_id, function_call_id, auth_config}`. Envia ao agente como `function_response(name="adk_request_credential")` para continuar a conversa, agora com token disponível |

**Comportamento adicional**: middleware Starlette redireciona 307 para `CANONICAL_URL` se o request chegar pelo segundo hostname do Cloud Run (a forma `<service>-<project_number>.<region>.run.app`). Cookies são per-origin — chat e validateUserId precisam estar no mesmo host.

### MCP server (`https://oauth-3lo-mcp-<hash>.<region>.run.app/mcp`)

| Método + path | Quem chama | O que faz |
|---------------|------------|-----------|
| `POST /mcp` | Agente (com `Authorization: Bearer <user_keycloak_token>`) | Endpoint MCP (Streamable HTTP, JSON-RPC). Middleware `KeycloakAuthMiddleware` valida JWT antes; sem Bearer válido → 401/403 |
| `POST /mcp` com método `tools/list` | Agente, no startup | Lista tools (`get_my_profile`, `echo`) |
| `POST /mcp` com método `tools/call` name=`get_my_profile` | Agente, em resposta a prompt | Retorna `{sub, username, email, realm_roles, given_name, family_name, issued_at, expires_at}` extraídos do JWT validado |
| `POST /mcp` com método `tools/call` name=`echo` args=`{message}` | Agente | Retorna `{message, echoed_by_sub, echoed_by_username}` |

### Agent (Agent Runtime / Reasoning Engine)

Chamado via REST em `https://<region>-aiplatform.googleapis.com/v1beta1/projects/<P>/locations/<L>/reasoningEngines/<ID>`:

| Método (class_method no body) | Quem chama | O que faz |
|-------------------------------|------------|-----------|
| `:query` com `class_method=create_session` | Frontend (lazy, antes do 1º stream_query) | Cria sessão `InMemorySessionService` no agente para o `(user_id, session_id)` |
| `:streamQuery?alt=sse` com `class_method=stream_query` | Frontend (em `/chat` e `/resume`) | Stream de eventos SSE. Eventos com `content.parts[].text` são respostas do modelo; eventos com `content.parts[].function_call.name="adk_request_credential"` indicam que o agente precisa de consent OAuth |

### Agent Identity Connector (Google-managed)

Chamado pela ADK via gRPC/REST em `iamconnectorcredentials.googleapis.com/v1alpha`:

| Método | Quem chama | O que faz |
|--------|------------|-----------|
| `connectors/<name>/credentials:retrieve` | Agente (internamente, via `gcp_auth_provider.py`) | Pede token para `user_id`. Se não há no vault → retorna LRO com `uriConsentRequired.authorizationUri` + `consentNonce`. Se há → retorna o token |
| `connectors/<name>/credentials:finalize` | Frontend (em `/validateUserId`) | Body `{userId, userIdValidationState, consentNonce}`. Amarra o token recém-obtido pelo Google ao `user_id` interno do agente |

### Keycloak

| Endpoint | Quem chama | O que faz |
|----------|------------|-----------|
| `/realms/<R>/.well-known/openid-configuration` | (raro) | Discovery OIDC; usado para confirmar issuer e URLs |
| `/realms/<R>/protocol/openid-connect/auth` | Usuário no popup (redirect do agente) | Tela de login Keycloak. Em sucesso, redireciona para o `redirect_uri` registrado (= URL do connector no `iamconnectorcredentials.googleapis.com`) com `?code=…` |
| `/realms/<R>/protocol/openid-connect/token` | Google (server-side, dentro do connector) | Troca `code` por `access_token` + `refresh_token` |
| `/realms/<R>/protocol/openid-connect/certs` | MCP server (PyJWT/JWKS, cache 1h) | Retorna chaves públicas para validar assinatura do JWT |

---

## Inventário de tokens

Tudo que é "token" no sistema, quem emite, quem guarda, quem valida, vida útil e formato. Use isto como mapa-mestre quando algo falha em "auth".

| Token | Emitido por | Formato | Onde fica armazenado | Quem usa pra autenticar | Vida útil | Renovável? |
|-------|-------------|---------|----------------------|--------------------------|-----------|-----------|
| **OAuth `code` do Keycloak** | Keycloak `/auth` (depois do consent) | Opaque string (~32 chars) | Query param `?code=…` no redirect do browser para o oauthcallback | Google (one-shot, troca por tokens) | ~10s | Não — uso único |
| **`access_token` do Keycloak** | Keycloak `/token` (depois do code-exchange) | JWT RS256, claims: `iss`, `aud`, `sub`, `preferred_username`, `email`, `realm_access.roles`, `exp`, `iat` | Vault gerenciado do Google (cofre do Connector), per-`(connector, user_id)` | MCP server (Bearer header, validado contra JWKS Keycloak) | 5 min (default Keycloak) | Sim, via refresh_token |
| **`refresh_token` do Keycloak** | Keycloak `/token` (junto com access_token) | Opaque string longa | Vault gerenciado do Google (com o access_token) | Google internamente (troca por novo access_token quando expira) | 30 min (default Keycloak) | Não — emite novo refresh a cada uso |
| **SPIFFE X.509 cert do agente** | Agent Identity (Google) na criação do RE com `--agent-identity` | X.509 cert + key, cert-bound (DPoP) | Container do Agent Runtime (volume injetado) | Agent → control-plane GCP (mTLS), Agent → APIs Google | 24h (rotacionado automaticamente) | Sim, automático |
| **Workload access token do agente** | Agent Identity, derivado do cert SPIFFE | Bearer JWT cert-bound | Memória do container | Calls do agente para `agentregistry.googleapis.com`, `iamconnectorcredentials.googleapis.com`, `aiplatform.googleapis.com` | ~1h | Sim, automático |
| **`user_id_validation_state`** | Google (no oauthcallback do connector) | Opaque base64-like string | Query param no redirect para `continue_uri` | Frontend → `credentials:finalize` (prova que o callback foi válido) | Curta (~minutos) | Não — uso único |
| **`consent_nonce`** | Google (no `retrieveCredentials` LRO, junto com `authorizationUri`) | UUID v4 | Frontend cookie `consent_nonce`; depois enviado em `:finalize` body | Google (binds finalize call ao consent attempt) | Curta (~minutos) | Não — uso único |
| **`user_id` (do agente)** | App do agente (ADK) | String livre | Frontend cookie `user_id`; campo `user_id` no Agent Runtime session | Google (chave de lookup no vault: token guardado sob `(connector, user_id)`) | Lifetime do session/cookie | N/A |
| **`Mcp-Session-Id`** | MCP server (FastMCP) na resposta de `initialize` | UUID hex | Headers entre Agent e MCP | MCP server (correlaciona requests da mesma sessão MCP) | Vida da sessão MCP no container | Não — nova sessão = novo ID |
| **`session_id` (Agent Runtime)** | Frontend (UUID-like local) | String livre | Field `session_id` no payload `stream_query` | Agent Runtime `InMemorySessionService` (correlaciona conversas) | Vida do container do agente | Não — sessão é per `(user_id, session_id)` |
| **OAuth `state` (no auth_uri)** | Google (dentro do connector) | Opaque base64 longo | Query param do auth_uri → redirect para Keycloak → preservado de volta no oauthcallback | Google (correlaciona o callback ao request original) | Curta | Não — uso único |
| **Cookie SA do frontend** | Cloud Run (default SA do projeto) | Service account token via ADC | ADC do container do frontend | Frontend → `iamconnectorcredentials.googleapis.com:finalize` (precisa `roles/iamconnectors.editor`) | ~1h | Sim, automático |

### Como os tokens se transformam end-to-end

```
                                    ┌─────────────┐
                                    │   USUÁRIO   │
                                    └──────┬──────┘
                                           │ credenciais Keycloak
                                           ▼
            ┌──────────────────────────────────────────────────────┐
            │                    KEYCLOAK                          │
            │  /auth  ──issue──▶  OAuth code  ──redirect──▶ Google │
            │                                                       │
            │  /token  ──exchange code──▶  access_token (JWT) +    │
            │                              refresh_token            │
            └──────────────────────────────────────────────────────┘
                              │
                              │ access_token + refresh_token
                              ▼
            ┌──────────────────────────────────────────────────────┐
            │           GOOGLE VAULT (iamconnectorcredentials)     │
            │   stored as: vault[(connector_name, user_id)]        │
            │   = { access_token, refresh_token, expires_at }      │
            └──────────────────────────────────────────────────────┘
                              │
                              │ retrieveCredentials(user_id)
                              ▼
            ┌──────────────────────────────────────────────────────┐
            │              AGENT RUNTIME (ADK)                     │
            │   - recebe o token do vault                          │
            │   - injeta como `Authorization: Bearer <token>`      │
            │     na próxima chamada MCP                           │
            └──────────────────────────────────────────────────────┘
                              │
                              │ Bearer <access_token>
                              ▼
            ┌──────────────────────────────────────────────────────┐
            │                 MCP SERVER                           │
            │   middleware:                                         │
            │     1. extrai Bearer                                  │
            │     2. PyJWT decode + valida sig contra JWKS         │
            │     3. valida iss, aud, exp                           │
            │     4. claims → request.state.claims                  │
            │   tool:                                               │
            │     get_http_request().state.claims → retorna dados  │
            └──────────────────────────────────────────────────────┘
```

---

## Inventário de cookies (frontend)

Cookies setados pelo frontend pra carregar estado através do redirect cross-origin (Keycloak → Google → frontend).

| Cookie | Setado em | Valor | Atributos | Lido em | Por quê |
|--------|-----------|-------|-----------|---------|---------|
| `user_id` | `POST /chat` (quando agente emite `adk_request_credential`) | O `user_id` do agente (passado pelo cliente JS) | `HttpOnly; Secure; SameSite=Lax; Max-Age=600; Path=/` | `GET /validateUserId` | Google redireciona para `continue_uri` com `user_id_validation_state` mas NÃO carrega `user_id` original. Frontend precisa do `user_id` pra montar o body do `:finalize` |
| `consent_nonce` | `POST /chat` (quando agente emite `adk_request_credential`) | Nonce extraído do `auth_config.oauth2.consent_nonce` da resposta do agente | `HttpOnly; Secure; SameSite=Lax; Max-Age=600; Path=/` | `GET /validateUserId` | Mesmo motivo — `:finalize` exige `consentNonce` no body, e Google não devolve no redirect |

**Por que `SameSite=Lax` e não `Strict`**: o popup OAuth segue cadeia cross-site (frontend → Keycloak → Google → frontend). Em `Strict`, cookies seriam descartados nessa cadeia. `Lax` permite navegação top-level cross-site preservando cookies (que é o caso de `window.open` + 302 redirects).

**Por que `HttpOnly`**: nenhum código JS precisa ler esses cookies — só o servidor (FastAPI) lê. Reduz superfície de XSS.

**Origem dos cookies importa**: cookies são per-origin. Frontend tem DUAS URLs Cloud Run (`<service>-<project_number>.<region>.run.app` e `<service>-<hash>-<region_short>.a.run.app`). Cookies setados num NÃO chegam no outro. Middleware `canonical_host_redirect` força tudo pra `CANONICAL_URL` (definido por env var no deploy) pra evitar split-brain.

---

## Cabeçalhos HTTP por hop

Quem manda quais headers em cada chamada da arquitetura. Útil para debugar com `curl -v` ou logs de proxy.

### Frontend → Agent Runtime (`POST /chat` interno → `:streamQuery`)

| Header | Valor | Quem seta |
|--------|-------|-----------|
| `Authorization` | `Bearer <SA token>` | Frontend, via `google.auth.default()` (Cloud Run SA) |
| `Content-Type` | `application/json` | Frontend |
| `Accept` | (não setado — implícito SSE) | Frontend |

Body: `{"class_method": "stream_query", "input": {"user_id", "session_id", "message"}}`

### Browser → Frontend `/chat`

| Header | Valor | Origem |
|--------|-------|--------|
| `Content-Type` | `application/json` | JS `fetch()` |
| `Cookie` | (nenhum na primeira chamada; nas seguintes pode ter `user_id`, `consent_nonce` de chats anteriores) | Browser |

### Frontend `/chat` response → Browser (quando needs_auth=true)

| Header | Valor | Setado por |
|--------|-------|-----------|
| `Set-Cookie: user_id=<U>` | `HttpOnly; Secure; SameSite=Lax; Max-Age=600; Path=/` | `chat()` handler |
| `Set-Cookie: consent_nonce=<N>` | `HttpOnly; Secure; SameSite=Lax; Max-Age=600; Path=/` | `chat()` handler |
| `Content-Type` | `application/json` | FastAPI |

### Browser → Keycloak (`GET /auth` no popup)

| Header | Valor | Origem |
|--------|-------|--------|
| `Referer` | URL do frontend (origin) | Browser |
| (nenhum auth — usuário ainda não logou) | | |

Query params: `client_id`, `redirect_uri`, `response_type=code`, `scope=openid profile email`, `state=<opaco>`

### Keycloak → Google (`GET oauthcallback?code=…`)

| Header | Valor | Origem |
|--------|-------|--------|
| (redirect 302, nenhum header customizado) | | |

### Google → Frontend `/validateUserId`

| Header | Valor | Origem |
|--------|-------|--------|
| `Cookie: user_id=<U>; consent_nonce=<N>` | (Browser anexa automaticamente — cookies da origin do frontend) | Browser |

Query params: `user_id_validation_state=<opaco>`, `connector_name=projects/.../connectors/<name>`

### Frontend → iamconnectorcredentials `:finalize`

| Header | Valor | Setado por |
|--------|-------|-----------|
| `Authorization` | `Bearer <SA token>` | Frontend (ADC) |
| `x-goog-user-project` | `${PROJECT_ID}` | Frontend (quota project) |
| `Content-Type` | `application/json` | Frontend |

Body: `{"userId": "<U>", "userIdValidationState": "<S>", "consentNonce": "<N>"}`

### Agent → iamconnectorcredentials `:retrieveCredentials` (interno, via ADK)

| Header | Valor | Setado por |
|--------|-------|-----------|
| `Authorization` | `Bearer <agent SPIFFE token>` (cert-bound DPoP) | ADK (google.auth) |
| `x-goog-user-project` | `${PROJECT_ID}` | ADK |
| `Content-Type` | `application/json` | google-cloud client lib |

Body: `{"userId": "<user_id_from_agent>", "continueUri": "<frontend>/validateUserId", "requestedScopes": ["openid", "profile", "email"]}`

### Agent → MCP server (tool call)

| Header | Valor | Setado por |
|--------|-------|-----------|
| `Authorization` | `Bearer <keycloak access_token>` (vindo do vault) | ADK, automaticamente após `retrieveCredentials` |
| `Content-Type` | `application/json` | MCP client |
| `Accept` | `application/json, text/event-stream` | MCP client (Streamable HTTP) |
| `Mcp-Session-Id` | (após `initialize`, vindo da resposta do server) | MCP client |

Body: `{"jsonrpc": "2.0", "id": <N>, "method": "tools/call", "params": {"name": "<tool>", "arguments": {…}}}`

### MCP server → Keycloak `/certs` (JWKS, em background)

| Header | Valor | Setado por |
|--------|-------|-----------|
| (nenhum auth — JWKS é endpoint público) | | |

PyJWKClient cacheia 1h; só refaz fetch quando o JWT tem um `kid` desconhecido.

---

## Shapes de request/response (JSON)

### Frontend `POST /chat`

**Request body**:
```json
{
  "message": "Qual é o meu perfil no sistema?",
  "session_id": "sess-abc123",
  "user_id": "user-xyz789"
}
```

**Response (caso 1 — agente respondeu direto, sem auth)**:
```json
{
  "needs_auth": false,
  "text": "Sua identidade no sistema é: …"
}
```

**Response (caso 2 — agente requer consent)**:
```json
{
  "needs_auth": true,
  "auth_uri": "https://<keycloak>/realms/<R>/protocol/openid-connect/auth?client_id=…&redirect_uri=…&response_type=code&scope=openid+profile+email&state=ATOEr7…",
  "function_call_id": "adk-abc-123-def",
  "auth_config": { …auth_config payload do ADK… },
  "consent_nonce": "fb9f8ff2-ca6e-4d4d-9a61-5a3109d3fead"
}
```

### Frontend `POST /resume`

**Request body**:
```json
{
  "session_id": "sess-abc123",
  "user_id": "user-xyz789",
  "function_call_id": "adk-abc-123-def",
  "auth_config": { …mesmo payload recebido no /chat… }
}
```

**Response**: idêntica à `/chat` resposta (caso 1 ou 2).

### Agent Runtime `POST :streamQuery?alt=sse`

**Request body**:
```json
{
  "class_method": "stream_query",
  "input": {
    "user_id": "user-xyz789",
    "session_id": "sess-abc123",
    "message": "Qual é o meu perfil no sistema?"
  }
}
```

**Response**: SSE stream. Cada linha:
```
data: {"content": {"parts": [{"function_call": {"name": "adk_request_credential", "id": "adk-abc-123", "args": {"auth_config": {…}}}}]}}

data: {"content": {"parts": [{"text": "Sua identidade…"}]}}
```

### MCP server `POST /mcp` (método `tools/call`)

**Request**:
```http
POST /mcp HTTP/1.1
Authorization: Bearer eyJhbGciOiJSUzI1NiIs...
Mcp-Session-Id: aaad62ff36c043e2b7743e82fa9cff94
Content-Type: application/json
Accept: application/json, text/event-stream

{
  "jsonrpc": "2.0",
  "id": 5,
  "method": "tools/call",
  "params": {
    "name": "get_my_profile",
    "arguments": {}
  }
}
```

**Response (sucesso)**:
```
event: message
data: {"jsonrpc":"2.0","id":5,"result":{"content":[{"type":"text","text":"{\"sub\":\"abc\",\"username\":\"alice\",\"email\":\"alice@example.com\",\"realm_roles\":[\"user\"]}"}],"isError":false}}
```

**Response (sem Bearer — middleware rejeita)**:
```http
HTTP/1.1 401 Unauthorized
WWW-Authenticate: Bearer realm="oauth-3lo-mcp"
Content-Type: application/json

{"error": "missing_bearer"}
```

**Response (Bearer inválido)**:
```http
HTTP/1.1 403 Forbidden
Content-Type: application/json

{"error": "invalid_token", "detail": "audience_mismatch (expected 'account')"}
```

### iamconnectorcredentials `POST :retrieveCredentials` (interno do agente)

**Request body**:
```json
{
  "userId": "user-xyz789",
  "continueUri": "https://oauth-3lo-frontend-….run.app/validateUserId",
  "requestedScopes": ["openid", "profile", "email"]
}
```

**Response (caso 1 — token cached no vault)**:
```json
{
  "name": "<lro-id>",
  "done": true,
  "response": {
    "@type": "...RetrieveCredentialsResponse",
    "token": "eyJhbGc...",
    "header": {"name": "Authorization", "value": "Bearer eyJhbGc..."}
  }
}
```

**Response (caso 2 — precisa consent)**:
```json
{
  "name": "<lro-id>",
  "metadata": {
    "@type": "...RetrieveCredentialsMetadata",
    "uriConsentRequired": {
      "authorizationUri": "https://<keycloak>/.../auth?...",
      "consentNonce": "fb9f8ff2-ca6e-4d4d-9a61-5a3109d3fead"
    }
  }
}
```

### iamconnectorcredentials `POST :finalize` (do frontend)

**Request body**:
```json
{
  "userId": "user-xyz789",
  "userIdValidationState": "ATOEr7tIHGlN2vxkgW5Ax...",
  "consentNonce": "fb9f8ff2-ca6e-4d4d-9a61-5a3109d3fead"
}
```

**Response (sucesso)**:
```json
{
  "name": "projects/.../operations/<op-id>",
  "done": true
}
```

---

## IAM

### Principal SPIFFE do agente

Quando o agente é deployado com `agents-cli deploy --agent-identity`, o Agent Runtime provisiona uma identidade SPIFFE:

```
principal://agents.global.org-<ORG_ID>.system.id.goog/
            resources/aiplatform/projects/<PROJECT_NUMBER>/
            locations/<REGION>/reasoningEngines/<ENGINE_ID>
```

E um principal set cobrindo *todos* os agentes do projeto:

```
principalSet://agents.global.org-<ORG_ID>.system.id.goog/
              attribute.platformContainer/aiplatform/projects/<PROJECT_NUMBER>
```

### Roles concedidas (em ordem de aparição na demo)

| Role | Aplicado em | Membro | Por quê |
|------|-------------|--------|---------|
| `roles/aiplatform.agentDefaultAccess` | projeto | principalSet | Acesso baseline do Agent Runtime |
| `roles/aiplatform.user` | projeto | principalSet | Predição Vertex AI |
| `roles/aiplatform.agentContextEditor` | projeto | principalSet | Manipulação de contexto/sessões |
| `roles/serviceusage.serviceUsageConsumer` | projeto | principalSet | Permite o agente consumir APIs faturadas no projeto |
| `roles/logging.logWriter` | projeto | principalSet | stdout/stderr → Cloud Logging |
| `roles/monitoring.metricWriter` | projeto | principalSet | Métricas |
| `roles/cloudapiregistry.viewer` | projeto | principalSet | Leitura do Cloud API Registry (legado) |
| `roles/agentregistry.viewer` | projeto | principalSet | **Crítico**: leitura do Agent Registry (mcpServers, bindings). Sem isso, `get_mcp_toolset` retorna 401 |
| `roles/storage.objectAdmin` | projeto | principalSet | GCS staging bucket |
| `roles/iamconnectors.user` | projeto | principalSet | **Crítico**: chamar `connectors/<name>/credentials:retrieve`. Permissão `iamconnectors.connectors.retrieveCredentials` |
| `roles/iamconnectors.user` | **connector específico** | (a) principalSet, (b) principal individual do agente, (c) usuário admin | Aplicado também no recurso connector via `setIamPolicy`. A presença do principal **individual** do agente é o que faz o Console mostrar o connector na tab Identity do agente |

### Env vars que afetam IAM em runtime

| Env var | Valor na demo | Por quê |
|---------|---------------|---------|
| `GOOGLE_API_PREVENT_AGENT_TOKEN_SHARING_FOR_GCP_SERVICES` | `False` | Permite o token cert-bound do SPIFFE ser usado em chamadas a APIs do GCP control-plane (agentregistry, iamconnectorcredentials). Sem isso → 401 mesmo com IAM correto. Mesmo workaround do A2A no `spiffe-registry-demo` |
| `GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY` | `False` | Desliga o exportador OTEL do Agent Engine — evita HTTPS concorrente que dispara um race no pyOpenSSL (sob carga) |
| `DISABLE_GCP_TELEMETRY` | `true` | Diz ao nosso `app/app_utils/telemetry.py` para fazer early-return e não configurar Cloud Trace/Logging exporters |

### Scopes OAuth no Keycloak

| Configurado em | Valor | Função |
|----------------|-------|--------|
| Connector (`--allowed-scopes`) | `openid,profile,email` | Whitelist de scopes que o connector pode pedir ao IdP. Sem isso, o request OAuth vai com `&scope&` (vazio) e o Keycloak rejeita com `invalid_scope` |
| Binding (`--auth-provider-binding-scopes`) | `openid,profile,email` | Scopes pedidos quando este binding específico é resolvido (subset opcional do allowed-scopes) |

---

## Diagramas de sequência

### Fluxo 1 — Primeiro consent do usuário

```
USUÁRIO        FRONTEND         AGENT RUNTIME       REGISTRY        CONNECTOR        KEYCLOAK        MCP SERVER
   │ "qual meu                                                                                                  
   │  perfil?"                                                                                                  
   ├──────────▶│  POST /chat                                                                                    
   │           │  (sem cookies)                                                                                 
   │           │                                                                                                
   │           │  POST :query                                                                                   
   │           │  class_method=create_session                                                                   
   │           ├──────────────▶│                                                                               
   │           │  ◀─────────── session OK                                                                       
   │           │                                                                                                
   │           │  POST :streamQuery?alt=sse                                                                     
   │           │  class_method=stream_query                                                                     
   │           ├──────────────▶│                                                                               
   │           │                │ get_mcp_toolset(mcp_server_name, continue_uri)                                
   │           │                ├────────────────▶│                                                            
   │           │                │                 │ GET /v1alpha/.../bindings                                  
   │           │                │                 │ → encontra binding cujo target = MCP                       
   │           │                │                 │ → retorna auth_provider name                               
   │           │                │ ◀────────────── │                                                            
   │           │                │                                                                              
   │           │                │ retrieveCredentials(userId, continueUri, scope)                              
   │           │                ├───────────────────────────────▶│                                            
   │           │                │                                 │ (nenhum token cacheado para este user)     
   │           │                │ ◀── LRO: authorizationUri + consentNonce ──│                                
   │           │                │                                                                              
   │           │  Stream event: function_call(name="adk_request_credential",                                   
   │           │                args={auth_config: { exchanged_auth_credential.oauth2:                          
   │           │                       { auth_uri, consent_nonce, ... } } })                                   
   │           │  ◀────────────│                                                                               
   │           │                                                                                                
   │           │  Set-Cookie: user_id=<U>;     SameSite=Lax; Secure                                            
   │           │  Set-Cookie: consent_nonce=<N>; SameSite=Lax; Secure                                          
   │           │  Body: {needs_auth: true, auth_uri, function_call_id, auth_config, consent_nonce}             
   │ ◀─────────│                                                                                                
   │                                                                                                            
   │  JS abre popup ────────────────────────────────────────────▶                                              
   │  popup navega para auth_uri (Keycloak)                                                                    
   │  popup mostra tela de login                                                                                
   │  usuário entra credenciais                                                                                 
   │ ◀───────────────────────────────────────────────────────── login OK                                        
   │                                                                                                            
   │  Keycloak redireciona popup para                                                                          
   │  iamconnectorcredentials.googleapis.com/.../oauthcallback?code=…                                           
   ├───────────────────────────────────────────────────────────▶│                                              
   │                                                              │ exchange code → token                       
   │                                                              ├───────────────────────────▶│               
   │                                                              │ ◀── {access_token, refresh_token} ──│      
   │                                                              │ guarda token no vault                       
   │                                                                                                            
   │  Google redireciona popup para                                                                            
   │  CANONICAL_URL/validateUserId?user_id_validation_state=…&connector_name=…                                  
   ├──────────▶│  GET /validateUserId                                                                          
   │           │  Cookies: user_id, consent_nonce                                                              
   │           │                                                                                                
   │           │  POST /v1alpha/<connector>/credentials:finalize                                                
   │           │  {userId, userIdValidationState, consentNonce}                                                 
   │           ├─────────────────────────────────▶│                                                            
   │           │                                    │ verifica state + nonce                                    
   │           │                                    │ BIND token ↔ userId                                       
   │           │ ◀── 200 OK ────────────────────── │                                                            
   │           │                                                                                                
   │           │  HTML: window.close() + postMessage(opener, 'consent-done')                                    
   │ ◀─────────│                                                                                                
   │  popup fecha                                                                                              
   │  parent detecta via popup.closed polling (postMessage não chega por COOP)                                  
   │                                                                                                            
   ├──────────▶│  POST /resume                                                                                  
   │           │  {session_id, user_id, function_call_id, auth_config}                                          
   │           │                                                                                                
   │           │  POST :streamQuery                                                                             
   │           │  class_method=stream_query                                                                     
   │           │  message.parts[0].function_response                                                           
   │           │    .name="adk_request_credential"                                                              
   │           │    .id=<function_call_id>                                                                      
   │           │    .response=<auth_config>                                                                     
   │           ├──────────────▶│                                                                               
   │           │                │ retrieveCredentials de novo                                                  
   │           │                ├───────────────────────────────▶│                                            
   │           │                │ ◀── access_token ─────────────│                                              
   │           │                │                                                                              
   │           │                │ LLM decide: chamar get_my_profile                                            
   │           │                │ MCP call: POST /mcp                                                          
   │           │                │ Authorization: Bearer <user_keycloak_token>                                  
   │           │                │ {jsonrpc, method: tools/call, params: {name: get_my_profile, arguments: {}}} 
   │           │                ├───────────────────────────────────────────────────────────────────▶│        
   │           │                │                                                                     │ KeycloakAuthMiddleware:
   │           │                │                                                                     │  - extrai Bearer       
   │           │                │                                                                     │  - PyJWT decode com    
   │           │                │                                                                     │    JWKS, verify_iss,   
   │           │                │                                                                     │    verify_aud, exp     
   │           │                │                                                                     │  - claims → ContextVar 
   │           │                │                                                                     │ tool reads ContextVar  
   │           │                │                                                                     │ retorna claims         
   │           │                │ ◀── {sub, username, email, realm_roles, ...} ──────────────────────│        
   │           │                │                                                                              
   │           │                │ LLM formata resposta em PT-BR                                                
   │           │  Stream event: content.parts[].text="Seu perfil: sub=..., usuário=..."                        
   │           │  ◀────────────│                                                                               
   │           │  Body: {needs_auth: false, text: "Seu perfil: ..."}                                           
   │ ◀─────────│                                                                                                
   │  UI mostra resposta                                                                                       
```

### Fluxo 2 — Interação subsequente (token em cache, sem popup)

```
USUÁRIO        FRONTEND         AGENT RUNTIME       REGISTRY        CONNECTOR        MCP SERVER
   │ "outra                                                                                       
   │  pergunta"                                                                                   
   ├──────────▶│  POST /chat                                                                      
   │           ├──────────────▶│ stream_query                                                    
   │           │                │ retrieveCredentials(userId)                                    
   │           │                ├──────────────────────────────▶│                                
   │           │                │ ◀── token (do vault, instantâneo) ──│                          
   │           │                │ MCP call com Bearer ────────────────────────────────▶│        
   │           │                │ ◀────── tool result ────────────────────────────────│        
   │           │  Stream event: text                                                                
   │           │  ◀────────────│                                                                  
   │ ◀─────────│  resposta                                                                        
```

Sem popup, sem `adk_request_credential` — o connector serve o token direto do vault até ele expirar (5 min default) E o refresh falhar OU o usuário revogar consent.

### Fluxo 3 — Token expirado, refresh transparente

```
   ... mesmo que Fluxo 2, exceto:
   
   │ Agente │ ────retrieveCredentials───▶ │ Connector │
   │        │                              │ access_token expirado → usa refresh_token internamente │
   │        │                              │ ────POST /token (com refresh_token)────▶ │ Keycloak │
   │        │                              │ ◀── novo access_token + refresh_token ─ │
   │        │ ◀────── novo access_token ── │
   ... continua transparente
```

Se o refresh_token também expirou ou foi revogado, o connector retorna `uriConsentRequired` de novo e o fluxo cai no padrão do Fluxo 1.

---

## Componente a componente

### Frontend (`frontend/app/main.py`)

- **Stack**: FastAPI + Jinja2 + httpx, deployado em Cloud Run com Dockerfile simples (python:3.12-slim)
- **Estado**: in-process (`_created_sessions` set, cookies no browser). Cloud Run pode reciclar instâncias — em caso de cold start, `create_session` simplesmente recria a sessão (idempotente)
- **Cookies setados em `/chat`** (quando agente pede consent):
  - `user_id` — `SameSite=Lax; Secure; HttpOnly; Max-Age=600`
  - `consent_nonce` — mesmo flags
  - Cookies escopados ao `CANONICAL_URL` host. Middleware redireciona quem cair no segundo hostname do Cloud Run
- **Detecção de popup close**: JS faz polling `setInterval` em `popup.closed` a cada 500ms. Quando true, chama `/resume`. Não depende de `postMessage` (Chrome COOP zera `window.opener` após navegações cross-origin)
- **Service account**: precisa de `roles/iamconnectors.editor` (ou ao menos `iamconnectors.connectors.finalizeCredential`) para o `:finalize`. A default Compute Engine SA tem via Owner; em prod, criar SA dedicada

### MCP server (`mcp-server/app/`)

- **Stack**: FastMCP 2.x + PyJWT (com `cryptography` extra para RS256) + Starlette, deployado em Cloud Run
- **Entrypoint**: `app/main.py` constrói o `FastMCP` instance, registra tools, monta como ASGI:
  ```python
  app = mcp.http_app(
      path="/mcp",
      middleware=[Middleware(KeycloakAuthMiddleware)],
      stateless_http=False,
  )
  ```
  `stateless_http=False` (stateful sessions) porque ADK envia `Mcp-Session-Id` e exige session continuity. Em produção com múltiplas instâncias Cloud Run, sticky sessions ou external session store (Redis) necessário.
- **`auth.py`**: `verify_keycloak_jwt(token) -> dict` usando `PyJWKClient` (cache 1h, auto-refresh em kid desconhecido). Valida assinatura RS256, `iss`, `aud` (controlável via env), `exp`, `nbf`
- **`middleware.py`**: ASGI puro (não `BaseHTTPMiddleware` — esse quebra SSE com body-peek). Lógica:
  1. Path `/health` ou `/` → passa
  2. GET/DELETE `/mcp` → passa (session-state, não user data)
  3. POST `/mcp` → peek no body, se `method` ∈ {`initialize`, `notifications/initialized`, `tools/list`, `prompts/list`, `resources/list`, `resources/templates/list`} → passa (discovery, sem auth necessária)
  4. Demais POSTs → exige Bearer JWT Keycloak válido. Sucesso: claims em `request.state.claims` e em `ContextVar current_claims`. Falha: 401 (sem bearer) ou 403 (inválido)
  
  O bypass de discovery methods é seguro: tool schemas já são públicos no Agent Registry (`toolspec.json`), session IDs não são segredos. Permite o agente fazer discovery antes do usuário consentir.
- **Tools**: `app/tools/profile.py` lê o `ContextVar` (`current_claims.get()`) ao invés do `request.state` direto — funciona independente de como o FastMCP expõe o request à tool

### Agente ADK (`agent/app/`)

- **Stack**: ADK 1.27+ com extras `[a2a,agent-identity]` (a2a-sdk é dep transitiva mesmo sem usar A2A — importado pelo módulo `agent_registry`), `google-cloud-aiplatform[agent_engines]`, deployado via `agents-cli deploy --agent-identity`
- **`agent.py`** — wiring de tools combina **três técnicas** que juntas formam o padrão mínimo confiável:
  1. **`_LazyMcpToolset`** — adia materialização do toolset até o primeiro `get_tools()`. Evita HTTPS em boot time (reduz overlap de threads → mitiga race do pyOpenSSL)
  2. **Bypass de `registry.get_mcp_toolset`** — replica os internals do método pra passar `tool_name_prefix=None`. Sem isso, ADK força um prefix baseado em `displayName` (`oauth-3lo-mcp` → `oauth_3lo_mcp_*`) e Gemini reliably encurta o nome no function_call, fazendo o lookup falhar silenciosamente
  3. **`auth_scheme` inline** — passa `GcpAuthProviderScheme(name=AUTH_PROVIDER_FULL_NAME, continue_uri=CONTINUE_URI)` direto, sem deixar ADK procurar o Binding em runtime. O Binding continua existindo no Registry (criado por `deploy.sh`, visível na Console, audited), só não é consultado pelo agente. Elimina a race do binding lookup
- **Por que esse padrão e não outros**:
  - Snippet "oficial" dos docs `registry.get_mcp_toolset(mcp_server_name=..., continue_uri=...)` + `LlmAgent(tools=[toolset])` PARECE simples, mas falha em produção porque Gemini emite o nome bare em vez do prefixado
  - Subclasse de `AgentRegistry` com override de `get_mcp_toolset` aceitando `tool_name_prefix` seria mais limpo, mas exige duplicar TODA a lógica do método e mantê-la em sync com upstream
  - Bypass localizado num `_LazyMcpToolset` isola a fragilidade num único ponto auditável
- **`agent.py`** — implementação:
  ```python
  class _LazyMcpToolset(BaseToolset):
      def __init__(self, mcp_server_name, auth_scheme, continue_uri):
          super().__init__()
          self._mcp_server_name = mcp_server_name
          self._auth_scheme = auth_scheme
          self._continue_uri = continue_uri
          self._inner = None

      def _resolve(self):
          if self._inner is None:
              CredentialManager.register_auth_provider(GcpAuthProvider())
              registry = AgentRegistry(project_id=PROJECT_ID, location=REGISTRY_LOCATION)
              # Bypass get_mcp_toolset to set tool_name_prefix=None
              server_details = registry.get_mcp_server(self._mcp_server_name)
              endpoint_uri, _, _ = registry._get_connection_uri(
                  server_details, protocol_binding=A2ATransport.jsonrpc
              )
              self._inner = AgentRegistrySingleMcpToolset(
                  destination_resource_id=server_details.get("mcpServerId"),
                  connection_params=StreamableHTTPConnectionParams(url=endpoint_uri),
                  tool_name_prefix=None,
                  auth_scheme=self._auth_scheme,
              )
          return self._inner

      async def get_tools(self, readonly_context=None):
          return await self._resolve().get_tools(readonly_context)
  ```
  Wiring no `_build_tools()`:
  ```python
  auth_scheme = GcpAuthProviderScheme(
      name=AUTH_PROVIDER_FULL_NAME,
      continue_uri=CONTINUE_URI,
  )
  toolset = _LazyMcpToolset(MCP_REGISTRY_NAME, auth_scheme, CONTINUE_URI)
  root_agent = Agent(..., tools=[toolset])
  ```
- **Telemetria desabilitada** (`DISABLE_GCP_TELEMETRY=true` + `GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY=False`) — sem fonte concorrente de HTTPS, race do pyOpenSSL (dep transitiva de `google-auth[pyopenssl]`) raramente dispara
- **Instruction sem nomes de tools** — schema do function_call é a única fonte de verdade pra Gemini. Listar nomes na instruction só atrapalha
- **`agent_runtime_app.py`**: subclasses `AdkApp`. Configura `vertexai.init()`, telemetria (early-return se `DISABLE_GCP_TELEMETRY=true`), `GcsArtifactService` para sessions. Importa comentário documentando o block de pyOpenSSL caso telemetria seja reativada
- **Instrução do agente**: lista os nomes exatos das tools (`get_my_profile`, `echo`) com aviso para não alterar — previne hallucination do LLM quando retoma após consent

### Agent Identity Connector (`projects/.../connectors/oauth-3lo-keycloak`)

- **Tipo**: 3-legged OAuth
- **Campos relevantes** (visível via `gcloud alpha agent-identity connectors describe`):
  ```yaml
  allowedScopes: [openid, profile, email]
  connectorTypeParams:
    threeLeggedOauth:
      authorizationUrl: https://<keycloak>/realms/<R>/protocol/openid-connect/auth
      tokenUrl:         https://<keycloak>/realms/<R>/protocol/openid-connect/token
      redirectUrl:      https://iamconnectorcredentials.googleapis.com/v1/projects/<P>/locations/<L>/connectors/<name>/oauthcallback
      clientId:         <KEYCLOAK_CLIENT_ID>
      # clientSecret oculto, mas configurado
  state: ENABLED
  ```
- **Vault**: tokens são armazenados internamente pelo Google, **inacessíveis ao código do agente**. Cert-bound (DPoP).

### Agent Registry Binding (`projects/.../bindings/oauth-3lo-agent-binding`)

```yaml
name: projects/<P>/locations/<L>/bindings/<binding>
source:
  identifier: urn:agent:projects-<N>:projects:<N>:locations:<L>:aiplatform:reasoningEngines:<ENGINE_ID>
target:
  identifier: urn:mcp:projects-<N>:projects:<N>:locations:<L>:agentregistry:services:<MCP_NAME>
authProviderBinding:
  authProvider: projects/<P>/locations/<L>/connectors/<connector>
  continueUri: https://<frontend>/validateUserId
  scopes: [openid, profile, email]
```

Esse single registro substitui qualquer wiring de auth no código do agente.

---

## Vantagens do Auth Provider gerenciado vs OAuth "em código"

### O que seria a alternativa DIY

Implementar OAuth 3LO sem usar Agent Identity Connector + Bindings significaria, ao menos:

```python
# Em agent.py
client_id     = os.environ["KEYCLOAK_CLIENT_ID"]
client_secret = os.environ["KEYCLOAK_CLIENT_SECRET"]   # segredo no agente!
auth_url      = os.environ["KEYCLOAK_AUTH_URL"]
token_url     = os.environ["KEYCLOAK_TOKEN_URL"]

token_store = SomeEncryptedKVStore()  # você implementa

def get_token_for_user(user_id):
    cached = token_store.get(user_id)
    if cached and not expired(cached):
        return cached.access_token
    if cached and cached.refresh_token:
        return refresh(cached.refresh_token, client_id, client_secret, token_url)
    raise NeedsConsent(build_auth_uri(client_id, auth_url, scopes, state=user_id))

def consent_callback(code, state):
    tokens = exchange(code, client_id, client_secret, token_url)
    token_store.put(state, tokens)
```

Mais o frontend que faz o handshake OAuth, mais a gestão de revogação, refresh tokens, encryption-at-rest, audit, IAM…

### Comparação detalhada

| Aspecto | DIY no código | Managed (esta demo) |
|---------|---------------|---------------------|
| **Segredo do client** | Env var no agente (e em logs, e em backups, e em snapshots) | Só no connector — `gcloud alpha agent-identity connectors update --three-legged-oauth-client-secret=...`. Nunca no código |
| **Armazenamento de tokens** | Você implementa: KV store + cifragem + rotação de chaves de cifragem | Google-managed vault. Tokens cert-bound (DPoP), inacessíveis ao código |
| **Refresh** | Você escreve o loop, trata 401s, evita thundering herd | Connector cuida transparentemente, com retry exponencial |
| **Revogação** | Você implementa endpoint `/revoke` e propaga | Console UI: enable/disable connector, ou `connectors revoke-credentials --user-id=...` |
| **Multi-tenant / multi-usuário** | Sua chave de KV é o user_id; bug de isolamento = leak de tokens | Vault namespaceia por `(connector, user_id)` automaticamente |
| **Mudar de IdP** | Edita código, deploya o agente | `connectors create` novo + `bindings update` no Registry. Zero deploy de código |
| **Múltiplos agentes, mesmo IdP** | Copy-paste do código de auth em cada agente | Um connector + um binding por agente. Connector é reutilizado |
| **Agente único, múltiplos targets com IdPs diferentes** | If/else gigante no código | Um binding por (agent, target). Código do agente fica idêntico |
| **Audit** | Você escreve logs (e garante que não rotacionou e perdeu) | Cloud Audit Logs registra todas as operações `connectors.*` e `bindings.*` automaticamente. Quem mudou o secret? Cloud Audit responde |
| **IAM granular** | Permissões implícitas no código | `iamconnectors.user` no connector — quem pode pedir tokens. `agentregistry.viewer` no projeto — quem pode ler bindings. Tudo via IAM |
| **Visibilidade na Console** | Nada — está no código | Tab Identity do agente mostra o connector como Auth Provider, mostra o binding em Bindings. Operador não precisa abrir o repo |
| **Compliance** | Você prova que o secret não vaza, que o token é descartado, que o refresh segue políticas | Google prova (via SOC/ISO). Você só prova a sua parte (frontend, agente, MCP) |
| **Cert-bound tokens** | Implementação rara e complexa em Python | Default. Token roubado não funciona em outra máquina |
| **Console UI para registro** | Você constrói uma UI admin própria | Console → Agent Registry → click agent → Identity tab → Add auth provider |

### Quando a abordagem DIY ainda faz sentido

- Você está num provedor cloud não-Google
- O IdP é exótico/legacy e não fala OAuth padrão (Connector requer authorization_url + token_url — qualquer OIDC/OAuth 2.0 vale, mas IdPs com fluxos custom não)
- Volume de tokens extremamente alto + custo de chamadas ao connector vira problema (não é o caso comum)

Para o resto, o gerenciado é objetivamente melhor — não há nada que o DIY faça que o gerenciado não faça melhor (e muito que o DIY não consegue ainda que tente).

---

## Como inspecionar o deployment

### Console

| Recurso | URL |
|---------|-----|
| Lista de agentes no Registry | `https://console.cloud.google.com/gemini-enterprise-agent-platform/scale/agentRegistry/locations/<region>/agents?project=<P>` |
| Tab Identity do agente (mostra Auth Providers + Bindings) | `https://console.cloud.google.com/gemini-enterprise-agent-platform/scale/agentRegistry/locations/<region>/agents/<agent_uuid>?project=<P>` (aba **Identity**) |
| Playground do Reasoning Engine | `https://console.cloud.google.com/vertex-ai/agents/agent-engines/locations/<region>/agent-engines/<engine_id>/playground?project=<P>` |
| Cloud Run services | `https://console.cloud.google.com/run?project=<P>` |
| Cloud Logging (filtrar por `resource.type="aiplatform.googleapis.com/ReasoningEngine"`) | `https://console.cloud.google.com/logs/query?project=<P>` |

### gcloud

```bash
# Connector
gcloud alpha agent-identity connectors describe <name> --location=<L> --format=yaml

# MCP server no Registry
gcloud alpha agent-registry mcp-servers list --location=<L> \
  --filter="displayName='<mcp_name>'" --format=yaml

# Bindings
gcloud alpha agent-registry bindings list --location=<L>
gcloud alpha agent-registry bindings describe <binding_name> --location=<L> --format=yaml

# Agent SPIFFE
curl -s "https://<region>-aiplatform.googleapis.com/v1beta1/projects/<N>/locations/<L>/reasoningEngines/<ID>" \
  -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  | jq '.spec.effectiveIdentity, .spec.identityType'
```

### Validação end-to-end (curl)

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
# Esperado: HTTP 200 com tools listadas

# 2. Frontend dispara consent corretamente
curl -X POST "$FRONTEND_URL/chat" -H "Content-Type: application/json" \
  -d '{"message":"olá","session_id":"v1","user_id":"v1"}' | jq
# Esperado: {"needs_auth": true, "auth_uri": "https://<keycloak>/...?scope=openid+profile+email&..."}
# scope= não pode estar vazio
```

---

## Estrutura de arquivos

```
oauth-3lo-keycloak-demo/
├── .env.template          variáveis com defaults + comentários
├── deploy.sh              12 passos idempotentes
├── undeploy.sh            reverte tudo (binding inclusive)
├── README.md              quickstart + walkthrough OAuth
├── ARCHITECTURE.md        este arquivo
├── LESSONS.md             jornada: bugs hits, decisões revertidas
├── DEMO.md                roteiro de demonstração
├── agent/
│   ├── pyproject.toml     google-adk[a2a,agent-identity] + a2a-sdk
│   └── app/
│       ├── agent.py                  root_agent + get_mcp_toolset (sem auth_scheme inline)
│       ├── agent_runtime_app.py      AdkApp + telemetria desabilitada
│       └── app_utils/{telemetry,typing}.py
├── mcp-server/
│   ├── Dockerfile         python:3.12-slim
│   ├── pyproject.toml     fastmcp + PyJWT[crypto]
│   ├── toolspec.json      catálogo das tools no Registry
│   └── app/
│       ├── main.py        FastMCP.http_app(path="/mcp", middleware=[…], stateless_http=True)
│       ├── config.py      KEYCLOAK_URL, REALM, AUDIENCE, JWKS_URL
│       ├── auth.py        verify_keycloak_jwt() via PyJWT + JWKS
│       ├── middleware.py  KeycloakAuthMiddleware → ContextVar
│       └── tools/profile.py  get_my_profile, echo
└── frontend/
    ├── Dockerfile
    ├── pyproject.toml     fastapi + jinja2 + httpx
    └── app/
        ├── main.py        / · /chat · /validateUserId · /resume + middleware canônico
        └── templates/index.html   chat UI + popup.closed polling
```
