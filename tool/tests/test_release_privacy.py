"""Behavioral tests for the release privacy gate; all secrets are synthetic."""
import hashlib
import gzip
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import struct
import unittest
import zipfile

SPEC = importlib.util.spec_from_file_location("release_privacy", Path(__file__).parents[1] / "release_privacy.py")
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)


def archive(entries, comment=b""):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as z:
        z.comment = comment
        for name, data in entries:
            z.writestr(name, data)
    return stream.getvalue()


class PrivacyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "release.ipa"

    def tearDown(self):
        self.temp.cleanup()

    def scan(self, payload, name="Payload/Runner.app/Runner", **kwargs):
        self.path.write_bytes(archive([(name, payload)]))
        return guard.scan_artifact(self.path, **kwargs)

    def test_public_identity_and_relative_routes(self):
        r = self.scan(b"https://github.com/aierlma/PiliPlus\x00lib/pages/home/controller.dart\x00/home/rcmd\x00https://api.example/home/user/item")
        self.assertEqual(r["status"], "pass")

    def test_home_paths_all_encodings_alignments(self):
        for text in ("/Users/private_person/project/file", "/home/private_person/src/a", "C:\\Users\\private_person\\src\\a", "file:///Users/private_person/src/a"):
            for encoding in ("utf-8", "utf-16-le", "utf-16-be"):
                for prefix in (b"", b"\x91"):
                    with self.subTest(text=text, encoding=encoding, prefix=prefix):
                        self.assertEqual(self.scan(prefix + text.encode(encoding))["status"], "blocked")

    def test_ci_exception_is_explicit(self):
        self.assertEqual(self.scan(b"/home/runner/work/build")["status"], "blocked")
        self.assertEqual(self.scan(b"/home/runner/work/build", allow_home_users=["runner"])["status"], "pass")
        self.assertEqual(self.scan(b"/Users/aierlma/build", allow_home_users=["aierlma"])["status"], "error")

    def test_narrow_member_exception(self):
        content = b"/Users/vendor_builder/release/library"
        exception = {"member": "Payload/lib.framework/lib", "sha256": hashlib.sha256(content).hexdigest(), "rules": ["home_path"]}
        self.assertEqual(self.scan(content, exception["member"], member_exceptions=[exception])["status"], "pass")
        self.assertEqual(self.scan(content, member_exceptions=[exception])["status"], "blocked")
        self.assertEqual(self.scan(content + b"!", exception["member"], member_exceptions=[exception])["status"], "blocked")
        self.assertEqual(self.scan(content, exception["member"], member_exceptions=[exception], blocked_terms=["vendor_builder"])["status"], "blocked")

    def test_exception_with_stored_zip_member(self):
        content = b"/Users/vendor_builder/release/library"
        name = "Payload/lib.framework/lib"
        exception = {"member": name, "sha256": hashlib.sha256(content).hexdigest(), "rules": ["home_path"]}
        with zipfile.ZipFile(self.path, "w", zipfile.ZIP_STORED) as z:
            z.writestr(name, content)
        self.assertEqual(guard.scan_artifact(self.path, member_exceptions=[exception])["status"], "pass")

    def test_archive_exception_cannot_allow_all_members(self):
        self.scan(b"/Users/private_person/build")
        exception = {"member": self.path.name, "sha256": hashlib.sha256(self.path.read_bytes()).hexdigest(), "rules": ["home_path"]}
        self.assertEqual(guard.scan_artifact(self.path, member_exceptions=[exception])["status"], "blocked")

    def test_blocked_terms_and_redaction(self):
        term = "secret_local_identity"
        for encoding in ("utf-8", "utf-16-le", "utf-16-be"):
            r = self.scan(term.encode(encoding), blocked_terms=[term])
            self.assertEqual(r["status"], "blocked")
            self.assertNotIn(term, json.dumps(r))
        with self.assertRaises(guard.PrivacyGuardError) as caught:
            guard.assert_public_artifact(self.path, blocked_terms=[term])
        self.assertNotIn(term, str(caught.exception))

    def test_nested_zip_without_extension(self):
        nested = archive([("private.txt", b"/Users/private_person/source")])
        self.assertEqual(self.scan(nested, "opaque.dat")["status"], "blocked")

    def test_archive_metadata(self):
        self.path.write_bytes(archive([("ok", b"ok")], comment=b"/Users/private_person/metadata"))
        self.assertEqual(guard.scan_artifact(self.path)["status"], "blocked")
        self.assertEqual(self.scan(b"ok", "Users/private_person/ok", blocked_terms=["private_person"])["status"], "blocked")

    def test_private_signing_files(self):
        for name in ("Payload/A.app/embedded.mobileprovision", "Payload/A.app/Plugins/B.appex/embedded.mobileprovision", "profile.p12", "key.p8"):
            self.assertEqual(self.scan(b"opaque", name)["status"], "blocked")

    def test_pem_markers_and_complete_secret(self):
        self.assertEqual(self.scan(b"-----BEGIN PRIVATE KEY-----\x00-----END PRIVATE KEY-----")["status"], "pass")
        pem = b"-----BEGIN PRIVATE KEY-----\n" + b"QUFB" * 32 + b"\n-----END PRIVATE KEY-----"
        self.assertEqual(self.scan(pem)["status"], "blocked")

    def test_credentials(self):
        for data in (b"ghp_" + b"x" * 40, b"ghs_123_" + b"z" * 100, b"SESSDATA=" + b"x" * 40):
            self.assertEqual(self.scan(data)["status"], "blocked")

    def test_chunk_boundary(self):
        old = guard.CHUNK
        guard.CHUNK = 47
        try:
            data = b"x" * 43 + b"\x00/Users/private_person/source"
            self.assertEqual(self.scan(data)["status"], "blocked")
        finally:
            guard.CHUNK = old

    def test_resource_limits_fail_closed(self):
        self.assertEqual(self.scan(b"a" * 200, limits={"max_member_bytes": 100})["status"], "error")
        nested = archive([("inner", archive([("leaf", b"ok")]))])
        r = self.scan(nested, "nested", limits={"max_depth": 1})
        self.assertEqual(r["status"], "error")
        self.assertFalse(r["complete"])

    def test_unsupported_archive_and_invalid_ipa(self):
        self.assertEqual(self.scan(b"not a real gzip", "nested.tar.gz")["status"], "error")
        self.path.write_bytes(b"not a zip")
        self.assertEqual(guard.scan_artifact(self.path)["status"], "error")

    def test_corruption_and_encryption_fail_closed(self):
        with zipfile.ZipFile(self.path, "w", zipfile.ZIP_STORED) as z:
            z.writestr("safe", b"original content")
        data = bytearray(self.path.read_bytes())
        data[data.index(b"original content")] ^= 1
        self.path.write_bytes(data)
        self.assertEqual(guard.scan_artifact(self.path)["status"], "error")
        data = bytearray(archive([("safe", b"safe")]))
        struct.pack_into("<H", data, 6, struct.unpack_from("<H", data, 6)[0] | 1)
        central = data.index(b"PK\x01\x02")
        struct.pack_into("<H", data, central + 8, struct.unpack_from("<H", data, central + 8)[0] | 1)
        self.path.write_bytes(data)
        r = guard.scan_artifact(self.path)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["error_code"], "encrypted_member")

    def test_zip_prefix_and_trailer_are_rejected(self):
        blob = archive([("safe", b"safe")])
        for content in (b"hidden prefix" + blob, b"PK\x03\x04hidden prefix" + blob, blob + b"hidden trailer"):
            self.path.write_bytes(content)
            self.assertEqual(guard.scan_artifact(self.path)["status"], "error")

    def test_standard_data_descriptor_is_supported(self):
        class Unseekable(io.BytesIO):
            def seekable(self):
                return False
            def seek(self, *args):
                raise OSError
        output = Unseekable()
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("safe", b"safe")
        self.path.write_bytes(output.getvalue())
        self.assertEqual(guard.scan_artifact(self.path)["status"], "pass")

    def test_duplicate_and_traversal(self):
        self.path.write_bytes(archive([("x", b"one"), ("X", b"two")]))
        self.assertEqual(guard.scan_artifact(self.path)["status"], "error")
        self.assertEqual(self.scan(b"ok", "../private")["status"], "error")

    def test_hash_binding(self):
        r = self.scan(b"safe")
        self.assertEqual(len(r["policy_sha256"]), 64)
        self.assertEqual(len(r["scanner_sha256"]), 64)
        self.assertTrue(guard.verify_artifact_hash(self.path, r))
        self.path.write_bytes(self.path.read_bytes() + b"changed")
        with self.assertRaises(guard.PrivacyGuardError):
            guard.verify_artifact_hash(self.path, r)

    def test_flutter_notices_gzip(self):
        name = "Payload/Runner.app/Frameworks/App.framework/flutter_assets/NOTICES.Z"
        self.assertEqual(self.scan(gzip.compress(b"Public copyright notices"), name)["status"], "pass")
        self.assertEqual(self.scan(gzip.compress(b"/Users/private_person/source"), name)["status"], "blocked")
        combined = gzip.compress(b"safe") + gzip.compress(b"/Users/private_person/source")
        self.assertEqual(self.scan(combined, name)["status"], "blocked")

    def test_gzip_metadata_crc_and_limits(self):
        blob = bytearray(gzip.compress(b"safe"))
        blob[3] |= 8
        blob[10:10] = b"/Users/private_person/header\0"
        self.assertEqual(self.scan(bytes(blob), "NOTICES.Z")["status"], "blocked")
        bad = bytearray(gzip.compress(b"safe"))
        bad[-8] ^= 1
        for content in (bytes(bad), gzip.compress(b"safe")[:-3], gzip.compress(b"safe") + b"hidden tail"):
            self.assertEqual(self.scan(content, "NOTICES.Z")["status"], "error")
        bomb = gzip.compress(b"a" * 4096)
        self.assertEqual(self.scan(bomb, "NOTICES.Z", limits={"max_member_bytes": 1024})["status"], "error")

    def test_public_policy_cannot_relax_rules(self):
        policy = Path(self.temp.name) / "policy.json"
        policy.write_text(json.dumps({"member_exceptions": []}))
        self.assertEqual(guard.load_public_policy(policy), {"member_exceptions": []})
        for extra in ({"limits": {}}, {"allow_home_users": ["aierlma"]}, {"blocked_terms": []}):
            policy.write_text(json.dumps({"member_exceptions": [], **extra}))
            with self.assertRaises(guard.PrivacyGuardError):
                guard.load_public_policy(policy)


if __name__ == "__main__":
    unittest.main()
