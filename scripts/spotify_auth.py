#!/usr/bin/env python3
"""Open the browser once to establish the reusable Spotify authorization cache."""

import argparse

from utils.config import load_config
from utils.spotify import WRITE_SCOPE, get_client


def main() -> None:
    argparse.ArgumentParser(description=__doc__).parse_args()
    profile = get_client(load_config(), scope=WRITE_SCOPE, open_browser=True).current_user()
    print(f"Authenticated as {profile.get('display_name') or profile.get('id')}.")


if __name__ == "__main__":
    main()
