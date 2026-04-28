# Observability Troubleshooting & Fix Plan

## Goal
Enable Cloud Trace spans and Prompt-Response Logging (to GCS) for the ADK Agent running in Agent Runtime, resolving the missing instrumentation warnings and disabled logging notifications seen in the logs.

## Diagnosis
Based on the Agent Runtime logs and ADK Observability guidelines:
1.  **Missing OTel Instrumentation:** The agent attempts to initialize OpenTelemetry but lacks the required underlying library instrumentations (`fastapi`, `grpc`, `httpx`). This prevents traces from being fully captured and exported to Cloud Trace.
2.  **Prompt-Response Logging Disabled:** ADK requires `LOGS_BUCKET_NAME` to be set to a valid GCS bucket and `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT` to be defined in order to capture and export LLM prompt/response data.
3.  **Permissions:** The Reasoning Engine Service Account needs explicit permissions (`roles/storage.objectAdmin` or `roles/storage.objectCreator`) to write logs to the specified GCS bucket.

## Implementation Steps

### 1. Fix Telemetry Dependencies
Update `deploy.sh` (specifically the embedded `deploy_agent.py` script) to include the missing OpenTelemetry libraries in the `requirements` array for `ReasoningEngine.create`:
*   `opentelemetry-instrumentation-fastapi>=0.46b0`
*   `opentelemetry-instrumentation-grpc>=0.46b0`
*   `opentelemetry-instrumentation-httpx>=0.46b0`

### 2. Enable Prompt-Response Logging (Environment Variables)
Modify `deploy.sh` to inject the required environment variables into the Agent Runtime environment.
*   Since `ReasoningEngine.create` doesn't natively expose an `env_vars` parameter in all SDK versions, we will inject these variables directly into `demo-agent/app/agent_runtime_app.py` or write them to a `.env` file that is packaged and loaded at runtime via `load_dotenv()`.
*   Variables to set:
    *   `LOGS_BUCKET_NAME="${STAGING_BUCKET}"` (we will reuse the staging bucket for simplicity, or create a dedicated logs bucket).
    *   `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT="true"` (to capture full message content, rather than just metadata).

### 3. Apply Required IAM Permissions
Update `deploy.sh` to grant the Reasoning Engine Service Account the necessary permissions to write to the logs bucket:
*   Grant `roles/storage.objectAdmin` on the specific `LOGS_BUCKET_NAME` bucket.
*   *(Note: The service account already has `roles/cloudtrace.agent` applied in the current script).*

### 4. Redeploy and Verify
*   Execute `./deploy.sh` to rebuild and deploy the Agent Runtime instance.
*   Invoke the agent.
*   Verify in Google Cloud Console:
    *   **Trace Explorer:** Look for new trace spans (`execute_tool`, `call_llm`).
    *   **Cloud Storage:** Check the logs bucket for `.jsonl` prompt-response export files.
