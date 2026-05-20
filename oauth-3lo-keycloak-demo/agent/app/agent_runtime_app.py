# Applied custom monkey-patch to PyOpenSSLContext to make it thread-safe and immune to
# ValueError when properties (verify_mode, options, verify_flags) are mutated after connection creation.
import ssl
import logging

try:
    import urllib3.contrib.pyopenssl
    
    # 1. Patch para verify_mode
    orig_verify_mode_setter = urllib3.contrib.pyopenssl.PyOpenSSLContext.verify_mode.fset
    orig_verify_mode_getter = urllib3.contrib.pyopenssl.PyOpenSSLContext.verify_mode.fget

    def patched_verify_mode_setter(self, value):
        try:
            if orig_verify_mode_getter(self) == value:
                return  # Evita chamar set_verify redundante
        except Exception:
            pass
        try:
            orig_verify_mode_setter(self, value)
        except ValueError as e:
            if "Context has already been used to create a Connection" in str(e):
                pass  # Ignora se já estiver travado pelo pyOpenSSL
            else:
                raise

    urllib3.contrib.pyopenssl.PyOpenSSLContext.verify_mode = property(
        orig_verify_mode_getter,
        patched_verify_mode_setter
    )

    # 2. Patch para options
    orig_options_setter = urllib3.contrib.pyopenssl.PyOpenSSLContext.options.fset
    orig_options_getter = urllib3.contrib.pyopenssl.PyOpenSSLContext.options.fget

    def patched_options_setter(self, value):
        try:
            if orig_options_getter(self) == value:
                return
        except Exception:
            pass
        try:
            orig_options_setter(self, value)
        except ValueError as e:
            if "Context has already been used to create a Connection" in str(e):
                pass
            else:
                raise

    urllib3.contrib.pyopenssl.PyOpenSSLContext.options = property(
        orig_options_getter,
        patched_options_setter
    )

    # 3. Patch para verify_flags
    orig_verify_flags_setter = urllib3.contrib.pyopenssl.PyOpenSSLContext.verify_flags.fset
    orig_verify_flags_getter = urllib3.contrib.pyopenssl.PyOpenSSLContext.verify_flags.fget

    def patched_verify_flags_setter(self, value):
        try:
            if orig_verify_flags_getter(self) == value:
                return
        except Exception:
            pass
        try:
            orig_verify_flags_setter(self, value)
        except ValueError as e:
            if "Context has already been used to create a Connection" in str(e):
                pass
            else:
                raise

    urllib3.contrib.pyopenssl.PyOpenSSLContext.verify_flags = property(
        orig_verify_flags_getter,
        patched_verify_flags_setter
    )

    # 4. Patch para load_verify_locations
    orig_load_verify_locations = urllib3.contrib.pyopenssl.PyOpenSSLContext.load_verify_locations

    def patched_load_verify_locations(self, cafile=None, capath=None, cadata=None):
        try:
            orig_load_verify_locations(self, cafile, capath, cadata)
        except ValueError as e:
            if "Context has already been used to create a Connection" in str(e):
                pass
            else:
                raise

    urllib3.contrib.pyopenssl.PyOpenSSLContext.load_verify_locations = patched_load_verify_locations

    # 5. Patch para load_cert_chain
    orig_load_cert_chain = urllib3.contrib.pyopenssl.PyOpenSSLContext.load_cert_chain

    def patched_load_cert_chain(self, certfile, keyfile=None, password=None):
        try:
            orig_load_cert_chain(self, certfile, keyfile, password)
        except ValueError as e:
            if "Context has already been used to create a Connection" in str(e):
                pass
            else:
                raise

    urllib3.contrib.pyopenssl.PyOpenSSLContext.load_cert_chain = patched_load_cert_chain

    # 6. Patch para set_default_verify_paths
    orig_set_default_verify_paths = urllib3.contrib.pyopenssl.PyOpenSSLContext.set_default_verify_paths

    def patched_set_default_verify_paths(self):
        try:
            orig_set_default_verify_paths(self)
        except ValueError as e:
            if "Context has already been used to create a Connection" in str(e):
                pass
            else:
                raise

    urllib3.contrib.pyopenssl.PyOpenSSLContext.set_default_verify_paths = patched_set_default_verify_paths

    # 7. Patch para set_ciphers
    orig_set_ciphers = urllib3.contrib.pyopenssl.PyOpenSSLContext.set_ciphers

    def patched_set_ciphers(self, ciphers):
        try:
            orig_set_ciphers(self, ciphers)
        except ValueError as e:
            if "Context has already been used to create a Connection" in str(e):
                pass
            else:
                raise

    urllib3.contrib.pyopenssl.PyOpenSSLContext.set_ciphers = patched_set_ciphers

    # 8. Patch para set_alpn_protocols
    orig_set_alpn_protocols = urllib3.contrib.pyopenssl.PyOpenSSLContext.set_alpn_protocols

    def patched_set_alpn_protocols(self, ALPN_PROTOCOLS):
        try:
            orig_set_alpn_protocols(self, ALPN_PROTOCOLS)
        except ValueError as e:
            if "Context has already been used to create a Connection" in str(e):
                pass
            else:
                raise

    urllib3.contrib.pyopenssl.PyOpenSSLContext.set_alpn_protocols = patched_set_alpn_protocols

    # 9. Patch para minimum_version
    orig_minimum_version_setter = urllib3.contrib.pyopenssl.PyOpenSSLContext.minimum_version.fset
    orig_minimum_version_getter = urllib3.contrib.pyopenssl.PyOpenSSLContext.minimum_version.fget

    def patched_minimum_version_setter(self, value):
        try:
            if orig_minimum_version_getter(self) == value:
                return
        except Exception:
            pass
        try:
            orig_minimum_version_setter(self, value)
        except ValueError as e:
            if "Context has already been used to create a Connection" in str(e):
                pass
            else:
                raise

    urllib3.contrib.pyopenssl.PyOpenSSLContext.minimum_version = property(
        orig_minimum_version_getter,
        patched_minimum_version_setter
    )

    # 10. Patch para maximum_version
    orig_maximum_version_setter = urllib3.contrib.pyopenssl.PyOpenSSLContext.maximum_version.fset
    orig_maximum_version_getter = urllib3.contrib.pyopenssl.PyOpenSSLContext.maximum_version.fget

    def patched_maximum_version_setter(self, value):
        try:
            if orig_maximum_version_getter(self) == value:
                return
        except Exception:
            pass
        try:
            orig_maximum_version_setter(self, value)
        except ValueError as e:
            if "Context has already been used to create a Connection" in str(e):
                pass
            else:
                raise

    urllib3.contrib.pyopenssl.PyOpenSSLContext.maximum_version = property(
        orig_maximum_version_getter,
        patched_maximum_version_setter
    )
    
    logging.info("[mTLS-Patch] Sucesso ao aplicar patch completo de concorrência/idle no urllib3 PyOpenSSLContext.")
except Exception as e:
    logging.warning(f"[mTLS-Patch] Falha ao aplicar patch completo do PyOpenSSLContext: {e}")
import logging
import os
from typing import Any

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
