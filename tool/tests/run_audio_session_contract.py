"""Execute the merged audio-session class against controllable event streams."""

import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "lib/services/audio_session.dart"
IMPORTS = [
    "import 'package:PiliPlus/plugin/pl_player/controller.dart';",
    "import 'package:PiliPlus/services/service_locator.dart';",
    "import 'package:PiliPlus/utils/ios/airplay_helper.dart';",
    "import 'package:audio_session/audio_session.dart';",
]

STUBS = r"""
import 'dart:async';

enum AudioInterruptionType { duck, pause, unknown }
class AudioInterruptionEvent {
  final bool begin;
  final AudioInterruptionType type;
  AudioInterruptionEvent(this.begin, this.type);
}
class AudioSessionConfiguration {
  const AudioSessionConfiguration.music();
}
class AudioSession {
  static final current = AudioSession();
  static Future<AudioSession> get instance async => current;
  final interruptions = StreamController<AudioInterruptionEvent>.broadcast(sync: true);
  final noisy = StreamController<void>.broadcast(sync: true);
  Stream<AudioInterruptionEvent> get interruptionEventStream => interruptions.stream;
  Stream<void> get becomingNoisyEventStream => noisy.stream;
  Future<void> configure(AudioSessionConfiguration value) async {}
  Future<bool> setActive(bool active) async => active;
}
class Status {
  bool isPlaying = false;
}
class Volume {
  double value = 1;
}
class PlPlayerController {
  static PlPlayerController? instance;
  static Future<void>? pauseIfExists() => instance?.pause();
  final playerStatus = Status();
  final volume = Volume();
  int pauses = 0;
  int plays = 0;
  bool? lastInterrupt;
  bool? lastIndicator;
  Future<void> pause({bool isInterrupt = false}) async {
    pauses++;
    lastInterrupt = isInterrupt;
  }
  Future<void> play() async { plays++; }
  Future<void> setVolume(double value, {bool showIndicator = true}) async {
    volume.value = value;
    lastIndicator = showIndicator;
  }
}
class VideoPlayerServiceHandler {
  bool Function()? isPlaying;
  Future<void>? Function()? onPause;
  Future<void>? Function()? onPlay;
}
VideoPlayerServiceHandler? videoPlayerServiceHandler;
class IOSAirPlayHelper {
  static bool isActive = false;
  static int pauses = 0;
  static Future<void> pause() async { pauses++; }
}
"""

CHECKS = r"""
void check(Object? actual, Object? expected, String label) {
  if (actual != expected) {
    throw StateError('$label: expected $expected, got $actual');
  }
}

Future<void> tick() => Future<void>.delayed(Duration.zero);

Future<void> main() async {
  final session = AudioSession.current;
  final player = PlPlayerController();
  PlPlayerController.instance = player;
  final service = VideoPlayerServiceHandler();
  videoPlayerServiceHandler = service;
  var servicePauses = 0;
  var servicePlays = 0;
  service.onPause = () async { servicePauses++; };
  service.onPlay = () async { servicePlays++; };
  service.isPlaying = () => true;
  final handler = AudioSessionHandler();
  await tick();
  check(session.interruptions.hasListener, true, 'interruption subscription');
  check(session.noisy.hasListener, true, 'noisy subscription');
  check(await handler.setActive(true), true, 'activate session');

  session.interruptions.add(AudioInterruptionEvent(true, AudioInterruptionType.pause));
  session.interruptions.add(AudioInterruptionEvent(false, AudioInterruptionType.pause));
  await tick();
  check(servicePauses, 1, 'pause active audio service');
  check(servicePlays, 1, 'resume interrupted audio service');
  check(player.pauses, 0, 'service pause has priority');

  service.isPlaying = () => false;
  service.onPlay = null;
  player.playerStatus.isPlaying = true;
  session.interruptions.add(AudioInterruptionEvent(true, AudioInterruptionType.pause));
  session.interruptions.add(AudioInterruptionEvent(false, AudioInterruptionType.pause));
  await tick();
  check(servicePauses, 1, 'do not pause an idle audio service');
  check(player.pauses, 0, 'idle audio service does not fall back to local player');
  check(player.plays, 0, 'idle audio service does not resume local player');

  service.isPlaying = null;
  session.interruptions.add(AudioInterruptionEvent(true, AudioInterruptionType.pause));
  session.interruptions.add(AudioInterruptionEvent(false, AudioInterruptionType.pause));
  await tick();
  check(player.pauses, 1, 'pause playing local controller');
  check(player.lastInterrupt, true, 'pause is marked as interruption');
  check(player.plays, 1, 'resume interrupted local controller');

  player.playerStatus.isPlaying = false;
  session.interruptions.add(AudioInterruptionEvent(true, AudioInterruptionType.pause));
  session.interruptions.add(AudioInterruptionEvent(false, AudioInterruptionType.pause));
  await tick();
  check(player.pauses, 1, 'do not pause an idle controller');
  check(player.plays, 1, 'do not resume an idle controller');

  service.isPlaying = () => true;
  service.onPlay = () async { servicePlays++; };
  session.interruptions.add(AudioInterruptionEvent(true, AudioInterruptionType.pause));
  await tick();
  check(servicePauses, 2, 'record pending service interruption');
  IOSAirPlayHelper.isActive = true;
  session.interruptions.add(AudioInterruptionEvent(false, AudioInterruptionType.pause));
  await tick();
  check(servicePlays, 1, 'AirPlay owns interruption handling');
  IOSAirPlayHelper.isActive = false;
  session.interruptions.add(AudioInterruptionEvent(false, AudioInterruptionType.pause));
  await tick();
  check(servicePlays, 1, 'AirPlay clears stale resume intent');

  player.playerStatus.isPlaying = true;
  player.volume.value = 0.8;
  IOSAirPlayHelper.isActive = true;
  session.interruptions.add(AudioInterruptionEvent(true, AudioInterruptionType.duck));
  session.interruptions.add(AudioInterruptionEvent(false, AudioInterruptionType.duck));
  await tick();
  check(player.volume.value, 0.8, 'AirPlay does not duck local player');
  session.noisy.add(null);
  await tick();
  check(IOSAirPlayHelper.pauses, 1, 'noisy event pauses AirPlay');
  check(servicePauses, 2, 'AirPlay noisy event bypasses service');
  check(player.pauses, 1, 'AirPlay noisy event bypasses local player');

  IOSAirPlayHelper.isActive = false;
  session.interruptions.add(AudioInterruptionEvent(true, AudioInterruptionType.duck));
  await tick();
  check(player.volume.value, 0.4, 'duck local player');
  check(player.lastIndicator, false, 'duck hides volume indicator');
  session.interruptions.add(AudioInterruptionEvent(false, AudioInterruptionType.duck));
  await tick();
  check(player.volume.value, 0.8, 'restore ducked volume');
  session.noisy.add(null);
  await tick();
  check(servicePauses, 3, 'noisy event pauses audio service');
  service.onPause = null;
  session.noisy.add(null);
  await tick();
  check(player.pauses, 2, 'noisy event falls back to local player');

  service.onPause = () async { servicePauses++; };
  session.interruptions.add(AudioInterruptionEvent(true, AudioInterruptionType.unknown));
  session.interruptions.add(AudioInterruptionEvent(false, AudioInterruptionType.unknown));
  await tick();
  check(servicePauses, 4, 'unknown interruption pauses active audio');
  check(servicePlays, 1, 'unknown interruption does not auto-resume');
  print('Audio session interruption and AirPlay contract passed');
}
"""


def main():
    lines = SOURCE.read_text(encoding="utf-8").splitlines(keepends=True)
    imports = [line.strip() for line in lines if line.startswith("import ")]
    if imports != IMPORTS:
        raise ValueError("Audio session imports differ from the reviewed candidate")
    source = "".join(line for line in lines if not line.startswith("import "))
    with tempfile.TemporaryDirectory() as directory:
        program = Path(directory) / "audio_session_contract.dart"
        program.write_text(STUBS + "\n" + source + "\n" + CHECKS, encoding="utf-8")
        subprocess.run(["dart", str(program)], check=True)


if __name__ == "__main__":
    main()
