#!/usr/bin/env python3
"""Validate a Panelyra news feed locally, without contacting its repository."""

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from usbdisplay.notifications import (  # noqa: E402
    MAX_BYTES, NotificationError, Source, load_source, parse_announcements,
)


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise NotificationError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("feed", nargs="?", type=Path, default=ROOT / "announcements.json",
                        help="JSON feed to validate (default: repository announcements.json)")
    parser.add_argument("--repository", metavar="OWNER/REPOSITORY",
                        help="validate links against this repository instead of the bundled source")
    args = parser.parse_args(argv)
    try:
        source = Source(args.repository) if args.repository is not None else load_source()
        with args.feed.open("rb") as stream:
            payload = stream.read(MAX_BYTES + 1)
        if len(payload) > MAX_BYTES:
            raise NotificationError("News feed exceeds 256 KiB")
        data = json.loads(payload.decode("utf-8"), object_pairs_hook=unique_keys)
        notices = parse_announcements(data, source)
        if notices and not source.configured:
            raise NotificationError(
                "Configure GITHUB_REPOSITORY or pass --repository OWNER/REPOSITORY "
                "before publishing a non-empty feed"
            )
    except (OSError, ValueError, RecursionError) as error:
        print(f"Invalid announcements: {error}", file=sys.stderr)
        return 1
    print(f"Valid announcements: {len(notices)} item(s). No network requests made.")
    if not source.configured:
        print("Public source is not configured; the empty preview feed is valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
