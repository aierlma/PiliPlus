"""Read a built IPA and record actual plist metadata, without executing it."""
import argparse
import hashlib
import json
import plistlib
import re
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib/scripts"))
from btr_ios_version import release_version


def verify(path, version, build, source, upstream, btr_upstream):
    with zipfile.ZipFile(path) as archive:
        entries = [name for name in archive.namelist() if re.fullmatch(r"Payload/[^/]+\.app/Info\.plist", name)]
        apps = [plistlib.loads(archive.read(name)) for name in entries]
        apps = [info for info in apps if info.get("CFBundlePackageType") == "APPL"]
        if len(apps) != 1:
            raise ValueError("IPA must have exactly one main application")
        info = apps[0]
    ios_version = release_version(version, build)
    expected = {"CFBundleIdentifier": "com.example.piliplus.btr", "CFBundleShortVersionString": ios_version, "CFBundleVersion": build, "PiliPlusUpstreamVersion": version}
    for key, value in expected.items():
        if info.get(key) != value:
            raise ValueError(f"Unexpected {key}: {info.get(key)}; expected {value}")
    minimum = info.get("MinimumOSVersion")
    if not isinstance(minimum, str) or not re.fullmatch(r"\d+(?:\.\d+)*", minimum):
        raise ValueError("Missing or invalid MinimumOSVersion")
    return {"source_sha": source, "upstream_sha": upstream, "btr_upstream_sha": btr_upstream, "bundle_id": info["CFBundleIdentifier"], "version": ios_version, "upstream_version": version, "build": build, "min_os_version": minimum, "size_bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("ipa", type=Path)
    for key in ("version", "build", "source", "upstream", "btr-upstream"):
        parser.add_argument(f"--{key}", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    data = verify(args.ipa, args.version, args.build, args.source, args.upstream, args.btr_upstream)
    args.out.write_text(json.dumps(data, indent=2) + "\n")
    print(json.dumps(data))
