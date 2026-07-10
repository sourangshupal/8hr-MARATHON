#!/usr/bin/env python3
"""Create AWS Secrets Manager entries from the local .env file."""

import os
import subprocess
import sys
from pathlib import Path

from dotenv import dotenv_values

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = REPO_ROOT / ".env"

# Secrets that must exist in .env for the ECS deployment.
SECRET_KEYS = [
    "NEON_DB_URL",
    "UPSTASH_REDIS_REST_URL",
    "UPSTASH_REDIS_REST_TOKEN",
    "QDRANT_URL",
    "QDRANT_API_KEY",
    "OPENAI_API_KEY",
    "JINA_API_KEY",
    "PORTKEY_API_KEY",
    "RAG_API_KEY",
    "LOGFIRE_TOKEN",
    "LANGSMITH_API_KEY",
]

# Map environment variable names used by the app to .env keys when they differ.
ENV_KEY_MAP = {
    "QDRANT_URL": "QDRANT_CLUSTER_ENDPOINT",
}


def main():
    project = os.environ.get("PROJECT", "rag")
    env = dotenv_values(ENV_PATH)

    # Resolve values, mapping app keys to .env keys where needed.
    values = {}
    for key in SECRET_KEYS:
        env_key = ENV_KEY_MAP.get(key, key)
        values[key] = env.get(env_key, "").strip()

    # Generate a production RAG_API_KEY if it is empty in .env.
    if not values["RAG_API_KEY"]:
        import secrets

        values["RAG_API_KEY"] = "rag-" + secrets.token_urlsafe(32)
        print(f"Generated RAG_API_KEY={values['RAG_API_KEY']}")
        print("Save this key; it is required for /query authorization.")

    missing = [k for k in SECRET_KEYS if not values[k]]
    if missing:
        print(f"ERROR: the following required secrets are missing/empty in {ENV_PATH}:")
        for k in missing:
            print(f"  - {k}")
        sys.exit(1)

    arns = {}
    for key in SECRET_KEYS:
        secret_name = f"{project}/{key.lower().replace('_', '-')}"
        value = values[key]

        result = subprocess.run(
            [
                "aws",
                "secretsmanager",
                "create-secret",
                "--name",
                secret_name,
                "--description",
                f"{key} for RAG deployment",
                "--secret-string",
                value,
                "--query",
                "ARN",
                "--output",
                "text",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        arn = result.stdout.strip()
        arns[key] = arn
        print(f"{key}_ARN={arn}")

    # Write a shell snippet with the ARNs so later commands can source it.
    state_path = REPO_ROOT / "scripts" / "aws_secret_arns.sh"
    with state_path.open("w") as f:
        f.write("#!/usr/bin/env bash\n# AWS Secrets Manager ARNs\n")
        for key, arn in arns.items():
            f.write(f'export {key}_ARN="{arn}"\n')
    print(f"ARNs written to {state_path}")


if __name__ == "__main__":
    main()
