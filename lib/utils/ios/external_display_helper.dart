import 'dart:async';
import 'dart:ui' show PlatformDispatcher;

import 'package:PiliPlus/common/widgets/scale_app.dart';
import 'package:PiliPlus/services/btr_proxy/range_core.dart' show BtrLog;
import 'package:flutter/services.dart';

/// Connects the phone player to a separate TV renderer, keeping comments usable.
abstract final class IOSExternalDisplayHelper {
  static const _channel = MethodChannel(
    'com.example.piliplus/external-display',
  );
  static bool get isActive => _active;
  static bool _active = false;
  static int? _cid;
  static Future<void> Function()? _update;

  static Future<void> showControls() =>
      _channel.invokeMethod<void>('showControls');

  static Future<void> close() async {
    if (_active) await _channel.invokeMethod<void>('close');
  }

  static void refresh() {
    if (_active) unawaited(_update?.call());
  }

  static void clearDanmaku() {
    if (_active) unawaited(_send('clearDanmaku'));
  }

  static void sendDanmaku(int cid, List<Map<String, Object?>> items) {
    if (_active && cid == _cid && items.isNotEmpty) {
      unawaited(_send('danmaku', items));
    }
  }

  static Future<void> _send(String method, [Object? arguments]) async {
    try {
      await _channel.invokeMethod<void>(method, arguments);
    } on PlatformException catch (error) {
      BtrLog.log('[External display] ${BtrLog.redact(error)}');
    }
  }

  static Future<String?> present({
    required String title,
    required String artwork,
    required bool darkMode,
    required int cid,
    required Map<String, Object>? Function() state,
    required Future<void> Function(String command, double? value) onCommand,
  }) async {
    if (_active) throw StateError('External display is already open');
    final binding = ScaledWidgetsFlutterBinding.instance;
    final view = PlatformDispatcher.instance.views.first;
    final referenceHeight =
        view.physicalSize.shortestSide /
        view.devicePixelRatio /
        binding.scaleForView(view);
    _active = true;
    _cid = cid;
    Map<String, Object>? snapshot() {
      final value = state();
      if (value == null) return null;
      return {...value, 'referenceHeight': referenceHeight};
    }

    Future<void> update() async {
      if (!_active) return;
      final value = snapshot();
      if (value == null) {
        await close();
        return;
      }
      try {
        await _channel.invokeMethod<void>('update', value);
      } on PlatformException catch (error) {
        BtrLog.log('[External display] ${BtrLog.redact(error)}');
      }
    }

    _update = update;

    _channel.setMethodCallHandler((call) async {
      if (call.method != 'command' || !_active) return;
      final args = Map<String, dynamic>.from(call.arguments as Map);
      await onCommand(
        args['command'] as String,
        (args['value'] as num?)?.toDouble(),
      );
      await update();
    });
    final timer = Timer.periodic(const Duration(milliseconds: 200), (_) {
      unawaited(update());
    });
    try {
      final response = await _channel.invokeMapMethod<String, dynamic>(
        'present',
        {
          'title': title,
          'artwork': artwork,
          'darkMode': darkMode,
          'state': snapshot(),
        },
      );
      if (response?['diagnostics'] case final List diagnostics) {
        for (final message in diagnostics.whereType<String>()) {
          BtrLog.log('[External display] $message');
        }
      }
      return response?['nextMode'] as String?;
    } finally {
      timer.cancel();
      _channel.setMethodCallHandler(null);
      _active = false;
      _cid = null;
      _update = null;
    }
  }
}
