"""Merge two pinned sources into a local candidate; never push or publish here."""
import argparse
import subprocess
from pathlib import Path


def git(*args, check=True):
    return subprocess.run(["git", *args], check=check, capture_output=True, text=True)


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
