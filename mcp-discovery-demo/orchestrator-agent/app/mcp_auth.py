"""Cloud Run auth helper (vestigial in current setup).

Cloud Run does NOT support SPIFFE / Agent Identity today (only Agent Runtime
and Gemini Enterprise do). Two options for service-to-service auth from a
SPIFFE-bound Agent Runtime agent → Cloud Run:

  A) IAM-enforced (`--no-allow-unauthenticated`) — Cloud Run only accepts OIDC
     ID tokens with `aud=<service URL>`. The agent has no GCE metadata server
     to mint such tokens (`google.oauth2.id_token.fetch_id_token()` raises
     "Compute Engine Metadata server unavailable"), and SPIFFE-bound access
     tokens are rejected by Cloud Run IAM (verified empirically: HTTP 401).
  B) `--allow-unauthenticated` — public Cloud Run, auth happens at the app
     layer (or via Agent Gateway, which this demo intentionally skips).

This demo uses option B. The header_provider below sends the SPIFFE-bound
bearer token anyway, so a future FastMCP middleware (or Agent Gateway) can
extract the agent's principal for audit / authorization without redeploying
the agent. Today the token is simply ignored by Cloud Run.

For production-grade SPIFFE end-to-end, migrate the MCPs to GKE with Managed
Workload Identity (Preview), or front them with Agent Gateway.
"""

from __future__ import annotations

import logging
from typing import Callable

import google.auth
import google.auth.transport.requests

logger = logging.getLogger(__name__)

_request = google.auth.transport.requests.Request()
_creds, _ = google.auth.default()


def make_cr_header_provider(audience: str) -> Callable[..., dict[str, str]]:
    """Return a header_provider that includes the SPIFFE-bound bearer token.

    With `--allow-unauthenticated` on Cloud Run this header is ignored by the
    platform's IAM check; it's still useful for application-layer auth and for
    visibility in Cloud Run access logs.
    """

    def provider(_ctx=None) -> dict[str, str]:
        if not _creds.valid:
            _creds.refresh(_request)
        return {"Authorization": f"Bearer {_creds.token}"}

    logger.info("cr_header_provider initialized for audience=%s", audience)
    return provider
