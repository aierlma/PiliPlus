"""Privacy preparation must preserve resources and stop before publication on failure."""
import plistlib
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from prepare_public_ios import prepare


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
            def command(args, **kwargs):
                return subprocess.CompletedProcess(args, int(failed_stage in args))
            with self.subTest(stage=failed_stage):
                with self.assertRaisesRegex(RuntimeError, "signature preparation failed"):
                    prepare(self.app, runner=command)


if __name__ == "__main__":
    unittest.main()
