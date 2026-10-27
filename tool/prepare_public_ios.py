#!/usr/bin/env python3
"""Remove native debug paths and verify ad-hoc framework signatures."""
import argparse
from pathlib import Path
import plistlib
import subprocess


MACHO_MAGIC = {
    b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe",
    b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca",
    b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca",
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
            raise RuntimeError("Native debug-symbol removal failed")
    for framework in sorted(app.rglob("*.framework"), key=lambda item: -len(item.parts)):
        for command in (
            ["codesign", "--force", "--sign", "-", "--preserve-metadata=identifier,entitlements", str(framework)],
            ["codesign", "--verify", "--strict", str(framework)],
        ):
            if runner(command, capture_output=True).returncode:
                raise RuntimeError("Public framework signature preparation failed")
    return len(paths)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app", type=Path)
    args = parser.parse_args()
    try:
        count = prepare(args.app)
    except Exception:
        parser.exit(1, "Public iOS preparation failed; publication is blocked.\n")
    print(f"Prepared {count} native executables and verified framework signatures; IPA scan is still required.")


if __name__ == "__main__":
    main()
