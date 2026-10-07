"""GitHub-hosted planning, gated publication, and durable failure records."""
import argparse
import json
import os
import re
import subprocess
from pathlib import Path

REPO = "aierlma/PiliPlus"
MAINTENANCE_DIRECTORIES = (".github/", ".vscode/", "tool/", "test/", "docs/")
MAINTENANCE_FILES = {".gitattributes", ".gitignore", "analysis_options.yaml"}


def run(*args, check=True):
    return subprocess.run(args, check=check, capture_output=True, text=True)


def api(path):
    result = run("gh", "api", f"repos/{REPO}/{path}", check=False)
    if result.returncode:
        if "(HTTP 404)" in result.stderr:
            return None
        raise RuntimeError(result.stderr)
    return json.loads(result.stdout)


def identity(sha):
    content = run("git", "show", f"{sha}:pubspec.yaml").stdout
    version = re.search(r"^version:\s*(\d+\.\d+\.\d+)", content, re.M).group(1)
    build = run("git", "rev-list", "--count", sha).stdout.strip()
    return version, build, f"v{version}-btr.{build}-{sha[:12]}"


def application_changes(released, candidate):
    """Compare app inputs, excluding known documentation and maintenance files.

    Unknown paths remain release-relevant. Git's diff includes deleted files,
    file modes and submodule changes, not just modified Dart files.
    """
    if released is None:
        return ["initial release"]
    paths = run("git", "diff", "--name-only", "--no-renames", "-z", released, candidate, "--").stdout.split("\0")
    return [path for path in paths if path
            and not path.startswith(MAINTENANCE_DIRECTORIES)
            and path not in MAINTENANCE_FILES
            and not ("/" not in path and path.startswith("README") and path.endswith(".md"))]


def released_source():
    release = api("releases/latest")
    if not release or release["draft"] or release["prerelease"]:
        return None
    tag = release["tag_name"]
    if not re.fullmatch(r"v\d+\.\d+\.\d+-btr\.\d+-[0-9a-f]{12}", tag):
        raise ValueError("Latest release is not a personal BTR release")
    assets = {asset["name"] for asset in release["assets"]}
    if "build-info.json" not in assets or not any(re.fullmatch(r"PiliPlus-BTR-ios-\d+\.\d+\.\d+-btr\.\d+-unsigned\.ipa", name) for name in assets):
        raise ValueError("Latest release lacks the verified BTR IPA and metadata")
    ref = f"refs/tags/{tag}"
    result = run("git", "rev-parse", "--verify", f"{ref}^{{commit}}", check=False)
    if result.returncode:
        run("git", "fetch", "origin", f"{ref}:{ref}")
        result = run("git", "rev-parse", "--verify", f"{ref}^{{commit}}")
    source = result.stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", source) or not tag.endswith(source[:12]):
        raise ValueError("Release tag differs from its named source commit")
    return source


def title(base, upstream, btr_upstream):
    return f"BTR sync blocked: {base[:12]} {upstream[:12]} {btr_upstream[:12]}"


def blocked(base, upstream, btr_upstream):
    items = json.loads(run("gh", "issue", "list", "--repo", REPO, "--state", "open", "--search", f'"{title(base, upstream, btr_upstream)}" in:title', "--json", "number,title").stdout)
    return next((item["number"] for item in items if item["title"] == title(base, upstream, btr_upstream)), None)


def plan(retry=False):
    run("git", "fetch", "https://github.com/bggRGjQaUbCoE/PiliPlus.git", "main")
    base = run("git", "rev-parse", "HEAD").stdout.strip()
    upstream = run("git", "rev-parse", "FETCH_HEAD").stdout.strip()
    run("git", "fetch", "https://github.com/nishuodedui1145-del/PiliPlus.git", "btr")
    btr_upstream = run("git", "rev-parse", "FETCH_HEAD").stdout.strip()
    released = released_source()
    synced = all(run("git", "merge-base", "--is-ancestor", sha, base, check=False).returncode == 0 for sha in (upstream, btr_upstream))
    changes = application_changes(released, base)
    changed = not synced or bool(changes)
    if changed and not retry and blocked(base, upstream, btr_upstream):
        print("Same three source SHAs are blocked by an open issue; skipping automatic retry")
        changed = False
    outputs = {"base": base, "upstream": upstream, "btr_upstream": btr_upstream, "changed": str(bool(changed)).lower()}
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        output.write("".join(f"{key}={value}\n" for key, value in outputs.items()))
    print(json.dumps(outputs))
    if synced and not changes:
        print("Both upstreams are included and app inputs match the latest release; no build or release needed")


def validate_candidate(base, upstream, btr_upstream, source):
    """Verify the pinned history and remote branch before any remote write."""
    if not re.fullmatch(r"[0-9a-f]{40}", source):
        raise ValueError("Invalid candidate source SHA")
    run("git", "fetch", "dist/candidate.bundle", "btr-candidate")
    if run("git", "rev-parse", "FETCH_HEAD").stdout.strip() != source:
        raise ValueError("Candidate bundle differs from the recorded source")
    if run("git", "merge-base", "--is-ancestor", base, source, check=False).returncode:
        raise ValueError("Candidate is not a fast-forward of the planned personal branch")
    if run("git", "merge-base", "--is-ancestor", upstream, source, check=False).returncode:
        raise ValueError("Candidate does not include the pinned official upstream")
    if run("git", "merge-base", "--is-ancestor", btr_upstream, source, check=False).returncode:
        raise ValueError("Candidate does not include the pinned BTR author upstream")
    run("git", "fetch", "origin", "btr")
    if run("git", "rev-parse", "FETCH_HEAD").stdout.strip() != base:
        raise ValueError("Personal branch moved during validation; refusing promotion")


def validate_release_progress(info, previous):
    """A package must be newer in both SideStore's stable version and build."""
    def triple(value):
        if not isinstance(value, str) or not re.fullmatch(r"\d+\.\d+\.\d+", value):
            raise ValueError("Invalid numeric iOS release version")
        return tuple(map(int, value.split(".")))

    if previous.get("bundle_id") != info["bundle_id"]:
        raise ValueError("Previous release has a different bundle identifier")
    if triple(info["version"]) <= triple(previous.get("version")):
        raise ValueError("iOS release version must increase; refusing a duplicate or downgrade")
    old_build = previous.get("build")
    if not isinstance(old_build, str) or not re.fullmatch(r"[1-9]\d*", old_build):
        raise ValueError("Invalid previous build number")
    if int(info["build"]) <= int(old_build):
        raise ValueError("Build number must increase; refusing a duplicate or downgrade")


def check_latest_release_progress(info):
    release = api("releases/latest")
    if release is None:
        return
    path = Path("dist/previous-build-info.json")
    run("gh", "release", "download", release["tag_name"], "--repo", REPO,
        "--pattern", "build-info.json", "--output", str(path), "--clobber")
    validate_release_progress(info, json.loads(path.read_text()))


def publish(base, upstream, btr_upstream):
    info = json.loads(Path("dist/build-info.json").read_text())
    source = info["source_sha"]
    validate_candidate(base, upstream, btr_upstream, source)
    version, build, tag = identity(source)
    ipa = next(Path("dist").glob("*.ipa"))
    from verify_btr_ipa import verify
    if verify(ipa, version, build, source, upstream, btr_upstream) != info:
        raise ValueError("Downloaded artifact no longer matches verified IPA metadata")
    if not application_changes(released_source(), source):
        # Keep the existing workflow and all validation gates. A docs-only
        # upstream merge advances history without publishing another package.
        run("git", "push", "origin", f"{source}:refs/heads/btr")
        issue = blocked(base, upstream, btr_upstream)
        if issue:
            run("gh", "issue", "close", str(issue), "--repo", REPO)
        print("Synced validated maintenance changes without publishing another IPA")
        return
    # Reject version collisions before staging a branch, creating a release or
    # advancing the default branch. This also catches upstream version resets.
    check_latest_release_progress(info)
    # Make the validated object available for a draft tag, without advancing btr.
    stage = f"builds/validated-{source[:12]}"
    run("git", "push", "origin", f"{source}:refs/heads/{stage}")
    existing = api(f"releases/tags/{tag}")
    if existing and not existing["draft"]:
        raise ValueError("Release already published; refusing to replace its assets")
    body = f"PiliPlus BTR {info['version']}, build {build}\nBased on PiliPlus {version}\n\nSource: {source}\nOfficial PiliPlus upstream: {upstream}\nBTR author upstream: {btr_upstream}\nMinimum iOS: {info['min_os_version']}\nIPA SHA-256: {info['sha256']}\n\nBoth source histories are included. BTR tests, static analysis and unsigned iOS build passed. Sideloading is required. The numeric iOS version includes the personal build number so SideStore can offer this package as a source update."
    Path("dist/release-notes.md").write_text(body)
    if not existing:
        run("gh", "release", "create", tag, str(ipa), "dist/build-info.json", "--repo", REPO, "--draft", "--target", source, "--title", f"PiliPlus BTR {tag}", "--notes-file", "dist/release-notes.md")
    else:
        run("gh", "release", "upload", tag, str(ipa), "dist/build-info.json", "--repo", REPO, "--clobber")
    # This non-force push also detects changes made after the earlier check.
    run("git", "push", "origin", f"{source}:refs/heads/btr")
    run("gh", "release", "edit", tag, "--repo", REPO, "--draft=false", "--latest")
    issue = blocked(base, upstream, btr_upstream)
    if issue:
        run("gh", "issue", "close", str(issue), "--repo", REPO)
    run("git", "push", "origin", "--delete", stage, check=False)
    print(f"Published https://github.com/{REPO}/releases/tag/{tag}")


def failure(base, upstream, btr_upstream):
    if not blocked(base, upstream, btr_upstream):
        url = f"https://github.com/{REPO}/actions/runs/{os.environ['GITHUB_RUN_ID']}"
        body = f"GitHub could not validate and publish this sync.\n\nPersonal source: {base}\nOfficial PiliPlus upstream: {upstream}\nBTR author upstream: {btr_upstream}\nRun and logs: {url}\n\nThe previous formal release remains available. This issue blocks automatic retries of the same three input SHAs. Fix and commit the source, or manually dispatch with retry_blocked enabled."
        Path("failure.md").write_text(body)
        run("gh", "issue", "create", "--repo", REPO, "--title", title(base, upstream, btr_upstream), "--body-file", "failure.md")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("plan", "publish", "failure"))
    parser.add_argument("--base")
    parser.add_argument("--upstream")
    parser.add_argument("--btr-upstream")
    parser.add_argument("--retry", action="store_true")
    args = parser.parse_args()
    if os.environ.get("GITHUB_REPOSITORY") != REPO:
        raise ValueError("This personal automation only operates on aierlma/PiliPlus")
    if args.command == "plan":
        plan(args.retry)
    else:
        if not all(re.fullmatch(r"[0-9a-f]{40}", value or "") for value in (args.base, args.upstream, args.btr_upstream)):
            raise ValueError("Pinned source SHAs are required")
        (publish if args.command == "publish" else failure)(args.base, args.upstream, args.btr_upstream)
