"""Minimal FastAPI frontend driving the Agent Identity 3LO consent handshake.

Adapted from the contributing/samples/gcp_auth reference in adk-python:
https://github.com/google/adk-python/tree/main/contributing/samples/gcp_auth

The ADK runtime emits an `adk_request_credential` function call when the
agent needs a user-scoped token. This frontend:

  1. POST /chat   — forwards the user prompt to the deployed Agent Runtime,
                    inspects the streamed events, and if an
                    `adk_request_credential` event appears returns the
                    auth_uri + function_call_id + auth_config to the browser.
  2. /validateUserId — the OAuth redirect_uri that Agent Identity Connector
                    points to. Receives `user_id_validation_state` +
                    `auth_provider_name`, POSTs to
                    iamconnectorcredentials.credentials:finalize, then
                    serves an HTML page that closes the popup.
  3. POST /resume — once the popup closes, the browser calls this to send
                    the auth_config back to the agent as a
                    `function_response(name=adk_request_credential)`,
                    resuming the conversation and producing the final
                    tool-call result.

Env vars (set by deploy.sh):
  PROJECT_ID
  REGION
  AGENT_ENGINE_ID                 — empty on first deploy stub
  AUTH_PROVIDER_FULL_NAME         — projects/<id>/locations/<loc>/connectors/<n>
"""

import logging
import os
from typing import Any

import google.auth
import google.auth.transport.requests
import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("oauth-3lo-frontend")

PROJECT_ID = os.environ.get("PROJECT_ID", "")
REGION = os.environ.get("REGION", "us-central1")
AGENT_ENGINE_ID = os.environ.get("AGENT_ENGINE_ID", "")
AUTH_PROVIDER_FULL_NAME = os.environ.get("AUTH_PROVIDER_FULL_NAME", "")
# Set by deploy.sh from `gcloud run services describe --format='value(status.url)'`.
# Cloud Run gives TWO URLs per service (project-number form + hash form). Cookies
# are per-origin, so we must canonicalize on ONE — the same one used by the
# binding's continue_uri. Mismatch = cookies set on host A, /validateUserId
# called on host B, "Missing user_id cookie" failure.
CANONICAL_URL = os.environ.get("CANONICAL_URL", "")

app = FastAPI(title="OAuth 3LO Keycloak demo — frontend")


@app.middleware("http")
async def canonical_host_redirect(request: Request, call_next):
    """Redirect to CANONICAL_URL if request lands on the secondary Cloud Run URL.

    Cookies the demo sets in /chat must reach /validateUserId after the
    Google → Keycloak → Google round-trip. Cookies are per-origin, so chat
    and validateUserId MUST be served by the same hostname. Cloud Run gives
    `<service>-<project_number>.<region>.run.app` AND
    `<service>-<hash>-<region_short>.a.run.app` — both reach the same service
    but are different cookie scopes. This middleware forces every request onto
    CANONICAL_URL.
    """
    if not CANONICAL_URL:
        return await call_next(request)
    # Cloud Run terminates HTTPS at the front-door; inside the container the
    # scheme is http. Compare HOSTS only (the cookie scoping concern).
    canon_host = CANONICAL_URL.rstrip("/").split("://", 1)[-1]
    incoming_host = request.url.netloc
    if incoming_host != canon_host:
        from starlette.responses import RedirectResponse
        target = CANONICAL_URL.rstrip("/") + request.url.path
        if request.url.query:
            target += f"?{request.url.query}"
        return RedirectResponse(url=target, status_code=307)
    return await call_next(request)
templates = Jinja2Templates(
    directory=os.path.join(os.path.dirname(__file__), "templates")
)


# ─── Helpers ────────────────────────────────────────────────────────────────


def _gcp_token() -> str:
    """Return a fresh GCP access token for the frontend's service account."""
    creds, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    creds.refresh(google.auth.transport.requests.Request())
    return creds.token


def _agent_engine_url() -> str:
    if not AGENT_ENGINE_ID:
        raise HTTPException(status_code=503, detail="AGENT_ENGINE_ID not configured yet")
    return (
        f"https://{REGION}-aiplatform.googleapis.com/v1beta1/"
        f"projects/{PROJECT_ID}/locations/{REGION}/reasoningEngines/{AGENT_ENGINE_ID}"
    )


def _extract_auth_request(events: list[dict]) -> dict | None:
    """Find an `adk_request_credential` function call in a stream of events.

    Returns {auth_uri, function_call_id, auth_config} or None.
    """
    for event in events:
        content = event.get("content", {})
        for part in content.get("parts", []) or []:
            fc = part.get("function_call") or part.get("functionCall")
            if not fc:
                continue
            if fc.get("name") != "adk_request_credential":
                continue
            args = fc.get("args", {}) or {}
            auth_config = args.get("auth_config") or args.get("authConfig") or {}
            exchanged = auth_config.get("exchanged_auth_credential") or auth_config.get(
                "exchangedAuthCredential"
            ) or {}
            oauth2 = exchanged.get("oauth2") or {}
            auth_uri = oauth2.get("auth_uri") or oauth2.get("authUri")
            consent_nonce = (
                oauth2.get("consent_nonce")
                or oauth2.get("consentNonce")
                or oauth2.get("nonce")
                or ""
            )
            if auth_uri:
                return {
                    "auth_uri": auth_uri,
                    "function_call_id": fc.get("id"),
                    "auth_config": auth_config,
                    "consent_nonce": consent_nonce,
                }
    return None


def _extract_final_text(events: list[dict]) -> str:
    """Concatenate any model text outputs from a stream of events."""
    chunks: list[str] = []
    for event in events:
        for part in (event.get("content", {}).get("parts") or []):
            text = part.get("text")
            if text:
                chunks.append(text)
    return "".join(chunks).strip()


# In-process cache of (user_id, session_id) pairs we've already created on the
# agent. Cloud Run instances may be recycled — that's fine; we'll just hit
# create_session again on next request and it will re-create.
_created_sessions: set[tuple[str, str]] = set()


async def _ensure_session(user_id: str, session_id: str) -> None:
    """Create the ADK session if we haven't already. Idempotent enough for a demo."""
    if (user_id, session_id) in _created_sessions:
        return
    url = _agent_engine_url() + ":query"
    headers = {
        "Authorization": f"Bearer {_gcp_token()}",
        "Content-Type": "application/json",
    }
    payload = {
        "class_method": "create_session",
        "input": {"user_id": user_id, "session_id": session_id},
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, headers=headers, json=payload)
        if resp.status_code >= 400:
            # If it already exists Agent Runtime returns 400; treat as success.
            body = resp.text[:200]
            if "already" in body.lower() or "exists" in body.lower():
                logger.info("Session %s/%s already exists", user_id, session_id)
            else:
                logger.warning(
                    "create_session %s/%s returned %d: %s",
                    user_id, session_id, resp.status_code, body,
                )
                resp.raise_for_status()
    _created_sessions.add((user_id, session_id))


async def _stream_agent(
    payload: dict[str, Any],
    *,
    ensure_session_for: tuple[str, str] | None = None,
) -> list[dict]:
    """POST to streamQuery and collect events as a list of dicts."""
    if ensure_session_for is not None:
        await _ensure_session(*ensure_session_for)

    url = _agent_engine_url() + ":streamQuery?alt=sse"
    headers = {
        "Authorization": f"Bearer {_gcp_token()}",
        "Content-Type": "application/json",
    }
    events: list[dict] = []
    async with httpx.AsyncClient(timeout=120) as client:
        async with client.stream("POST", url, headers=headers, json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                line = line.strip()
                if not line or line.startswith(":"):
                    continue
                if line.startswith("data:"):
                    line = line[len("data:") :].strip()
                if not line or line == "[DONE]":
                    continue
                try:
                    import json
                    events.append(json.loads(line))
                except Exception:
                    logger.warning("Skipped non-JSON SSE line: %r", line[:200])
    return events


# ─── Routes ─────────────────────────────────────────────────────────────────


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> Any:
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "agent_configured": bool(AGENT_ENGINE_ID),
            "project_id": PROJECT_ID,
            "region": REGION,
            "auth_provider_short": AUTH_PROVIDER_FULL_NAME.split("/")[-1] if AUTH_PROVIDER_FULL_NAME else "(not set)",
        },
    )


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "agent_configured": bool(AGENT_ENGINE_ID)}


class ChatRequest(BaseModel):
    message: str
    session_id: str
    user_id: str


@app.post("/chat")
async def chat(req: ChatRequest) -> JSONResponse:
    """Forward user prompt to the agent and surface either text or an auth request.

    When the agent emits `adk_request_credential`, we set TWO cookies that the
    later `/validateUserId` callback (called by Google after Keycloak login)
    needs to construct the `credentials:finalize` request body:

      - `user_id`        — passed through to `iamconnectorcredentials.finalize`
      - `consent_nonce`  — bound to the specific consent attempt; comes from
                           the auth_config returned by the agent

    The Google redirect back to `/validateUserId` only carries
    `user_id_validation_state` and `connector_name` as query parameters — it
    does NOT carry `user_id`. Cookies bridge that gap on the SAME-ORIGIN
    request (the redirect lands back on this frontend's domain, so the cookies
    are sent automatically).
    """
    payload = {
        "class_method": "stream_query",
        "input": {
            "user_id": req.user_id,
            "session_id": req.session_id,
            "message": req.message,
        },
    }
    events = await _stream_agent(payload, ensure_session_for=(req.user_id, req.session_id))

    auth_req = _extract_auth_request(events)
    if auth_req:
        logger.info(
            "Agent requested user credential (function_call_id=%s, has_nonce=%s)",
            auth_req["function_call_id"], bool(auth_req.get("consent_nonce")),
        )
        resp = JSONResponse({"needs_auth": True, **auth_req})
        # Cookies must reach /validateUserId after the redirect. SameSite=lax
        # so they survive the cross-site redirect (Keycloak → Google → us).
        # Secure=True because Cloud Run is always HTTPS.
        resp.set_cookie(
            "user_id", req.user_id,
            max_age=600, httponly=True, secure=True, samesite="lax", path="/",
        )
        if auth_req.get("consent_nonce"):
            resp.set_cookie(
                "consent_nonce", auth_req["consent_nonce"],
                max_age=600, httponly=True, secure=True, samesite="lax", path="/",
            )
        return resp

    return JSONResponse({
        "needs_auth": False,
        "text": _extract_final_text(events) or "(empty response)",
    })


class ResumeRequest(BaseModel):
    session_id: str
    user_id: str
    function_call_id: str
    auth_config: dict


@app.post("/resume")
async def resume(req: ResumeRequest) -> JSONResponse:
    """Send the function_response(name=adk_request_credential) back to the agent."""
    payload = {
        "class_method": "stream_query",
        "input": {
            "user_id": req.user_id,
            "session_id": req.session_id,
            "message": {
                "parts": [{
                    "function_response": {
                        "id": req.function_call_id,
                        "name": "adk_request_credential",
                        "response": req.auth_config,
                    }
                }],
            },
        },
    }
    events = await _stream_agent(payload, ensure_session_for=(req.user_id, req.session_id))

    # If the agent re-requests auth (e.g., consent failed), surface it again.
    auth_req = _extract_auth_request(events)
    if auth_req:
        return JSONResponse({"needs_auth": True, **auth_req})

    return JSONResponse({
        "needs_auth": False,
        "text": _extract_final_text(events) or "(empty response after consent)",
    })


@app.get("/validateUserId", response_class=HTMLResponse)
async def validate_user_id(request: Request) -> Any:
    """OAuth redirect_uri target — finalizes the credential then closes the popup.

    Google's iamconnectorcredentials oauthcallback redirects here with TWO
    query params:
      - `user_id_validation_state` — state to verify the request
      - `connector_name`           — full resource name of the connector

    `user_id` and `consent_nonce` come from cookies set by /chat (see chat()
    for why). The redirect itself does NOT carry user_id — the docs sample at
    adk-python contributing/samples/gcp_auth uses the same cookie pattern.
    """
    user_id_validation_state = request.query_params.get("user_id_validation_state")
    # Google sends `connector_name`; older docs/code use `auth_provider_name`. Accept both.
    connector_name = (
        request.query_params.get("connector_name")
        or request.query_params.get("auth_provider_name")
        or AUTH_PROVIDER_FULL_NAME
    )
    user_id = request.cookies.get("user_id") or request.query_params.get("user_id", "")
    consent_nonce = (
        request.cookies.get("consent_nonce")
        or request.query_params.get("consent_nonce")
        or request.query_params.get("nonce", "")
    )

    if not user_id_validation_state or not connector_name:
        raise HTTPException(
            status_code=400,
            detail="Missing user_id_validation_state or connector_name",
        )
    if not user_id:
        return HTMLResponse(
            "<h2>Missing user_id cookie</h2>"
            "<p>Open the chat (<code>/</code>) and submit a prompt first so the cookie is set, then retry.</p>",
            status_code=400,
        )

    finalize_url = (
        f"https://iamconnectorcredentials.googleapis.com/v1alpha/"
        f"{connector_name}/credentials:finalize"
    )
    body = {
        "userId": user_id,
        "userIdValidationState": user_id_validation_state,
        "consentNonce": consent_nonce,
    }
    headers = {
        "Authorization": f"Bearer {_gcp_token()}",
        "x-goog-user-project": PROJECT_ID,
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(finalize_url, headers=headers, json=body)
    if resp.status_code >= 400:
        logger.error("finalize failed: %s %s", resp.status_code, resp.text)
        return HTMLResponse(
            f"<h2>Finalize failed ({resp.status_code})</h2><pre>{resp.text}</pre>",
            status_code=resp.status_code,
        )

    logger.info("Finalized credential for user_id=%s on %s", user_id, connector_name)
    # Close the popup and notify the opener so it can call /resume.
    return HTMLResponse(
        """
<!doctype html><html><body>
<h3>Consent recorded ✔</h3>
<p>You can close this window.</p>
<script>
  try { window.opener && window.opener.postMessage({type: 'oauth-3lo-consent-done'}, '*'); } catch (e) {}
  window.close();
</script>
</body></html>
"""
    )
