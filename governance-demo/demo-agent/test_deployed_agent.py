import asyncio
from vertexai.preview import reasoning_engines
import vertexai

def main():
    vertexai.init(project="vibe-cabral", location="us-central1")
    engine = reasoning_engines.ReasoningEngine("projects/280799742875/locations/us-central1/reasoningEngines/7191268390294519808")
    
    print("Testing 'get_account_balance' (Read-Only) via Agent Gateway...")
    try:
        response = engine.stream_query(
            message="Check my account balance for user123",
            user_id="user_test_1"
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
            user_id="user_test_1"
        )
        for chunk in response:
            print(chunk, end="")
        print("\n")
    except Exception as e:
        print(f"\nBlocked! Expected error occurred: {e}")

if __name__ == "__main__":
    main()
