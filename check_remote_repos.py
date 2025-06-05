#!/usr/bin/env python3
"""
check_remote_repos.py

Usage:
    python check_remote_repos.py \
        --url https://artifactory.mycompany.com/artifactory \
        --apikey YOUR_API_KEY

Or, if you prefer basic auth:
    python check_remote_repos.py \
        --url https://artifactory.mycompany.com/artifactory \
        --user alice \
        --password secret123

What it does:
  1. Retrieves all remote repositories from Artifactory.
  2. For each remote repo, fetches its configuration and extracts the 'url' field.
  3. Issues an HTTP HEAD request to that 'url', tracking the status code (2xx/3xx vs 4xx/5xx).
  4. Prints a table to stdout with: repoKey, remote URL, and the status (or error).
"""

import argparse
import sys
import requests

# ----------------------------------------------------------------------
def parse_args():
    parser = argparse.ArgumentParser(
        description="Check remote repos in Artifactory for upstream availability."
    )
    parser.add_argument(
        "--url",
        required=True,
        help="Base Artifactory URL (e.g. https://artifactory.mycompany.com/artifactory)",
    )
    auth_group = parser.add_mutually_exclusive_group(required=True)
    auth_group.add_argument(
        "--apikey",
        help="Artifactory API key (sent as 'X-JFrog-Art-Api' header)"
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
    Returns (headers, auth) tuple for requests:
      - headers: dict of HTTP headers
      - auth: either None or (user, password) tuple
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

# ----------------------------------------------------------------------
def get_all_remote_repos(base_url, headers, auth):
    """
    GET /api/repositories?type=remote
    Returns a list of repository summaries (JSON).
    """
    endpoint = f"{base_url.rstrip('/')}/api/repositories"
    params = {"type": "remote"}
    resp = requests.get(endpoint, headers=headers, params=params, auth=auth)
    resp.raise_for_status()
    return resp.json()

def get_repo_config(base_url, repo_key, headers, auth):
    """
    GET /api/repositories/{repoKey}
    Returns the full repository configuration (JSON).
    """
    endpoint = f"{base_url.rstrip('/')}/api/repositories/{repo_key}"
    resp = requests.get(endpoint, headers=headers, auth=auth)
    resp.raise_for_status()
    return resp.json()

# ----------------------------------------------------------------------
def test_upstream_url(upstream_url, timeout=10):
    """
    Issues an HTTP HEAD request to upstream_url (allow_redirects=True).
    Returns a tuple (status_code, error_msg):
      - If the request succeeds: (status_code, None)
      - If it fails (timeout, DNS, connect error, or non-HTTPable URL):
          (None, "<ExceptionType>: <message>")
    """
    try:
        resp = requests.head(upstream_url, allow_redirects=True, timeout=timeout)
        return resp.status_code, None
    except requests.RequestException as e:
        # Catch all request‐related errors (timeout, connection, invalid URL, etc.)
        return None, f"{type(e).__name__}: {str(e)}"

# ----------------------------------------------------------------------
def main():
    args = parse_args()
    headers, auth = build_auth_headers(args)

    # 1. Fetch all remote repos
    try:
        all_remote = get_all_remote_repos(args.url, headers, auth)
    except requests.HTTPError as e:
        print(f"Failed to fetch remote repositories: {e}", file=sys.stderr)
        sys.exit(1)

    if not all_remote:
        print("No remote repositories found.", file=sys.stderr)
        sys.exit(0)

    # 2. Iterate over each remote repo
    report = []
    for repo in sorted(all_remote, key=lambda r: r.get("key", "")):
        repo_key = repo.get("key")
        if not repo_key:
            continue

        # 2a. Fetch the repo config to extract "url"
        try:
            cfg = get_repo_config(args.url, repo_key, headers, auth)
        except requests.HTTPError as e:
            # Could not retrieve config; record error and skip
            report.append({
                "repo_key": repo_key,
                "remote_url": "<ERROR_FETCHING_CONFIG>",
                "status": f"CONFIG_ERROR: {str(e)}"
            })
            continue

        upstream_url = cfg.get("url")
        if not upstream_url:
            # If there is no "url" field, note it
            report.append({
                "repo_key": repo_key,
                "remote_url": "<NO_URL_IN_CONFIG>",
                "status": "N/A"
            })
            continue

        # 2b. Test the upstream URL
        status_code, error = test_upstream_url(upstream_url)
        if error:
            status_str = error
        else:
            status_str = str(status_code)

        report.append({
            "repo_key": repo_key,
            "remote_url": upstream_url,
            "status": status_str
        })

    # 3. Print the results as a table
    col1 = "REMOTE_REPO_KEY"
    col2 = "REMOTE_URL"
    col3 = "TEST_STATUS"
    print(f"{col1:30s}  {col2:60s}  {col3}")
    print(f"{'-'*30}  {'-'*60}  {'-'*20}")
    for item in report:
        rk = item["repo_key"] or ""
        url = item["remote_url"] or ""
        st = item["status"] or ""
        # Trim long URLs to fit reasonably
        if len(url) > 58:
            url_display = url[:55] + "..."
        else:
            url_display = url
        print(f"{rk:30s}  {url_display:60s}  {st}")

if __name__ == "__main__":
    main()
