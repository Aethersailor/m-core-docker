import os
from pathlib import Path
import sys
import unittest
import urllib.error
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import reconcile


def package_version(version_id, *tags):
    return {"id": version_id, "metadata": {"container": {"tags": list(tags)}}}


class WithdrawalTests(unittest.TestCase):
    def test_only_stable_version_and_its_helpers_are_candidates(self):
        published = {"v1.2.3", "v1.2.3-amd64-v1", "v1.2.3-amd64-v3", "latest", "Alpha", "manual", "v1.2.30"}
        with patch.object(reconcile, "upstream_release", side_effect=lambda tag: None if tag == "v1.2.3" else {}):
            self.assertEqual(reconcile.deletion_tags(published, "release", "v1.2.30"),
                             {"v1.2.3", "v1.2.3-amd64-v1", "v1.2.3-amd64-v3"})

    def test_no_stable_release_removes_latest_aliases(self):
        published = {"v1.2.3", "latest", "latest-amd64-v1", "latest-amd64-v2", "latest-amd64-v3", "Alpha"}
        with patch.object(reconcile, "upstream_release", return_value=None):
            self.assertEqual(reconcile.deletion_tags(published, "release", None), published - {"Alpha"})

    def test_alpha_withdrawal_does_not_touch_stable(self):
        published = {"Alpha", "Alpha-amd64-v1", "Alpha-amd64-v3", "latest", "v1.2.3", "Alpha-test"}
        with patch.object(reconcile, "upstream_release", return_value=None):
            self.assertEqual(reconcile.deletion_tags(published, "alpha", None),
                             {"Alpha", "Alpha-amd64-v1", "Alpha-amd64-v3"})

    def test_only_explicit_not_found_means_withdrawn(self):
        for status in (403, 429, 500, 503):
            with patch.object(reconcile.sync, "api", side_effect=urllib.error.HTTPError("test", status, "error", {}, None)):
                with self.assertRaises(urllib.error.HTTPError):
                    reconcile.upstream_release("v1.2.3")
        with patch.object(reconcile.sync, "api", side_effect=urllib.error.HTTPError("test", 404, "missing", {}, None)):
            self.assertIsNone(reconcile.upstream_release("v1.2.3"))

    def test_shared_live_tag_prevents_digest_deletion(self):
        versions = [package_version(1, "v1.2.3", "latest"), package_version(2, "v1.2.3-amd64-v1")]
        with self.assertRaisesRegex(ValueError, "shared with surviving tags"):
            reconcile.safe_ghcr_deletions(versions, {"v1.2.3", "v1.2.3-amd64-v1"})

    def test_untagged_platform_manifests_are_preserved(self):
        versions = [package_version(1, "v1.2.3"), package_version(2), package_version(3, "latest", "v1.2.4")]
        self.assertEqual(reconcile.safe_ghcr_deletions(versions, {"v1.2.3"}), [1])

    def test_latest_must_have_reached_official_fallback(self):
        with patch.object(reconcile.sync, "inspect", return_value={"annotations": {"org.opencontainers.image.version": "v1.2.3"}}):
            with self.assertRaisesRegex(ValueError, "rolled back"):
                reconcile.assert_latest(["ghcr.io/test/image", "docker.io/test/image"], "v1.2.2")

    def test_republished_release_aborts_pending_deletion(self):
        env = {"GITHUB_REPOSITORY": "Owner/mihomo-docker", "GHCR_IMAGE": "ghcr.io/owner/mihomo-docker",
               "DOCKERHUB_IMAGE": "docker.io/owner/mihomo-docker", "DOCKERHUB_USERNAME": "owner"}
        with patch.dict(os.environ, env), \
             patch.object(reconcile.sync, "api", return_value={"id": 123, "private": False}), \
             patch.object(reconcile, "current_stable", return_value="v1.2.4"), \
             patch.object(reconcile, "gh_versions", return_value=[package_version(1, "v1.2.3")]), \
             patch.object(reconcile, "hub_tags", return_value={"v1.2.3"}), \
             patch.object(reconcile, "deletion_tags", side_effect=[{"v1.2.3"}, set()]), \
             patch.object(reconcile.time, "sleep"), \
             patch.object(reconcile, "delete_tags") as delete:
            with self.assertRaisesRegex(ValueError, "returned during withdrawal"):
                reconcile.reconcile("release")
            delete.assert_not_called()

    def test_delete_targets_are_tag_scoped_and_verified(self):
        versions = [package_version(7, "v1.2.3"), package_version(8, "latest", "v1.2.4")]
        with patch.dict(os.environ, {"GH_TOKEN": "not-a-real-token"}), \
             patch.object(reconcile, "hub_login", return_value="not-a-real-token"), \
             patch.object(reconcile, "request") as request, \
             patch.object(reconcile, "hub_tags", return_value={"latest", "v1.2.4"}), \
             patch.object(reconcile, "gh_versions", return_value=[versions[1]]):
            reconcile.delete_tags("Owner", "mihomo-docker", "owner/mihomo-docker", versions, {"v1.2.3"}, {"v1.2.3"})
            urls = [call.args[0] for call in request.call_args_list]
            self.assertEqual(urls, ["https://hub.docker.com/v2/repositories/owner/mihomo-docker/tags/v1.2.3/",
                                    "https://api.github.com/users/Owner/packages/container/mihomo-docker/versions/7"])


if __name__ == "__main__":
    unittest.main()
