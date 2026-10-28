"""Offline publisher behavior: real IPA verification and privacy scanning."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import btr_release
import release_privacy
import verify_btr_ipa


class ReleaseGateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.previous = Path.cwd()
        os.chdir(self.temporary.name)
        Path("dist").mkdir()
        self.source, self.upstream, self.author = "a" * 40, "b" * 40, "c" * 40
        self.version, self.build = "2.1.6", "5671"
        self.tag = f"v{self.version}-btr.{self.build}-{self.source[:12]}"
        self.ipa = Path("dist/release.ipa")
        self.write_ipa(b"clean application bytes")
        self.calls = []
        self.user = "aierlma"
        self.user_code = 0
        self.push_permission = True
        self.repository_name = btr_release.REPO
        self.remote_assets = None
        self.release_id = 271
        self.other_releases = []
        self.existing_draft = False
        self.remote_corruption = None
        self.before_upload = None
        self.scanned = []
        self.uploaded = []
        self.app_changes = ["lib/main.dart"]

    def tearDown(self):
        os.chdir(self.previous)
        self.temporary.cleanup()

    def write_ipa(self, payload):
        with zipfile.ZipFile(self.ipa, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("Payload/Runner.app/Info.plist", plistlib.dumps({
                "CFBundlePackageType": "APPL",
                "CFBundleIdentifier": "com.example.piliplus.btr",
                "CFBundleShortVersionString": "2.1.5671",
                "CFBundleVersion": self.build,
                "PiliPlusUpstreamVersion": self.version,
                "MinimumOSVersion": "15.0",
            }))
            archive.writestr("Payload/Runner.app/Runner", payload)
        self.info = verify_btr_ipa.verify(self.ipa, self.version, self.build,
                                        self.source, self.upstream, self.author)
        Path("dist/build-info.json").write_text(json.dumps(self.info))

    def command(self, *args, **kwargs):
        self.calls.append(args)
        if args == ("gh", "api", "user"):
            return subprocess.CompletedProcess(args, self.user_code,
                                               json.dumps({"login": self.user}), "")
        if args == ("gh", "api", f"repos/{btr_release.REPO}"):
            value = {"full_name": self.repository_name,
                     "permissions": {"push": self.push_permission}}
            return subprocess.CompletedProcess(args, 0, json.dumps(value), "")
        prefix = f"repos/{btr_release.REPO}/"
        if args[:2] == ("gh", "api") and args[2].startswith(prefix):
            value = self.release_api(args[2][len(prefix):])
            return subprocess.CompletedProcess(args, int(value is None),
                json.dumps(value), "gh: Not Found (HTTP 404)" if value is None else "")
        if args[:2] == ("git", "push"):
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[:3] in (("gh", "release", "create"), ("gh", "release", "upload")):
            self.remote_assets = []
            for name in args[4:6]:
                path = Path(name)
                self.uploaded.append(path)
                # The uploaded files are private snapshots, not dist aliases.
                self.assertEqual(path.stat().st_mode & 0o777, 0o400)
                self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
                self.remote_assets.append({"name": path.name, "state": "uploaded",
                    "size": path.stat().st_size,
                    "digest": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()})
            if self.remote_corruption == "digest":
                self.remote_assets[0]["digest"] = "sha256:" + "0" * 64
            elif self.remote_corruption == "size":
                self.remote_assets[0]["size"] += 1
            elif self.remote_corruption == "metadata":
                self.remote_assets[1]["digest"] = "sha256:" + "0" * 64
            elif self.remote_corruption == "extra":
                self.remote_assets.append({"name": "unexpected.bin"})
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[:3] == ("gh", "release", "edit"):
            return subprocess.CompletedProcess(args, 0, "", "")
        raise AssertionError(f"Unexpected mocked command: {args[:3]}")

    def release_api(self, path):
        self.calls.append(("api", path))
        # GitHub's by-tag REST contract covers published releases, not drafts.
        if path.startswith("releases/tags/"):
            return None
        if self.remote_assets is None and self.before_upload:
            self.before_upload()
            self.before_upload = None
        release = {"id": self.release_id, "tag_name": self.tag,
                   "draft": True, "assets": self.remote_assets or []}
        exists = self.existing_draft or self.remote_assets is not None
        if path.startswith("releases?per_page=100&page="):
            page = int(path.rsplit("=", 1)[1])
            releases = self.other_releases + ([release] if exists else [])
            return releases[(page - 1) * 100:page * 100]
        if path == f"releases/{self.release_id}":
            if self.remote_corruption == "release_id":
                release["id"] += 1
            elif self.remote_corruption == "tag":
                release["tag_name"] = "another-tag"
            elif self.remote_corruption == "published":
                release["draft"] = False
            return release if exists else None
        raise AssertionError(f"Unexpected mocked release endpoint: {path}")

    def publish(self, *, environment=None, scanner=None):
        original_scan = release_privacy.assert_public_artifact
        def scan(path, **kwargs):
            self.scanned.append(Path(path))
            return (scanner or original_scan)(path, **kwargs)
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, environment or {}, clear=True))
            stack.enter_context(patch.object(btr_release, "validate_candidate"))
            stack.enter_context(patch.object(btr_release, "identity", return_value=(self.version, self.build, self.tag)))
            stack.enter_context(patch.object(btr_release, "released_source", return_value="d" * 40))
            stack.enter_context(patch.object(btr_release, "application_changes", return_value=self.app_changes))
            stack.enter_context(patch.object(btr_release, "check_latest_release_progress"))
            stack.enter_context(patch.object(btr_release, "close_resolved_failures"))
            stack.enter_context(patch.object(btr_release, "run", side_effect=self.command))
            stack.enter_context(patch.object(release_privacy, "assert_public_artifact", side_effect=scan))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            btr_release.publish("d" * 40, self.upstream, self.author)

    def assert_no_remote_writes(self):
        self.assertFalse(any(call[:2] == ("git", "push") or call[:2] == ("gh", "release")
                             for call in self.calls))

    def assert_not_public(self):
        self.assertFalse(any(call[:3] == ("gh", "release", "edit") for call in self.calls))
        self.assertFalse(any(call[:2] == ("git", "push") and
                             f"{self.source}:refs/heads/btr" in call for call in self.calls))

    def test_clean_snapshot_is_scanned_uploaded_and_digest_checked(self):
        self.publish()
        self.assertEqual(self.scanned, self.uploaded[:1])
        self.assertNotEqual(self.scanned[0], self.ipa.resolve())
        self.assertFalse(self.scanned[0].exists())
        edit = next(i for i, call in enumerate(self.calls) if call[:3] == ("gh", "release", "edit"))
        readback = max(i for i, call in enumerate(self.calls) if call[0] == "api")
        self.assertLess(readback, edit)

    def test_private_home_path_refuses_before_push_or_upload(self):
        self.write_ipa(b"/Users/synthetic_private_person/project/source")
        with self.assertRaisesRegex(ValueError, "privacy gate") as caught:
            self.publish()
        self.assertNotIn("synthetic_private_person", str(caught.exception))
        self.assert_no_remote_writes()

    def test_existing_draft_recovery_verifies_replacement_assets(self):
        self.existing_draft = True
        self.publish()
        self.assertTrue(any(call[:3] == ("gh", "release", "upload") for call in self.calls))
        edit = next(call for call in self.calls if call[:3] == ("gh", "release", "edit"))
        self.assertIn("--notes-file", edit)
        self.assertEqual(self.scanned, self.uploaded[:1])

    def test_draft_tag_endpoint_404_still_recovers_draft_on_later_page(self):
        self.existing_draft = True
        self.other_releases = [{"id": 1000 + index, "tag_name": f"old-{index}"}
                               for index in range(100)]
        with patch.object(btr_release, "run", side_effect=self.command):
            self.assertIsNone(btr_release.api(f"releases/tags/{self.tag}"))
        self.publish()
        self.assertIn(("api", "releases?per_page=100&page=2"), self.calls)
        self.assertIn(("api", f"releases/{self.release_id}"), self.calls)
        self.assertTrue(any(call[:3] == ("gh", "release", "upload") for call in self.calls))
        self.assertFalse(any(call[:3] == ("gh", "release", "create") for call in self.calls))
        self.assertTrue(any(call[:3] == ("gh", "release", "edit") for call in self.calls))

    def test_new_draft_is_read_by_id_before_publication(self):
        self.publish()
        create = next(i for i, call in enumerate(self.calls) if call[:3] == ("gh", "release", "create"))
        readback = self.calls.index(("api", f"releases/{self.release_id}"))
        edit = next(i for i, call in enumerate(self.calls) if call[:3] == ("gh", "release", "edit"))
        self.assertLess(create, readback)
        self.assertLess(readback, edit)
        self.assertFalse(any(call[0] == "api" and call[1].startswith("releases/tags/")
                             for call in self.calls))

    def test_ambiguous_draft_tags_refuse_upload(self):
        self.existing_draft = True
        self.other_releases = [{"id": self.release_id + 1, "tag_name": self.tag,
                                "draft": True, "assets": []}]
        with self.assertRaisesRegex(ValueError, "uniquely resolve"):
            self.publish()
        self.assertEqual(self.uploaded, [])
        self.assert_not_public()

    def test_readback_release_identity_or_state_change_refuses_publication(self):
        for corruption in ("release_id", "tag", "published"):
            with self.subTest(corruption=corruption):
                self.remote_assets, self.calls = None, []
                self.remote_corruption = corruption
                with self.assertRaisesRegex(ValueError, "draft remains unpublished"):
                    self.publish()
                self.assert_not_public()

    def test_credentials_refuse_before_push_or_upload(self):
        self.write_ipa(b"SESSDATA=" + b"s" * 32)
        with self.assertRaisesRegex(ValueError, "privacy gate"):
            self.publish()
        self.assert_no_remote_writes()

    def test_wrong_identity_refuses_before_push_or_upload(self):
        self.user = "another-account"
        with self.assertRaisesRegex(ValueError, "identity"):
            self.publish()
        self.assert_no_remote_writes()

    def test_scanner_crash_is_redacted_and_refuses_before_upload(self):
        def crash(*args, **kwargs):
            raise RuntimeError("sensitive synthetic value /Users/private_person/key")
        with self.assertRaisesRegex(ValueError, "privacy gate") as caught:
            self.publish(scanner=crash)
        self.assertNotIn("private_person", str(caught.exception))
        self.assertIsNone(caught.exception.__cause__)
        self.assertTrue(caught.exception.__suppress_context__)
        self.assert_no_remote_writes()

    def test_incomplete_scanner_report_cannot_admit_package(self):
        with self.assertRaisesRegex(ValueError, "privacy gate"):
            self.publish(scanner=lambda *a, **k: {"status": "pass", "complete": False})
        self.assert_no_remote_writes()

    def test_missing_policy_refuses_before_push_or_upload(self):
        with patch.object(btr_release, "PRIVACY_POLICY", Path("missing-policy.json")):
            with self.assertRaisesRegex(ValueError, "privacy gate"):
                self.publish()
        self.assert_no_remote_writes()

    def test_exactly_one_ipa_is_required(self):
        Path("dist/extra.ipa").write_bytes(self.ipa.read_bytes())
        with self.assertRaisesRegex(ValueError, "exactly one IPA"):
            self.publish()
        self.assert_no_remote_writes()

    def test_zero_ipas_are_rejected(self):
        self.ipa.unlink()
        with self.assertRaisesRegex(ValueError, "exactly one IPA"):
            self.publish()
        self.assert_no_remote_writes()

    def test_original_dist_replacement_cannot_change_uploaded_snapshot(self):
        original_scan = release_privacy.assert_public_artifact
        def replace_original(path, **kwargs):
            report = original_scan(path, **kwargs)
            self.ipa.write_bytes(b"/Users/private_person/replaced")
            return report
        expected = self.info["sha256"]
        self.publish(scanner=replace_original)
        self.assertEqual(self.remote_assets[0]["digest"], "sha256:" + expected)

    def test_staged_file_mutation_after_scan_refuses_upload(self):
        def mutate():
            path = self.scanned[0]
            path.chmod(0o600)
            path.write_bytes(path.read_bytes() + b"changed after scanning")
            path.chmod(0o400)
        self.before_upload = mutate
        with self.assertRaises(release_privacy.PrivacyGuardError):
            self.publish()
        self.assertEqual(self.uploaded, [])
        self.assert_not_public()

    def test_remote_digest_size_and_extra_assets_refuse_publication(self):
        for corruption in ("digest", "size", "metadata", "extra"):
            with self.subTest(corruption=corruption):
                self.remote_assets, self.calls = None, []
                self.remote_corruption = corruption
                with self.assertRaisesRegex(ValueError, "draft remains unpublished"):
                    self.publish()
                self.assert_not_public()

    def test_actions_token_without_user_endpoint_needs_real_push_permission(self):
        self.user_code = 1
        environment = {"GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": btr_release.REPO}
        self.push_permission = False
        with self.assertRaisesRegex(ValueError, "identity"):
            self.publish(environment=environment)
        self.assert_no_remote_writes()
        self.calls = []
        self.push_permission = True
        self.publish(environment=environment)
        self.assertIn(("gh", "api", f"repos/{btr_release.REPO}"), self.calls)

    def test_actions_environment_does_not_admit_another_human_account(self):
        self.user = "another-account"
        with self.assertRaisesRegex(ValueError, "identity"):
            self.publish(environment={"GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": btr_release.REPO})
        self.assert_no_remote_writes()

    def test_actions_token_from_another_repository_is_rejected(self):
        self.user_code = 1
        with self.assertRaisesRegex(ValueError, "restricted"):
            self.publish(environment={"GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": "someone/fork"})
        self.assert_no_remote_writes()

    def test_actions_bot_is_allowed_only_with_verified_repository_permissions(self):
        self.user = "github-actions[bot]"
        with self.assertRaisesRegex(ValueError, "identity"):
            self.publish()
        self.assert_no_remote_writes()
        self.calls = []
        self.publish(environment={"GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": btr_release.REPO})

    def test_docs_only_sync_still_runs_the_artifact_gate(self):
        self.app_changes = []
        self.write_ipa(b"/Users/private_person/source")
        with self.assertRaisesRegex(ValueError, "privacy gate"):
            self.publish()
        self.assert_no_remote_writes()
        self.write_ipa(b"clean application")
        self.publish()
        self.assertEqual(self.uploaded, [])
        self.assertTrue(any(call[:2] == ("git", "push") for call in self.calls))

    def test_privacy_packaging_inputs_are_release_relevant(self):
        paths = ["tool/prepare_public_ios.py", "tool/release_privacy.py",
                 "tool/release-privacy-policy.json", "tool/README.md", "docs/notes.md"]
        result = subprocess.CompletedProcess([], 0, "\0".join(paths) + "\0", "")
        with patch.object(btr_release, "run", return_value=result):
            self.assertEqual(btr_release.application_changes("a" * 40, "b" * 40), paths[:3])


if __name__ == "__main__":
    unittest.main()
