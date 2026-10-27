"""GitHub-hosted planning, gated publication, and durable failure records."""
import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

REPO = "aierlma/PiliPlus"
MAINTENANCE_DIRECTORIES = (".github/", ".vscode/", "tool/", "test/", "docs/")
MAINTENANCE_FILES = {".gitattributes", ".gitignore", "analysis_options.yaml"}
PUBLIC_ARTIFACT_INPUTS = {
    "tool/prepare_public_ios.py", "tool/release_privacy.py",
    "tool/release-privacy-policy.json",
}
PRIVACY_POLICY = Path(__file__).with_name("release-privacy-policy.json")


def run(*args, check=True):
    return subprocess.run(args, check=check, capture_output=True, text=True)


def api(path):
    result = run("gh", "api", f"repos/{REPO}/{path}", check=False)
    if result.returncode:
        if "(HTTP 404)" in result.stderr:
            return None
        raise RuntimeError(result.stderr)
    return json.loads(result.stdout)


def verify_publishing_identity():
    """Authenticate the publisher, including Actions installation tokens."""
    actions = os.environ.get("GITHUB_ACTIONS") == "true"
    if actions and os.environ.get("GITHUB_REPOSITORY") != REPO:
        raise ValueError("CI publication is restricted to the personal repository")
    try:
        user = run("gh", "api", "user", check=False)
        if user.returncode == 0:
            login = json.loads(user.stdout)["login"].casefold()
            if login != "aierlma" and not (actions and login == "github-actions[bot]"):
                raise ValueError
        elif not actions:
            raise ValueError
        if actions:
            # GITHUB_TOKEN may not implement /user. Do not trust environment
            # variables alone: the active token must have push access here.
            repository = run("gh", "api", f"repos/{REPO}", check=False)
            data = json.loads(repository.stdout)
            if (repository.returncode or data.get("full_name") != REPO
                    or data.get("permissions", {}).get("push") is not True):
                raise ValueError
    except Exception:
        # gh errors and unexpected API values must not echo account details.
        raise ValueError("GitHub publishing identity or repository permission could not be verified") from None


def stage_ipa(source, destination):
    """Copy a regular input into our private, exclusive upload directory."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        with os.fdopen(os.open(source, flags), "rb") as original:
            if not stat.S_ISREG(os.fstat(original.fileno()).st_mode):
                raise ValueError
            with destination.open("xb") as staged:
                shutil.copyfileobj(original, staged)
        destination.chmod(0o400)
    except Exception:
        raise ValueError("Could not stage a regular IPA for publication") from None


def scan_public_ipa(path):
    from release_privacy import assert_public_artifact, load_public_policy, verify_artifact_hash
    try:
        report = assert_public_artifact(path, allow_home_users=["runner"],
                                        **load_public_policy(PRIVACY_POLICY))
        verify_artifact_hash(path, report)
        return report
    except Exception:
        # Fail closed even if the scanner itself crashes with sensitive bytes
        # or filenames in its exception; do not publish those diagnostics.
        raise ValueError("Release privacy gate refused the IPA or could not complete") from None


def verify_uploaded_assets(tag, expected):
    """Require GitHub's uploaded bytes to match the admitted local files."""
    try:
        release = api(f"releases/tags/{tag}")
        assets = release["assets"]
        if not release["draft"] or len(assets) != len(expected):
            raise ValueError
        for name, (size, digest) in expected.items():
            matches = [asset for asset in assets if asset["name"] == name]
            if (len(matches) != 1 or matches[0].get("state") != "uploaded"
                    or matches[0].get("size") != size
                    or matches[0].get("digest") != f"sha256:{digest}"):
                raise ValueError
    except Exception:
        raise ValueError("Uploaded release assets do not match the verified bytes; draft remains unpublished") from None


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
            and (path in PUBLIC_ARTIFACT_INPUTS or not path.startswith(MAINTENANCE_DIRECTORIES))
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


def close_resolved_failures(source):
    """Close only generated failures whose three inputs are now validated ancestors."""
    items = json.loads(run("gh", "issue", "list", "--repo", REPO, "--state", "open",
                           "--limit", "1000", "--search", '"BTR sync blocked:" in:title',
                           "--json", "number,title,body").stdout)
    pattern = (r"Personal source: ([0-9a-f]{40})\n"
               r"Official PiliPlus upstream: ([0-9a-f]{40})\n"
               r"BTR author upstream: ([0-9a-f]{40})")
    for item in items:
        match = re.search(pattern, item.get("body", ""))
        if not match or item.get("title") != title(*match.groups()):
            continue
        if all(run("git", "merge-base", "--is-ancestor", sha, source,
                   check=False).returncode == 0 for sha in match.groups()):
            run("gh", "issue", "close", str(item["number"]), "--repo", REPO)


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
    candidates = list(Path("dist").glob("*.ipa"))
    if len(candidates) != 1:
        raise ValueError("Publication requires exactly one IPA")
    # Scan and upload this same private snapshot, never a mutable dist path.
    with tempfile.TemporaryDirectory(prefix="piliplus-release-") as temporary:
        directory = Path(temporary)
        ipa = directory / candidates[0].name
        stage_ipa(candidates[0], ipa)
        try:
            from verify_btr_ipa import verify
            actual = verify(ipa, version, build, source, upstream, btr_upstream)
        except Exception:
            raise ValueError("Downloaded artifact no longer matches verified IPA metadata") from None
        if actual != info:
            raise ValueError("Downloaded artifact no longer matches verified IPA metadata")
        verify_publishing_identity()
        report = scan_public_ipa(ipa)
        if report["sha256"] != info["sha256"] or report["size_bytes"] != info["size_bytes"]:
            raise ValueError("IPA changed after metadata verification")
        metadata = directory / "build-info.json"
        metadata.write_text(json.dumps(info, indent=2) + "\n")
        metadata.chmod(0o400)
        expected = {
            ipa.name: (report["size_bytes"], report["sha256"]),
            metadata.name: (metadata.stat().st_size, hashlib.sha256(metadata.read_bytes()).hexdigest()),
        }
        _publish_staged(info, version, build, tag, ipa, metadata, report, expected,
                        upstream, btr_upstream)


def _publish_staged(info, version, build, tag, ipa, metadata, report, expected,
                    upstream, btr_upstream):
    from release_privacy import verify_artifact_hash
    source = info["source_sha"]
    verify_artifact_hash(ipa, report)
    if not application_changes(released_source(), source):
        # Keep the existing workflow and all validation gates. A docs-only
        # upstream merge advances history without publishing another package.
        run("git", "push", "origin", f"{source}:refs/heads/btr")
        close_resolved_failures(source)
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
    notes = ipa.parent / "release-notes.md"
    notes.write_text(body)
    notes.chmod(0o400)
    verify_artifact_hash(ipa, report)
    if (metadata.stat().st_size, hashlib.sha256(metadata.read_bytes()).hexdigest()) != expected[metadata.name]:
        raise ValueError("Release metadata changed before upload")
    if not existing:
        run("gh", "release", "create", tag, str(ipa), str(metadata), "--repo", REPO, "--draft", "--target", source, "--title", f"PiliPlus BTR {tag}", "--notes-file", str(notes))
    else:
        run("gh", "release", "upload", tag, str(ipa), str(metadata), "--repo", REPO, "--clobber")
    verify_artifact_hash(ipa, report)
    verify_uploaded_assets(tag, expected)
    # This non-force push also detects changes made after the earlier check.
    run("git", "push", "origin", f"{source}:refs/heads/btr")
    run("gh", "release", "edit", tag, "--repo", REPO, "--notes-file", str(notes), "--draft=false", "--latest")
    close_resolved_failures(source)
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
