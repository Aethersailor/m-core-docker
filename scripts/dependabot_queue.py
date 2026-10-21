#!/usr/bin/env python3
"""Serialize verified Dependabot merges and explicitly trigger image publication."""

import argparse
import json
import os
import re
import subprocess


def api(path, *, method="GET", data=None):
    command = ["gh", "api", "--method", method, path]
    payload = None
    if data is not None:
        command += ["--input", "-"]
        payload = json.dumps(data)
    result = subprocess.run(command, input=payload, check=True, text=True,
                            encoding="utf-8", stdout=subprocess.PIPE)
    return json.loads(result.stdout) if result.stdout.strip() else None


def current_base(repo):
    return api(f"repos/{repo}/git/ref/heads/main")["object"]["sha"]


def trusted_pr(repo, number):
    pr = api(f"repos/{repo}/pulls/{number}")
    if (pr["state"] != "open" or pr["draft"] or pr["base"]["ref"] != "main"
            or pr["user"]["login"] != "dependabot[bot]" or pr["user"]["id"] != 49699333
            or not pr["head"].get("repo") or pr["head"]["repo"]["full_name"] != repo
            or not pr["head"]["ref"].startswith("dependabot/")):
        return None
    files = api(f"repos/{repo}/pulls/{number}/files?per_page=100")
    if not files or len(files) != pr["changed_files"]:
        return None
    if any(not (f["filename"] == "Dockerfile" or re.fullmatch(r"\.github/workflows/[^/]+\.ya?ml", f["filename"]))
           or f["status"] != "modified" for f in files):
        return None
    commits = api(f"repos/{repo}/pulls/{number}/commits?per_page=100")
    if not commits or len(commits) != pr["commits"]:
        return None
    if any((c.get("author") or {}).get("id") != 49699333 or not c["commit"]["verification"]["verified"] for c in commits):
        return None
    return pr, files


def checks_pass(repo, head):
    response = api(f"repos/{repo}/commits/{head}/check-runs?per_page=100")
    checks = response["check_runs"]
    if not checks or response["total_count"] != len(checks):
        return False
    actions_tests = [c for c in checks if c["name"] == "test" and c["app"]["slug"] == "github-actions"]
    if not actions_tests or not any(c["status"] == "completed" and c["conclusion"] == "success" for c in actions_tests):
        return False
    if any(c["status"] != "completed" or c["conclusion"] not in ("success", "skipped", "neutral") for c in checks):
        return False
    status = api(f"repos/{repo}/commits/{head}/status")
    return not status["statuses"] or status["state"] == "success"


def dispatch_images(repo):
    api(f"repos/{repo}/actions/workflows/sync.yml/dispatches", method="POST",
        data={"ref": "main", "inputs": {"channel": "both", "force": "false"}})


def base_is_green(repo, base, *, recover=False):
    runs = api(f"repos/{repo}/actions/workflows/sync.yml/runs?head_sha={base}&per_page=100")["workflow_runs"]
    eligible = [r for r in runs if r["event"] in ("push", "workflow_dispatch") and r["head_branch"] == "main"]
    if not eligible and recover:
        dispatch_images(repo)
        print("Recovered a missing main image workflow dispatch.")
    return bool(eligible) and eligible[0]["status"] == "completed" and eligible[0]["conclusion"] == "success"


def retry_checks(repo, head):
    runs = api(f"repos/{repo}/actions/workflows/sync.yml/runs?head_sha={head}&event=pull_request&per_page=100")["workflow_runs"]
    if runs and runs[0]["status"] == "completed" and runs[0]["conclusion"] in ("failure", "cancelled", "timed_out") and runs[0]["run_attempt"] < 3:
        api(f"repos/{repo}/actions/runs/{runs[0]['id']}/rerun", method="POST")
        print(f"Retrying PR checks, attempt {runs[0]['run_attempt'] + 1}/3.")


def plan(repo):
    base = current_base(repo)
    if not base_is_green(repo, base, recover=True):
        print("Waiting for current main image workflow to pass.")
        return
    prs = api(f"repos/{repo}/pulls?state=open&base=main&sort=created&direction=asc&per_page=100")
    for candidate in prs:
        if candidate["user"]["id"] != 49699333:
            continue
        number = candidate["number"]
        try:
            verified = trusted_pr(repo, number)
        except subprocess.CalledProcessError:
            print(f"PR #{number}: API changed while scanning; try other PRs.")
            continue
        if verified is None:
            print(f"PR #{number}: bot identity or dependency-only scope is not verified; keep open.")
            continue
        pr, files = verified
        if not checks_pass(repo, pr["head"]["sha"]):
            print(f"PR #{number}: checks have not passed; keep open.")
            retry_checks(repo, pr["head"]["sha"])
            continue
        if pr["mergeable"] is not True:
            print(f"PR #{number}: waiting for a clean merge.")
            continue
        values = {"number": number, "head": pr["head"]["sha"], "base": base,
                  "image_changed": str(any(f["filename"] == "Dockerfile" for f in files)).lower()}
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            for key, value in values.items():
                output.write(f"{key}={value}\n")
        print(f"PR #{number}: validate {values['head']} against main {base}.")
        return
    print("No merge-ready Dependabot PRs.")


def merge(repo):
    number = int(os.environ["PR_NUMBER"])
    head = os.environ["EXPECTED_HEAD"]
    base = os.environ["EXPECTED_BASE"]
    verified = trusted_pr(repo, number)
    if verified is None or verified[0]["head"]["sha"] != head or current_base(repo) != base:
        print("Source changed after validation; defer to the next queue run.")
        return
    if not checks_pass(repo, head) or not base_is_green(repo, base):
        print("Checks changed after validation; keep PR open.")
        return
    result = api(f"repos/{repo}/pulls/{number}/merge", method="PUT", data={"sha": head, "merge_method": "squash"})
    if not result["merged"]:
        raise RuntimeError(f"GitHub did not confirm PR #{number} was merged")
    print(f"Merged PR #{number}: {result['sha']}")
    # GITHUB_TOKEN merges do not generate a push-triggered workflow run.
    dispatch_images(repo)
    print("Dispatched image synchronization for main.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("plan", "merge"))
    args = parser.parse_args()
    {"plan": plan, "merge": merge}[args.mode](os.environ["GITHUB_REPOSITORY"])
