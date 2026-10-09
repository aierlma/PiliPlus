"""Check that upstream merges retain the BTR integration and personal identity."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECKS = {
    "lib/pages/video/controller.dart": ["Pref.btrEnabled", "BtrProxyServer.instance.buildProxyUrl", "BtrProxyServer.instance.scheduleStop"],
    "lib/pages/video/widgets/header_control.dart": ["btr_quick_setting.dart"],
    "lib/pages/setting/models/video_settings.dart": ["btrEnabled", "btrConcurrency", "btrCdnRace"],
    "lib/pages/setting/widgets/btr_quick_setting.dart": ["BtrProxyServer"],
    "lib/http/api.dart": ["https://api.github.com/repos/aierlma/PiliPlus/releases"],
    "lib/utils/update.dart": ["BtrUpdate.publishedReleases", "BtrUpdate.availableRelease", "BuildConfig.versionCode"],
    "lib/utils/btr_update.dart": ["https://raw.githubusercontent.com/aierlma/AltGallery/refs/heads/master/apps/PiliPlus-BTR/apps.json", "com.example.piliplus.btr"],
    "lib/common/constants.dart": ["https://github.com/aierlma/PiliPlus"],
    "ios/Runner/Info.plist": ["com.example.piliplus.btr", "PiliPlus BTR"],
    "ios/Runner.xcodeproj/project.pbxproj": ["PRODUCT_BUNDLE_IDENTIFIER = com.example.piliplus.btr"],
}


def check(root=ROOT):
    for file, markers in CHECKS.items():
        text = (root / file).read_text()
        for marker in markers:
            if marker not in text:
                raise ValueError(f"Missing BTR integration in {file}: {marker}")
    for name in ("proxy_server", "multi_range_downloader", "cdn_pool", "cdn_racer", "range_core", "sidx_parser"):
        if not (root / f"lib/services/btr_proxy/{name}.dart").is_file():
            raise ValueError(f"Missing BTR module: {name}")


if __name__ == "__main__":
    check()
    print("BTR integration, bundle ID and personal updater verified")
