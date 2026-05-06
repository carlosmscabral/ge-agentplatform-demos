import os
from typing import Optional

import google.cloud.logging as google_cloud_logging
import vertexai
from google.adk.agents.base_agent import BaseAgent
from vertexai.agent_engines.templates.adk import AdkApp

from app.app_utils.telemetry import setup_telemetry
from app.agent import app as adk_app


class AgentEngineApp(AdkApp):
    def set_up(self) -> None:
        vertexai.init()
        setup_telemetry()
        super().set_up()
        logging_client = google_cloud_logging.Client()
        self.logger = logging_client.logger(__name__)


agent_runtime = AgentEngineApp(
    agent=adk_app if isinstance(adk_app, BaseAgent) else None,
    app=adk_app if not isinstance(adk_app, BaseAgent) else None,
)
