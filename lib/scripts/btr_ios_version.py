"""Stamp a numeric iOS release version that SideStore can distinguish."""
import argparse
import plistlib
import re
from pathlib import Path


def release_version(upstream_version, build):
    if not re.fullmatch(r"\d+\.\d+\.\d+", upstream_version):
        raise ValueError("Expected a three-part upstream version")
    if not re.fullmatch(r"[1-9]\d*", build):
        raise ValueError("Expected a positive Git history build number")
    major, minor, _ = upstream_version.split(".")
    return f"{major}.{minor}.{build}"


def stamp(path, upstream_version, build):
    version = release_version(upstream_version, build)
    info = plistlib.loads(path.read_bytes())
    if info.get("CFBundleIdentifier") != "com.example.piliplus.btr":
        raise ValueError("Refusing to stamp a different application")
    info["CFBundleShortVersionString"] = version
    info["PiliPlusUpstreamVersion"] = upstream_version
    path.write_bytes(plistlib.dumps(info, sort_keys=False))
    return version


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("upstream_version")
    parser.add_argument("build")
    args = parser.parse_args()
    print(stamp(Path("ios/Runner/Info.plist"), args.upstream_version, args.build))
