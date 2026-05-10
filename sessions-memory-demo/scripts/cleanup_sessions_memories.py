"""Delete all sessions, user-scoped state, and memories from the deployed agent.

Cleans up ALL persistent data without deleting the Agent Engine itself:
  1. Nullifies user: scoped state via appendEvent (stored at user level,
     survives session deletion)
  2. Deletes all sessions (and their session-scoped state + events)
  3. Purges all Memory Bank entries

Supports a --dry-run flag to preview what would be deleted.

Usage:
    cd demo-agent
    uv run python ../scripts/cleanup_sessions_memories.py
    uv run python ../scripts/cleanup_sessions_memories.py --dry-run
"""

import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone

from google.genai import types as genai_types
import vertexai
from vertexai import Client

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEMO_AGENT_DIR = os.path.join(SCRIPT_DIR, "..", "demo-agent")
METADATA_FILE = os.path.join(DEMO_AGENT_DIR, "deployment_metadata.json")


def get_agent_resource_name():
    if not os.path.exists(METADATA_FILE):
        print("ERROR: deployment_metadata.json not found.")
        print("Run ../deploy.sh first to deploy the agent.")
        sys.exit(1)

    with open(METADATA_FILE) as f:
        return json.load(f)["remote_agent_runtime_id"]


def clear_user_state(client, agent_name, dry_run=False):
    """Nullify all user: scoped state keys across all sessions.

    user: state lives at the user level and survives session deletion.
    The only way to clear it is to append an event with stateDelta
    setting each user: key to null.
    """
    print("\n>>> User-scoped state")

    sessions = list(client.agent_engines.sessions.list(name=agent_name))
    if not sessions:
        print("    No sessions found — skipping.")
        return 0

    # Collect all user: keys across all sessions, grouped by user_id
    user_keys = {}
    for session in sessions:
        uid = getattr(session, "user_id", None) or "unknown"
        state = getattr(session, "session_state", None) or {}
        for key in state:
            if key.startswith("user:") and state[key] is not None:
                user_keys.setdefault(uid, set()).add(key)

    if not user_keys:
        print("    No user: state keys found.")
        return 0

    total_keys = sum(len(keys) for keys in user_keys.values())
    print(f"    Found {total_keys} user: keys across {len(user_keys)} users:")
    for uid, keys in sorted(user_keys.items()):
        # Find a session for this user to show values
        for s in sessions:
            if (getattr(s, "user_id", None) or "unknown") == uid:
                state = getattr(s, "session_state", None) or {}
                for k in sorted(keys):
                    val = state.get(k, "?")
                    print(f"      {uid} / {k} = {val}")
                break

    if dry_run:
        print(f"    [DRY RUN] Would nullify {total_keys} user: keys.")
        return 0

    # SDK doesn't expose appendEvent — use REST API directly
    location = agent_name.split("/")[3]
    base_url = f"https://{location}-aiplatform.googleapis.com/v1beta1"
    token = subprocess.check_output(
        ["gcloud", "auth", "print-access-token"], text=True
    ).strip()

    cleared = 0
    for uid, keys in user_keys.items():
        session_for_user = next(
            s for s in sessions
            if (getattr(s, "user_id", None) or "unknown") == uid
        )
        null_delta = {k: None for k in keys}
        now = datetime.now(timezone.utc).isoformat()
        body = json.dumps({
            "author": "system",
            "invocationId": "cleanup-user-state",
            "actions": {"stateDelta": null_delta},
            "timestamp": now,
        }).encode()
        try:
            req = urllib.request.Request(
                f"{base_url}/{session_for_user.name}:appendEvent",
                data=body,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            urllib.request.urlopen(req)
            cleared += len(keys)
            print(f"    Cleared {len(keys)} keys for {uid}")
        except Exception as e:
            print(f"    FAILED for {uid}: {e}")

    print(f"    Nullified {cleared}/{total_keys} user: state keys.")
    return cleared


def delete_all_sessions(client, agent_name, dry_run=False):
    """List and delete all sessions. Returns count deleted."""
    print("\n>>> Sessions")

    sessions = list(client.agent_engines.sessions.list(name=agent_name))
    if not sessions:
        print("    No sessions found.")
        return 0

    user_counts = {}
    for s in sessions:
        uid = getattr(s, "user_id", None) or "unknown"
        user_counts[uid] = user_counts.get(uid, 0) + 1

    print(f"    Found {len(sessions)} sessions across {len(user_counts)} users:")
    for uid, count in sorted(user_counts.items()):
        print(f"      {uid}: {count} sessions")

    if dry_run:
        print(f"    [DRY RUN] Would delete {len(sessions)} sessions.")
        return 0

    deleted = 0
    for i, session in enumerate(sessions, 1):
        session_id = session.name.split("/")[-1]
        uid = getattr(s, "user_id", None) or "?"
        try:
            client.agent_engines.sessions.delete(name=session.name)
            deleted += 1
            print(f"    [{i}/{len(sessions)}] Deleted {session_id}")
        except Exception as e:
            print(f"    [{i}/{len(sessions)}] FAILED {session_id}: {e}")

    print(f"    Deleted {deleted}/{len(sessions)} sessions.")
    return deleted


def purge_all_memories(client, agent_name, dry_run=False):
    """Purge all Memory Bank entries. Returns count purged."""
    print("\n>>> Memory Bank")

    memories = list(client.agent_engines.memories.list(name=agent_name))
    if not memories:
        print("    No memories found.")
        return 0

    print(f"    Found {len(memories)} memories.")
    for m in memories[:5]:
        fact = getattr(m, "fact", "") or ""
        if len(fact) > 70:
            fact = fact[:67] + "..."
        scope = getattr(m, "scope", {}) or {}
        print(f"      scope={scope}  fact={fact}")
    if len(memories) > 5:
        print(f"      ... and {len(memories) - 5} more")

    try:
        result = client.agent_engines.memories.purge(
            name=agent_name,
            filter='create_time>"1970-01-01T00:00:00Z"',
            force=not dry_run,
        )
        if dry_run:
            purge_count = getattr(getattr(result, "response", None), "purge_count", None)
            print(f"    [DRY RUN] Would purge {purge_count or len(memories)} memories.")
            return 0
        else:
            purge_count = getattr(getattr(result, "response", None), "purge_count", None)
            print(f"    Purged {purge_count or len(memories)} memories.")
            return purge_count or len(memories)
    except Exception as e:
        print(f"    Bulk purge failed ({e}), falling back to individual deletes...")
        if dry_run:
            print(f"    [DRY RUN] Would delete {len(memories)} memories individually.")
            return 0

        deleted = 0
        for i, memory in enumerate(memories, 1):
            try:
                client.agent_engines.memories.delete(name=memory.name)
                deleted += 1
                print(f"    [{i}/{len(memories)}] Deleted {memory.name.split('/')[-1]}")
            except Exception as e2:
                print(f"    [{i}/{len(memories)}] FAILED: {e2}")

        print(f"    Deleted {deleted}/{len(memories)} memories.")
        return deleted


def main():
    dry_run = "--dry-run" in sys.argv

    agent_name = get_agent_resource_name()
    parts = agent_name.split("/")
    project_id = parts[1]
    location = parts[3]

    print("╔══════════════════════════════════════════════════════════════╗")
    if dry_run:
        print("║     Cleanup Sessions & Memories  [DRY RUN]                 ║")
    else:
        print("║     Cleanup Sessions & Memories                            ║")
    print("╚══════════════════════════════════════════════════════════════╝")
    print(f"  Agent:   {agent_name}")
    print(f"  Project: {project_id}")
    print(f"  Region:  {location}")

    if not dry_run:
        print()
        print("  WARNING: This will permanently delete ALL sessions, user")
        print("  state, and memories. The Agent Engine itself is NOT deleted.")
        print()
        confirm = input("  Proceed? [y/N] ").strip().lower()
        if confirm != "y":
            print("  Aborted.")
            sys.exit(0)

    vertexai.init(project=project_id, location=location)
    client = Client(
        project=project_id,
        location=location,
        http_options=genai_types.HttpOptions(api_version="v1beta1"),
    )

    t0 = time.time()

    # Order matters: clear user state BEFORE deleting sessions
    # (we need an existing session to append the nullifying event)
    user_keys_cleared = clear_user_state(client, agent_name, dry_run)
    sessions_deleted = delete_all_sessions(client, agent_name, dry_run)
    memories_purged = purge_all_memories(client, agent_name, dry_run)

    elapsed = time.time() - t0

    print()
    print("=" * 62)
    if dry_run:
        print("  DRY RUN complete.")
    else:
        print(f"  Done in {elapsed:.1f}s.")
        print(f"    User state keys cleared: {user_keys_cleared}")
        print(f"    Sessions deleted:        {sessions_deleted}")
        print(f"    Memories purged:         {memories_purged}")
    print(f"  Agent Engine: PRESERVED (not deleted)")
    print("=" * 62)


if __name__ == "__main__":
    main()
