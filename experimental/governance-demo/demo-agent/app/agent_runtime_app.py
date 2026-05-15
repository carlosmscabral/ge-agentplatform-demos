import os
from typing import Optional

from google.adk.agents.base_agent import BaseAgent
from vertexai.agent_engines.templates.adk import AdkApp

# Patch resource_manager_utils.get_project_id to handle Agent Gateway
# network restrictions. When deployed behind a gateway, outbound gRPC to
# Resource Manager is blocked, causing a 60s timeout. The SDK only catches
# PermissionDenied/Unauthenticated, not RetryError/ServiceUnavailable.
import google.cloud.aiplatform.utils.resource_manager_utils as _rm_utils
_original_get_project_id = _rm_utils.get_project_id


def _resilient_get_project_id(project_number, **kwargs):
    try:
        return _original_get_project_id(project_number, **kwargs)
    except Exception:
        return project_number


_rm_utils.get_project_id = _resilient_get_project_id


from app.agent import app as adk_app

agent_runtime = AdkApp(
    agent=adk_app if isinstance(adk_app, BaseAgent) else None,
    app=adk_app if not isinstance(adk_app, BaseAgent) else None,
)
