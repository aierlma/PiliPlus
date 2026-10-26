import 'dart:math';

class AirPlayComment {
  const AirPlayComment(this.timeMs, this.mode, this.text);

  final int timeMs;
  final int mode;
  final String text;
}

/// Experimental native WebVTT rendition. The TV, rather than a phone overlay,
/// must render these positions. Receiver behavior is verified on the device.
String airPlayDanmakuVtt(List<AirPlayComment> comments, int segment) {
  final start = segment * 360000;
  final end = start + 360000;
  final cues = <({int start, int end, String settings, String text})>[];
  final lanes = List.filled(4, 0);
  final sorted = comments.toList()
    ..sort((a, b) => a.timeMs.compareTo(b.timeMs));
  final unique = <(int, String)>{};
  for (final comment in sorted) {
    if (comment.timeMs >= end ||
        comment.timeMs + 8000 <= start ||
        comment.mode < 1 ||
        comment.mode > 5 ||
        comment.text.trim().isEmpty) {
      continue;
    }
    if (!unique.add((comment.timeMs ~/ 1000, comment.text))) continue;
    final text = comment.text
        .replaceAll(RegExp(r'[\r\n]+'), ' ')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;');
    if (comment.mode == 4 || comment.mode == 5) {
      cues.add((
        start: max(start, comment.timeMs),
        end: min(end, comment.timeMs + 5000),
        settings:
            'line:${comment.mode == 4 ? 90 : 5}% position:50% align:center size:90%',
        text: text,
      ));
      continue;
    }
    final lane = lanes.indexWhere((freeAt) => freeAt <= comment.timeMs);
    if (lane == -1) continue;
    lanes[lane] = comment.timeMs + 8000;
    // Fixed-width cues avoid repeated line wrapping during movement. Standard
    // WebVTT has no motion command, so each cue supplies the next position.
    final width = min(80, max(15, comment.text.runes.length * 3));
    for (var elapsed = 0; elapsed < 8000; elapsed += 50) {
      final time = comment.timeMs + elapsed;
      if (time < start || time >= end) continue;
      final position = (100 - width) * (1 - elapsed / 8000);
      cues.add((
        start: time,
        end: min(end, time + 50),
        settings:
            'line:${5 + lane * 8}% position:${position.toStringAsFixed(2)}%,line-left align:left size:$width%',
        text: text,
      ));
    }
  }
  cues.sort((a, b) => a.start.compareTo(b.start));
  final result = StringBuffer(
    'WEBVTT\nX-TIMESTAMP-MAP=LOCAL:00:00:00.000,MPEGTS:0\n\n',
  );
  for (final (index, cue) in cues.indexed) {
    result
      ..writeln('${segment}_$index')
      ..writeln('${_time(cue.start)} --> ${_time(cue.end)} ${cue.settings}')
      ..writeln(cue.text)
      ..writeln();
  }
  return result.toString();
}

String _time(int milliseconds) {
  final hours = milliseconds ~/ 3600000;
  final minutes = milliseconds ~/ 60000 % 60;
  final seconds = milliseconds ~/ 1000 % 60;
  return '${hours.toString().padLeft(2, '0')}:'
      '${minutes.toString().padLeft(2, '0')}:'
      '${seconds.toString().padLeft(2, '0')}.'
      '${(milliseconds % 1000).toString().padLeft(3, '0')}';
}
