"""Offline failure and preservation checks for the unattended sync gates."""
import contextlib
import json
import os
import plistlib
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import prepare_btr_sync
import verify_btr_ipa
import btr_release


def git(*args):
    return subprocess.check_output(["git", *args], text=True).strip()


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.previous = Path.cwd()
        os.chdir(self.directory.name)
        git("init", "-b", "btr")
        git("config", "user.name", "test")
        git("config", "user.email", "test@example.invalid")
        Path(".github/workflows").mkdir(parents=True)
        Path(".github/workflows/build.yml").write_text("old official workflow")
        Path("README.md").write_text("original")
        Path("README-upstream.md").write_text("original")
        Path("source.dart").write_text("shared source")
        git("add", ".")
        git("commit", "-m", "common base")
        git("branch", "official")
        Path("btr.dart").write_text("BTR implementation")
        git("add", ".")
        git("commit", "-m", "original BTR")
        self.author_base = git("rev-parse", "HEAD")
        git("branch", "btr-author")
        Path(".gitattributes").write_text("README.md merge=keepPersonal\n")
        Path("README.md").write_text("personal BTR documentation")
        Path("btr.dart").write_text("BTR implementation")
        Path(".github/workflows/build.yml").write_text("personal workflow")
        git("add", ".")
        git("commit", "-m", "BTR fork")
        self.base = git("rev-parse", "HEAD")

    def tearDown(self):
        os.chdir(self.previous)
        self.directory.cleanup()

    def official(self, source="new official source"):
        git("checkout", "official")
        Path("README.md").write_text("updated official documentation")
        Path("source.dart").write_text(source)
        Path(".github/workflows/build.yml").write_text("updated official workflow")
        Path(".github/workflows/new.yml").write_text("new official workflow")
        git("add", ".")
        git("commit", "-m", "official update")
        sha = git("rev-parse", "HEAD")
        git("checkout", "btr")
        return sha

    def author(self):
        git("checkout", "btr-author")
        Path("btr.dart").write_text("updated BTR implementation")
        Path("README.md").write_text("updated BTR author documentation")
        Path(".github/workflows/build.yml").write_text("updated BTR workflow")
        git("add", ".")
        git("commit", "-m", "BTR author update")
        sha = git("rev-parse", "HEAD")
        git("checkout", "btr")
        return sha

    def test_merge_retains_btr_personal_docs_and_ci_but_updates_source(self):
        upstream = self.official()
        candidate = prepare_btr_sync.prepare(upstream, self.base, self.author_base)
        self.assertNotEqual(candidate, self.base)
        self.assertEqual(Path("source.dart").read_text(), "new official source")
        self.assertEqual(Path("btr.dart").read_text(), "BTR implementation")
        self.assertEqual(Path("README.md").read_text(), "personal BTR documentation")
        self.assertEqual(Path("README-upstream.md").read_text(), "updated official documentation")
        self.assertEqual(Path(".github/workflows/build.yml").read_text(), "personal workflow")
        self.assertFalse(Path(".github/workflows/new.yml").exists())
        self.assertEqual(git("status", "--porcelain"), "")
        self.assertEqual(prepare_btr_sync.prepare(upstream, candidate, self.author_base), candidate)

    def test_btr_only_update_is_merged(self):
        btr_upstream = self.author()
        upstream = git("rev-parse", "official")
        candidate = prepare_btr_sync.prepare(upstream, self.base, btr_upstream)
        self.assertNotEqual(candidate, self.base)
        self.assertEqual(Path("btr.dart").read_text(), "updated BTR implementation")
        self.assertEqual(Path("source.dart").read_text(), "shared source")
        self.assertEqual(Path("README.md").read_text(), "personal BTR documentation")
        self.assertEqual(Path("README-BTR-upstream.md").read_text(), "updated BTR author documentation")
        self.assertEqual(Path(".github/workflows/build.yml").read_text(), "personal workflow")

    def test_both_source_updates_are_merged(self):
        upstream, btr_upstream = self.official(), self.author()
        candidate = prepare_btr_sync.prepare(upstream, self.base, btr_upstream)
        self.assertEqual(Path("source.dart").read_text(), "new official source")
        self.assertEqual(Path("btr.dart").read_text(), "updated BTR implementation")
        for sha in (upstream, btr_upstream):
            self.assertEqual(subprocess.run(["git", "merge-base", "--is-ancestor", sha, candidate]).returncode, 0)
        self.assertEqual(git("status", "--porcelain"), "")

    def test_lagging_btr_author_does_not_revert_new_official_source(self):
        upstream = self.official()
        current = prepare_btr_sync.prepare(upstream, self.base, self.author_base)
        btr_upstream = self.author()
        prepare_btr_sync.prepare(upstream, current, btr_upstream)
        self.assertEqual(Path("source.dart").read_text(), "new official source")
        self.assertEqual(Path("btr.dart").read_text(), "updated BTR implementation")

    def test_code_conflict_aborts_without_changing_branch(self):
        Path("source.dart").write_text("personal source change")
        git("add", ".")
        git("commit", "-m", "personal source")
        self.base = git("rev-parse", "HEAD")
        upstream = self.official()
        btr_upstream = self.author()
        with self.assertRaisesRegex(ValueError, "manual resolution"):
            prepare_btr_sync.prepare(upstream, self.base, btr_upstream)
        self.assertEqual(git("rev-parse", "HEAD"), self.base)
        self.assertEqual(git("status", "--porcelain"), "")
        self.assertEqual(Path("source.dart").read_text(), "personal source change")
        self.assertEqual(Path("btr.dart").read_text(), "BTR implementation")
        self.assertFalse(Path("README-BTR-upstream.md").exists())

    def test_btr_conflict_aborts_without_publishing_official_update(self):
        Path("btr.dart").write_text("personal BTR source change")
        git("add", ".")
        git("commit", "-m", "personal BTR source")
        self.base = git("rev-parse", "HEAD")
        upstream, btr_upstream = self.official(), self.author()
        with self.assertRaisesRegex(ValueError, "BTR author merge requires manual resolution"):
            prepare_btr_sync.prepare(upstream, self.base, btr_upstream)
        self.assertEqual(git("rev-parse", "HEAD"), self.base)
        self.assertEqual(Path("source.dart").read_text(), "shared source")
        self.assertEqual(Path("btr.dart").read_text(), "personal BTR source change")
        self.assertEqual(git("status", "--porcelain"), "")

    def test_dirty_checkout_is_rejected(self):
        Path("btr.dart").write_text("uncommitted user change")
        with self.assertRaisesRegex(ValueError, "dirty checkout"):
            prepare_btr_sync.prepare(self.base, self.base, self.author_base)
        self.assertEqual(Path("btr.dart").read_text(), "uncommitted user change")

    def test_moved_branch_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "moved"):
            prepare_btr_sync.prepare(self.base, "0" * 40, self.author_base)


class PackageTests(unittest.TestCase):
    def test_ios_captcha_patch_applies_without_unreachable_fallback(self):
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            for name in ("lib/pages/login/geetest/geetest_webview_dialog.dart", "pubspec.yaml"):
                destination = target / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes((root / name).read_bytes())
            subprocess.run(["git", "apply", str(root / "lib/scripts/geetest_ios.patch")], cwd=target, check=True)
            source = (target / "lib/pages/login/geetest/geetest_webview_dialog.dart").read_text()
            body = source.split("static Future<Map<String, dynamic>?> geetest(", 1)[1].split("\n  }", 1)[0]
            self.assertEqual(body.count("return "), 1)
            self.assertIn("return GeetestPlugin.geetest(gt, challenge);", body)

    def test_promotion_stops_before_remote_write_when_personal_branch_moves(self):
        with tempfile.TemporaryDirectory() as directory:
            previous = Path.cwd()
            try:
                os.chdir(directory)
                Path("dist").mkdir()
                Path("dist/build-info.json").write_text(json.dumps({"source_sha": "a" * 40}))
                heads = iter(("a" * 40, "c" * 40))
                def command(*args, **kwargs):
                    output = next(heads) if args[1:3] == ("rev-parse", "FETCH_HEAD") else ""
                    return subprocess.CompletedProcess(args, 0, output, "")
                with patch.object(btr_release, "run", side_effect=command) as commands:
                    with self.assertRaisesRegex(ValueError, "branch moved"):
                        btr_release.publish("b" * 40, "d" * 40, "e" * 40)
                self.assertFalse(any(call.args[:2] == ("git", "push") for call in commands.call_args_list))
            finally:
                os.chdir(previous)

    def test_actual_metadata_and_bundle_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "release.ipa"
            info = {"CFBundlePackageType": "APPL", "CFBundleIdentifier": "com.example.piliplus.btr", "CFBundleShortVersionString": "2.1.6", "CFBundleVersion": "5483", "MinimumOSVersion": "15.0"}
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("Payload/Runner.app/Info.plist", plistlib.dumps(info, fmt=plistlib.FMT_BINARY))
                archive.writestr("Payload/Runner.app/PlugIns/Other.app/Info.plist", plistlib.dumps({"CFBundlePackageType": "APPL"}))
            data = verify_btr_ipa.verify(path, "2.1.6", "5483", "a" * 40, "b" * 40, "c" * 40)
            self.assertEqual(data["min_os_version"], "15.0")
            self.assertEqual(data["btr_upstream_sha"], "c" * 40)
            self.assertEqual(len(data["sha256"]), 64)
            with self.assertRaisesRegex(ValueError, "CFBundleVersion"):
                verify_btr_ipa.verify(path, "2.1.6", "5416", "a" * 40, "b" * 40, "c" * 40)
            info["CFBundleIdentifier"] = "com.example.piliplus"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("Payload/Runner.app/Info.plist", plistlib.dumps(info))
            with self.assertRaisesRegex(ValueError, "CFBundleIdentifier"):
                verify_btr_ipa.verify(path, "2.1.6", "5483", "a" * 40, "b" * 40, "c" * 40)

    def test_published_release_is_not_treated_as_a_draft(self):
        # A published version skips repeat builds; a draft requires recovery.
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "outputs"
            def command(*args, **kwargs):
                if args[1:3] == ("rev-parse", "HEAD"):
                    return subprocess.CompletedProcess(args, 0, "a" * 40, "")
                if args[1:3] == ("rev-parse", "FETCH_HEAD"):
                    return subprocess.CompletedProcess(args, 0, next(fetch_heads), "")
                return subprocess.CompletedProcess(args, 0, "", "")
            for draft in (False, True):
                fetch_heads = iter(("b" * 40, "c" * 40))
                output.write_text("")
                with patch.dict(os.environ, GITHUB_OUTPUT=str(output)), patch.object(btr_release, "run", side_effect=command), patch.object(btr_release, "identity", return_value=("2.1.6", "5483", "tag")), patch.object(btr_release, "api", return_value={"draft": draft, "prerelease": False}), patch.object(btr_release, "blocked", return_value=None):
                    btr_release.plan()
                self.assertIn(f"changed={str(draft).lower()}", output.read_text())

    def test_either_source_update_schedules_a_build_even_with_published_release(self):
        for updated in ("b" * 40, "c" * 40):
            with self.subTest(updated=updated), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "outputs"
                fetch_heads = iter(("b" * 40, "c" * 40))
                def command(*args, **kwargs):
                    if args[1:3] == ("rev-parse", "HEAD"):
                        return subprocess.CompletedProcess(args, 0, "a" * 40, "")
                    if args[1:3] == ("rev-parse", "FETCH_HEAD"):
                        return subprocess.CompletedProcess(args, 0, next(fetch_heads), "")
                    code = int(args[3] == updated) if args[1:3] == ("merge-base", "--is-ancestor") else 0
                    return subprocess.CompletedProcess(args, code, "", "")
                with patch.dict(os.environ, GITHUB_OUTPUT=str(output)), patch.object(btr_release, "run", side_effect=command), patch.object(btr_release, "identity", return_value=("2.1.6", "5483", "tag")), patch.object(btr_release, "api", return_value={"draft": False, "prerelease": False}), patch.object(btr_release, "blocked", return_value=None):
                    btr_release.plan()
                self.assertIn("changed=true", output.read_text())
                self.assertIn("btr_upstream=" + "c" * 40, output.read_text())

    def test_promotion_requires_btr_author_history_before_any_remote_write(self):
        with tempfile.TemporaryDirectory() as directory:
            previous = Path.cwd()
            try:
                os.chdir(directory)
                Path("dist").mkdir()
                Path("dist/build-info.json").write_text(json.dumps({"source_sha": "a" * 40}))
                def command(*args, **kwargs):
                    code = int(args[3] == "c" * 40) if args[1:3] == ("merge-base", "--is-ancestor") else 0
                    value = "a" * 40 if args[1:3] == ("rev-parse", "FETCH_HEAD") else ""
                    return subprocess.CompletedProcess(args, code, value, "")
                with patch.object(btr_release, "run", side_effect=command) as commands:
                    with self.assertRaisesRegex(ValueError, "pinned BTR author"):
                        btr_release.publish("d" * 40, "b" * 40, "c" * 40)
                self.assertFalse(any(call.args[:2] == ("git", "push") for call in commands.call_args_list))
            finally:
                os.chdir(previous)

    def test_btr_input_change_has_a_distinct_failure_key(self):
        self.assertNotEqual(btr_release.title("a" * 40, "b" * 40, "c" * 40), btr_release.title("a" * 40, "b" * 40, "d" * 40))


if __name__ == "__main__":
    unittest.main()
