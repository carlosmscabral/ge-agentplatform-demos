import os
import sys

import vertexai
from vertexai.preview import reasoning_engines


def main():
    project_id = os.environ.get("PROJECT_ID")
    region = os.environ.get("REGION", "us-central1")

    if not project_id:
        print("ERROR: Set PROJECT_ID environment variable")
        sys.exit(1)

    engine_resource = os.environ.get("AGENT_RESOURCE_NAME")
    if not engine_resource and os.path.exists("deployed_engine.txt"):
        with open("deployed_engine.txt") as f:
            engine_resource = f.read().strip()
    if not engine_resource:
        print("ERROR: Set AGENT_RESOURCE_NAME or ensure deployed_engine.txt exists")
        sys.exit(1)

    vertexai.init(project=project_id, location=region)
    engine = reasoning_engines.ReasoningEngine(engine_resource)

    print("Testing 'get_account_balance' (Read-Only) via Agent Gateway...")
    try:
        response = engine.stream_query(
            message="Check my account balance for user123",
            user_id="user_test_1",
        )
        for chunk in response:
            print(chunk, end="")
        print("\n")
    except Exception as e:
        print(f"Error occurred: {e}")

    print("\nTesting 'transfer_funds' (Write Operation) via Agent Gateway...")
    try:
        response = engine.stream_query(
            message="Transfer $100 from user123 to user456",
            user_id="user_test_1",
        )
        for chunk in response:
            print(chunk, end="")
        print("\n")
    except Exception as e:
        print(f"\nBlocked! Expected error occurred: {e}")


if __name__ == "__main__":
    main()
