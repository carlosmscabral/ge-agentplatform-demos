import os

from google.adk.agents import Agent
from google.adk.app import App


root_agent = Agent(
    name="demo_agent",
    model=os.environ.get("GEMINI_MODEL", "gemini-3-flash-preview"),
    instruction="You are a helpful assistant.",
    tools=[],
)

app = App(
    root_agent=root_agent,
    name="app",
)
