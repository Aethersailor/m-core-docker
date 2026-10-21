#!/usr/bin/env python3
"""Smoke-test a Dependabot base-image update without credentials or publication."""

import json
import os
from pathlib import Path
import subprocess
import tempfile

import sync


def main():
    plan = sync.resolve("release")
    plan["assets"] = {p: a for p, a in plan["assets"].items() if p in ("linux/amd64", "linux/amd64/v3")}
    images = []
    with tempfile.TemporaryDirectory(prefix="mihomo-dependabot-") as tmp:
        context = Path(tmp)
        (context / ".codex-test.json").write_text(json.dumps({"purpose": "Dependabot candidate image validation",
            "repository": os.environ["GITHUB_REPOSITORY"], "owner": os.environ["GITHUB_RUN_ID"], "cleanup_boundary": tmp}))
        hashes = sync.prepare(context, plan)
        config = context / "smoke.yaml"
        config.write_text("mixed-port: 7890\nmode: rule\nlog-level: silent\nrules:\n  - MATCH,DIRECT\n")
        try:
            for platform in plan["assets"]:
                suffix = platform.rsplit("/", 1)[-1]
                image = f"mihomo-dependabot-{os.environ['GITHUB_RUN_ID']}:{suffix}"
                images.append(image)
                sync.run("docker", "buildx", "build", "--platform", platform, "--provenance=false", "--load", "--tag", image, str(context))
                common = ["docker", "run", "--rm", "--network", "none", "--platform", platform]
                actual = sync.run(*common, "--entrypoint", "sha256sum", image, "/mihomo", capture=True).split()[0]
                if actual != hashes[platform]:
                    raise ValueError("Candidate image changed the upstream binary")
                version = sync.run(*common, image, "-v", capture=True)
                if plan["version"] not in version:
                    raise ValueError("Candidate image version mismatch")
                sync.run(*common, "--mount", f"type=bind,src={config},dst=/smoke.yaml,readonly", image,
                         "-t", "-d", "/tmp/mihomo-smoke", "-f", "/smoke.yaml")
        finally:
            for image in images:
                subprocess.run(["docker", "image", "rm", image], check=False)


if __name__ == "__main__":
    main()
