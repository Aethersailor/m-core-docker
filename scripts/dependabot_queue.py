#!/usr/bin/env python3
"""Serialize verified Dependabot merges and explicitly trigger image publication."""

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
    if response["total_count"] != len(checks):
        return False
    if any(c["status"] != "completed" or c["conclusion"] not in ("success", "skipped", "neutral") for c in checks):
        return False
    status = api(f"repos/{repo}/commits/{head}/status")
    return not status["statuses"] or status["state"] == "success"


def dispatch_images(repo):
    api(f"repos/{repo}/actions/workflows/sync.yml/dispatches", method="POST",
        data={"ref": "main", "inputs": {"channel": "both", "force": "false"}})


def recover_publication(repo, base):
    runs = api(f"repos/{repo}/actions/workflows/sync.yml/runs?head_sha={base}&per_page=100")["workflow_runs"]
    eligible = [r for r in runs if r["event"] in ("push", "workflow_dispatch") and r["head_branch"] == "main"]
    if not eligible:
        dispatch_images(repo)
        print("Recovered a missing main image workflow dispatch.")


def main(repo):
    base = current_base(repo)
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
        pr, _ = verified
        if not checks_pass(repo, pr["head"]["sha"]):
            print(f"PR #{number}: checks have not passed; keep open.")
            continue
        if pr["mergeable"] is not True:
            print(f"PR #{number}: waiting for a clean merge.")
            continue
        head = pr["head"]["sha"]
        latest = api(f"repos/{repo}/pulls/{number}")
        if latest["head"]["sha"] != head or current_base(repo) != base:
            print("Source changed while scanning; retry next run.")
            return
        result = api(f"repos/{repo}/pulls/{number}/merge", method="PUT", data={"sha": head, "merge_method": "squash"})
        if not result["merged"]:
            raise RuntimeError(f"GitHub did not confirm PR #{number} was merged")
        print(f"Merged PR #{number}: {result['sha']}")
        # GITHUB_TOKEN merges do not generate a push-triggered workflow run.
        dispatch_images(repo)
        print("Dispatched image synchronization for main.")
        return
    print("No merge-ready Dependabot PRs.")
    recover_publication(repo, base)


if __name__ == "__main__":
    main(os.environ["GITHUB_REPOSITORY"])
