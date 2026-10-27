import 'dart:async';
import 'dart:ui' show PlatformDispatcher;

import 'package:PiliPlus/common/widgets/scale_app.dart';
import 'package:PiliPlus/services/btr_proxy/range_core.dart' show BtrLog;
import 'package:flutter/services.dart';

/// Keeps the original player/texture/overlays while UIKit moves its viewport.
abstract final class IOSExternalDisplayHelper {
  static const _channel = MethodChannel(
    'com.example.piliplus/external-display',
  );
  static bool get isActive => _active;
  static bool _active = false;

  static Future<void> present({
    required String title,
    required Map<String, Object> Function() state,
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
    binding.externalDisplayHeight = referenceHeight;
    Future<void> update() async {
      if (!_active) return;
      try {
        await _channel.invokeMethod<void>('update', state());
      } on PlatformException catch (error) {
        BtrLog.log('[External display] ${BtrLog.redact(error)}');
      }
    }

    _channel.setMethodCallHandler((call) async {
      if (call.method != 'command' || !_active) return;
      final args = Map<String, dynamic>.from(call.arguments as Map);
      await onCommand(
        args['command'] as String,
        (args['value'] as num?)?.toDouble(),
      );
      await update();
    });
    final timer = Timer.periodic(const Duration(milliseconds: 500), (_) {
      unawaited(update());
    });
    try {
      final response = await _channel.invokeMapMethod<String, dynamic>(
        'present',
        {'title': title},
      );
      if (response?['diagnostics'] case final List diagnostics) {
        for (final message in diagnostics.whereType<String>()) {
          BtrLog.log('[External display] $message');
        }
      }
    } finally {
      timer.cancel();
      _channel.setMethodCallHandler(null);
      _active = false;
      binding.externalDisplayHeight = null;
    }
  }
}
