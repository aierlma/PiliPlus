#!/usr/bin/env python3
"""Fail-closed privacy gate for public release files (stdlib only).

ZIP/IPA, nested ZIP and bounded gzip streams are supported. Other archive formats are explicitly
rejected until a bounded parser is added. No matched values or input paths are
included in reports. This is a pattern-based release gate, not proof that an
arbitrary binary contains no possible secret.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import struct
import tempfile
import time
import zipfile
import zlib


DEFAULT_LIMITS = {
    "max_artifact_bytes": 2 * 1024**3,
    "max_member_bytes": 1024**3,
    "max_total_bytes": 8 * 1024**3,
    "max_members": 30000,
    "max_depth": 4,
    "max_metadata_bytes": 32 * 1024**2,
    "max_findings": 1000,
    "max_seconds": 300,
}
CHUNK = 1024 * 1024
OVERLAP = 132 * 1024
# Only generic, public CI account names can be opted into. Public account names
# such as aierlma are NOT an exception to private home-directory detection.
GENERIC_CI_USERS = frozenset({"runner"})
UNSUPPORTED_SUFFIXES = (".tar", ".tgz", ".tar.gz", ".bz2", ".xz", ".7z", ".rar", ".zst")
PRIVATE_SUFFIXES = (".mobileprovision", ".provisionprofile", ".p12", ".pfx", ".p8", ".keychain", ".keychain-db")
BOUNDARY = r"(?<![\w./\\:\-])"
# Require the home user to be followed by a path separator: /home/rcmd is
# commonly an API route, while /home/user/build is a build-machine path.
HOME = re.compile(
    BOUNDARY + r"(?:file://(?:localhost)?)?/(?:Users|home)/(?P<user>[^/\\\x00\s\"'<>]{1,128})[/\\]"
    r"|" + BOUNDARY + r"(?:file:///(?:localhost/)?)?[A-Za-z]:[/\\]+(?:Users|Documents and Settings)[/\\]+(?P<winuser>[^/\\\x00\r\n\"'<>]{1,128})[/\\]",
    re.I | re.ASCII,
)
TOKEN = re.compile(r"(?<![A-Za-z0-9_])(?:gh[pousr]_|github_pat_)[A-Za-z0-9_.\-]{20,}")
COOKIE = re.compile(r"\b(?:SESSDATA|bili_jct)[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9%+/_=\-]{16,}", re.I)
BEARER = re.compile(r"\bAuthorization[\"']?\s*[:=]\s*[\"']?Bearer\s+[A-Za-z0-9_.~+/=\-]{20,}", re.I)
UDID = re.compile(r"(?<![0-9A-Fa-f])0000[0-9A-Fa-f]{4}-[0-9A-Fa-f]{16}(?![0-9A-Fa-f])")
PEM_START = r"-----BEGIN (?P<label>(?:(?:RSA|EC|DSA|OPENSSH|ENCRYPTED) )?PRIVATE KEY)-----[ \t]*\r?\n"
PEM = re.compile(PEM_START + r"[A-Za-z0-9+/=:\-, \t\r\n]{32,65536}?-----END (?P=label)-----")
PEM_OVERFLOW = re.compile(PEM_START + r"[A-Za-z0-9+/=:\-, \t\r\n]{65537}")


class PrivacyGuardError(RuntimeError):
    """A value-free error. Its report is safe for publication."""

    def __init__(self, report):
        self.report = report
        super().__init__("Release privacy gate refused the artifact (" + report["status"] + ")")


class _Incomplete(Exception):
    pass


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _policy(allow_home_users, blocked_terms, member_exceptions, limits):
    users = sorted(set(allow_home_users))
    terms = sorted(set(blocked_terms))
    effective = dict(DEFAULT_LIMITS)
    if limits:
        if set(limits) - set(effective):
            raise _Incomplete("invalid_policy")
        effective.update(limits)
    if not set(users) <= GENERIC_CI_USERS:
        raise _Incomplete("invalid_policy")
    if (len(terms) > 128 or any(not isinstance(v, str) or not v or len(v) > 4096 for v in terms)
            or any(isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0 for v in effective.values())):
        raise _Incomplete("invalid_policy")
    exceptions = []
    for item in member_exceptions:
        if (set(item) != {"member", "sha256", "rules"} or not isinstance(item["member"], str)
                or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"])
                or item["rules"] != ["home_path"]):
            raise _Incomplete("invalid_policy")
        exceptions.append(dict(item))
    return {"version": 1, "allow_home_users": users, "blocked_terms": terms,
            "member_exceptions": exceptions, "limits": effective}


class _Scanner:
    def __init__(self, policy):
        self.policy = policy
        self.limits = policy["limits"]
        self.start = time.monotonic()
        self.total = 0
        self.members = 0
        self.findings = []
        self.seen = set()

    def check(self):
        if time.monotonic() - self.start > self.limits["max_seconds"]:
            raise _Incomplete("time_limit")

    def add(self, rule, index, offset=0, encoding="bytes"):
        key = (rule, index, encoding)
        if key in self.seen:
            return
        if len(self.findings) >= self.limits["max_findings"]:
            raise _Incomplete("finding_limit")
        self.seen.add(key)
        self.findings.append({"rule": rule, "member_index": index,
                              "offset": offset, "encoding": encoding})

    def scan_bytes(self, data, index, base=0, *, allowed_home=False):
        self.check()
        # latin1 is a lossless one-byte view. UTF-16 is checked at both byte
        # alignments, without depending on a BOM in arbitrary Mach-O sections.
        views = [(data.decode("latin1"), "ascii", 0)]
        for encoding in ("utf-16-le", "utf-16-be"):
            for alignment in (0, 1):
                views.append((data[alignment:].decode(encoding, errors="replace"), encoding, alignment))
        for text, encoding, alignment in views:
            def offset(pos):
                return base + alignment + (pos if encoding == "ascii" else len(text[:pos].encode(encoding)))
            for match in HOME.finditer(text):
                user = match.group("user") or match.group("winuser")
                if not allowed_home and user not in self.policy["allow_home_users"]:
                    self.add("home_path", index, offset(match.start()), encoding)
            for rule, pattern in (("github_token", TOKEN), ("session_credential", COOKIE),
                                  ("authorization_credential", BEARER), ("device_identifier", UDID),
                                  ("private_key", PEM)):
                match = pattern.search(text)
                if match:
                    self.add(rule, index, offset(match.start()), encoding)
            if PEM_OVERFLOW.search(text):
                raise _Incomplete("secret_candidate_limit")
        # Match private terms as encoded bytes, so non-ASCII terms are not
        # silently lost in the byte-preserving ASCII view.
        for term in self.policy["blocked_terms"]:
            for encoding in ("utf-8", "utf-16-le", "utf-16-be"):
                match = re.search(re.escape(term.encode(encoding)), data, re.I)
                if match:
                    self.add("blocked_term", index, base + match.start(), encoding)

    def copy(self, source, target, limit):
        digest = hashlib.sha256()
        size = 0
        while True:
            self.check()
            chunk = source.read(CHUNK)
            if not chunk:
                break
            size += len(chunk)
            self.total += len(chunk)
            if size > limit or self.total > self.limits["max_total_bytes"]:
                raise _Incomplete("byte_limit")
            target.write(chunk)
            digest.update(chunk)
        target.seek(0)
        return digest.hexdigest(), size

    def inspect_stream(self, stream, name, index, digest, depth):
        if depth > self.limits["max_depth"]:
            raise _Incomplete("depth_limit")
        stream.seek(0)
        head = stream.read(512)
        stream.seek(0)
        lower = name.lower()
        if head.startswith(b"\x1f\x8b"):
            self.inspect_gzip(stream, name, index, depth)
            return
        if lower.endswith(".gz"):
            raise _Incomplete("invalid_gzip")
        if (lower.endswith(UNSUPPORTED_SUFFIXES) or head.startswith((b"\x1f\x9d", b"BZh", b"\xfd7zXZ", b"7z\xbc\xaf\x27\x1c", b"Rar!", b"\x28\xb5\x2f\xfd"))
                or head[257:262] == b"ustar"):
            raise _Incomplete("unsupported_archive")
        is_zip = zipfile.is_zipfile(stream)
        stream.seek(0)
        if is_zip:
            # A stored member must not also be scanned as anonymous outer ZIP
            # bytes; otherwise exact member/hash exceptions cannot work. ZIP
            # names, comments and both local/central extra fields are inspected.
            if not head.startswith((b"PK\x03\x04", b"PK\x05\x06")):
                raise _Incomplete("unsupported_zip_layout")
            self.inspect_zip(stream, name, depth)
            return
        if lower.endswith((".zip", ".ipa", ".apk", ".jar")) or head.startswith(b"PK"):
            raise _Incomplete("invalid_archive")
        allow_home = index > 0 and any(e["member"] == name and e["sha256"] == digest
                         for e in self.policy["member_exceptions"])
        stream.seek(0)
        previous = b""
        consumed = 0
        while True:
            chunk = stream.read(CHUNK)
            if not chunk:
                break
            data = previous + chunk
            self.scan_bytes(data, index, consumed - len(previous), allowed_home=allow_home)
            consumed += len(chunk)
            previous = data[-OVERLAP:]

    def inspect_gzip(self, stream, parent, index, depth):
        """Scan each gzip header and bounded payload; zlib validates CRC/ISIZE.

        Flutter's NOTICES.Z is gzip despite its suffix. Concatenated members
        are supported, but trailing garbage or truncated streams are rejected.
        """
        stream.seek(0, os.SEEK_END)
        length = stream.tell()
        stream.seek(0)
        component = 0
        while stream.tell() < length:
            self.check()
            start = stream.tell()
            header = bytearray(stream.read(10))
            if len(header) != 10 or header[:3] != b"\x1f\x8b\x08" or header[3] & 0xe0:
                raise _Incomplete("invalid_gzip")
            flags = header[3]
            if flags & 4:
                extra_length = stream.read(2)
                if len(extra_length) != 2:
                    raise _Incomplete("invalid_gzip")
                size = struct.unpack("<H", extra_length)[0]
                extra = stream.read(size)
                if len(extra) != size:
                    raise _Incomplete("invalid_gzip")
                header.extend(extra_length + extra)
            for flag in (8, 16):
                if flags & flag:
                    while True:
                        byte = stream.read(1)
                        if not byte:
                            raise _Incomplete("invalid_gzip")
                        header.extend(byte)
                        if len(header) > min(self.limits["max_metadata_bytes"], 128 * 1024):
                            raise _Incomplete("metadata_limit")
                        if byte == b"\0":
                            break
            if flags & 2:
                crc = stream.read(2)
                if len(crc) != 2:
                    raise _Incomplete("invalid_gzip")
                header.extend(crc)
            self.scan_bytes(bytes(header), index, start)
            stream.seek(start)
            decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
            digest = hashlib.sha256()
            size = 0
            with tempfile.SpooledTemporaryFile(max_size=8 * 1024**2) as spool:
                while not decoder.eof:
                    self.check()
                    pending = stream.read(CHUNK)
                    if not pending:
                        raise _Incomplete("invalid_gzip")
                    while pending:
                        self.check()
                        output = decoder.decompress(pending, CHUNK)
                        size += len(output)
                        self.total += len(output)
                        if size > self.limits["max_member_bytes"] or self.total > self.limits["max_total_bytes"]:
                            raise _Incomplete("byte_limit")
                        spool.write(output)
                        digest.update(output)
                        if decoder.eof:
                            stream.seek(-len(decoder.unused_data), os.SEEK_CUR)
                            break
                        pending = decoder.unconsumed_tail
                self.members += 1
                component += 1
                if self.members > self.limits["max_members"]:
                    raise _Incomplete("member_limit")
                self.inspect_stream(spool, parent + "!gzip-" + str(component), self.members,
                                    digest.hexdigest(), depth + 1)

    def inspect_zip(self, stream, parent, depth):
        # Bound central-directory allocation before ZipFile parses it. ZIP64
        # is not needed for the bounded IPA policy and is rejected explicitly.
        stream.seek(0, os.SEEK_END)
        length = stream.tell()
        stream.seek(max(0, length - 65557))
        tail = stream.read()
        at = tail.rfind(b"PK\x05\x06")
        if at < 0 or len(tail) - at < 22:
            raise _Incomplete("invalid_archive")
        fields = struct.unpack_from("<4s4H2LH", tail, at)
        _, disk, central_disk, disk_entries, entries, cd_size, cd_offset, comment_size = fields
        if disk or central_disk or disk_entries != entries or entries == 65535 or cd_size == 0xffffffff:
            raise _Incomplete("unsupported_zip_layout")
        if cd_size > self.limits["max_metadata_bytes"] or entries + self.members > self.limits["max_members"]:
            raise _Incomplete("metadata_limit")
        if at + 22 + comment_size != len(tail):
            raise _Incomplete("invalid_archive")
        eocd_offset = length - len(tail) + at
        if cd_offset + cd_size != eocd_offset:
            raise _Incomplete("unsupported_zip_layout")
        stream.seek(0)
        with zipfile.ZipFile(stream) as archive:
            self.scan_bytes(archive.comment, 0)
            records = archive.infolist()
            if len(records) != entries:
                raise _Incomplete("invalid_archive")
            # Require a fully accounted-for local-file region. A valid ZIP
            # prefix/trailer or hidden gap must not become an unscanned payload.
            expected = 0
            for info in sorted(records, key=lambda item: item.header_offset):
                if info.header_offset != expected:
                    raise _Incomplete("unsupported_zip_layout")
                stream.seek(expected)
                local = stream.read(30)
                if len(local) != 30 or local[:4] != b"PK\x03\x04":
                    raise _Incomplete("invalid_archive")
                name_size, extra_size = struct.unpack_from("<HH", local, 26)
                expected += 30 + name_size + extra_size + info.compress_size
                if info.flag_bits & 8:
                    stream.seek(expected)
                    descriptor = stream.read(16)
                    signature = descriptor.startswith(b"PK\x07\x08")
                    values = struct.unpack_from("<LLL", descriptor, 4 if signature else 0)
                    if values != (info.CRC, info.compress_size, info.file_size):
                        raise _Incomplete("invalid_archive")
                    expected += 16 if signature else 12
            if expected != cd_offset:
                raise _Incomplete("unsupported_zip_layout")
            names = set()
            for info in records:
                self.check()
                self.members += 1
                index = self.members
                if self.members > self.limits["max_members"]:
                    raise _Incomplete("member_limit")
                name = info.filename
                self.scan_bytes(name.encode("utf-8"), index)
                self.scan_bytes(info.comment + info.extra, index)
                # A local header may contain extra metadata absent from the
                # central directory. Do not let that become an unscanned lane.
                stream.seek(info.header_offset)
                local = stream.read(30)
                if len(local) != 30 or local[:4] != b"PK\x03\x04":
                    raise _Incomplete("invalid_archive")
                name_size, extra_size = struct.unpack_from("<HH", local, 26)
                self.scan_bytes(stream.read(name_size + extra_size), index)
                parts = PurePosixPath(name).parts
                if (name.startswith(("/", "\\")) or ".." in parts or "\\" in name
                        or "\x00" in name or re.match(r"^[A-Za-z]:", name)):
                    raise _Incomplete("unsafe_member_path")
                normalized = name.rstrip("/").casefold()
                if normalized in names:
                    raise _Incomplete("duplicate_member")
                names.add(normalized)
                if name.lower().endswith(PRIVATE_SUFFIXES):
                    self.add("private_signing_material", index)
                mode = info.external_attr >> 16
                if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)):
                    raise _Incomplete("unsupported_member_type")
                if info.flag_bits & 1:
                    raise _Incomplete("encrypted_member")
                if info.file_size > self.limits["max_member_bytes"]:
                    raise _Incomplete("byte_limit")
                if info.is_dir():
                    if info.file_size:
                        raise _Incomplete("invalid_directory")
                    continue
                child = name if depth == 0 else parent + "!" + name
                with archive.open(info) as source, tempfile.SpooledTemporaryFile(max_size=8 * 1024**2) as spool:
                    digest, size = self.copy(source, spool, self.limits["max_member_bytes"])
                    if size != info.file_size:
                        raise _Incomplete("size_mismatch")
                    self.inspect_stream(spool, child, index, digest, depth + 1)


def scan_artifact(path, *, allow_home_users=(), blocked_terms=(), member_exceptions=(), limits=None):
    """Return a redacted report; only status=pass AND complete=True is admitted.

    member_exceptions: exact {member, sha256, rules:["home_path"]} records.
    A member exception never bypasses private terms, credentials or profile rules.
    The artifact is snapshotted before scanning. Callers must hash-check the
    exact staged file again before upload; reports cannot freeze external paths.
    """
    report = {"status": "error", "complete": False, "sha256": None,
              "size_bytes": None, "policy_sha256": None, "scanner_sha256": None, "findings": []}
    scanner = None
    try:
        report["scanner_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        policy = _policy(allow_home_users, blocked_terms, member_exceptions, limits)
        report["policy_sha256"] = hashlib.sha256(_canonical(policy)).hexdigest()
        scanner = _Scanner(policy)
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        with os.fdopen(os.open(path, flags), "rb") as source:
            before = os.fstat(source.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise _Incomplete("not_regular_file")
            if before.st_size > scanner.limits["max_artifact_bytes"]:
                raise _Incomplete("byte_limit")
            with tempfile.SpooledTemporaryFile(max_size=8 * 1024**2) as snapshot:
                digest, size = scanner.copy(source, snapshot, scanner.limits["max_artifact_bytes"])
                report.update(sha256=digest, size_bytes=size)
                after = os.fstat(source.fileno())
                if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                    raise _Incomplete("artifact_changed")
                scanner.inspect_stream(snapshot, Path(path).name, 0, digest, 0)
        report.update(status="blocked" if scanner.findings else "pass", complete=True)
    except _Incomplete as error:
        report["error_code"] = error.args[0]
    except Exception:
        # Library errors often echo filenames or offending bytes. Never include
        # their message, repr, or traceback in a public release report.
        report["error_code"] = "scan_failed"
    if scanner is not None:
        report["findings"] = scanner.findings
        report["members_scanned"] = scanner.members
        report["expanded_bytes"] = scanner.total
    return report


def assert_public_artifact(path, **kwargs):
    report = scan_artifact(path, **kwargs)
    if report["status"] != "pass" or not report["complete"]:
        raise PrivacyGuardError(report)
    return report


def load_public_policy(path):
    """Load reviewed member/hash exceptions, without relaxing other rules.

    Public policy is deliberately narrower than a local private configuration:
    it cannot change limits, CI account allowance or private-term detection.
    """
    try:
        with open(path, "rb") as source:
            raw = source.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError
        policy = json.loads(raw)
        if not isinstance(policy, dict) or set(policy) != {"member_exceptions"}:
            raise ValueError
        checked = _policy((), (), policy["member_exceptions"], None)
        return {"member_exceptions": checked["member_exceptions"]}
    except Exception:
        raise PrivacyGuardError({"status": "invalid_public_policy"}) from None


def verify_artifact_hash(path, report):
    """Require an admitted report and recheck the upload candidate's bytes."""
    if report.get("status") != "pass" or report.get("complete") is not True:
        raise PrivacyGuardError({"status": "invalid_report"})
    try:
        digest = hashlib.sha256()
        size = 0
        with open(path, "rb") as artifact:
            while chunk := artifact.read(CHUNK):
                digest.update(chunk)
                size += len(chunk)
        if digest.hexdigest() != report.get("sha256") or size != report.get("size_bytes"):
            raise ValueError
    except Exception:
        raise PrivacyGuardError({"status": "artifact_changed"}) from None
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--allow-home-user", action="append", default=[])
    parser.add_argument("--policy", type=Path, help="Reviewed public JSON with exact member/hash home-path exceptions only")
    parser.add_argument("--private-config", type=Path, help="Local JSON: blocked_terms/member_exceptions/limits; never publish it")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    try:
        config = json.loads(args.private_config.read_text()) if args.private_config else {}
        if set(config) - {"blocked_terms", "member_exceptions", "limits"}:
            raise ValueError
        if args.policy:
            public = load_public_policy(args.policy)
            config["member_exceptions"] = public["member_exceptions"] + config.get("member_exceptions", [])
        report = scan_artifact(args.artifact, allow_home_users=args.allow_home_user, **config)
        rendered = json.dumps(report, sort_keys=True, indent=2) + "\n"
        if args.report:
            args.report.write_text(rendered)
        print(rendered, end="")
        return 0 if report["status"] == "pass" else (1 if report["status"] == "blocked" else 2)
    except Exception:
        print('{"status":"error","complete":false,"error_code":"configuration_or_report_io_failed"}')
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
