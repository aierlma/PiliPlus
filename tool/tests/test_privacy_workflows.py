"""Offline release-gate topology and fail-closed shell behavior checks.

These tests do not build an app or contact GitHub. External packaging commands
are stubbed to prove a nonzero privacy result prevents publishing an IPA output.
"""
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github/workflows"
NAMES = ("btr-maintain.yml", "btr-sync-validate.yml", "ios.yml", "ios14.yml")
GUARD = "python3 tool/release_privacy.py"
IDENTITY = "python3 tool/check_public_identity.py"
PREPARE = "python3 tool/prepare_public_ios.py build/ios/iphoneos/Runner.app"
OUTPUT = "${{ steps.package.outputs.ipa_path }}"


def jobs(text):
    """Read this repository's fixed YAML indentation without extra CI deps."""
    body = text.split("\njobs:\n", 1)[1]
    markers = list(re.finditer(r"^  ([A-Za-z0-9_-]+):\s*$", body, re.M))
    for n, marker in enumerate(markers):
        end = markers[n + 1].start() if n + 1 < len(markers) else len(body)
        yield marker.group(1), body[marker.end():end]


def steps(job):
    markers = list(re.finditer(r"^      - ", job, re.M))
    for n, marker in enumerate(markers):
        end = markers[n + 1].start() if n + 1 < len(markers) else len(job)
        yield job[marker.start():end]


def script(step):
    lines = step.splitlines()
    for n, line in enumerate(lines):
        if line == "        run: |":
            result = []
            for following in lines[n + 1:]:
                if following and not following.startswith("          "):
                    break
                result.append(following[10:] if following else "")
            return "\n".join(result) + "\n"
        if line.startswith("        run: "):
            return line[len("        run: "):] + "\n"
    return ""


class PrivacyWorkflowTests(unittest.TestCase):
    def read(self, name):
        return (WORKFLOWS / name).read_text()

    def test_every_upload_consumes_only_guarded_ipa_output(self):
        uploads = 0
        for name in NAMES:
            for job_name, job in jobs(self.read(name)):
                seen_package = False
                for step in steps(job):
                    if "id: package" in step:
                        content = script(step)
                        self.assertIn(GUARD, content, (name, job_name))
                        self.assertIn("--allow-home-user runner --policy tool/release-privacy-policy.json", content)
                        self.assertIn("set -euo pipefail", content)
                        self.assertLess(content.index(GUARD), content.index('echo "ipa_path='))
                        self.assertNotIn("continue-on-error", step)
                        self.assertNotIn("|| true", content)
                        seen_package = True
                    if re.search(r"uses: (?:actions/upload-artifact|softprops/action-gh-release)@", step):
                        uploads += 1
                        self.assertTrue(seen_package, (name, job_name))
                        self.assertIn(OUTPUT, step, (name, job_name))
                        self.assertNotRegex(step, r"\*[^\n]*\.ipa|path: dist/\s*$")
                        self.assertNotIn("always()", step)
                        self.assertNotIn("continue-on-error", step)
                        if "actions/upload-artifact" in step:
                            self.assertIn("if-no-files-found: error", step)
        self.assertEqual(uploads, 5)

    def test_source_identity_precedes_patching_and_uses_secret(self):
        for name in NAMES:
            checked = 0
            for _, job in jobs(self.read(name)):
                if "flutter build ios" not in job:
                    continue
                self.assertIn(IDENTITY, job, name)
                self.assertLess(job.index(IDENTITY), job.index("lib/scripts/patch"), name)
                if "prepare_btr_sync.py" in job:
                    self.assertLess(job.index("prepare_btr_sync.py"), job.index(IDENTITY), name)
                identity_step = next(step for step in steps(job) if IDENTITY in script(step))
                self.assertIn("RELEASE_PRIVATE_TERMS_JSON: ${{ secrets.RELEASE_PRIVATE_TERMS_JSON }}", identity_step)
                checked += 1
            self.assertEqual(checked, 1, name)

    def test_preparation_precedes_packaging_without_ignored_sign_failures(self):
        for name in NAMES:
            text = self.read(name)
            self.assertNotRegex(text, r"find[^\n]+-exec codesign", name)
            for _, job in jobs(text):
                for step in steps(job):
                    content = script(step)
                    if "flutter build ios" in content:
                        self.assertIn(PREPARE, content, name)
                        self.assertLess(content.index(PREPARE), content.index("zip -"), name)
                        self.assertLess(content.index("zip -"), content.index(GUARD), name)
                        self.assertIn("set -euo pipefail", content)

    def test_downloaded_ipa_is_rechecked_before_publisher(self):
        publish = dict(jobs(self.read("btr-maintain.yml")))["publish"]
        command = next(script(step) for step in steps(publish) if "btr_release.py publish" in script(step))
        self.assertLess(command.index(IDENTITY), command.index(GUARD))
        self.assertIn("shopt -s nullglob", command)
        self.assertIn('test "${#ipa_files[@]}" -eq 1', command)
        self.assertLess(command.index(GUARD), command.index("btr_release.py publish"))

    def test_reusable_workflows_require_private_term_secret(self):
        for name in ("ios.yml", "ios14.yml"):
            text = self.read(name)
            self.assertRegex(text, r"workflow_call:\n    secrets:\n      RELEASE_PRIVATE_TERMS_JSON:\n        required: true")

    def test_shell_syntax(self):
        for name in NAMES:
            for _, job in jobs(self.read(name)):
                for step in steps(job):
                    if "shell: pwsh" in step:
                        continue
                    content = re.sub(r"\$\{\{.*?\}\}", "false", script(step))
                    if content:
                        result = subprocess.run(["/bin/bash", "-n"], input=content, text=True, capture_output=True)
                        self.assertEqual(result.returncode, 0, (name, result.stderr))

    def test_nonzero_guard_never_produces_upload_path(self):
        for name in ("btr-maintain.yml", "ios.yml", "ios14.yml"):
            package = next(step for _, job in jobs(self.read(name)) for step in steps(job) if "id: package" in step)
            command = re.sub(r"\$\{\{.*?\}\}", "false", script(package))
            for code in (0, 1, 2):
                with self.subTest(workflow=name, guard_exit=code), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    (root / "bin").mkdir()
                    (root / "package").mkdir()
                    stub = root / "bin/stub"
                    stub.write_text('#!/bin/sh\n'
                                    'case "$1" in tool/release_privacy.py) exit "$GUARD_EXIT";; esac\n'
                                    'exit 0\n')
                    stub.chmod(0o755)
                    for executable in ("python3", "flutter", "ln", "zip", "mkdir", "ditto", "git", "bash"):
                        (root / "bin" / executable).symlink_to(stub)
                    output = root / "outputs"
                    env = dict(os.environ, PATH=str(root / "bin") + os.pathsep + os.environ.get("PATH", ""),
                               GUARD_EXIT=str(code), GITHUB_OUTPUT=str(output), version="1.2.3+456",
                               UPSTREAM="a" * 40, BTR_UPSTREAM="b" * 40)
                    result = subprocess.run(["/bin/bash", "-c", command], cwd=root, env=env, capture_output=True, text=True)
                    self.assertEqual(result.returncode, code, result.stderr)
                    if code:
                        self.assertFalse(output.exists(), "Failed gate must not emit a publishable IPA output")
                    else:
                        self.assertRegex(output.read_text(), r"^ipa_path=.+\.ipa\n$")


if __name__ == "__main__":
    unittest.main()
