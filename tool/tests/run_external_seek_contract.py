"""Exercise the real player and PiP seek code with controlled player events."""

import argparse
from pathlib import Path
import re
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[2]
CONTROLLER = "lib/plugin/pl_player/controller.dart"
PIP = "lib/utils/ios/pip_helper.dart"
ADAPTER = ROOT / "lib/utils/ios/external_seek.dart"

STUBS = r"""
final events = <String>[];
const kDebugMode = false;
void debugPrint(String message) {}

class IOSExternalDisplayHelper {
  static void clearDanmaku() => events.add('external-clear');
}
class FakeDanmaku {
  void clear() => events.add('local-clear');
}
class FakeStream {
  final controller = StreamController<Duration>.broadcast(sync: true);
  Stream<Duration> get buffer => controller.stream;
}
class FakeVideoPlayer {
  final stream = FakeStream();
  Completer<void>? barrier;
  bool playing = false;
  Future<void> seek(Duration value) async {
    events.add('seek:${value.inMilliseconds}');
    if (barrier case final wait?) await wait.future;
  }
}
class FakeDuration {
  int value = 60;
  final changes = StreamController<int>.broadcast(sync: true);
  StreamSubscription<int> listen(void Function(int) callback) =>
      changes.stream.listen(callback);
  void emit(int value) {
    this.value = value;
    changes.add(value);
  }
}
class PlPlayerController {
  static PlPlayerController? instance;
  int _playerCount = 1;
  int _heartDuration = -1;
  int positionInMilliseconds = 10000;
  final duration = FakeDuration();
  final _videoPlayerController = FakeVideoPlayer();
  final danmakuController = FakeDanmaku();
  StreamSubscription<int>? _subForSeek;
  void _cancelSubForSeek() {
    _subForSeek?.cancel();
    _subForSeek = null;
  }
  void _updateIOSPip([Duration? position]) =>
      events.add('pip:${position?.inMilliseconds}');
"""

CHECKS = r"""
class MethodCall {
  final String method;
  final Object arguments;
  MethodCall(this.method, this.arguments);
}

void check(bool condition, String message) {
  if (!condition) throw StateError('$message; events=$events');
}
Future<void> tick() => Future<void>.delayed(Duration.zero);
void checkSeek(int ms, String message) {
  check(events.join(',') ==
      'local-clear,external-clear,seek:$ms,pip:$ms', message);
}

Future<void> main() async {
  final controller = PlPlayerController();
  PlPlayerController.instance = controller;
  final video = controller._videoPlayerController;

  // No buffer event is ever emitted: external seeks must still complete.
  await seekExternalPlayback(controller.seekTo, const Duration(seconds: 12))
      .timeout(const Duration(seconds: 2));
  await tick();
  checkSeek(12000, 'seek clears both danmaku displays and updates PiP');
  check(controller._heartDuration == 12, 'seek updates heartbeat position');
  check(!video.playing, 'seek does not resume paused playback');

  events.clear();
  controller._playerCount = 0;
  await seekExternalPlayback(controller.seekTo, const Duration(seconds: 30));
  await tick();
  check(events.isEmpty, 'disposed player ignores a seek');
  controller._playerCount = 1;

  events.clear();
  await seekExternalPlayback(controller.seekTo, const Duration(seconds: -1));
  await tick();
  checkSeek(0, 'external seeks retain the nonnegative position guard');

  events.clear();
  controller.duration.value = 0;
  await seekExternalPlayback(controller.seekTo, const Duration(seconds: 21));
  await tick();
  if (legacy) {
    check(events.isEmpty, 'legacy API defers until duration is available');
    controller.duration.emit(60);
    await tick();
  }
  checkSeek(21000, 'seek works with the API duration semantics');

  events.clear();
  controller.duration.value = 60;
  video.barrier = Completer<void>();
  var completed = false;
  final operation = seekExternalPlayback(controller.seekTo, const Duration(seconds: 4))
      .then((_) => completed = true);
  await tick();
  check(events.join(',') == 'local-clear,external-clear,seek:4000',
      'PiP update follows the underlying player seek');
  check(completed == legacy, 'preserve each API\'s completion behavior');
  video.barrier!.complete();
  await operation.timeout(const Duration(seconds: 2));
  await tick();
  checkSeek(4000, 'PiP receives the completed seek position');
  video.barrier = null;

  events.clear();
  handlePiPSkip(6000);
  await tick();
  checkSeek(16000, 'PiP skip uses the relative target');
  check(controller._heartDuration == 16, 'PiP skip updates heartbeat position');

  Future<void> fails(Duration _) async => throw StateError('expected failure');
  try {
    await seekExternalPlayback(fails, Duration.zero);
    throw StateError('error was swallowed');
  } on StateError catch (error) {
    check(error.message == 'expected failure', 'forward seek errors');
  }
  print('External seek and PiP contract passed ($mode)');
}
"""


def source(path, ref):
    if ref is None:
        return (ROOT / path).read_text(encoding="utf-8")
    return subprocess.check_output(["git", "show", f"{ref}:{path}"], cwd=ROOT, text=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--legacy-ref")
    args = parser.parse_args()
    if args.legacy_ref and not re.fullmatch(r"[0-9a-f]{40}", args.legacy_ref):
        raise ValueError("Legacy reference must be a full commit SHA")
    legacy = args.legacy_ref is not None
    player = source(CONTROLLER, args.legacy_ref)
    start = player.index("  Future<void> seek(Duration position, {bool isSeek = false}) async {"
                         if legacy else "  Future<void> seekTo(Duration position) async {")
    methods = player[start:player.index("\n  /// 设置倍速", start)]
    pip = source(PIP, args.legacy_ref)
    start = pip.index("      case 'skip':\n")
    pip_case = pip[start:pip.index("\n    }\n  }", start)]
    mode = "legacy" if legacy else "upstream"
    wrapper = ("\n}\n\nvoid handlePiPSkip(int milliseconds) {\n"
               "  final call = MethodCall('skip', milliseconds);\n"
               "  switch (call.method) {\n" + pip_case + "\n  }\n}\n")
    header = ("import 'dart:async';\n"
              f"import '{ADAPTER.as_uri()}';\n"
              f"const legacy = {'true' if legacy else 'false'};\n"
              f"const mode = '{mode}';\n")
    with tempfile.TemporaryDirectory() as directory:
        program = Path(directory) / "external_seek_contract.dart"
        program.write_text(header + STUBS + methods + wrapper + CHECKS, encoding="utf-8")
        subprocess.run(["dart", str(program)], check=True, cwd=ROOT)


if __name__ == "__main__":
    main()
