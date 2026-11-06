"""Behavior checks for the source and publication identity guard."""
import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import check_public_identity as guard


class PublicIdentityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.git("init", "-b", "main")
        self.git("config", "user.name", "aierlma")
        self.git("config", "user.email", "55280375+aierlma@users.noreply.github.com")

    def tearDown(self):
        self.directory.cleanup()

    def git(self, *arguments, **kwargs):
        return subprocess.run(["git", "-C", str(self.root), *arguments], check=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs)

    def commit(self, content="public source", filename="source.txt"):
        (self.root / filename).write_text(content)
        self.git("add", ".")
        self.git("commit", "-m", "Public change")

    def scan(self, terms=None):
        return guard.check_public_identity(self.root, terms if terms is not None else ["PrivatePerson"])

    def test_private_word_blocks_without_disclosing_value_or_path(self):
        self.commit("Local work for pRiVaTePeRsOn", "personal-notes.txt")
        report = self.scan()
        self.assertEqual(report["status"], "blocked")
        self.assertTrue(report["complete"])
        self.assertEqual(report["findings"], [{"rule": "private_file_content", "file_index": 0}])
        rendered = json.dumps(report).lower()
        self.assertNotIn("privateperson", rendered)
        self.assertNotIn("personal-notes", rendered)

    def test_word_boundaries_and_utf16_binary_are_checked(self):
        self.commit("PrivatePersonSuffix PrefixPrivatePerson 私PrivatePerson人")
        self.assertEqual(self.scan()["status"], "pass")
        (self.root / "source.txt").write_bytes(b"x" + "PrivatePerson".encode("utf-16-be"))
        self.assertEqual(self.scan()["status"], "blocked")
        (self.root / "source.txt").write_text("file:///private/build/output")
        self.assertEqual(self.scan(["/private/build/"])["status"], "blocked")

    def test_missing_empty_or_malformed_config_fails_closed(self):
        self.commit()
        for value in (None, "[]", "not json", '{"secret":"hidden"}', '[""]'):
            env = {"GITHUB_REPOSITORY": guard.REPOSITORY}
            if value is not None:
                env["RELEASE_PRIVATE_TERMS_JSON"] = value
            with self.subTest(value=value), patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertNotEqual(guard.main(), 0)
                report = json.loads(output.getvalue())
                self.assertEqual(report["status"], "error")
                self.assertFalse(report["complete"])
                self.assertNotIn("hidden", output.getvalue())

    def test_wrong_head_identity_blocks_but_upstream_ancestors_are_allowed(self):
        self.git("config", "user.name", "Upstream Maintainer")
        self.git("config", "user.email", "upstream@example.invalid")
        self.commit()
        report = self.scan()
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(len(report["findings"]), 2)
        self.git("config", "user.name", "aierlma")
        self.git("config", "user.email", "55280375+aierlma@users.noreply.github.com")
        self.commit("updated public source")
        self.assertEqual(self.scan()["status"], "pass")
        self.git("config", "user.name", "github-actions[bot]")
        self.git("config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
        self.commit("automatic merge")
        self.assertEqual(self.scan()["status"], "pass")

    def webflow_commit(self):
        (self.root / "source.txt").write_text("public source")
        self.git("add", ".")
        env = dict(os.environ, GIT_AUTHOR_NAME="Aierlma",
                   GIT_AUTHOR_EMAIL="55280375+aierlma@users.noreply.github.com",
                   GIT_COMMITTER_NAME="GitHub", GIT_COMMITTER_EMAIL="noreply@github.com")
        self.git("commit", "-m", "Public change", env=env)
        sha = self.git("rev-parse", "HEAD").stdout.decode().strip()
        return {"sha": sha, "author": {"login": "aierlma"}, "committer": {"login": "web-flow"},
                "commit": {"author": {"name": "Aierlma", "email": env["GIT_AUTHOR_EMAIL"]},
                           "committer": {"name": "GitHub", "email": "noreply@github.com"},
                           "verification": {"verified": True, "reason": "valid"}}}

    def test_verified_webflow_commit_is_accepted_without_skipping_private_scan(self):
        record = self.webflow_commit()
        with patch.dict(os.environ, {"GH_TOKEN": "test-token"}), \
                patch.object(guard, "_github_commit_record", return_value=record) as fetch:
            self.assertEqual(self.scan()["status"], "pass")
            fetch.assert_called_once_with(record["sha"])
            self.assertEqual(self.scan(["GitHub"])["status"], "blocked")
            (self.root / "source.txt").write_text("PrivatePerson")
            report = self.scan()
            self.assertEqual(report["status"], "blocked")
            self.assertNotIn("PrivatePerson", json.dumps(report))

    def test_unverified_or_mismatched_webflow_commit_is_rejected(self):
        record = self.webflow_commit()
        variants = []
        for path, value in ((('sha',), '0' * 40),
                            (('author', 'login'), 'someone-else'),
                            (('committer', 'login'), 'someone-else'),
                            (('commit', 'author', 'name'), 'someone-else'),
                            (('commit', 'author', 'email'), 'other@example.invalid'),
                            (('commit', 'committer', 'email'), 'other@example.invalid'),
                            (('commit', 'verification', 'verified'), False),
                            (('commit', 'verification', 'reason'), 'unknown_key')):
            bad = copy.deepcopy(record)
            target = bad
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
            variants.append(bad)
        variants.extend(({}, None))
        with patch.dict(os.environ, {"GH_TOKEN": "test-token"}):
            for bad in variants:
                with self.subTest(record=bad), patch.object(guard, "_github_commit_record", return_value=bad):
                    self.assertEqual(self.scan()["status"], "blocked")
            for error in (OSError(), subprocess.TimeoutExpired("gh", 20), ValueError()):
                with self.subTest(error=type(error).__name__), patch.object(guard, "_github_commit_record", side_effect=error):
                    self.assertEqual(self.scan()["status"], "blocked")
        with patch.dict(os.environ, {"GH_TOKEN": ""}), patch.object(guard, "_github_commit_record") as fetch:
            self.assertEqual(self.scan()["status"], "blocked")
            fetch.assert_not_called()

    def test_webflow_shape_rejects_other_authors_and_non_noreply_addresses(self):
        self.webflow_commit()
        bad_shapes = [
            ["SomeoneElse", "55280375+aierlma@users.noreply.github.com", "GitHub", "noreply@github.com"],
            ["Aierlma", "other@example.invalid", "GitHub", "noreply@github.com"],
            ["Aierlma", "55280375+aierlma@users.noreply.github.com", "GitHub", "other@example.invalid"],
        ]
        with patch.dict(os.environ, {"GH_TOKEN": "test-token"}), patch.object(guard, "_github_commit_record") as fetch:
            for identity in bad_shapes:
                with self.subTest(identity=identity):
                    self.assertFalse(guard._verified_webflow_identity(self.root, identity, "aierlma"))
            fetch.assert_not_called()

    def test_verification_uses_fixed_github_endpoint_without_token_in_arguments(self):
        sha = "a" * 40
        response = subprocess.CompletedProcess([], 0, stdout='{"sha":"ok"}', stderr="")
        with patch.object(guard.subprocess, "run", return_value=response) as run:
            self.assertEqual(guard._github_commit_record(sha), {"sha": "ok"})
        self.assertEqual(run.call_args.args[0], ["gh", "api", "--hostname", "github.com",
                                               f"repos/aierlma/PiliPlus/commits/{sha}"])
        self.assertEqual(run.call_args.kwargs["timeout"], 20)

    def test_symlink_or_large_file_refuses_incomplete_scan(self):
        self.commit()
        with tempfile.TemporaryDirectory() as external:
            target = Path(external) / "private.txt"
            target.write_text("PrivatePerson")
            (self.root / "source.txt").unlink()
            (self.root / "source.txt").symlink_to(target)
            report = self.scan()
            self.assertEqual(report["status"], "error")
            self.assertFalse(report["complete"])
        (self.root / "source.txt").unlink()
        (self.root / "source.txt").write_bytes(b"clean bytes")
        with patch.object(guard, "MAX_FILE_BYTES", 2):
            self.assertEqual(self.scan()["status"], "error")

    def test_repository_identity_cannot_be_changed_by_environment(self):
        env = {"GITHUB_REPOSITORY": "school/PiliPlus", "RELEASE_PRIVATE_TERMS_JSON": '["PrivatePerson"]'}
        with patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertNotEqual(guard.main(), 0)
        self.assertEqual(json.loads(output.getvalue())["error_code"], "identity_configuration_invalid")

    def reviewed_line(self):
        line = "  // Public reference: https://PrivatePerson.example/reference"
        entries = [{"path": "source.txt", "sha256": hashlib.sha256(line.encode()).hexdigest(),
                    "verified_upstream_commits": ["1" * 40, "2" * 40]}]
        return line, entries

    def test_reviewed_line_only_exempts_exact_line_in_exact_file(self):
        line, entries = self.reviewed_line()
        self.commit(line + "\npublic source\n")
        self.assertEqual(self.scan()["status"], "blocked")
        report = guard.check_public_identity(self.root, ["PrivatePerson"], line_exceptions=entries)
        self.assertEqual(report["status"], "pass")
        (self.root / "source.txt").write_text(line + "\nPrivatePerson wrote this new line\n")
        report = guard.check_public_identity(self.root, ["PrivatePerson"], line_exceptions=entries)
        self.assertEqual(report["status"], "blocked")

    def test_modified_reviewed_line_does_not_match_its_original_hash(self):
        line, entries = self.reviewed_line()
        self.commit(line + " PrivatePerson private addition\n")
        report = guard.check_public_identity(self.root, ["PrivatePerson"], line_exceptions=entries)
        self.assertEqual(report["status"], "blocked")

    def test_reviewed_line_cannot_be_moved_to_another_file(self):
        line, entries = self.reviewed_line()
        self.commit(line + "\n", "another.txt")
        report = guard.check_public_identity(self.root, ["PrivatePerson"], line_exceptions=entries)
        self.assertEqual(report["status"], "blocked")

    def test_public_policy_is_strict_and_contains_only_reviewed_line_digest(self):
        policy = guard.load_public_policy()
        self.assertEqual(len(policy), 1)
        self.assertEqual(policy[0]["path"], "lib/http/api.dart")
        self.assertEqual(len(policy[0]["verified_upstream_commits"]), 2)
        self.commit()
        for entry in ({"path": "source.txt"}, {"path": "../source.txt", "sha256": "a" * 64,
                      "verified_upstream_commits": ["1" * 40]}):
            report = guard.check_public_identity(self.root, ["PrivatePerson"], line_exceptions=[entry])
            self.assertEqual(report["status"], "error")
            self.assertFalse(report["complete"])


if __name__ == "__main__":
    unittest.main()
