import importlib.util
import os
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("queue", Path(__file__).parents[1] / "scripts" / "dependabot_queue.py")
queue = importlib.util.module_from_spec(spec)
spec.loader.exec_module(queue)


def fixture():
    return {"state": "open", "draft": False, "base": {"ref": "main"},
            "user": {"login": "dependabot[bot]", "id": 49699333},
            "head": {"sha": "head", "ref": "dependabot/github_actions/checkout-7", "repo": {"full_name": "owner/repo"}},
            "changed_files": 1, "commits": 1, "mergeable": True}


class QueueTests(unittest.TestCase):
    def test_major_dependency_update_is_eligible(self):
        responses = [fixture(), [{"filename": ".github/workflows/sync.yml", "status": "modified"}],
                     [{"author": {"id": 49699333}, "commit": {"verification": {"verified": True}}}]]
        with patch.object(queue, "api", side_effect=responses):
            self.assertIsNotNone(queue.trusted_pr("owner/repo", 1))

    def test_fork_and_spoofed_bot_are_rejected(self):
        for variant in ("fork", "identity"):
            pr = fixture()
            if variant == "fork":
                pr["head"]["repo"]["full_name"] = "attacker/repo"
            else:
                pr["user"]["id"] = 123
            with patch.object(queue, "api", return_value=pr):
                self.assertIsNone(queue.trusted_pr("owner/repo", 1))

    def test_script_changes_and_unverified_commits_are_rejected(self):
        for filename, verified in (("scripts/sync.py", True), ("Dockerfile", False)):
            responses = [fixture(), [{"filename": filename, "status": "modified"}],
                         [{"author": {"id": 49699333}, "commit": {"verification": {"verified": verified}}}]]
            with patch.object(queue, "api", side_effect=responses):
                self.assertIsNone(queue.trusted_pr("owner/repo", 1))

    def test_head_or_base_change_prevents_merge(self):
        env = {"PR_NUMBER": "1", "EXPECTED_HEAD": "head", "EXPECTED_BASE": "base"}
        for change in ("head", "base"):
            pr = fixture()
            if change == "head":
                pr["head"]["sha"] = "new-head"
            with patch.dict(os.environ, env), patch.object(queue, "trusted_pr", return_value=(pr, [])), \
                 patch.object(queue, "current_base", return_value="new-base" if change == "base" else "base"), \
                 patch.object(queue, "api") as api:
                queue.merge("owner/repo")
                api.assert_not_called()

    def test_verified_merge_is_sha_conditional_and_dispatches_publication(self):
        env = {"PR_NUMBER": "1", "EXPECTED_HEAD": "head", "EXPECTED_BASE": "base"}
        with patch.dict(os.environ, env), patch.object(queue, "trusted_pr", return_value=(fixture(), [])), \
             patch.object(queue, "current_base", return_value="base"), patch.object(queue, "checks_pass", return_value=True), \
             patch.object(queue, "base_is_green", return_value=True), \
             patch.object(queue, "api", side_effect=[{"merged": True, "sha": "merged"}, None]) as api:
            queue.merge("owner/repo")
            self.assertEqual(api.call_args_list[0].kwargs["data"], {"sha": "head", "merge_method": "squash"})
            self.assertEqual(api.call_args_list[1].args[0], "repos/owner/repo/actions/workflows/sync.yml/dispatches")

    def test_retry_is_bounded(self):
        for attempt, expected_calls in ((1, 2), (3, 1)):
            run = {"id": 123, "status": "completed", "conclusion": "failure", "run_attempt": attempt}
            with patch.object(queue, "api", side_effect=[{"workflow_runs": [run]}, None]) as api:
                queue.retry_checks("owner/repo", "head")
                self.assertEqual(api.call_count, expected_calls)

    def test_missing_post_merge_workflow_has_recovery(self):
        with patch.object(queue, "api", side_effect=[{"workflow_runs": []}, None]) as api:
            self.assertFalse(queue.base_is_green("owner/repo", "merged", recover=True))
            self.assertEqual(api.call_args_list[1].kwargs["method"], "POST")


if __name__ == "__main__":
    unittest.main()
