import 'dart:io';

import 'package:PiliPlus/utils/storage.dart';
import 'package:PiliPlus/utils/storage_key.dart';
import 'package:PiliPlus/utils/storage_pref.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:hive_ce/hive.dart';

void main() {
  late Directory tempDir;

  setUpAll(() async {
    tempDir = await Directory.systemTemp.createTemp('piliplus-pref-test-');
    Hive.init(tempDir.path);
    GStorage.setting = await Hive.openBox<dynamic>('setting');
  });

  setUp(() => GStorage.setting.clear());

  tearDownAll(() async {
    await Hive.close();
    await tempDir.delete(recursive: true);
  });

  test('BTR defaults survive an upstream preferences merge', () {
    expect(Pref.btrEnabled, isFalse);
    expect(Pref.btrConcurrency, 8);
    expect(Pref.btrGroup, 'auto');
    expect(Pref.btrCdnRace, isTrue);
  });

  test('BTR preferences use the existing persisted keys', () async {
    await GStorage.setting.putAll({
      SettingBoxKey.btrEnabled: true,
      SettingBoxKey.btrConcurrency: 4,
      SettingBoxKey.btrGroup: 'overseas',
      SettingBoxKey.btrCdnRace: false,
    });
    expect(Pref.btrEnabled, isTrue);
    expect(Pref.btrConcurrency, 4);
    expect(Pref.btrGroup, 'overseas');
    expect(Pref.btrCdnRace, isFalse);

    Pref.btrCdnRace = true;
    expect(GStorage.setting.get(SettingBoxKey.btrCdnRace), isTrue);
    expect(Pref.btrCdnRace, isTrue);
  });
}
