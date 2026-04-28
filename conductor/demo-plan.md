# Demo Plan: Google Agent Platform Governance & Read-Only Policies

## Objective
Create a Google Agent Platform demo showcasing an ADK agent running in Agent Runtime, accessing a FastMCP Server hosted on Cloud Run. The MCP server will be registered in Agent Registry, and an Agent Gateway IAM policy will be configured using the agent's SPIFFE ID to strictly enforce read-only tool usage for mock financial transactions.

## Background & Motivation
This demo serves as a tangible proof-of-concept for enterprise governance in multi-agent and tool-integrated systems. It demonstrates granular control by enforcing which capabilities an AI agent can execute (e.g., blocking destructive/write operations like transferring funds) while permitting safe operations (e.g., checking an account balance), utilizing the modern SPIFFE Agent Identity pattern.

## Scope & Impact
*   **Target Environments:** Agent Runtime (for the ADK Agent) and Cloud Run (for the FastMCP Server).
*   **Security & Governance:** Agent Gateway (IAP) and IAM condition bindings using the `roles/iap.egressor` role with `ReadOnly` enforcement, authenticated via the Agent's SPIFFE ID.
*   **Automation:** Bash scripts (`deploy.sh` and `undeploy.sh`) will manage the lifecycle of the infrastructure, while `agents-cli` manages the agent deployment.

## Proposed Solution

1.  **FastMCP Server (Mock Financial Tools):**
    *   Build a simple Python FastMCP server containing two mocked tools:
        *   `get_account_balance`: Returns a mock balance. Configured as `isReadOnly = true`.
        *   `transfer_funds`: Mocks a transaction. Configured as a destructive/write operation (`isReadOnly = false`).
    *   Package with a Dockerfile for Cloud Run.

2.  **ADK Python Agent:**
    *   Scaffold an ADK project using `agents-cli scaffold create demo-agent`.
    *   Implement an `LlmAgent` with an `instruction` to manage user finances.
    *   Connect the agent to the remote MCP server using `McpToolset` with `SseConnectionParams`.

3.  **Deployment Automation (`deploy.sh`):**
    *   Deploy the FastMCP server to Cloud Run (`gcloud run deploy`).
    *   Register the MCP server with Agent Registry (via API or `gcloud`).
    *   Deploy the ADK agent using `agents-cli deploy --deployment-target agent_runtime --agent-identity` to provision the agent with a SPIFFE ID.
    *   Extract the generated SPIFFE ID of the deployed agent.
    *   Apply the IAM Allow Policy (Agent Gateway) granting `roles/iap.egressor` to the agent's SPIFFE principal on the MCP server resource, including the CEL Condition: `request.mcp.tool.isReadOnly == true`.

4.  **Teardown Automation (`undeploy.sh`):**
    *   Remove the IAM policy.
    *   Delete the ADK Agent from Agent Runtime.
    *   Unregister the MCP server from Agent Registry.
    *   Delete the FastMCP server from Cloud Run.

5.  **Verification & Demo Flow:**
    *   **Test 1 (Allowed):** Prompt the agent to "Check my account balance". The agent invokes the `get_account_balance` tool successfully.
    *   **Test 2 (Blocked):** Prompt the agent to "Transfer $500 to John". The agent attempts to invoke the `transfer_funds` tool, but the Agent Gateway blocks the request due to the IAM read-only policy. The agent reports the inability to complete the transaction.

## Alternatives Considered
*   **Gemini Enterprise Registration:** Skipped to focus purely on Agent Gateway and Agent Registry mechanics using SPIFFE IDs.
*   **Manual Console Configuration:** Instead of relying on the Google Cloud Console, we are prioritizing `deploy.sh` and `undeploy.sh` to make the demo reproducible and automated.

## Migration & Rollback
*   The `undeploy.sh` script guarantees a clean slate, removing all created resources (Cloud Run, Agent Runtime, Registries, and IAM bindings).