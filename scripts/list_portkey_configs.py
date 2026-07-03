"""List saved Portkey configs and their system-generated pc-... IDs.

Run:
    PYTHONPATH=. python scripts/list_portkey_configs.py

The output shows the human-readable slug and the `id` you need for
PORTKEY_PRIMARY_CONFIG_ID in `.env`.
"""

import sys

import requests

from app.config import settings


def main() -> int:
    if not settings.PORTKEY_API_KEY:
        print("PORTKEY_API_KEY is not set in .env", file=sys.stderr)
        return 1

    url = "https://api.portkey.ai/v1/configs"
    headers = {
        "x-portkey-api-key": settings.PORTKEY_API_KEY,
        "Content-Type": "application/json",
    }

    try:
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
    except requests.HTTPError as e:
        print(f"Portkey API request failed: {e}", file=sys.stderr)
        print(resp.text, file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Failed to reach Portkey: {e}", file=sys.stderr)
        return 1

    data = resp.json()
    configs = data.get("data", []) if isinstance(data, dict) else data

    if not configs:
        print("No saved configs found in this Portkey workspace.")
        return 0

    print("\nSaved Portkey configs:")
    print("-" * 70)
    print(f"{'ID':<30} {'Slug':<30} {'Status':<10}")
    print("-" * 70)
    for cfg in configs:
        cfg_id = cfg.get("id", "n/a")
        slug = cfg.get("slug", cfg.get("name", "n/a"))
        status = cfg.get("status", "n/a")
        print(f"{cfg_id:<30} {slug:<30} {status:<10}")
    print("-" * 70)
    print("\nAdd the desired ID to .env (no spaces, no quotes):")
    print("  PORTKEY_PRIMARY_CONFIG_ID=pc-xxxxxxxx")
    return 0


if __name__ == "__main__":
    sys.exit(main())
