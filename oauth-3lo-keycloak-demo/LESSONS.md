# LESSONS.md — Jornada, bugs, decisões revertidas

Histórico cronológico do desenvolvimento deste demo. Os bugs aqui são todos *reais* — cada um custou ao menos 10 minutos de diagnóstico. Estão registrados para que ninguém repita.

Para a arquitetura final em si (sem ruído histórico), veja [ARCHITECTURE.md](ARCHITECTURE.md).

---

## Linha do tempo resumida

1. ✅ Skill `fastmcp-builder` aponta o caminho certo: FastMCP 2.x, mas API mudou
2. ❌ `mcp.streamable_http_app()` não existe na 2.x — é `mcp.http_app(path=..., middleware=[...])`
3. ❌ `gcloud alpha agent-registry services create` retorna `services/<name>`, mas ADK quer `mcpServers/agentregistry-<UUID>` — APIs irmãs, recursos diferentes
4. ❌ Toolspec rejeita `tags` em `annotations` (`additionalProperties: false`) — padrão do repo é encode `[tag:X]` na description
5. ❌ Agente importa `agent_registry`, que importa `from a2a.types import ...` — precisa `google-adk[a2a,agent-identity]` + `a2a-sdk` mesmo sem usar A2A
6. ❌ `agents-cli deploy` introspeção quebra com `os.environ["X"]` no module-level — wrap em função e use `os.environ.get("X", "")`
7. ❌ `agents-cli deploy` 0.1.3 lança `AttributeError: 'NoneType' object has no attribute 'name'` no fim. Cosmético — agente está deployado
8. ❌ Agente recebe 401 em `agentregistry.googleapis.com` apesar de IAM correta — faltava env var `GOOGLE_API_PREVENT_AGENT_TOKEN_SHARING_FOR_GCP_SERVICES=False`
9. ❌ Faltava `roles/agentregistry.viewer` na principal set (não bastava `cloudapiregistry.viewer` — APIs irmãs distintas)
10. ❌ Stream retorna HTTP 200 + content-length 0 — `SessionNotFoundError`. ADK não auto-cria sessão via REST; precisa `class_method=create_session` antes
11. ❌ pyOpenSSL monkey-patch em `urllib3` quebra HTTPS concorrente — primeira tentativa: `extract_from_urllib3()` (insuficiente)
12. ❌ pyOpenSSL re-injetado por `requests/__init__.py` e `google.auth.transport.requests` — segunda tentativa: `inject_into_urllib3 = lambda: None` (ainda insuficiente, import order)
13. ❌ Terceira tentativa: `sys.modules["urllib3.contrib.pyopenssl"] = None` antes de qualquer import (funcional, mas frágil)
14. ✅ **Decisão**: desligar telemetria GCP pra evitar o race entirely. Bug pyOpenSSL fica documentado mas inativo
15. ❌ Conector OAuth criado sem `--allowed-scopes` → auth URL com `&scope&` vazio → Keycloak rejeita com `invalid_scope`
16. ❌ Audience mismatch — Keycloak por default emite `aud=["account"]`, não o client_id. Solução: Audience Mapper no client OU `KEYCLOAK_AUDIENCE=account` no MCP
17. ❌ Starlette `Jinja2Templates.TemplateResponse` mudou assinatura na 0.27+ — `request` agora é primeiro arg posicional. `TypeError: unhashable type: 'dict'` no template cache
18. ❌ Connector redirectUrl está nested em `connectorTypeParams.threeLeggedOauth.redirectUrl`, não no top-level
19. ❌ Frontend retorna 400 "User ID is required" — `iamconnectorcredentials/oauthcallback` redireciona com `connector_name` mas NÃO carrega `user_id`. Solução: cookies setados em `/chat`, lidos em `/validateUserId`
20. ❌ Cookies não chegavam — usuário abriu chat em `<service>-<project_number>...` mas continue_uri aponta para `<service>-<hash>...`. Origens diferentes, cookies diferentes. Solução: middleware redireciona pra hostname canônico
21. ✅ **Refator**: agente para usar Agent Registry **Binding** ao invés de `auth_scheme` inline. Console Identity tab passa a listar o connector
22. ❌ Tab Identity do agente lista o binding mas Auth Provider sai "vazio" — Console usa IAM no connector com **principal individual** do agente (não principalSet)
23. ❌ pyOpenSSL race volta com binding-resolved auth — `DISABLE_GCP_TELEMETRY=true` no nosso código não basta, container do Agent Engine liga OTEL via `GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY` (true por default)
24. ✅ **Solução final**: setar `GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY=False` no deploy
25. ❌ `postMessage` do popup → parent não chega — Chrome COOP zera `window.opener` após cadeia cross-origin (Keycloak → Google → frontend). Solução: parent faz polling em `popup.closed`
26. ❌ LLM alucinou nome de tool: chamou `get_user_profile` em vez de `get_my_profile`. Solução: instruction lista nomes exatos com aviso

---

## Segunda rodada (validação de idempotência undeploy/deploy)

27. ❌ Após `undeploy.sh`, segundo `deploy.sh` falhou em Step 5 — `services create` retorna OK mas o recurso espelho em `/mcpServers/` propaga assincronamente. `mcp-servers list` retornou vazio. Solução: loop com retry de até 50s
28. ❌ Connector `delete` é **soft-delete** com retenção de 30 dias (não há flag `--purge`). Após undeploy, `describe` ainda retorna o recurso mas `update` dá `NOT_FOUND` e `create` dá `ALREADY_EXISTS`. Solução: `deploy.sh` chama `connectors undelete` no início do Step 6 — no-op se não estava deletado, restaura se estava
29. ❌ Quando agente bootava antes do binding existir (Step 9 cria agente, Step 10 cria binding), `get_mcp_toolset` resolveu binding como `None` silenciosamente → tool calls sem auth → 401. ADK não logava nem warning sobre o lookup falhar. Solução: `_LazyMcpToolset` (padrão do `mcp-discovery-demo`) defere materialização até primeiro uso, momento em que o binding já existe
30. ❌ MCP server bloqueava `initialize`/`tools/list` sem Bearer — agente faz discovery ANTES de ter token de usuário. Solução: middleware ASGI puro reescrito (não mais `BaseHTTPMiddleware` que quebra SSE) com bypass por método JSON-RPC: `initialize`, `notifications/initialized`, `tools/list`, `prompts/list`, `resources/list`, `resources/templates/list` + GET/DELETE liberados (são session-state, não user data)
31. ❌ `BaseHTTPMiddleware` + body-peek quebra response SSE com `RuntimeError: Unexpected message received: http.request`. Reescrito como ASGI puro com `_replay_receive` + delegate ao receive original quando buffer esgota
32. ❌ Após segundo deploy, agente Engine ID **mudou** (RE recriado). Binding velho apontava para Engine ID antigo (já apagado). `deploy.sh` recria binding com `bindings create` mas se já existe com novo source → `describe` + `update` cobre. ✓
33. ❌ FastMCP com `stateless_http=True` ADK reclama "Missing session ID" — voltei para `stateless_http=False` (stateful). Trade-off: em prod com múltiplas instâncias Cloud Run, sticky session ou external session store (Redis) necessário
34. ✅ **Confirmado nas docs REST**: `ReasoningEngineSpec.deploymentSpec` e `deployment_source` são **Optional**. Você PODE criar um RE vazio (sem código) só pra provisionar SPIFFE, depois `PATCH` com o código. Mas `agents-cli` não expõe esse two-step — usar REST direto. **Não adotamos** pra esta demo porque LazyToolset é mais simples e idiomático no repo

---

## Decisões revertidas (segunda rodada)

### "Cookie SameSite=Lax basta para popup flow"
- **Sintoma**: Após undeploy/deploy, cookies não chegavam ao `/validateUserId` mesmo com middleware canonical-host correto
- **Causa real**: usuário abriu chat em URL hash variável (Cloud Run gera DUAS URLs por serviço); cookies eram setados em um host, callback ia para outro
- **Solução**: middleware canonical-host redireciona 307 → `CANONICAL_URL` independente do host de entrada

### "stateless_http=True para Cloud Run multi-instance"
- **Tentativa**: setei `stateless_http=True` no FastMCP achando que era ideal para Cloud Run escalado
- **Resultado**: ADK reclama "Missing session ID" — ADK espera stateful
- **Decisão final**: stateless_http=False. Para Cloud Run multi-instance em prod, externalize sessão via Redis/Memorystore

---

## Detalhes dos bugs mais custosos

### Bug #11–14: pyOpenSSL + concurrent HTTPS

**Sintoma**: `/streamQuery` retorna HTTP 200 com body vazio. Logs do agente mostram `RuntimeError("Failed to retrieve credential for user X on connector Y")`, mesmo com IAM impecável e `curl` direto contra `iamconnectorcredentials.../credentials:retrieve` funcionando.

**Causa raíz** (descoberta no 4º round de debug):
1. `google-adk` declara `google-auth[pyopenssl]>=2.47` como dep dura
2. `pyOpenSSL` é importado no venv
3. `requests/__init__.py:138` e `google.auth.transport.requests.py:216` AMBOS chamam `urllib3.contrib.pyopenssl.inject_into_urllib3()` em import time se pyOpenSSL estiver importável
4. Isso faz urllib3 rotear TODA conexão HTTPS via `pyOpenSSL.SSL.Context`, que **não é thread-safe**
5. Sob carga concorrente — agente chamando `iamconnectorcredentials` ENQUANTO o exporter OTEL manda spans pro Cloud Trace — pyOpenSSL levanta `ValueError: Context has already been used to create a Connection, it cannot be mutated again` no meio do handshake
6. ADK captura a exception e relança como o opaco `RuntimeError("Failed to retrieve credential …")` 
7. Thread do runner morre antes de emitir qualquer evento → stream vazio

**Tentativas que falharam**:
- `extract_from_urllib3()` no agent_runtime_app.py — pyOpenSSL re-injetado depois por libs importadas em ordem indeterminada
- `inject_into_urllib3 = lambda: None` monkey-patch — `requests` já tinha rodado o inject ANTES do meu código importar
- `sys.modules["urllib3.contrib.pyopenssl"] = None` antes de qualquer import — funcionou mas frágil, dependente de import order de cada container

**Solução adotada**: desligar telemetria GCP (`DISABLE_GCP_TELEMETRY=true` + `GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY=False`). Sem o exporter OTEL como fonte de HTTPS concorrente, o race nunca acontece. Trade-off: sem Cloud Trace / sem structured Cloud Logging. Aceitável neste demo (foco é OAuth, não observabilidade). Quem ligar telemetria de volta precisa também aplicar o `sys.modules` block — comentado em `agent/app/agent_runtime_app.py` como referência.

### Bug #19–20: cookies cross-domain + canonical host

**Sintoma 1**: Após login Keycloak completar com sucesso, frontend mostra `Finalize failed (400) "User ID is required."`.

**Sintoma 2**: Mesmo após cookies serem setados, `/validateUserId` reportava `Missing user_id cookie`.

**Causa raíz #1**: Google's `iamconnectorcredentials/oauthcallback` redireciona pro `continue_uri` com apenas `user_id_validation_state` e `connector_name`. O `user_id` (que o agente passou no `retrieveCredentials`) NÃO é carregado — OAuth padrão não tem conceito de user_id interno. Frontend precisa passar `user_id` via cookies (padrão usado pelo sample `contributing/samples/gcp_auth` do adk-python).

**Causa raíz #2**: Cloud Run dá DOIS hostnames pra cada service:
- `<service>-<project_number>.<region>.run.app` (impresso por `gcloud run deploy`)
- `<service>-<hash>-<region_short>.a.run.app` (retornado por `services describe --format='value(status.url)'`)

Ambos atingem o mesmo container, mas o browser os trata como origens diferentes para fins de cookie. O usuário abriu o chat no hostname A → cookies setados em A. O `continue_uri` (registrado no binding) apontava pro hostname B → `/validateUserId` chamado em B → cookies de A não foram enviados → 400.

**Solução**: middleware Starlette no frontend redireciona 307 toda request que chegar pelo hostname não-canônico para o canônico (definido via env var `CANONICAL_URL`, setada pelo deploy.sh com o output de `services describe`). Independente de qual URL o usuário abrir, ele acaba no canônico.

### Bug #21–22: Binding visível mas Auth Provider vazio na Console

**Sintoma**: Após criar o Binding com `gcloud alpha agent-registry bindings create`, a tab Identity do agente na Console mostra a Binding listada, mas o campo Auth Provider sai em branco.

**Causa raíz**: Console mostra dois conceitos separados:
- **Bindings** — listados a partir do recurso `agentregistry.bindings`
- **Auth Providers** — listados a partir de quem tem `roles/iamconnectors.user` no **resource connector** com o **principal individual** do agente

O `deploy.sh` original concedia `roles/iamconnectors.user` ao **principalSet** (cobertura coletiva de todos os agentes do projeto). Funcionalmente isso já permite o agente usar o connector. Mas a Console filtra por principal INDIVIDUAL para popular a tab Identity → connector "invisível".

**Solução**: `deploy.sh` agora concede `roles/iamconnectors.user` no connector também ao principal individual do agente (`principal://...reasoningEngines/<ENGINE_ID>`) via `setIamPolicy` direto na API `iamconnectors.googleapis.com`.

### Bug #25: postMessage cross-origin morto pela COOP

**Sintoma**: Após popup fechar, parent não recebe o `postMessage` e nunca chama `/resume`.

**Causa raíz**: Chrome implementa Cross-Origin-Opener-Policy (COOP) por default. Quando um popup navega através de cadeia cross-origin (frontend → Keycloak → Google → frontend), o browser **zera o `window.opener` reference** ao retornar para o frontend. Mesmo que o popup chame `window.opener.postMessage(...)`, `window.opener` é null e a chamada falha silenciosamente.

**Solução**: parent não espera por `postMessage` — ele faz polling em `popup.closed` (uma das poucas propriedades cross-origin-readable). Quando o popup fecha (via `window.close()` no `/validateUserId` retornado), o polling detecta e dispara `/resume`. `postMessage` ainda é chamado pelo popup como backup, mas não é o mecanismo primário.

### Bug #26: LLM alucinou nome de tool

**Sintoma**: Após consent, agente retorna `(empty response)`. Logs: `ValueError: Tool 'get_user_profile' not found.`

**Causa raíz**: O LLM (Gemini) chamou `get_user_profile` mas a tool real é `get_my_profile`. O prior do modelo sobre "ferramentas que retornam perfil" é "get_user_profile" — quando a instrução é vaga, o modelo prefere o nome canônico do seu treinamento ao nome literal exposto.

**Solução**: instruction do agente lista nomes exatos com aviso `"use EXATAMENTE esses nomes — não invente, não traduza, não altere case"`. Em produção, valeria também renomear a tool para algo que case com priors do modelo (`get_user_profile`) — mas pra demo, instruction explícita basta.

---

## Decisões revertidas

### "Tags via toolspec annotations"
- **Tentativa**: adicionar `"tags": [...]` no `annotations` de cada tool no `toolspec.json`
- **Resultado**: API rejeitou — schema MCP tem `additionalProperties: false` em annotations
- **Decisão final**: encode no `--description` do `agent-registry services` como `[tag:identity] [domain:auth-demo] ...`. Padrão usado também no `mcp-discovery-demo`

### "SPIFFE no Cloud Run do MCP"
- **Tentativa inicial**: usuário sugeriu dar SPIFFE identity também ao Cloud Run do MCP server
- **Resultado**: SPIFFE só está disponível pra Agent Runtime (flag `--agent-identity`). Cloud Run usa GCP service accounts. Confirmado nas docs e no `mcp-discovery-demo` que também só dá SPIFFE ao agente
- **Decisão final**: MCP server usa SA default do Cloud Run; identidade é controlada na borda via JWT Keycloak

### "Manter telemetria + sys.modules block"
- **Tentativa**: manter Cloud Trace ativo e bloquear pyOpenSSL via `sys.modules["urllib3.contrib.pyopenssl"] = None`
- **Resultado**: funcionou intermitentemente, dependente de import order do container Agent Engine
- **Decisão final**: desligar telemetria. Foco do demo é OAuth, não observabilidade. Bug + solução documentados pra quem quiser reativar

### "Agent code com auth_scheme inline"
- **Tentativa**: agente declarava `GcpAuthProviderScheme(name=..., continue_uri=...)` direto no código
- **Resultado**: funcional, mas Console UI mostra Identity tab vazia. Acoplamento alto entre código e config
- **Decisão final**: agente sem `auth_scheme` inline; Agent Registry Binding faz a resolução. Console UI passa a listar tudo

---

## Coisas que dariam pra melhorar (não feitas neste demo)

- **`_LazyToolset`** como no `mcp-discovery-demo` — defere construção do toolset até primeiro uso, evitando que healthcheck do Agent Runtime dependa do Registry estar acessível em import time
- **Audience Mapper obrigatório no `.env.template`** — explicar que `KEYCLOAK_AUDIENCE=account` é fallback de debug, prod deve usar mapper
- **Renomear tool `get_my_profile` → `get_user_profile`** — alinha com priors do LLM, reduz instrução explícita
- **WIF para SPIFFE no Cloud Run do MCP** — overkill pra demo mas seria o padrão "puro" se viesse necessidade futura
- **Re-ligar telemetria com sys.modules block** — para validar em prod que esse trade-off é mesmo necessário, ou se já foi corrigido em versão mais nova do `google-adk` / `pyOpenSSL`
- **Refresh token explicitamente testado** — fluxo #3 do ARCHITECTURE não foi exercitado end-to-end; assumido funcional
- **`iamconnectors.editor` em SA dedicada do frontend** — hoje frontend roda como Compute Engine default SA (que tem via Owner). Em prod, criar SA com escopo mínimo

Cada item acima vale uma linha extra de TODO mas não bloqueia o uso do demo como está.
