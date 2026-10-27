#!/usr/bin/env python3
"""Remove native debug paths and verify ad-hoc framework signatures."""
import argparse
import hashlib
import json
from pathlib import Path
import plistlib
import subprocess


MACHO_MAGIC = {
    b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe",
    b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca",
    b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca",
}


class PreparationError(RuntimeError):
    def __init__(self, message, code, path, app, result):
        super().__init__(message)
        relative = str(path.relative_to(app)).encode()
        diagnostic = (result.stderr or b"").lower()
        categories = {
            b"permission denied": "permission_denied",
            b"operation not permitted": "operation_not_permitted",
            b"resource fork": "extended_attributes",
            b"not signed at all": "unsigned_object",
            b"invalid signature": "invalid_signature",
            b"bundle format": "bundle_format",
            b"not an object file": "object_format",
            b"no symbols": "no_symbols",
        }
        self.report = {
            "status": "error", "error_code": code,
            "member_digest": hashlib.sha256(relative).hexdigest()[:16],
            "tool_exit": result.returncode,
            "reason": next((v for k, v in categories.items() if k in diagnostic), "tool_failure"),
        }


def prepare(app, *, runner=subprocess.run):
    """Strip only debug symbols; preserve executable code and dynamic symbols.

    The complete IPA privacy scan must run after this step. This
    cannot remove source URIs compiled into Dart AOT code, so public packages
    are built in GitHub Actions rather than personal home directories.
    """
    app = Path(app)
    if app.is_symlink() or not app.is_dir() or app.suffix != ".app":
        raise ValueError("Expected a regular application directory")
    info = plistlib.loads((app / "Info.plist").read_bytes())
    if info.get("CFBundlePackageType") != "APPL":
        raise ValueError("Expected an application bundle")
    paths = []
    for path in sorted(app.rglob("*")):
        if path.is_symlink():
            raise ValueError("Application contains a symbolic link")
        if path.is_file():
            if path.suffix.lower() in {".mobileprovision", ".provisionprofile", ".p12", ".pfx"}:
                raise ValueError("Public application contains personal signing material")
            with path.open("rb") as source:
                if source.read(4) in MACHO_MAGIC:
                    paths.append(path)
    if not paths:
        raise ValueError("No native executables found")
    for path in paths:
        result = runner(["xcrun", "strip", "-S", str(path)], capture_output=True)
        if result.returncode:
            raise PreparationError("Native debug-symbol removal failed", "native_strip_failed", path, app, result)
    for framework in sorted(app.rglob("*.framework"), key=lambda item: -len(item.parts)):
        for command in (
            ["codesign", "--force", "--sign", "-", "--preserve-metadata=identifier,entitlements", str(framework)],
            ["codesign", "--verify", "--strict", str(framework)],
        ):
            result = runner(command, capture_output=True)
            if result.returncode:
                code = "framework_sign_failed" if "--force" in command else "framework_verify_failed"
                raise PreparationError("Public framework signature preparation failed", code, framework, app, result)
    return len(paths)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app", type=Path)
    args = parser.parse_args()
    try:
        count = prepare(args.app)
    except PreparationError as error:
        print(json.dumps(error.report, sort_keys=True))
        parser.exit(1, "Public iOS preparation failed; publication is blocked.\n")
    except Exception as error:
        codes = {
            "Expected a regular application directory": "invalid_application_directory",
            "Expected an application bundle": "invalid_bundle_type",
            "Application contains a symbolic link": "symbolic_link",
            "Public application contains personal signing material": "personal_signing_material",
            "No native executables found": "no_native_executables",
        }
        print(json.dumps({"status": "error", "error_code": codes.get(str(error), "inspection_incomplete")}))
        parser.exit(1, "Public iOS preparation failed; publication is blocked.\n")
    print(f"Prepared {count} native executables and verified framework signatures; IPA scan is still required.")


if __name__ == "__main__":
    main()
