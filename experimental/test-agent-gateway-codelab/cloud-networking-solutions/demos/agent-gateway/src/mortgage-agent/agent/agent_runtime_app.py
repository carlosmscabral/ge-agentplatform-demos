"""Agent Runtime entry point — wraps root_agent in InstrumentedAdkApp.

Used with source_packages deploy: the platform imports this module and
uses `agent_runtime` as the entry point object.

Includes monkey-patch for resource_manager_utils.get_project_id which
crashes behind Agent Gateway (gRPC to Resource Manager is blocked).
"""

import google.cloud.aiplatform.utils.resource_manager_utils as _rm_utils

_original_get_project_id = _rm_utils.get_project_id


def _resilient_get_project_id(project_number, **kwargs):
    try:
        return _original_get_project_id(project_number, **kwargs)
    except Exception:
        return project_number


_rm_utils.get_project_id = _resilient_get_project_id

from google.adk.agents.base_agent import BaseAgent

from agent.agent import root_agent
from agent.otel_setup import InstrumentedAdkApp

agent_runtime = InstrumentedAdkApp(
    agent=root_agent if isinstance(root_agent, BaseAgent) else None,
    app=root_agent if not isinstance(root_agent, BaseAgent) else None,
    enable_tracing=True,
)
