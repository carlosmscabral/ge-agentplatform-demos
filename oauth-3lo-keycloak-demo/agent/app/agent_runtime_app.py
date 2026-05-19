import logging
import os
from typing import Any

# NOTE on pyOpenSSL / telemetry interaction (see ARCHITECTURE.md for full story):
# google-adk → google-auth[pyopenssl] pulls pyOpenSSL into the venv, and both
# `requests/__init__.py:138` and `google/auth/transport/requests.py:216` auto-call
# `urllib3.contrib.pyopenssl.inject_into_urllib3()` on import. The resulting
# pyOpenSSL SSL.Context is NOT thread-safe. Under concurrent HTTPS — agent
# calling iamconnectorcredentials WHILE the OTEL exporter ships a span to
# Cloud Trace/Logging — pyOpenSSL raises
#   ValueError: Context has already been used to create a Connection
# which ADK swallows as RuntimeError("Failed to retrieve credential …").
#
# This demo opts OUT of GCP telemetry to remove the concurrent-HTTPS race
# (telemetry isn't the focus here — OAuth is). `setup_telemetry()` honors
# DISABLE_GCP_TELEMETRY=true and returns early.
#
# If you ever need to re-enable telemetry here, you also need to block the
# pyOpenSSL inject — uncomment the two lines below BEFORE any other import:
#   import sys
#   sys.modules["urllib3.contrib.pyopenssl"] = None  # type: ignore[assignment]

import vertexai
from dotenv import load_dotenv
from google.adk.artifacts import GcsArtifactService, InMemoryArtifactService
from google.cloud import logging as google_cloud_logging
from vertexai.agent_engines.templates.adk import AdkApp

from app.agent import app as adk_app
from app.app_utils.telemetry import setup_telemetry
from app.app_utils.typing import Feedback

load_dotenv()


class AgentEngineApp(AdkApp):
    def set_up(self) -> None:
        """Initialize the agent engine app with logging and telemetry."""
        vertexai.init()
        setup_telemetry()
        super().set_up()
        logging.basicConfig(level=logging.INFO)
        logging_client = google_cloud_logging.Client()
        self.logger = logging_client.logger(__name__)
        if gemini_location:
            os.environ["GOOGLE_CLOUD_LOCATION"] = gemini_location

    def register_feedback(self, feedback: dict[str, Any]) -> None:
        feedback_obj = Feedback.model_validate(feedback)
        self.logger.log_struct(feedback_obj.model_dump(), severity="INFO")

    def register_operations(self) -> dict[str, list[str]]:
        operations = super().register_operations()
        operations[""] = [*operations.get("", []), "register_feedback"]
        return operations


gemini_location = os.environ.get("GOOGLE_CLOUD_LOCATION")
logs_bucket_name = os.environ.get("LOGS_BUCKET_NAME")
agent_runtime = AgentEngineApp(
    app=adk_app,
    artifact_service_builder=lambda: (
        GcsArtifactService(bucket_name=logs_bucket_name)
        if logs_bucket_name
        else InMemoryArtifactService()
    ),
)
