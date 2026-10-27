import 'dart:typed_data';

import 'package:PiliPlus/services/airplay_hls.dart';
import 'package:PiliPlus/services/btr_proxy/sidx_parser.dart';
import 'package:PiliPlus/utils/ios/airplay_danmaku.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('HLS byte ranges preserve the complete MP4 init and SIDX offsets', () {
    // ftyp(16) + moov(24) + a version-0 sidx with two references(56).
    final bytes = Uint8List(96);
    final data = ByteData.sublistView(bytes);
    void box(int at, int size, String type) {
      data.setUint32(at, size);
      bytes.setRange(at + 4, at + 8, type.codeUnits);
    }

    box(0, 16, 'ftyp');
    box(16, 24, 'moov');
    box(40, 56, 'sidx');
    data.setUint32(56, 1000); // timescale
    data.setUint16(70, 2);
    data.setUint32(72, 1200);
    data.setUint32(76, 5000);
    data.setUint32(84, 900);
    data.setUint32(88, 3125);
    final index = SidxParser.parseSidx(bytes)!;
    expect(airPlayInitLength(bytes), 40);
    final playlist = airPlayMediaPlaylist(
      index,
      mediaPath: 'video.m4s',
      initLength: 40,
    );
    expect(playlist, contains('BYTERANGE="40@0"'));
    expect(playlist, contains('#EXT-X-BYTERANGE:1200@96'));
    expect(playlist, contains('#EXT-X-BYTERANGE:900@1296'));
    expect(playlist, contains('#EXTINF:3.125000,'));
    expect(playlist, contains('#EXT-X-TARGETDURATION:5'));
    expect(airPlayInitLength(bytes.sublist(0, 44)), isNull);
  });

  test(
    'master joins separate audio and video and advertises native captions',
    () {
      final master = airPlayMasterPlaylist(
        video: const AirPlayTrack(
          url: 'https://example.com/v',
          codecs: 'avc1.640028',
          bandwidth: 4000000,
          width: 1920,
          height: 1080,
        ),
        audio: const AirPlayTrack(
          url: 'https://example.com/a',
          codecs: 'mp4a.40.2',
          bandwidth: 192000,
        ),
        subtitles: true,
      );
      expect(master, contains('RESOLUTION=1920x1080'));
      expect(master, contains('CODECS="avc1.640028,mp4a.40.2"'));
      expect(master, contains('URI="audio.m3u8"'));
      expect(master, contains('SUBTITLES="danmaku"'));
      expect(master, contains('URI="danmaku.m3u8"'));
    },
  );

  test(
    'local AirPlay accepts LAN addresses and excludes public/VPN/loopback',
    () {
      for (final address in [
        '192.168.1.2',
        '10.0.0.2',
        '172.16.1.2',
        '172.31.1.2',
      ]) {
        expect(isPrivateAirPlayAddress(address), isTrue);
      }
      for (final address in [
        '127.0.0.1',
        '8.8.8.8',
        '100.64.0.1',
        '172.32.1.2',
        'example.com',
        '::1',
      ]) {
        expect(isPrivateAirPlayAddress(address), isFalse);
      }
    },
  );

  test('caption segments preserve absolute timing and escape source text', () {
    final text = airPlayDanmakuVtt([
      const AirPlayComment(359000, 1, '<hello> & world'),
      const AirPlayComment(361000, 5, 'top comment'),
      const AirPlayComment(361000, 7, 'advanced comment is unsupported'),
    ], 1).replaceAll('\u2060', '');
    expect(
      text,
      startsWith('WEBVTT\nX-TIMESTAMP-MAP=LOCAL:00:00:00.000,MPEGTS:0'),
    );
    expect(text, contains('00:06:00.000 --> 00:06:00.017'));
    expect(text, isNot(contains('00:05:59.')));
    expect(text, contains('&lt;hello&gt; &amp; world'));
    expect(text, contains('00:06:01.000 --> 00:06:05.000'));
    expect(text, isNot(contains('advanced comment is unsupported')));
    final positions = RegExp(r'position:(\d+\.\d+)')
        .allMatches(text)
        .map((m) => double.parse(m.group(1)!))
        .toList();
    expect(positions.length, greaterThan(1));
    expect(positions.first, greaterThan(positions.last));
  });

  test(
    'scrolling captions enter and leave near the edges without full-row jumps',
    () {
      final text = airPlayDanmakuVtt(
        [const AirPlayComment(0, 1, '甲乙丙丁戊己庚辛', 0xff0000)],
        0,
        measureWidth: (s) => s.length * 3.0,
      );
      final blocks = text
          .split('\n\n')
          .where((b) => b.contains(' --> '))
          .toList();
      expect(blocks.length, greaterThan(300));
      final positions = blocks
          .map(
            (b) => double.parse(
              RegExp(r'position:([0-9.]+)').firstMatch(b)!.group(1)!,
            ),
          )
          .toList();
      expect(positions.first, greaterThan(95));
      expect(positions.last, lessThan(5));
      expect(blocks.first, contains('>甲</c>'));
      expect(blocks.last, contains('>辛</c>'));
      expect(text, contains('rgba(255, 0, 0, 1.000)'));
      expect(
        text,
        isNot(contains('甲乙丙丁戊己庚辛')),
      ); // joined against native wrapping
    },
  );

  test('caption styling and type switches follow the player preferences', () {
    final text = airPlayDanmakuVtt(
      [
        const AirPlayComment(1000, 1, 'scroll'),
        const AirPlayComment(1000, 4, 'bottom'),
        const AirPlayComment(1000, 5, 'top', 0xff0000),
      ],
      0,
      style: const AirPlayDanmakuStyle(
        hideScroll: true,
        hideBottom: true,
        whiteOnly: true,
        staticDuration: 2,
        opacity: 0.4,
        fontSize: 6,
        fontWeight: 6,
        strokeWidth: 0,
      ),
    );
    expect(text, isNot(contains('>scroll<')));
    expect(text, isNot(contains('>bottom<')));
    expect(text, contains('>top</c>'));
    expect(text, contains('00:00:01.000 --> 00:00:03.000'));
    expect(text, contains('font-size: 6.000vh'));
    expect(text, contains('font-weight: 700'));
    expect(text, contains('text-shadow: none'));
    expect(text, contains('rgba(255, 255, 255, 0.400)'));
  });

  test(
    'fixed velocity allows longer comments more time to leave the screen',
    () {
      String render(bool fixed) => airPlayDanmakuVtt(
        [const AirPlayComment(0, 1, 'abcdefghij')],
        0,
        style: AirPlayDanmakuStyle(duration: 2, fixedVelocity: fixed),
        measureWidth: (s) => s.length * 4.0,
      );
      expect(render(false), isNot(contains('00:00:02.')));
      expect(render(true), contains('00:00:02.'));
    },
  );
}
