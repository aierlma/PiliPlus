import 'dart:async';
import 'dart:io';

import 'package:PiliPlus/pages/setting/models/model.dart';
import 'package:PiliPlus/pages/setting/models/video_settings.dart';
import 'package:PiliPlus/utils/storage.dart';
import 'package:PiliPlus/utils/storage_key.dart';
import 'package:PiliPlus/utils/storage_pref.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:get/get.dart';
import 'package:hive_ce/hive.dart';
import 'package:material_ui/material_ui.dart';

// Live-buffer controls belong to the pinned official merge candidate.
const _isSyncCandidate = bool.fromEnvironment('BTR_SYNC_CANDIDATE');

NormalModel _setting(String title) =>
    videoSettings.whereType<NormalModel>().singleWhere(
      (model) => model.title == title,
    );

class _RefreshRecorder {
  int count = 0;
  final completed = Completer<void>();

  void call() {
    count++;
    if (!completed.isCompleted) completed.complete();
  }
}

Future<_RefreshRecorder> _openSetting(
  WidgetTester tester,
  String title,
) async {
  final model = _setting(title);
  final refresh = _RefreshRecorder();
  await tester.pumpWidget(
    GetMaterialApp(
      home: Scaffold(
        body: Builder(
          builder: (context) => TextButton(
            onPressed: () => model.onTap!(context, refresh.call),
            child: const Text('Open setting'),
          ),
        ),
      ),
    ),
  );
  // Start the callback outside FakeAsync so its Hive continuation can finish.
  await tester.runAsync(() => tester.tap(find.text('Open setting')));
  await tester.pumpAndSettle();
  expect(find.text(title), findsOneWidget);
  return refresh;
}

Future<void> _selectAndWaitForWrite(
  WidgetTester tester,
  _RefreshRecorder refresh,
  Finder selection,
) async {
  // The dialog awaits a real Hive file write before refreshing its caller.
  await tester.runAsync(() async {
    await tester.tap(selection);
    await refresh.completed.future.timeout(const Duration(seconds: 5));
  });
  await tester.pumpAndSettle();
  expect(refresh.count, 1);
  expect(find.byType(AlertDialog), findsNothing);
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  late Directory tempDir;

  setUpAll(() async {
    tempDir = await Directory.systemTemp.createTemp('piliplus-video-settings-');
    Hive.init(tempDir.path);
    GStorage.setting = await Hive.openBox<dynamic>('setting');
  });

  setUp(() async {
    Get.testMode = true;
    await GStorage.setting.clear();
  });

  tearDown(() {
    Get.reset();
  });

  tearDownAll(() async {
    await Hive.close();
    await tempDir.delete(recursive: true);
  });

  testWidgets('BTR concurrency selection persists and refreshes', (tester) async {
    expect(Pref.btrConcurrency, 8);
    final refresh = await _openSetting(tester, 'BTR 并发上限');
    await _selectAndWaitForWrite(tester, refresh, find.text('16 线程'));

    expect(GStorage.setting.get(SettingBoxKey.btrConcurrency), 16);
    expect(Pref.btrConcurrency, 16);
  });

  testWidgets('BTR group selection persists and refreshes', (tester) async {
    expect(Pref.btrGroup, 'auto');
    final refresh = await _openSetting(tester, 'BTR 节点分组');
    await _selectAndWaitForWrite(
      tester,
      refresh,
      find.text('海外节点（接近大陆网络差时用）'),
    );

    expect(GStorage.setting.get(SettingBoxKey.btrGroup), 'overseas');
    expect(Pref.btrGroup, 'overseas');
  });

  for (final (title, key, initial) in const [
    ('BTR 并发上限', SettingBoxKey.btrConcurrency, 32),
    ('BTR 节点分组', SettingBoxKey.btrGroup, 'mainland'),
  ]) {
    testWidgets('$title dismissal does not write or refresh', (tester) async {
      await tester.runAsync(() => GStorage.setting.put(key, initial));
      final changes = <BoxEvent>[];
      final subscription = GStorage.setting.watch(key: key).listen(changes.add);
      addTearDown(subscription.cancel);
      final refresh = await _openSetting(tester, title);

      await tester.tapAt(const Offset(5, 5));
      await tester.pumpAndSettle();

      expect(find.byType(AlertDialog), findsNothing);
      expect(GStorage.setting.get(key), initial);
      expect(changes, isEmpty);
      expect(refresh.count, 0);
    });
  }

  for (final (title, key, value, subtitle) in const [
    ('直播缓冲大小', 'liveBufferSize', 12.5, '12.5MB'),
    ('直播缓冲时长', 'liveBufferSec', 7.25, '7.25s'),
  ]) {
    testWidgets(
      '$title persists a decimal and refreshes',
      (tester) async {
        final refresh = await _openSetting(tester, title);
        await tester.enterText(find.byType(TextFormField), value.toString());
        await _selectAndWaitForWrite(tester, refresh, find.text('确定'));

        expect(GStorage.setting.get(key), isA<double>());
        expect(GStorage.setting.get(key), value);
        expect(_setting(title).effectiveSubtitle, contains(subtitle));
      },
      skip: !_isSyncCandidate,
    );
  }
}
