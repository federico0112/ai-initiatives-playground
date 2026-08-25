#!/usr/bin/env python3
"""Manual test script for the /chat RAG endpoint.

Run the service locally first:
    python app.py

Then run this script:
    python tests/test_chat_api.py

Or with arguments:
    python tests/test_chat_api.py --message "What is in my documents?"
    python tests/test_chat_api.py --session-id my-session --top-k 10
"""

import argparse
import json
import sys
import requests


DEFAULT_BASE_URL = "http://localhost:8080"


def test_chat(
    message: str,
    base_url: str = DEFAULT_BASE_URL,
    session_id: str | None = None,
    top_k: int = 5,
    model: str = "gemini-2.5-flash",
) -> None:
    """Send a chat request and print streaming response."""
    url = f"{base_url}/chat"

    payload = {
        "message": message,
        "top_k": top_k,
        "model": model,
    }
    if session_id:
        payload["session_id"] = session_id

    print(f"POST {url}")
    print(f"Payload: {json.dumps(payload, indent=2)}")
    print("-" * 50)

    try:
        response = requests.post(
            url,
            json=payload,
            headers={"Content-Type": "application/json"},
            stream=True,
        )

        if response.status_code != 200:
            print(f"Error: HTTP {response.status_code}")
            print(response.text)
            return

        print("Response (streaming NDJSON):\n")

        full_response = ""
        for line in response.iter_lines():
            if line:
                event = json.loads(line.decode("utf-8"))
                event_type = event.get("type")

                if event_type == "metadata":
                    print(f"[metadata] session_id={event.get('session_id')}, model={event.get('model')}")

                elif event_type == "sources":
                    docs = event.get("documents", [])
                    print(f"[sources] {len(docs)} document(s) retrieved:")
                    for doc in docs:
                        pages = doc.get("pages", [])
                        page_info = f" (pages {', '.join(map(str, pages))})" if pages else ""
                        score = doc.get("score")
                        score_info = f" [{score:.2%}]" if score else ""
                        print(f"  - {doc.get('filename')}{page_info}{score_info}")

                elif event_type == "chunk":
                    content = event.get("content", "")
                    full_response += content
                    print(content, end="", flush=True)

                elif event_type == "done":
                    print("\n")
                    print("-" * 50)
                    print(f"[done] Response length: {len(full_response)} chars")

                elif event_type == "error":
                    print(f"\n[error] {event.get('error')}")

    except requests.ConnectionError:
        print(f"Error: Could not connect to {base_url}")
        print("Make sure the service is running: python app.py")
        sys.exit(1)


def test_health(base_url: str = DEFAULT_BASE_URL) -> bool:
    """Check if service is healthy."""
    try:
        response = requests.get(f"{base_url}/health")
        if response.status_code == 200:
            print(f"Service healthy: {response.json()}")
            return True
        else:
            print(f"Service unhealthy: {response.status_code}")
            return False
    except requests.ConnectionError:
        print(f"Error: Could not connect to {base_url}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Test the /chat RAG endpoint")
    parser.add_argument(
        "--message", "-m",
        default="What information is in my documents?",
        help="The question to ask (default: 'What information is in my documents?')",
    )
    parser.add_argument(
        "--base-url", "-u",
        default=DEFAULT_BASE_URL,
        help=f"Base URL of the service (default: {DEFAULT_BASE_URL})",
    )
    parser.add_argument(
        "--session-id", "-s",
        help="Session ID for conversation continuity",
    )
    parser.add_argument(
        "--top-k", "-k",
        type=int,
        default=5,
        help="Number of documents to retrieve (default: 5)",
    )
    parser.add_argument(
        "--model",
        default="gemini-2.5-flash",
        choices=["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.0-flash"],
        help="Model to use (default: gemini-2.5-flash)",
    )
    parser.add_argument(
        "--health-only",
        action="store_true",
        help="Only check service health",
    )

    args = parser.parse_args()

    print("=" * 50)
    print("RAG Chat API Test")
    print("=" * 50)

    # Check health first
    if not test_health(args.base_url):
        sys.exit(1)

    if args.health_only:
        return

    print()

    # Run chat test
    test_chat(
        message=args.message,
        base_url=args.base_url,
        session_id=args.session_id,
        top_k=args.top_k,
        model=args.model,
    )


if __name__ == "__main__":
    main()
