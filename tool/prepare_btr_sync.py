"""Merge two pinned sources into a local candidate; never push or publish here."""
import argparse
import re
import subprocess
from pathlib import Path

AUDIO_SESSION_PATH = "lib/services/audio_session.dart"
AUDIO_SESSION_STAGES = (
    "3155b35c84e15d72be0043a49ff5f95368a75906",  # common
    "e13121b6af38ac3758fb210dd7531e837e4082e0",  # personal AirPlay guards
    "a90b531edb11701f9701bb299afc2f70483cd0f5",  # official audio callbacks
)
BTR_SEEK_PATH = "lib/plugin/pl_player/controller.dart"
BTR_SEEK_STAGES = (
    "f4f3d86a49e93e21a0f3545a524769a8df2cdf1d",
    "bc0a4c53dc7c673105d3fae7c0d9c1dbd9c92474",
    "693a6f1ad1a901d944c08d92fca815baaa5e20bd",
)
VIDEO_CONTROLLER_PATH = "lib/pages/video/controller.dart"
VIDEO_CONTROLLER_STAGES = (
    "65fcbc18cbbf3d7cb8be3480d4c7f6cd28fa035c",
    "b8422d3cb69caa64133dfafff465e2408fc1e40f",
    "e193c370aea577361c3609f73a30d66b74ca9514",
)
BTR_SEEK_PERSONAL = "    IOSExternalDisplayHelper.clearDanmaku();\n"
BTR_SEEK_UPSTREAM = "    _notifySeekListeners(position);\n"
VIDEO_PERSONAL_IMPORTS = (
    "import 'package:PiliPlus/utils/image_utils.dart';\n"
    "import 'package:PiliPlus/utils/ios/airplay_helper.dart';\n"
    "import 'package:PiliPlus/utils/ios/airplay_danmaku.dart';\n"
    "import 'package:PiliPlus/utils/ios/external_display_helper.dart';\n"
    "import 'package:PiliPlus/utils/ios/external_seek.dart';\n"
)
VIDEO_SIZE_IMPORT = "import 'package:PiliPlus/utils/extension/size_ext.dart';\n"


def git(*args, check=True):
    return subprocess.run(["git", *args], check=check, capture_output=True, text=True)


def resolve_reviewed_conflict(path, expected_stages, source, personal, upstream, combined):
    """Resolve exactly one reviewed hunk; refuse altered blobs or other hunks."""
    stages = [git("rev-parse", f":{stage}:{path}", check=False) for stage in (1, 2, 3)]
    if any(stage.returncode for stage in stages):
        return False
    if tuple(stage.stdout.strip() for stage in stages) != expected_stages:
        return False
    conflict = f"<<<<<<< HEAD\n{personal}=======\n{upstream}>>>>>>> {source}\n"
    file = Path(path)
    content = file.read_text(encoding="utf-8")
    if content.count(conflict) != 1:
        return False
    resolved = content.replace(conflict, combined, 1)
    if re.search(r"^(<<<<<<< |=======|>>>>>>> )", resolved, re.MULTILINE):
        return False
    file.write_text(resolved, encoding="utf-8")
    git("add", "--", path)
    return True


def resolve_preapplied_gitignore():
    """Resolve only an upstream test allowance already present in full locally."""
    stages = [git("show", f":{stage}:.gitignore", check=False) for stage in (1, 2, 3)]
    if any(result.returncode for result in stages):
        return False
    common, personal, official = (result.stdout for result in stages)
    if not common.endswith("test*"):
        return False
    if official != common + "\n!/test/\n!/test/**\n" or not personal.startswith(official):
        return False
    Path(".gitignore").write_text(personal, encoding="utf-8")
    git("add", "--", ".gitignore")
    return True


def resolve_known_audio_session():
    """Combine the reviewed upstream callbacks with the personal AirPlay guards."""
    stages = [git("rev-parse", f":{stage}:{AUDIO_SESSION_PATH}", check=False)
              for stage in (1, 2, 3)]
    if any(result.returncode for result in stages):
        return False
    if tuple(result.stdout.strip() for result in stages) != AUDIO_SESSION_STAGES:
        return False
    official = git("show", f":3:{AUDIO_SESSION_PATH}").stdout
    replacements = (
        ("import 'package:PiliPlus/services/service_locator.dart';\n",
         "import 'package:PiliPlus/services/service_locator.dart';\n"
         "import 'package:PiliPlus/utils/ios/airplay_helper.dart';\n"),
        ("    _session.becomingNoisyEventStream.listen((_) {\n",
         "    _session.becomingNoisyEventStream.listen((_) {\n"
         "      if (IOSAirPlayHelper.isActive) {\n"
         "        IOSAirPlayHelper.pause();\n"
         "        return;\n"
         "      }\n"),
        ("  void _onInterrupted(AudioInterruptionEvent event) {\n",
         "  void _onInterrupted(AudioInterruptionEvent event) {\n"
         "    if (IOSAirPlayHelper.isActive) {\n"
         "      _shouldResume = false;\n"
         "      return; // AVKit handles interruptions during external playback.\n"
         "    }\n"),
    )
    if any(official.count(before) != 1 for before, _ in replacements):
        return False
    for before, after in replacements:
        official = official.replace(before, after, 1)
    Path(AUDIO_SESSION_PATH).write_text(official, encoding="utf-8")
    git("add", "--", AUDIO_SESSION_PATH)
    return True


def prepare(upstream, base, btr_upstream):
    if git("rev-parse", "HEAD").stdout.strip() != base:
        raise ValueError("Personal branch moved since planning")
    if git("status", "--porcelain").stdout:
        raise ValueError("Refusing to merge into a dirty checkout")
    git("config", "merge.keepPersonal.driver", "true")
    sources = (("BTR author", btr_upstream), ("official PiliPlus", upstream))
    if all(git("merge-base", "--is-ancestor", sha, base, check=False).returncode == 0 for _, sha in sources):
        return base
    try:
        for label, sha in sources:
            if git("merge-base", "--is-ancestor", sha, "HEAD", check=False).returncode == 0:
                continue
            result = git("merge", "--no-commit", "--no-ff", sha, check=False)
            # Both source projects' workflows require separate personal review.
            git("restore", "--source=HEAD", "--staged", "--worktree", ".github/workflows")
            conflicts = git("diff", "--name-only", "--diff-filter=U").stdout.strip()
            conflict_paths = set(conflicts.splitlines())
            if label == "BTR author" and BTR_SEEK_PATH in conflict_paths:
                resolve_reviewed_conflict(
                    BTR_SEEK_PATH, BTR_SEEK_STAGES, sha, BTR_SEEK_PERSONAL,
                    BTR_SEEK_UPSTREAM, BTR_SEEK_PERSONAL + BTR_SEEK_UPSTREAM,
                )
            if label == "official PiliPlus":
                if ".gitignore" in conflict_paths:
                    resolve_preapplied_gitignore()
                if AUDIO_SESSION_PATH in conflict_paths:
                    resolve_known_audio_session()
                if VIDEO_CONTROLLER_PATH in conflict_paths:
                    resolve_reviewed_conflict(
                        VIDEO_CONTROLLER_PATH, VIDEO_CONTROLLER_STAGES, sha,
                        VIDEO_SIZE_IMPORT + VIDEO_PERSONAL_IMPORTS, "", VIDEO_PERSONAL_IMPORTS,
                    )
            conflicts = git("diff", "--name-only", "--diff-filter=U").stdout.strip()
            merge_pending = git("rev-parse", "--verify", "MERGE_HEAD", check=False).returncode == 0
            if conflicts or (result.returncode and not merge_pending):
                raise ValueError(f"{label} merge requires manual resolution: {conflicts or result.stderr}")
            snapshots = (("README-upstream.md", upstream), ("README-BTR-upstream.md", btr_upstream))
            for path, source in snapshots:
                Path(path).write_text(git("show", f"{source}:README.md").stdout)
                git("add", path)
            git("commit", "-m", f"Merge {label} {sha[:12]} while preserving personal BTR")
    except Exception:
        git("merge", "--abort", check=False)
        # The initial clean-checkout guard makes rollback of our candidate safe.
        git("reset", "--hard", base)
        raise
    return git("rev-parse", "HEAD").stdout.strip()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--upstream", required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--btr-upstream", required=True)
    args = parser.parse_args()
    print(prepare(args.upstream, args.base, args.btr_upstream))
