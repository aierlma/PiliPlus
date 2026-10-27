import 'package:PiliPlus/utils/ios/external_display_app.dart';
import 'package:canvas_danmaku/canvas_danmaku.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:material_ui/material_ui.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  const channel = MethodChannel('com.example.piliplus/external-renderer');
  final messenger =
      TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger;

  tearDown(() => messenger.setMockMethodCallHandler(channel, null));

  test('TV danmaku keeps colors, placement and membership decorations', () {
    final item = externalDanmakuItem({
      'text': '彩色顶部弹幕',
      'mode': 5,
      'color': 0xffff5078,
      'colorful': true,
      'selfSend': true,
      'count': 3,
    })!;
    expect(item.type, DanmakuItemType.top);
    expect(item.color.toARGB32(), 0xffff5078);
    expect(item.isColorful, isTrue);
    expect(item.selfSend, isTrue);
    expect(item.count, 3);
    expect(externalDanmakuItem({'text': 'broken special', 'mode': 7}), isNull);
    expect(
      externalDanmakuItem({'text': 'next normal', 'mode': 4})?.type,
      DanmakuItemType.bottom,
    );
  });

  test('TV receives full-screen timing, density and filtering preferences', () {
    final option = externalDanmakuOptions({
      'fontSize': 23.5,
      'area': 0.6,
      'duration': 4.5,
      'staticDuration': 2.5,
      'hideSpecial': true,
      'hideTop': true,
      'massiveMode': true,
      'fixedVelocity': true,
    });
    expect(option.fontSize, 23.5);
    expect(option.area, 0.6);
    expect(option.durationInMilliseconds, 4500);
    expect(option.staticDurationInMilliseconds, 2500);
    expect(option.hideSpecial && option.hideTop, isTrue);
    expect(option.massiveMode && option.scrollFixedVelocity, isTrue);
    expect(option.safeArea, isTrue);
  });

  testWidgets(
    'subtitle and danmaku changes keep the same shared video surface',
    (tester) async {
      final outgoing = <String>[];
      messenger.setMockMethodCallHandler(channel, (call) async {
        outgoing.add(call.method);
        return null;
      });
      await tester.pumpWidget(const ExternalDisplayApp());
      await tester.pump();
      expect(outgoing, ['ready']);

      Future<void> send(String method, Object? arguments) async {
        await messenger.handlePlatformMessage(
          channel.name,
          const StandardMethodCodec().encodeMethodCall(
            MethodCall(method, arguments),
          ),
          (_) {},
        );
        await tester.pump();
      }

      final state = <String, Object>{
        'textureId': 41,
        'width': 1920,
        'height': 1080,
        'playing': true,
        'danmaku': true,
        'subtitleText': '独立字幕仍然可见',
        'subtitleStyle': {'backgroundOpacity': 0.65},
      };
      await send('update', state);
      final video = tester.element(find.byType(Texture));
      expect(tester.widget<Texture>(find.byType(Texture)).textureId, 41);
      expect(find.text('独立字幕仍然可见'), findsOneWidget);
      expect(find.byType(DanmakuScreen<void>), findsOneWidget);

      await send('danmaku', [
        {'text': '普通弹幕', 'color': 0xffff0000, 'mode': 1},
        {'text': 'invalid advanced item', 'mode': 7},
      ]);
      await send('update', {
        ...state,
        'danmaku': false,
        'subtitleText': '切换后的字幕',
      });
      expect(tester.element(find.byType(Texture)), same(video));
      expect(find.text('切换后的字幕'), findsOneWidget);
      expect(find.text('独立字幕仍然可见'), findsNothing);
      final opacity = tester.widget<Opacity>(
        find.byWidgetPredicate(
          (widget) => widget is Opacity && widget.child is DanmakuScreen<void>,
        ),
      );
      expect(opacity.opacity, 0);
      await send('clearDanmaku', null);
      await send('update', {...state, 'playing': false});
      expect(tester.element(find.byType(Texture)), same(video));
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox.shrink());
    },
  );
}
