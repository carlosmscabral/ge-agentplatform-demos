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
- **Resultado intermediário**: funcional, mas Console UI mostra Identity tab vazia. Acoplamento alto entre código e config
- **Decisão intermediária**: agente sem `auth_scheme` inline; Agent Registry Binding faz a resolução. Console UI passa a listar tudo
- **Decisão REVERTIDA na rodada 3 (ver abaixo)**: voltamos pra `auth_scheme` inline porque o lookup do Binding em runtime tinha race condition (agent boota antes do binding existir) E não eliminava o problema do `tool_name_prefix` forçado. Binding continua sendo criado por `deploy.sh` pra aparecer na Console; agente apenas não lê

---

## Terceira rodada (simplificação do wiring de tools)

35. ❌ Sintoma persistente: Frontend retornava `(empty response)` consistentemente mesmo com Binding-resolved auth + LazyToolset funcionando. Tornou-se evidente que o problema era **tool name mismatch**, não auth/binding
36. ❌ `AgentRegistry.get_mcp_toolset(mcp_server_name)` **força** `tool_name_prefix=<sanitized displayName>` no toolset (código fonte: `agent_registry.py` linha 315 + 368). DisplayName `oauth-3lo-mcp` → tools viram `oauth_3lo_mcp_get_my_profile`, `oauth_3lo_mcp_echo`. Não existe flag para desabilitar nem keyword arg
37. ❌ Gemini 3 **consistentemente encurta** o nome — vimos LLM emitir `get_my_profile` (bare) em vez de `oauth_3lo_mcp_get_my_profile` (do schema). Função schema enviada pro modelo tem o nome prefixado, mas Gemini prefere a forma semântica natural. Testamos com renomear displayName pra `identity` (prefix vira `identity_`); LLM continuou emitindo `get_my_profile`. **Não há instruction que segure essa preferência do modelo**
38. ✅ Solução: bypassar `get_mcp_toolset` e construir `AgentRegistrySingleMcpToolset` direto com `tool_name_prefix=None`. ~30 linhas de código que replicam internals do upstream
39. ❌ Tentativa de `sys.modules["urllib3.contrib.pyopenssl"] = None` (block agressivo) — quebra mTLS do google-auth: `ModuleNotFoundError: import of urllib3.contrib.pyopenssl halted; None in sys.modules`
40. ❌ Tentativa de stub no `sys.modules` com `inject_into_urllib3 = lambda: None` — quebra mTLS em path diferente: `AttributeError: 'SSLContext' object has no attribute '_ctx'` (google-auth espera pyOpenSSL semantics no SSLContext pra cert-bound token handling)
41. ✅ Aceitar: pyOpenSSL é dep transitiva necessária pra mTLS do Agent Identity. **Não dá pra neutralizar**. Mitigação: manter telemetria OFF (sem fonte concorrente de HTTPS, race do pyOpenSSL raramente dispara) + LazyToolset (defer materialização → ainda menos overlap concorrente em boot)
42. ✅ **Combinação final que funciona**:
    1. `_LazyMcpToolset` defere materialização até primeiro request
    2. Bypass de `get_mcp_toolset` para forçar `tool_name_prefix=None`
    3. `auth_scheme` passado inline (sem binding lookup → sem race binding)
    4. SEM stub de pyOpenSSL (deixa mTLS funcionar normalmente)
    5. Telemetria GCP OFF (`DISABLE_GCP_TELEMETRY=true` + `GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY=False`)
43. ✅ Validação: dois requests consecutivos com user_ids diferentes ambos retornaram `needs_auth=true` com auth_uri Keycloak. Estável

---

## Conclusões da rodada 3

### O `tool_name_prefix` forçado é o maior atrito do Agent Registry pra MCP

Os 3 docs oficiais (`resolve-endpoints-and-build-orchestrators`, `search-agents-and-tools`, `authenticate-toolsets`) mostram o snippet `registry.get_mcp_toolset(mcp_server_name=...)` como canônico, mas **nenhum** menciona o prefix forçado nem fornece workaround. Bug ou feature, a única solução prática é replicar os internals do método e passar `tool_name_prefix=None`.

A demo `mcp-discovery-demo` (no root, não experimental) usa o mesmo bypass. É **o padrão de fato** neste repo apesar de não ser idiomático per docs oficiais.

### Gemini 3 é altamente opinated sobre nomes de tools

Tentar instruir o LLM a usar nomes prefixados foi inútil. Ele consistentemente "encurta" pro nome semântico que reconhece (`get_my_profile`, não `oauth_3lo_mcp_get_my_profile`). A única forma confiável de fazer a tool ser chamável é **dar a ela o nome exato que o LLM vai emitir**. Renomear `displayName` no Registry pra algo mais natural não basta — Gemini prefere encurtar até quando o prefix é curto e semântico.

### Binding tem valor operacional, não funcional

Originalmente quisemos usar `auth_scheme=None` + Binding-resolved auth pelo argumento de decoupling: agente não sabe o connector, Binding diz. Mas:
- Tem race condition com criação do Binding (agente boota antes)
- Não simplifica o código (lazy ainda necessário pelo race)
- Tem o mesmo problema de tool_name_prefix

Voltamos pra `auth_scheme` inline. O Binding continua sendo criado por `deploy.sh` pra **valor operacional**: aparece na Console (tab Identity do agente), audit logs registram, ferramentas externas podem inspecionar. Mas o agente não depende dele em runtime.

### pyOpenSSL é dívida da plataforma — não dá pra neutralizar

Tentamos 3 estratégias diferentes de bloquear pyOpenSSL (null sys.modules, stub completo, stub atributo). Todas quebram caminhos legítimos de mTLS que `google-auth[pyopenssl]` (dep dura de `google-adk`) usa. A única mitigação viável é **reduzir concorrência HTTPS** ao mínimo, principalmente desligando exporters de telemetria. Em produção, ligar telemetria de volta exige aceitar reinícios ocasionais do runner thread (raro mas existe).

---

## Quarta rodada (resiliência do deploy e dilema final pyOpenSSL/mTLS)

44. ❌ `deploy.sh` Step 10 listava `agents` por filter pra resolver `AGENT_URN`, e se o registry ainda não tinha mirrorado o agent (race de propagação), entrava num branch `else` com warning silencioso e **pulava a criação do Binding**. Deploy "succeeded" mas o agente subia sem `(agent, MCP, auth_provider)` registrado → runtime não emitia `adk_request_credential` → popup nunca disparava. Reproduzido limpo: undeploy + deploy → sem binding → falha exatamente nesse modo
45. ✅ Fix: retry loop 10×5s para AGENT_URN e MCP_URN (espelha o pattern do Step 5), `exit 1` ao invés de warning silencioso, e `bindings describe` pós-create como sanity check
46. ❌ `gcloud alpha agent-registry services update` NÃO passa `--display-name` por default — então o `displayName` no mirror em `mcp-servers/` mantém o valor antigo se um deploy anterior usou outro valor. Resultado: `mcp-servers list --filter="displayName=oauth-3lo-mcp"` retorna vazio (porque o mirror tinha `displayName: identity` de uma sessão antiga) → Step 5 falhava com "Could not resolve MCP registry name after 50s"
47. ✅ Fix: `services update` agora passa `--display-name=${MCP_REGISTRY_DISPLAY_NAME}` explicitamente. `undeploy.sh` também passou a procurar o service pelo NOME (`services describe oauth-3lo-mcp`) em vez de filter por displayName, evitando órfãos que driftaram
48. ❌ `bindings update` rejeitando `--auth-provider-binding`: API retorna `Cannot update the 'auth_provider_binding.auth_provider' of a binding` — esse campo é imutável após criação. Causa o segundo deploy a abortar Step 10 antes do IAM update e do Step 11 (frontend redeploy)
49. ✅ Fix: `bindings update` só envia campos mutáveis (`continue_uri`, `scopes`). Para trocar o auth_provider, delete+recreate manual
50. ❌ `gcloud run deploy --source=./frontend` SEM `--set-env-vars` nem `--update-env-vars` **RESETA as variáveis de ambiente** do serviço — quebrou o frontend (`AGENT_ENGINE_ID` virou empty) depois de rebuild de iteração. Cosmético (fácil de notar e restaurar) mas trap
51. ❌ user_id randomizado por aba do browser (`'user-' + Math.random()`) causa um false-positive de "user mismatch": finalize escreve a credencial no vault como `user-XYZ`, /resume chama o agente como `user-XYZ`, agente busca no vault como `user-XYZ` → deveria casar. Mas em alguns casos (provavelmente propagação) `get_auth_credential` retorna como se consent ainda fosse requerido (`_is_consent_completed(context) == True` mas `metadata.uri_consent_required is not None` → `RuntimeError("Failed to retrieve consent based credential.")` na linha 275 do `gcp_auth_provider.py`)
52. ⚠️ Mitigação parcial: frontend usa `userId = 'demo-user'` fixo, simulando "single signed-in user". Elimina a variável user_id como suspeito e reusa entrada quente no vault. Resolve o sintoma da linha 275; NÃO resolve o item 53 abaixo
53. ❌ **DILEMA SEM SOLUÇÃO CODE-LEVEL**: após ~5min de idle do Reasoning Engine, `_retrieve_credentials` falha determinísticamente na chamada HTTPS pra `iamconnectorcredentials.googleapis.com` com `ValueError: Context has already been used to create a Connection, it cannot be mutated again` em `urllib3/contrib/pyopenssl.py:452`. ADK swallow → `RuntimeError("Failed to retrieve credential for user X on connector Y")` (linha 243, não a 275) → stream vazio → popup nunca dispara. Tentamos 4 abordagens DIFERENTES (todas falharam):
    1. Manter telemetria OFF (já estava) — não basta; iamconnectorcredentials sozinho dispara a race
    2. `sys.modules["urllib3.contrib.pyopenssl"] = None` antes de qualquer import — `ModuleNotFoundError` em google-auth's mtls path
    3. `urllib3.contrib.pyopenssl.extract_from_urllib3() + inject_into_urllib3 = lambda: None` — quebra mTLS: `AttributeError: 'SSLContext' object has no attribute '_ctx'` em `google/auth/transport/requests.py:223` (o `_MutualTlsAdapter` do google-auth lê `ctx_poolmanager._ctx.use_certificate(x509)` — assume PyOpenSSLContext que tem `_ctx`; stdlib `ssl.SSLContext` não tem)
    4. Stub no `sys.modules["urllib3.contrib.pyopenssl"]` com `inject_into_urllib3 = lambda: None` mas mantendo o módulo importável — mesmo `_ctx` AttributeError
54. ✅ Reverter: voltar pro estado em que pyOpenSSL fica injetado normalmente. Demo funciona janela de ~5min pós-deploy, depois quebra deterministicamente até próximo redeploy. Sem keep-warm externo, é o melhor que conseguimos chegar code-level

---

## Conclusões da rodada 4

### O problema raiz é acoplamento profundo google-auth → pyOpenSSL

`google/auth/transport/requests.py:223` no `_MutualTlsAdapter.__init__` faz:

```python
ctx_poolmanager._ctx.use_certificate(x509)
```

— ou seja, lê o `_ctx` interno da `PoolManager` da urllib3 assumindo que é uma `PyOpenSSLContext`. Esse atributo `_ctx` é **específico do pyOpenSSL injetado**. Stdlib `ssl.SSLContext` não tem `_ctx`. Então:

- **Com pyOpenSSL injetado**: mTLS funciona, MAS `PyOpenSSLContext` não é thread-safe → race condition `Context has already been used` em concorrência
- **Sem pyOpenSSL injetado (qualquer estratégia)**: mTLS quebra na inicialização do `iamconnectorcredentials` Client

Toda chamada do agente pro `iamconnectorcredentials` precisa mTLS (configurado automaticamente pelo client gRPC/REST gerado). Não dá pra escapar. Logo, **não dá pra escapar do pyOpenSSL injetado**.

A solução real precisa vir upstream — ou em `google-auth` (remover o reach into `_ctx`, usar API agnóstica), ou em `urllib3.contrib.pyopenssl` (sincronizar o `Context.set_verify`).

### Por que o "5min pós-deploy" funciona

Reasoning Engine recém-instanciado: connection pool zerada, SSL contexts frescos, nenhum context ainda foi reusado pra criar Connection. Primeiras N requests passam limpas. Depois de uso, contexts viram "dirty" — qualquer mutação subsequente (incluindo `verify_mode` setter da urllib3, que dispara em todo `_validate_conn`) crasha.

Cloud Run idle CPU throttling probabilísticamente piora — contexts ficam vivos no pool por mais tempo, mais chance de serem reusados num estado dirty.

### Mitigações operacionais que NÃO implementamos aqui

- **Cloud Scheduler keep-warm**: chamar `/chat` (com prompt no-op) a cada 3-4min mantém o pool quente, NÃO resolve a degradação a longo prazo (eventualmente quebra mesmo sob uso ativo, só atrasa)
- **Auto-restart por hora**: redeployar o Reasoning Engine periodicamente. Brusco — sessões em andamento morrem
- **Subclassar `GcpAuthProvider`** com `_retrieve_credentials` que cata `ValueError` e força nova HTTPSConnection: hacky, pode não resolver porque o pool não está sob nosso controle direto

Decisão pra esta demo: aceitar a limitação, documentar, manter em `experimental/`.

### O que esta rodada confirmou (negativos importantes)

- **NÃO é problema de user_id**. Mesmo com `demo-user` fixo, race aparece pós-idle
- **NÃO é problema de binding**. Binding existe e está correto durante a falha
- **NÃO é problema de cache de browser**. Reproduz com curl direto pro frontend
- **NÃO é problema de cookies/Keycloak**. Cookies chegam, finalize succeed, agent recebe consent — race está depois disso, no path interno do ADK pro vault
- **É problema do pyOpenSSL race no path agent → iamconnectorcredentials**, confirmado por stack trace completo capturado em prod

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
