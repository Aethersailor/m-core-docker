import copy
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("sync", Path(__file__).parents[1] / "scripts" / "sync.py")
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


def fixture(version="alpha-123abcd"):
    assets = []
    for arch, _ in sync.PLATFORMS.values():
        name = f"mihomo-linux-{arch}-{version}.gz"
        data = gzip.compress(b"\x7fELF" + arch.encode(), mtime=0)
        assets.append({"name": name, "digest": "sha256:" + hashlib.sha256(data).hexdigest(),
                       "size": len(data), "browser_download_url": "https://example.com/" + name})
    return {"assets": assets}


def index():
    result = []
    for i, name in enumerate(sync.PLATFORMS):
        parts = name.split("/")
        platform = {"os": parts[0], "architecture": parts[1]}
        if len(parts) > 2:
            platform["variant"] = parts[2]
        result.append({"platform": platform, "digest": "sha256:" + str(i) * 64})
    return {"manifests": result}


class ReleaseTests(unittest.TestCase):
    def test_selects_exact_assets_not_legacy_or_older_go_builds(self):
        release = fixture()
        release["assets"].extend(fixture("v1-go123-alpha-123abcd")["assets"])
        selected = sync.select_assets(release, "alpha-123abcd")
        self.assertEqual(len(selected), 6)
        self.assertEqual(selected["linux/amd64/v2"]["name"], "mihomo-linux-amd64-v2-alpha-123abcd.gz")

    def test_incomplete_or_mixed_alpha_snapshot_is_rejected(self):
        for replacement in (None, "mihomo-linux-amd64-v3-alpha-456abcd.gz"):
            release = fixture()
            if replacement:
                release["assets"][2]["name"] = replacement
            else:
                release["assets"].pop()
            with self.assertRaises(ValueError):
                sync.select_assets(release, "alpha-123abcd")

    def test_unsigned_or_duplicated_asset_is_rejected(self):
        release = fixture()
        release["assets"][0]["digest"] = None
        with self.assertRaises(ValueError):
            sync.select_assets(release, "alpha-123abcd")
        release = fixture()
        release["assets"].append(release["assets"][0])
        with self.assertRaises(ValueError):
            sync.select_assets(release, "alpha-123abcd")

    def test_download_rejects_mutated_alpha_asset(self):
        asset = fixture()["assets"][0]
        with patch.object(sync, "fetch", return_value=b"replaced"):
            with self.assertRaisesRegex(ValueError, "failed verification"):
                sync.download(asset)

    def test_download_validates_size_and_sha256(self):
        asset = fixture()["assets"][0]
        data = gzip.compress(b"\x7fELFamd64-v1", mtime=0)
        with patch.object(sync, "fetch", return_value=data):
            self.assertEqual(sync.download(asset), data)
            with self.assertRaises(ValueError):
                sync.download({**asset, "size": asset["size"] + 1})

    def test_fingerprint_changes_with_upstream_or_builder(self):
        plan = {"version": "alpha-123abcd", "revision": "a" * 40,
                "assets": sync.select_assets(fixture(), "alpha-123abcd")}
        original = sync.fingerprint(plan, "builder-a")
        self.assertNotEqual(original, sync.fingerprint(plan, "builder-b"))
        changed = copy.deepcopy(plan)
        changed["assets"]["linux/amd64/v2"]["digest"] = "sha256:" + "f" * 64
        self.assertNotEqual(original, sync.fingerprint(changed, "builder-a"))

    def test_release_and_alpha_tags_do_not_overlap(self):
        self.assertEqual(sync.aliases({"channel": "alpha", "version": "alpha-123abcd"}), ["Alpha"])
        self.assertEqual(sync.aliases({"channel": "release", "version": "v1.2.3"}), ["v1.2.3", "latest"])


class ManifestTests(unittest.TestCase):
    def test_valid_index_preserves_all_instruction_levels(self):
        self.assertEqual(set(sync.manifests(index())), set(sync.PLATFORMS))
        self.assertEqual(sync.platform_name({"os": "linux", "architecture": "amd64", "variant": "v1"}), "linux/amd64")

    def test_missing_or_duplicate_platform_fails(self):
        bad = index()
        bad["manifests"].pop()
        with self.assertRaises(ValueError):
            sync.manifests(bad)
        bad = index()
        bad["manifests"].append(bad["manifests"][0])
        with self.assertRaisesRegex(ValueError, "Duplicate platform"):
            sync.manifests(bad)

    def test_skip_requires_every_registry_and_helper_tag(self):
        full = index()
        full["annotations"] = {sync.FINGERPRINT: "fingerprint"}
        expected = sync.manifests(full)

        def registry(reference, **kwargs):
            if "-amd64-v" in reference:
                level = int(reference[-1])
                platform = "linux/amd64" + (f"/v{level}" if level > 1 else "")
                return {"manifests": [{"digest": expected[platform]}]}
            return full

        plan = {"channel": "release", "version": "v1.2.3"}
        with patch.object(sync, "inspect", side_effect=registry) as mock:
            self.assertTrue(sync.up_to_date(["ghcr.io/test/image", "docker.io/test/image"], plan, "fingerprint"))
            self.assertEqual(mock.call_count, 16)
        with patch.object(sync, "inspect", side_effect=lambda ref, **kw: None if ref.endswith("latest-amd64-v3") else registry(ref)):
            self.assertFalse(sync.up_to_date(["test/image"], plan, "fingerprint"))

    def test_inspection_does_not_treat_auth_failure_as_missing_image(self):
        result = type("Result", (), {"returncode": 1, "stderr": "unauthorized: authentication required"})()
        with patch.object(sync.subprocess, "run", return_value=result):
            with self.assertRaisesRegex(RuntimeError, "unauthorized"):
                sync.inspect("test/image:Alpha", optional=True)

    def test_both_registries_are_verified_before_promotion(self):
        plan = {"channel": "alpha", "version": "alpha-123abcd", "revision": "a" * 40,
                "assets": sync.select_assets(fixture(), "alpha-123abcd")}
        env = {"GITHUB_SHA": "a" * 40, "GHCR_IMAGE": "ghcr.io/test/image", "DOCKERHUB_IMAGE": "docker.io/test/image"}
        with patch.object(sync.os, "environ", env), \
             patch.object(sync, "resolve", return_value=plan), \
             patch.object(sync, "up_to_date", return_value=False), \
             patch.object(sync, "prepare", return_value={}), \
             patch.object(sync, "build", return_value="sha256:" + "a" * 64), \
             patch.object(sync, "verify", side_effect=[{}, ValueError("bad second registry")]), \
             patch.object(sync, "promote") as promote, \
             patch("sys.argv", ["sync.py", "--channel", "alpha"]):
            with self.assertRaisesRegex(ValueError, "bad second registry"):
                sync.main()
            promote.assert_not_called()


if __name__ == "__main__":
    unittest.main()
