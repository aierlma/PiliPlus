/// A formal personal iOS release whose IPA follows the publication contract.
final class BtrIosRelease {
  BtrIosRelease._(this.data, this.version, this.build, this.downloadUrl);

  final Map<String, dynamic> data;
  final String version;
  final int build;
  final String downloadUrl;

  static BtrIosRelease? parse(dynamic data) {
    if (data is! Map<String, dynamic> ||
        data['draft'] != false ||
        data['prerelease'] != false) {
      return null;
    }
    final tag = data['tag_name'];
    if (tag is! String) return null;
    final match = RegExp(r'^v(\d+\.\d+\.\d+)-btr\.([1-9]\d*)-[0-9a-f]{12}$')
        .firstMatch(tag);
    if (match == null) return null;
    final version = match[1]!;
    final build = int.tryParse(match[2]!);
    if (build == null) return null;
    final name = 'PiliPlus-BTR-ios-$version-btr.$build-unsigned.ipa';
    final url =
        'https://github.com/aierlma/PiliPlus/releases/download/$tag/$name';
    final assets = data['assets'];
    if (assets is! List ||
        !assets.any(
          (asset) =>
              asset is Map &&
              asset['name'] == name &&
              asset['state'] == 'uploaded' &&
              asset['browser_download_url'] == url,
        )) {
      return null;
    }
    return BtrIosRelease._(data, version, build, url);
  }
}

abstract final class BtrUpdate {
  static const sourceUrl =
      'https://raw.githubusercontent.com/aierlma/AltGallery/refs/heads/master/apps/PiliPlus-BTR/apps.json';
  static const bundleIdentifier = 'com.example.piliplus.btr';

  static List<BtrIosRelease> publishedReleases(dynamic response) {
    if (response is! List) {
      throw const FormatException('Invalid GitHub releases response');
    }
    final releases =
        response.map(BtrIosRelease.parse).whereType<BtrIosRelease>().toList()
          ..sort((a, b) => b.build.compareTo(a.build));
    if (releases.isEmpty) {
      throw const FormatException('No formal personal iOS release found');
    }
    return releases;
  }

  /// Use the IPA metadata published in the source, never release timestamps.
  static BtrIosRelease? availableRelease(
    dynamic source,
    List<BtrIosRelease> releases,
    int installedBuild,
  ) {
    if (source is! Map || source['apps'] is! List) {
      throw const FormatException('Invalid SideStore source');
    }
    final apps = (source['apps'] as List).where(
      (app) => app is Map && app['bundleIdentifier'] == bundleIdentifier,
    );
    if (apps.length != 1 || apps.single['versions'] is! List) {
      throw const FormatException('Missing or ambiguous personal BTR app');
    }
    final versions = apps.single['versions'] as List;
    if (versions.isEmpty) {
      throw const FormatException('No version in the SideStore source');
    }
    for (final release in releases) {
      if (release.build <= installedBuild) continue;
      final parts = release.version.split('.');
      final iosVersion = '${parts[0]}.${parts[1]}.${release.build}';
      if (versions.any(
        (version) =>
            version is Map &&
            version['version'] == iosVersion &&
            version['buildVersion'] == '${release.build}' &&
            version['downloadURL'] == release.downloadUrl,
      )) {
        return release;
      }
    }
    return null;
  }
}
