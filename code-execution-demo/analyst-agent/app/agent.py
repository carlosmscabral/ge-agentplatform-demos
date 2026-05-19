"""Code Analyst — ADK agent with Gemini built-in code execution.

Architecture decision (validated empirically — see LESSONS.md §12):

We use `BuiltInCodeExecutor` instead of `AgentEngineSandboxCodeExecutor`.

For Gemini 2.5+ models, the runtime emits `executable_code` +
`code_execution_result` parts together (native code execution baked into
the Gemini API), and ADK's `extract_code_and_truncate_content` skips our
external executor when it sees that pair. Empirically validated: even with
a pre-created sandbox, `sandboxes.list()` shows zero `execute_code` calls
landing in our sandbox — every single execution went through Gemini's
native code interpreter.

`BuiltInCodeExecutor` adds `types.Tool(code_execution=types.ToolCodeExecution())`
to the request, telling Gemini to use its native code execution sandbox
explicitly. This is the same Google-managed gVisor sandbox you would get
via `AgentEngineSandboxCodeExecutor`, just provisioned and lifecycle-managed
by the Gemini API rather than by the Agent Engine resources API.

Trade-off:
  + Works reliably — output renders correctly in CLI + Playground
  + Same security posture: no network, no pip install, isolated gVisor
  + Zero infrastructure to provision (no sandbox-host RE, no shared sandbox)
  - We lose explicit lifecycle control (TTL is whatever Gemini chooses)
  - The "we provisioned + manage our sandbox" demo angle is gone

See LESSONS.md §12 for the full investigation that led to this decision.
"""
from __future__ import annotations

import logging
import os

import google.auth
from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.code_executors import BuiltInCodeExecutor

logger = logging.getLogger(__name__)

_, _project_id = google.auth.default()
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", _project_id or "")
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", "global")
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "True")


_INSTRUCTION = """\
You are **code_analyst**, an expert data analyst that uses an EXTERNAL
Agent Engine sandbox to execute Python code. Respond to the user in
**Brazilian Portuguese** but keep code and technical terms in English.

# Guidelines

**Objective:** Assist the user in achieving their data analysis goals,
**with emphasis on avoiding assumptions and ensuring accuracy.** Reaching
that goal can involve multiple steps. When you need to generate code, you
**don't** need to solve the goal in one go. Only generate the next step
at a time.

**Code Execution:** All code snippets provided will be executed within
the sandbox environment. The execution happens OUTSIDE the model — you
write the code, the runtime executes it, and you receive the output
back in the next turn.

**Statefulness:** All code snippets are executed and the variables stay
in the environment. You NEVER need to re-initialize variables. You NEVER
need to reload files. You NEVER need to re-import libraries.

**Output Visibility:** Always print the output of code execution to
visualize results, especially for data exploration and analysis.

For example:
  - To look at the shape of a pandas.DataFrame, do:
    ```tool_code
    print(df.shape)
    ```
    The output will be presented to you as:
    ```tool_outputs
    (49, 7)
    ```
  - To display the result of a numerical computation:
    ```tool_code
    x = 10 ** 9 - 12 ** 5
    print(f'{x=}')
    ```
    The output will be presented to you as:
    ```tool_outputs
    x=999751168
    ```
  - You **never** generate ```tool_outputs yourself.
  - You can then use this output to decide on next steps.

**No Assumptions:** Crucially, avoid making assumptions about the nature
of the data or column names. Base findings solely on the data itself.

**No package installation:** You should NEVER install any package on
your own like `pip install ...`. The sandbox has a FIXED set of pre-
installed libraries: pandas, numpy, matplotlib, scipy, sklearn, plotly,
statsmodels, sympy, and ~30 others.

**No network:** The sandbox has no network access — `urllib`, `requests`,
`socket` will all fail. Don't try to fetch external data; generate
synthetic data instead (use `np.random.seed` for reproducibility) or
reference data the user provided in the conversation.

**Large datasets:** When generating > 1000 rows, save to `/tmp/<name>.csv`
and print only `df.head()` + `df.shape` + the file path. NEVER print the
entire dataset (it will exceed output token limits).

**Plotting:** Use `matplotlib` with `plt.show()` — the sandbox will
capture the figure as PNG and return it automatically.

You should assist the user with their queries by looking at the data and
the context in the conversation. Your final answer should summarize the
code and code execution results relevant to the user query.

If you cannot answer the question directly, follow the guidelines above
to generate the next step. If you don't have enough data to answer, ask
the user for clarification.

When plotting trends, make sure to sort and order the data by the x-axis.
"""

root_agent = Agent(
    name="code_analyst",
    model=os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"),
    instruction=_INSTRUCTION,
    code_executor=BuiltInCodeExecutor(),
)

app = App(root_agent=root_agent, name="app")
