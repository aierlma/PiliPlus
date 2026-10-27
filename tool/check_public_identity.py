#!/usr/bin/env python3
"""Check publication identities without echoing private values or paths."""
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess


REPOSITORY = "aierlma/PiliPlus"
MAX_FILE_BYTES = 16 * 1024**2
PUBLIC_POLICY = Path(__file__).with_name("public-source-privacy-policy.json")


def _patterns(terms):
    if (not isinstance(terms, list) or not 0 < len(terms) <= 128
            or any(not isinstance(term, str) or not term.strip() or len(term) > 4096 for term in terms)):
        raise ValueError
    return [re.compile((r"(?<!\w)" if re.fullmatch(r"\w+", term) else "")
                       + re.escape(term)
                       + (r"(?!\w)" if re.fullmatch(r"\w+", term) else ""), re.I)
            for term in terms]


def _contains_private(value, patterns):
    return any(pattern.search(value) for pattern in patterns)


def _line_exceptions(entries):
    if not isinstance(entries, (list, tuple)) or len(entries) > 32:
        raise ValueError
    for item in entries:
        if not isinstance(item, dict) or set(item) != {"path", "sha256", "verified_upstream_commits"}:
            raise ValueError
        name, digest, commits = item["path"], item["sha256"], item["verified_upstream_commits"]
        if (not isinstance(name, str) or not name or "\\" in name or Path(name).is_absolute()
                or ".." in Path(name).parts or Path(name).as_posix() != name
                or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
                or not isinstance(commits, list) or not 1 <= len(commits) <= 8
                or any(not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha) for sha in commits)):
            raise ValueError
    return entries


def load_public_policy():
    policy = json.loads(PUBLIC_POLICY.read_text())
    if not isinstance(policy, dict) or set(policy) != {"line_exceptions"}:
        raise ValueError
    return _line_exceptions(policy["line_exceptions"])


def _remove_reviewed_lines(name, data, exceptions):
    digests = {item["sha256"] for item in exceptions if item["path"] == name}
    if not digests:
        return data
    # Hash exact line bytes, including indentation but excluding line endings.
    # Keep every unmatched byte; an added character makes the line scan again.
    return b"".join(b"\n" if hashlib.sha256(body).hexdigest() in digests else whole
                    for body, whole in zip(data.splitlines(), data.splitlines(keepends=True)))


def _git(root, *arguments):
    return subprocess.check_output(["git", "-C", str(root), *arguments], stderr=subprocess.PIPE)


def _read_source(root, name):
    relative = Path(name)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise ValueError
    # Open every component relative to an already-open directory. No symlink
    # can redirect a read outside the checkout, including a parent directory.
    with contextlib.ExitStack() as stack:
        directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        stack.callback(os.close, directory)
        for part in relative.parts[:-1]:
            directory = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            stack.callback(os.close, directory)
        descriptor = os.open(relative.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        source = stack.enter_context(os.fdopen(descriptor, "rb"))
        before = os.fstat(source.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_FILE_BYTES:
            raise ValueError
        data = source.read(MAX_FILE_BYTES + 1)
        after = os.fstat(source.fileno())
        if (len(data) != before.st_size or len(data) > MAX_FILE_BYTES
                or (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
            raise ValueError
        return data


def _valid_identity(name, email, owner):
    if name == owner:
        return re.fullmatch(r"(?:[0-9]+\+)?" + re.escape(owner) + r"@users\.noreply\.github\.com", email) is not None
    return (name == "github-actions[bot]"
            and email == "41898282+github-actions[bot]@users.noreply.github.com")


def check_public_identity(root, private_terms, expected_owner="aierlma", *, line_exceptions=()):
    """Check tracked files and HEAD; inherited upstream authors are untouched."""
    report = {"status": "error", "complete": False, "files_scanned": 0, "findings": []}
    try:
        patterns = _patterns(private_terms)
        exceptions = _line_exceptions(line_exceptions)
        if not isinstance(expected_owner, str) or not re.fullmatch(r"[A-Za-z0-9-]+", expected_owner):
            raise ValueError
        root = Path(os.fsdecode(_git(root, "rev-parse", "--show-toplevel")).rstrip("\n")).resolve()
        identity = _git(root, "show", "-s", "--format=%an%x00%ae%x00%cn%x00%ce", "HEAD").rstrip(b"\n").decode("utf-8").split("\0")
        if len(identity) != 4:
            raise ValueError
        for index, (name, email) in enumerate(zip(identity[::2], identity[1::2])):
            if not _valid_identity(name, email, expected_owner):
                report["findings"].append({"rule": "unexpected_commit_identity", "identity_index": index})
            if _contains_private(name + "\n" + email, patterns):
                report["findings"].append({"rule": "private_identity", "identity_index": index})
        names = _git(root, "ls-files", "-z", "--cached").split(b"\0")
        for index, raw in enumerate(filter(None, names)):
            name = os.fsdecode(raw)
            if _contains_private(name, patterns):
                report["findings"].append({"rule": "private_file_name", "file_index": index})
            data = _read_source(root, name)
            data = _remove_reviewed_lines(name, data, exceptions)
            views = [data.decode("utf-8", errors="replace")]
            views.extend(data[offset:].decode(encoding, errors="replace")
                         for encoding in ("utf-16-le", "utf-16-be") for offset in (0, 1))
            if any(_contains_private(view, patterns) for view in views):
                report["findings"].append({"rule": "private_file_content", "file_index": index})
            report["files_scanned"] += 1
        report.update(status="blocked" if report["findings"] else "pass", complete=True)
    except Exception:
        report["error_code"] = "identity_scan_incomplete"
    return report


def main():
    try:
        if os.environ.get("GITHUB_REPOSITORY") != REPOSITORY:
            raise ValueError
        terms = json.loads(os.environ["RELEASE_PRIVATE_TERMS_JSON"])
        report = check_public_identity(Path.cwd(), terms, line_exceptions=load_public_policy())
    except Exception:
        report = {"status": "error", "complete": False, "error_code": "identity_configuration_invalid"}
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "pass" and report["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
