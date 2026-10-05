"""Merge a pinned upstream into a local candidate; never push or publish here."""
import argparse
import subprocess
from pathlib import Path


def git(*args, check=True):
    return subprocess.run(["git", *args], check=check, capture_output=True, text=True)


def prepare(upstream, base):
    if git("rev-parse", "HEAD").stdout.strip() != base:
        raise ValueError("Personal branch moved since planning")
    if git("status", "--porcelain").stdout:
        raise ValueError("Refusing to merge into a dirty checkout")
    git("config", "merge.keepPersonal.driver", "true")
    if git("merge-base", "--is-ancestor", upstream, base, check=False).returncode == 0:
        return base
    result = git("merge", "--no-commit", "--no-ff", upstream, check=False)
    # CI belongs to this fork. Upstream workflow edits require separate review.
    git("restore", "--source=HEAD", "--staged", "--worktree", ".github/workflows")
    conflicts = git("diff", "--name-only", "--diff-filter=U").stdout.strip()
    merge_pending = git("rev-parse", "--verify", "MERGE_HEAD", check=False).returncode == 0
    if conflicts or (result.returncode and not merge_pending):
        git("merge", "--abort", check=False)
        raise ValueError(f"Upstream merge requires manual resolution: {conflicts or result.stderr}")
    Path("README-upstream.md").write_text(git("show", f"{upstream}:README.md").stdout)
    git("add", "README-upstream.md")
    git("commit", "-m", f"Merge official PiliPlus {upstream[:12]} while preserving BTR")
    return git("rev-parse", "HEAD").stdout.strip()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--upstream", required=True)
    parser.add_argument("--base", required=True)
    args = parser.parse_args()
    print(prepare(args.upstream, args.base))
