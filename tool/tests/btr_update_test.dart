import 'dart:convert';
import 'dart:io';

// Run against the real library without requiring Flutter package resolution.
// ignore: avoid_relative_lib_imports
import '../../lib/utils/btr_update.dart';

Map<String, dynamic> release(
  int build, {
  bool draft = false,
  bool prerelease = false,
  String version = '2.1.6',
}) {
  final tag = 'v$version-btr.$build-59ac60873c6e';
  final name = 'PiliPlus-BTR-ios-$version-btr.$build-unsigned.ipa';
  return {
    'tag_name': tag,
    'created_at': '2099-01-01T00:00:00Z',
    'draft': draft,
    'prerelease': prerelease,
    'assets': [
      {
        'name': name,
        'state': 'uploaded',
        'browser_download_url':
            'https://github.com/aierlma/PiliPlus/releases/download/$tag/$name',
      },
    ],
  };
}

Map<String, dynamic> source(List<Map<String, dynamic>> releases) => {
  'apps': [
    {
      'bundleIdentifier': BtrUpdate.bundleIdentifier,
      'versions': [
        for (final data in releases)
          {
            'version':
                '${BtrIosRelease.parse(data)!.version.split('.').take(2).join('.')}.${BtrIosRelease.parse(data)!.build}',
            'buildVersion': '${BtrIosRelease.parse(data)!.build}',
            'downloadURL': BtrIosRelease.parse(data)!.downloadUrl,
          },
      ],
    },
  ],
};

void equal(Object? actual, Object? expected) {
  if (actual != expected) {
    throw StateError('Expected $expected, got $actual');
  }
}

void invalid(void Function() body) {
  try {
    body();
  } on FormatException {
    return;
  }
  throw StateError('Expected invalid source to be rejected');
}

void main(List<String> args) {
  var passed = 0;
  void test(String name, void Function() body) {
    body();
    passed++;
    stdout.writeln('PASS: $name');
  }

  final old = release(5502);
  final newest = release(5506);
  final releases = BtrUpdate.publishedReleases([old, newest]);

  test('Sort by package build, not API order or release timestamp', () {
    equal(releases.first.build, 5506);
  });
  test('Same installed package never prompts despite later creation time', () {
    equal(BtrUpdate.availableRelease(source([newest]), releases, 5506), null);
  });
  test('A source behind the GitHub release cannot offer that release', () {
    equal(BtrUpdate.availableRelease(source([old]), releases, 5502), null);
  });
  test('A published source offers its newer package', () {
    equal(
      BtrUpdate.availableRelease(source([newest]), releases, 5502)?.build,
      5506,
    );
  });
  test('A lagging source can still offer an intermediate verified package', () {
    equal(
      BtrUpdate.availableRelease(source([old]), releases, 5488)?.build,
      5502,
    );
  });
  test('Already installed newer build is never downgraded', () {
    equal(BtrUpdate.availableRelease(source([old]), releases, 5506), null);
  });
  test('Drafts and prereleases cannot become the latest iOS release', () {
    equal(
      BtrUpdate.publishedReleases([
        release(5510, draft: true),
        release(5509, prerelease: true),
        newest,
      ]).first.build,
      5506,
    );
  });
  test('Official and ios14 tags are excluded from this update channel', () {
    for (final tag in [
      '2.1.6',
      'v2.1.4-ios14.1',
      'v2.1.6-btr.0-59ac60873c6e',
    ]) {
      final data = release(5510)..['tag_name'] = tag;
      equal(BtrIosRelease.parse(data), null);
    }
  });
  test('A release without an uploaded matching IPA is excluded', () {
    final data = release(5510);
    (data['assets'] as List).single['state'] = 'new';
    equal(BtrIosRelease.parse(data), null);
    data['assets'] = [];
    equal(BtrIosRelease.parse(data), null);
  });
  test('An IPA pointing outside the personal release is excluded', () {
    final data = release(5510);
    (data['assets'] as List).single['browser_download_url'] =
        'https://github.com/other/PiliPlus/foreign.ipa';
    equal(BtrIosRelease.parse(data), null);
  });
  test('Unrelated official app entry cannot offer a BTR update', () {
    final data = source([newest]);
    (data['apps'] as List).insert(0, {
      'bundleIdentifier': 'com.example.piliplus',
      'versions': [],
    });
    equal(BtrUpdate.availableRelease(data, releases, 5502)?.build, 5506);
  });
  test('Source version must agree with build metadata and IPA URL', () {
    for (final field in ['version', 'buildVersion', 'downloadURL']) {
      final data = source([newest]);
      (data['apps'] as List).single['versions'][0][field] = 'incorrect';
      equal(BtrUpdate.availableRelease(data, releases, 5502), null);
    }
  });
  test('Duplicate or missing BTR entries are rejected', () {
    final duplicate = source([newest]);
    (duplicate['apps'] as List).add((duplicate['apps'] as List).single);
    invalid(() => BtrUpdate.availableRelease(duplicate, releases, 5502));
    invalid(() => BtrUpdate.availableRelease({'apps': []}, releases, 5502));
  });
  test('Malformed API and source responses fail without an update prompt', () {
    for (final data in [
      null,
      'not json',
      {},
      {'apps': null},
    ]) {
      invalid(() => BtrUpdate.publishedReleases(data));
      invalid(() => BtrUpdate.availableRelease(data, releases, 5502));
    }
    invalid(() => BtrUpdate.publishedReleases([]));
    final data = source([]);
    invalid(() => BtrUpdate.availableRelease(data, releases, 5502));
  });
  test('A source version needs a corresponding formal GitHub release', () {
    equal(
      BtrUpdate.availableRelease(source([release(5510)]), releases, 5502),
      null,
    );
  });
  test('New official minor version retains the personal build identity', () {
    final data = release(5510, version: '2.2.0');
    equal(
      BtrUpdate.availableRelease(
        source([data]),
        BtrUpdate.publishedReleases([data]),
        5506,
      )?.build,
      5510,
    );
  });
  test('Raw GitHub JSON text is accepted after decoding', () {
    equal(
      BtrUpdate.availableRelease(
        jsonDecode(jsonEncode(source([newest]))),
        releases,
        5502,
      )?.build,
      5506,
    );
  });

  if (args.isNotEmpty) {
    final live = jsonDecode(File(args.single).readAsStringSync());
    final published = BtrUpdate.publishedReleases(live['personal_releases']);
    for (final key in ['individual', 'aggregate']) {
      final app = (live[key]['apps'] as List).singleWhere(
        (app) => app['bundleIdentifier'] == BtrUpdate.bundleIdentifier,
      );
      final sourceBuild = int.parse(
        app['versions'][0]['buildVersion'] as String,
      );
      test('Actual $key source offers only its build $sourceBuild', () {
        equal(
          BtrUpdate.availableRelease(live[key], published, 5502)?.build,
          sourceBuild > 5502 ? sourceBuild : null,
        );
        equal(
          BtrUpdate.availableRelease(live[key], published, 5488)?.build,
          sourceBuild,
        );
        equal(
          BtrUpdate.availableRelease(live[key], published, sourceBuild),
          null,
        );
      });
    }
  }
  stdout.writeln('$passed BTR update checks passed');
}
