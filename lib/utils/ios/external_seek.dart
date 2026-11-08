typedef _LegacySeek = Future<void> Function(Duration position, {bool isSeek});

/// Seek without waiting for a buffer event when external playback ends.
Future<void> seekExternalPlayback(
  Future<void> Function(Duration position) seek,
  Duration position,
) {
  final target = position.isNegative ? Duration.zero : position;
  if (seek is _LegacySeek) return seek(target, isSeek: false);
  return seek(target);
}
