"""Privacy preparation must preserve resources and stop before publication on failure."""
import plistlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from prepare_public_ios import PreparationError, prepare, safe_diagnostic


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.app = Path(self.temporary.name) / "Runner.app"
        self.app.mkdir()
        (self.app / "Info.plist").write_bytes(plistlib.dumps({"CFBundlePackageType": "APPL"}))
        (self.app / "Runner").write_bytes(b"\xcf\xfa\xed\xfe" + b"code")

    def test_only_native_executables_are_stripped(self):
        resource = self.app / "resource.dat"
        resource.write_bytes(b"unchanged resource")
        runner = Mock(return_value=subprocess.CompletedProcess([], 0))
        self.assertEqual(prepare(self.app, runner=runner), 1)
        self.assertEqual(runner.call_args.args[0][:3], ["xcrun", "strip", "-S"])
        self.assertEqual(resource.read_bytes(), b"unchanged resource")

    def test_strip_failure_blocks(self):
        runner = Mock(return_value=subprocess.CompletedProcess([], 1, stderr=b"private diagnostic"))
        with self.assertRaisesRegex(RuntimeError, "Native debug-symbol removal failed"):
            prepare(self.app, runner=runner)

    def test_signing_material_blocks_before_modification(self):
        (self.app / "embedded.mobileprovision").write_bytes(b"personal")
        runner = Mock()
        with self.assertRaises(ValueError):
            prepare(self.app, runner=runner)
        runner.assert_not_called()

    def test_symlink_blocks_before_modification(self):
        (self.app / "linked").symlink_to(self.app / "Runner")
        runner = Mock()
        with self.assertRaises(ValueError):
            prepare(self.app, runner=runner)
        runner.assert_not_called()

    def test_failed_sign_or_verification_blocks_packaging(self):
        framework = self.app / "Frameworks/Plugin.framework"
        framework.mkdir(parents=True)
        (framework / "Plugin").write_bytes(b"\xcf\xfa\xed\xfe" + b"code")
        for failed_stage in ("--force", "--verify"):
            strip_started = False
            def command(args, **kwargs):
                nonlocal strip_started
                if args[:2] == ["xcrun", "strip"]:
                    strip_started = True
                return subprocess.CompletedProcess(args, int(strip_started and failed_stage in args))
            with self.subTest(stage=failed_stage):
                with self.assertRaisesRegex(PreparationError, "signature preparation failed") as caught:
                    prepare(self.app, runner=command)
                self.assertEqual(
                    caught.exception.report["error_code"],
                    "framework_sign_failed" if failed_stage == "--force" else "framework_verify_failed",
                )

    def test_presigns_all_frameworks_before_any_strip_and_resigns_after(self):
        outer = self.app / "Frameworks/Outer.framework"
        inner = outer / "Frameworks/Inner.framework"
        inner.mkdir(parents=True)
        for framework in (outer, inner):
            (framework / framework.stem).write_bytes(b"\xcf\xfa\xed\xfe" + b"code")
        runner = Mock(return_value=subprocess.CompletedProcess([], 0))

        self.assertEqual(prepare(self.app, runner=runner), 3)
        commands = [call.args[0] for call in runner.call_args_list]
        strip_indices = [i for i, args in enumerate(commands) if args[:2] == ["xcrun", "strip"]]
        first_strip, last_strip = min(strip_indices), max(strip_indices)
        self.assertEqual(len(strip_indices), 3)
        self.assertEqual(first_strip, 2)
        self.assertEqual([args[-1] for args in commands[:first_strip]], [str(inner), str(outer)])
        for framework in (inner, outer):
            sign_indices = [
                i for i, args in enumerate(commands)
                if args == ["codesign", "--force", "--sign", "-",
                            "--preserve-metadata=identifier,entitlements", str(framework)]
            ]
            verify_indices = [
                i for i, args in enumerate(commands)
                if args == ["codesign", "--verify", "--strict", str(framework)]
            ]
            self.assertEqual(len(sign_indices), 2)
            self.assertEqual(len(verify_indices), 1)
            self.assertLess(sign_indices[0], first_strip)
            self.assertGreater(sign_indices[1], last_strip)
            self.assertGreater(verify_indices[0], sign_indices[1])

    def test_presign_failure_blocks_before_any_strip(self):
        for name in ("First", "Second"):
            framework = self.app / f"Frameworks/{name}.framework"
            framework.mkdir(parents=True)
            (framework / name).write_bytes(b"\xcf\xfa\xed\xfe" + b"code")
        runner = Mock(side_effect=[
            subprocess.CompletedProcess([], 0),
            subprocess.CompletedProcess([], 2, stderr=b"private-account/path: Permission denied"),
        ])

        with self.assertRaises(PreparationError) as caught:
            prepare(self.app, runner=runner)
        report = caught.exception.report
        self.assertEqual(report["error_code"], "framework_presign_failed")
        self.assertEqual(report["tool_exit"], 2)
        self.assertEqual(report["reason"], "permission_denied")
        self.assertNotIn("private-account", str(report))
        self.assertNotIn(str(self.app), str(report))
        self.assertEqual(runner.call_count, 2)
        self.assertTrue(all(call.args[0][:2] == ["codesign", "--force"] for call in runner.call_args_list))

    def test_native_failure_diagnostic_does_not_echo_private_paths(self):
        runner = Mock(return_value=subprocess.CompletedProcess(
            [], 1, stderr=b"private-account/path: Permission denied"))
        with self.assertRaises(PreparationError) as caught:
            prepare(self.app, runner=runner)
        report = caught.exception.report
        self.assertEqual(report["error_code"], "native_strip_failed")
        self.assertEqual(report["reason"], "permission_denied")
        self.assertNotIn("private-account", str(report))
        self.assertNotIn(str(self.app), str(report))

    def test_opt_in_diagnostic_redacts_paths_and_private_identity(self):
        with patch.dict(os.environ, {
                "RELEASE_NATIVE_DIAGNOSTICS": "1",
                "RELEASE_PRIVATE_TERMS_JSON": '["synthetic_account"]'}):
            value = safe_diagnostic(
                b"/Users/synthetic_account/project/library: malformed object for SYNTHETIC_ACCOUNT", self.app)
        self.assertIn("malformed object", value)
        self.assertNotIn("synthetic_account", value.lower())
        self.assertNotIn("/Users/", value)

    def test_diagnostic_with_credentials_or_invalid_policy_is_withheld(self):
        for terms in ('["synthetic_account"]', 'invalid'):
            with self.subTest(terms=terms), patch.dict(os.environ, {
                    "RELEASE_NATIVE_DIAGNOSTICS": "1",
                    "RELEASE_PRIVATE_TERMS_JSON": terms}):
                value = safe_diagnostic(b"failure ghp_" + b"a" * 36, self.app)
                self.assertIsNone(value)


if __name__ == "__main__":
    unittest.main()
