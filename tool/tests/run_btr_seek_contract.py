"""Exercise the merged BTR seek and buffer methods with controlled collaborators."""

from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]


def between(text, start, end):
    first = text.index(start)
    return text[first:text.index(end, first)]


def main():
    proxy = (ROOT / "lib/services/btr_proxy/proxy_server.dart").read_text()
    player = (ROOT / "lib/plugin/pl_player/controller.dart").read_text()
    video = (ROOT / "lib/pages/video/controller.dart").read_text()
    pref = (ROOT / "lib/utils/storage_pref.dart").read_text()
    round_handle = between(proxy, "class _SeekRoundHandle {", "\n/// 本地 BTR HTTP 代理服务器")
    notify = between(proxy, "  void notifySeek([Duration? target]) {", "\n  /// R5-B：seek 取消在途轮次后")
    on_seek = between(video, "  void _onPlayerSeek(Duration position) {", "\n  }\n") + "\n  }\n"
    buffer_methods = between(pref, "  static Map<String, String> initBuffer(", "\n  static String get audioOutput")
    buffer_getter = between(player, "  Map<String, String> get buffer =>", "\n  // 配置播放器")
    program = STUBS + round_handle + "\nclass BtrProxyServer {\n" + PROXY_FIELDS + notify + "\n}\n"
    program += "class VideoDetailController {\n" + VIDEO_FIELDS + on_seek + "\n}\n"
    program += "class Pref {\n" + PREF_FIELDS + buffer_methods + "\n}\n"
    program += "class PlayerBuffer {\n  final _playbackSpeed = Speed();\n" + buffer_getter + "\n}\n" + CHECKS
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "btr_seek_contract.dart"
        path.write_text(program, encoding="utf-8")
        subprocess.run(["dart", str(path)], check=True, cwd=ROOT)


STUBS = r"""
class CancellationToken {
  bool isCancelled = false;
  String? reason;
  void cancel(String value) { isCancelled = true; reason = value; }
}
class Downloader {
  final calls = <String>[];
  void cancelAll(String reason) => calls.add(reason);
}
class BtrLog {
  static String last = '';
  static void rateLimitedLog(String key, String message) { last = message; }
}
class Value<T> { Value(this.value); T value; }
class FakePlayer { int cid = 1; }
class Speed { double value = 2.0; }
"""

PROXY_FIELDS = r"""
  static final instance = BtrProxyServer();
  Object? _server;
  final _activeTokens = <CancellationToken>{};
  final _seekPendingTokens = <CancellationToken>{};
  final _activeSeekRounds = <_SeekRoundHandle>{};
  final _downloaderCache = <String, Downloader>{};
"""

VIDEO_FIELDS = r"""
  bool isFileSource = false;
  final plPlayerController = FakePlayer();
  final cid = Value<int>(1);
"""

PREF_FIELDS = r"""
  static bool btrEnabled = false;
  static double bufferSec = 12.0;
  static double bufferSize = 3.0;
  static double liveBufferSize = 0.0;
  static double liveBufferSec = 0.0;
"""

CHECKS = r"""
void check(bool value, String description) {
  if (!value) throw StateError(description);
}
void main() {
  final proxy = BtrProxyServer.instance;
  final active = CancellationToken();
  final completed = CancellationToken()..cancel('complete');
  final round = _SeekRoundHandle();
  final downloader = Downloader();
  proxy._activeTokens.addAll([active, completed]);
  proxy._activeSeekRounds.add(round);
  proxy._downloaderCache['cdn'] = downloader;
  final video = VideoDetailController();

  video._onPlayerSeek(const Duration(milliseconds: 12345));
  check(!round.token.isCancelled && proxy._seekPendingTokens.isEmpty,
      'a stopped proxy does not cancel requests');
  proxy._server = Object();
  video.isFileSource = true;
  video._onPlayerSeek(Duration.zero);
  video.isFileSource = false;
  video.plPlayerController.cid = 2;
  video._onPlayerSeek(Duration.zero);
  check(!round.token.isCancelled && downloader.calls.isEmpty,
      'local files and other video controllers do not cancel requests');
  video.plPlayerController.cid = 1;
  video._onPlayerSeek(const Duration(milliseconds: 12345));
  check(proxy._seekPendingTokens.length == 1 && proxy._seekPendingTokens.contains(active),
      'only active client requests are marked for seek');
  check(!active.isCancelled, 'the active client connection remains available');
  check(round.abortedBySeek && round.token.isCancelled && round.token.reason == 'Client seek',
      'the stale range round is cancelled');
  check(downloader.calls.length == 1 && downloader.calls.single == 'Client seek',
      'in-flight range downloaders are cancelled');
  check(BtrLog.last.contains('12345ms'), 'the target reaches the proxy');

  final player = PlayerBuffer();
  check(player.buffer['demuxer-max-bytes'] == '3145728' && player.buffer['cache-secs'] == '24.000',
      'normal playback retains user buffer settings');
  final live = player.liveBuffer;
  check(live['demuxer-max-bytes'] == '6291456', 'live playback retains its own buffer');
  Pref.btrEnabled = true;
  check(player.buffer['demuxer-max-bytes'] == '33554432' && player.buffer['cache-secs'] == '120.000',
      'BTR buffering is selected and scales with speed');
  check(player.liveBuffer['demuxer-max-bytes'] == '6291456', 'BTR does not replace live buffering');
  player._playbackSpeed.value = 1;
  Pref.btrEnabled = false;
  check(player.buffer['demuxer-max-bytes'] == '3145728' && player.buffer['cache-secs'] == '12.000',
      'disabling BTR restores the user buffer immediately');
  print('BTR seek cancellation and buffer contract passed');
}
"""


if __name__ == "__main__":
    main()
