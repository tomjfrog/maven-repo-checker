#!/usr/bin/env python3
"""
check_artifactory_maturity.py

Usage:
    python check_artifactory_maturity.py \
        --url https://artifactory.mycompany.com/artifactory \
        --apikey YOUR_API_KEY

Alternatively, you can provide --user USERNAME --password PASSWORD instead of --apikey.

What it does:
  1. Retrieves all local repositories from Artifactory.
  2. Filters to only the ones where "packageType" == "maven".
  3. Fetches each repo's configuration to inspect handleSnapshots/handleReleases.
  4. Determines whether each Maven repo is snapshot-only, release-only, both, or none.
  5. Checks if the repoKey contains "snapshot" for snapshot‐only repos, or "release" for release‐only repos.
  6. Prints a report, indicating any mismatches.
"""

import argparse
import sys
import requests

def parse_args():
    parser = argparse.ArgumentParser(
        description="Check that Maven repos in Artifactory have names matching their snapshot/release configuration."
    )
    parser.add_argument(
        "--url",
        required=True,
        help="Base Artifactory URL (e.g. https://artifactory.mycompany.com/artifactory)",
    )
    auth_group = parser.add_mutually_exclusive_group(required=True)
    auth_group.add_argument(
        "--apikey",
        help="Artifactory API key (will be sent as header 'X-JFrog-Art-Api')"
    )
    auth_group.add_argument(
        "--user",
        help="Username for basic auth (must be used along with --password)"
    )
    parser.add_argument(
        "--password",
        help="Password for basic auth (must be used along with --user)"
    )
    return parser.parse_args()

def build_auth_headers(args):
    """
    Returns a tuple (headers, auth) where:
      - headers is a dict of HTTP headers to send
      - auth is either None or a (user, password) tuple for requests
    """
    headers = {"Accept": "application/json"}
    auth = None

    if args.apikey:
        headers["X-JFrog-Art-Api"] = args.apikey
    else:
        if not args.password:
            print("ERROR: When using --user, you must also supply --password.", file=sys.stderr)
            sys.exit(1)
        auth = (args.user, args.password)

    return headers, auth

def get_all_local_repos(base_url, headers, auth):
    """
    GET /api/repositories?type=local
    Returns the JSON list of repository summaries.
    """
    endpoint = f"{base_url.rstrip('/')}/api/repositories"
    params = {"type": "local"}  # only local repos
    resp = requests.get(endpoint, headers=headers, params=params, auth=auth)
    resp.raise_for_status()
    return resp.json()

def get_repo_config(base_url, repo_key, headers, auth):
    """
    GET /api/repositories/{repoKey}
    Returns the JSON configuration of that repo.
    """
    endpoint = f"{base_url.rstrip('/')}/api/repositories/{repo_key}"
    resp = requests.get(endpoint, headers=headers, auth=auth)
    resp.raise_for_status()
    return resp.json()

def determine_maturity_flags(repo_config):
    """
    Given a repo configuration JSON (for a local Maven repo), return:
      - is_snapshot_only: True if handleSnapshots == True AND handleReleases == False
      - is_release_only: True if handleReleases == True AND handleSnapshots == False
      - is_mixed: True if both handleSnapshots and handleReleases are True
      - is_neither: True if both are False (unusual)
    """
    hs = repo_config.get("handleSnapshots", False)
    hr = repo_config.get("handleReleases", False)
    is_snapshot_only = hs and not hr
    is_release_only = hr and not hs
    is_mixed = hs and hr
    is_neither = not hs and not hr
    return is_snapshot_only, is_release_only, is_mixed, is_neither

def check_name_keyword(repo_key, is_snapshot_only, is_release_only):
    """
    Returns (matches, expected_keyword, found) where:
      - matches is True if naming matches the maturity:
          * snapshot-only => repo_key.lower() contains "snapshot"
          * release-only  => repo_key.lower() contains "release"
      - expected_keyword is "snapshot" or "release" or None if mixed/neither
      - found is True/False if keyword is present
    """
    key_lower = repo_key.lower()
    if is_snapshot_only:
        found = "snapshot" in key_lower
        return found, "snapshot", found
    elif is_release_only:
        found = "release" in key_lower
        return found, "release", found
    else:
        return True, None, None  # for mixed or neither, we do not check

def main():
    args = parse_args()
    headers, auth = build_auth_headers(args)

    # 1. Fetch all local repos
    try:
        all_local = get_all_local_repos(args.url, headers, auth)
    except requests.HTTPError as e:
        print(f"Failed to fetch local repositories: {e}", file=sys.stderr)
        sys.exit(1)

    # 2. Filter to Maven repos
    maven_repos = []
    for repo in all_local:
        # Each entry has "key", "packageType", etc.
        if repo.get("packageType", "").lower() == "maven":
            maven_repos.append(repo["key"])

    if not maven_repos:
        print("No local Maven repositories found.", file=sys.stderr)
        sys.exit(0)

    # 3. Iterate and check each one
    report = []
    for repo_key in sorted(maven_repos):
        try:
            cfg = get_repo_config(args.url, repo_key, headers, auth)
        except requests.HTTPError as e:
            print(f"  [ERROR] Could not fetch config for '{repo_key}': {e}", file=sys.stderr)
            continue

        is_snapshot_only, is_release_only, is_mixed, is_neither = determine_maturity_flags(cfg)
        matches, expected_keyword, found = check_name_keyword(
            repo_key, is_snapshot_only, is_release_only
        )

        # determine a human-readable maturity
        if is_snapshot_only:
            maturity = "snapshot-only"
        elif is_release_only:
            maturity = "release-only"
        elif is_mixed:
            maturity = "mixed (both snapshots & releases)"
        else:
            maturity = "neither (handleSnapshots=false, handleReleases=false)"

        # determine name‐check status
        if expected_keyword is None:
            name_check = "n/a"
            note = "" if is_mixed or is_neither else ""
        else:
            if matches:
                name_check = f"OK (contains '{expected_keyword}')"
                note = ""
            else:
                name_check = f"✗ (does not contain '{expected_keyword}')"
                note = f"-> Consider renaming '{repo_key}' to include '{expected_keyword}'"

        report.append({
            "repo_key": repo_key,
            "maturity": maturity,
            "name_check": name_check,
            "note": note,
        })

    # 4. Print a simple table/report
    col1 = "REPO_KEY"
    col2 = "MATURITY"
    col3 = "NAME_CHECK"
    print(f"{col1:40s}  {col2:35s}  {col3}")
    print(f"{'-'*40}  {'-'*35}  {'-'*20}")
    for item in report:
        print(f"{item['repo_key']:40s}  {item['maturity']:35s}  {item['name_check']}")

    # Also print any notes separately
    notes = [r["note"] for r in report if r["note"]]
    if notes:
        print("\nNotes:")
        for n in notes:
            print(f"  - {n}")

if __name__ == "__main__":
    main()
