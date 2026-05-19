import logging
import os


def setup_telemetry() -> str | None:
    """Configure OpenTelemetry and GenAI telemetry with GCS upload.

    Honors deploy-time OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT
    (typically EVENT_ONLY per Rule #4). Uses setdefault for everything else
    so user overrides win.

    Set DISABLE_GCP_TELEMETRY=true to opt out entirely. The 3LO demo does
    this to avoid a pyOpenSSL/urllib3 race triggered by concurrent HTTPS
    (background OTEL exporter racing the foreground retrieveCredentials call).
    See ARCHITECTURE.md → 'The pyOpenSSL trap'.
    """
    if os.environ.get("DISABLE_GCP_TELEMETRY", "").lower() == "true":
        logging.info(
            "GCP telemetry disabled via DISABLE_GCP_TELEMETRY — no Cloud Trace, "
            "no Cloud Logging structured logs, no GenAI payload upload. "
            "Stdout logs still flow to Cloud Logging via the Cloud Run capture."
        )
        return None

    os.environ.setdefault("GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY", "true")

    bucket = os.environ.get("LOGS_BUCKET_NAME")
    capture_content = os.environ.get(
        "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", "false"
    )
    if bucket and capture_content != "false":
        logging.info(
            "Prompt-response logging enabled - mode: %s", capture_content
        )
        os.environ.setdefault("OTEL_INSTRUMENTATION_GENAI_UPLOAD_FORMAT", "jsonl")
        os.environ.setdefault("OTEL_INSTRUMENTATION_GENAI_COMPLETION_HOOK", "upload")
        os.environ.setdefault(
            "OTEL_SEMCONV_STABILITY_OPT_IN", "gen_ai_latest_experimental"
        )
        commit_sha = os.environ.get("COMMIT_SHA", "dev")
        os.environ.setdefault(
            "OTEL_RESOURCE_ATTRIBUTES",
            f"service.namespace=oauth-3lo-agent,service.version={commit_sha}",
        )
        path = os.environ.get("GENAI_TELEMETRY_PATH", "completions")
        os.environ.setdefault(
            "OTEL_INSTRUMENTATION_GENAI_UPLOAD_BASE_PATH",
            f"gs://{bucket}/{path}",
        )
    else:
        logging.info(
            "Prompt-response logging disabled "
            "(set LOGS_BUCKET_NAME and OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=EVENT_ONLY to enable)"
        )

    return bucket
