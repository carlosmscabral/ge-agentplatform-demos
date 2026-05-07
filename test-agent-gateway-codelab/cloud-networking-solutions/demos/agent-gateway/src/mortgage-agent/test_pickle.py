
import os
import sys

# Mock env vars needed for build BEFORE importing anything
os.environ["MODEL_NAME"] = "gemini-3.1-flash-lite-preview"
os.environ["MCP_REGISTRY_PROJECT"] = "vibe-cabral"
os.environ["MCP_REGISTRY_LOCATION"] = "us-central1"

import cloudpickle
from agent.agent import root_agent
from agent.otel_setup import InstrumentedAdkApp

app = InstrumentedAdkApp(agent=root_agent, enable_tracing=True)

try:
    print("Attempting to pickle app with cloudpickle...")
    pickled = cloudpickle.dumps(app)
    print(f"Success! Pickled size: {len(pickled)} bytes")
    
    print("Attempting to unpickle app...")
    unpickled = cloudpickle.loads(pickled)
    print("Success! Unpickled app type:", type(unpickled))
    # Check attributes
    print("Unpickled app dir:", [a for a in dir(unpickled) if not a.startswith('_')])
    
    # AdkApp stores the agent in ._agent usually or it's passed to the base Template
    if hasattr(unpickled, 'agent'):
        print("Agent name:", unpickled.agent.name)
    elif hasattr(unpickled, '_agent'):
        print("Agent name (from _agent):", unpickled._agent.name)
    else:
        print("Could not find agent attribute on unpickled app")

except Exception as e:
    print(f"Pickling failed: {e}")
    import traceback
    traceback.print_exc()
