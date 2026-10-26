import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

import 'btr/cdn_pool.dart';
import 'btr/cdn_racer.dart';

void main() {
  const akamaiHost = 'upos-hz-mirrorakam.akamaized.net';
  const cosovHost = 'upos-sz-mirrorcosov.bilivideo.com';
  const akamai =
      'https://$akamaiHost/upgcxcode/01/video.m4s?os=akam&hdnts=exp%3D123~hmac%3Dabc';
  const cosov =
      'https://$cosovHost/upgcxcode/01/video.m4s?os=cosovbv&upsig=cosov-signature';

  test('cached race winner cannot transplant an Akamai signature', () {
    final pool = CdnPool(originalUrls: [akamai]);
    pool.applyRacerHint(cosovHost, 1000000);
    expect(pool.hasRacerHint, isFalse);
    expect(pool.anchorUrl, akamai);
  });

  test('race hint cannot synthesize an unsigned Akamai URL', () {
    final pool = CdnPool(originalUrls: [cosov]);
    pool.applyRacerHint(akamaiHost, 1000000);
    expect(pool.hasRacerHint, isFalse);
  });

  test('race hint may select an authentic Akamai URL already in the pool', () {
    final pool = CdnPool(originalUrls: [cosov, akamai]);
    pool.applyRacerHint(akamaiHost, 1000000);
    expect(pool.hasRacerHint, isTrue);
  });

  test(
    'Akamai measures the original signed URL despite a foreign cache',
    () async {
      final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
      final requests = <String>[];
      final connections = <String>[];
      server.listen((request) async {
        requests.add(request.uri.toString());
        request.response
          ..statusCode = HttpStatus.partialContent
          ..contentLength = 65536;
        request.response.add(List<int>.filled(65536, 42));
        await request.response.close();
      });
      final client = HttpClient()
        ..connectionFactory = (uri, proxyHost, proxyPort) {
          connections.add(uri.host);
          return Socket.startConnect(InternetAddress.loopbackIPv4, server.port);
        };
      final racer = CdnRacer(httpClient: client, logger: (_) {})
        ..cached = CdnRaceResult(
          host: cosovHost,
          bytesPerSec: 1e12,
          measuredAtMs: DateTime.now().millisecondsSinceEpoch,
          candidateCount: 1,
          group: 'overseas',
        );
      const signedPath = '/video.m4s?hdnts=exp%3D123~hmac%3Dabc&x=one%2btwo';
      try {
        final result = await racer.raceThroughput(
          candidates: [cosovHost],
          sampleUrl: 'http://$akamaiHost$signedPath',
          group: 'overseas',
        );
        expect(result, isNotNull);
        expect(result!.host, akamaiHost);
        expect(result.bytesPerSec, greaterThan(0));
        expect(result.candidateCount, 1);
        expect(requests, [Uri.parse(signedPath).toString()]);
        expect(connections.toSet(), {akamaiHost});

        racer.cached = CdnRaceResult(
          host: akamaiHost,
          bytesPerSec: 1e12,
          measuredAtMs: DateTime.now().millisecondsSinceEpoch,
          candidateCount: 1,
          group: 'overseas',
        );
        final ordinary = await racer.raceThroughput(
          candidates: [cosovHost],
          sampleUrl: 'http://$cosovHost/video.m4s?upsig=other-signature',
          group: 'overseas',
        );
        expect(ordinary!.host, cosovHost);
        expect(requests.last, '/video.m4s?upsig=other-signature');
      } finally {
        client.close(force: true);
        await server.close(force: true);
      }
    },
  );

  test('banned Akamai never falls back to a rewritten signature', () async {
    final connections = <String>[];
    final client = HttpClient()
      ..connectionFactory = (uri, proxyHost, proxyPort) {
        connections.add(uri.host);
        throw StateError('No network request was expected');
      };
    try {
      final racer = CdnRacer(httpClient: client, logger: (_) {});
      expect(
        await racer.raceThroughput(
          candidates: [cosovHost],
          sampleUrl: akamai,
          group: 'overseas',
          bannedHosts: [akamaiHost],
        ),
        isNull,
      );
      expect(connections, isEmpty);
    } finally {
      client.close(force: true);
    }
  });
}
