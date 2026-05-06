import os
import google.auth

os.environ.setdefault("GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY", "true")

_, project = google.auth.default()
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", project or "")
