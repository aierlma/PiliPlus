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

  test('Akamai source is not used for host-replacement probes', () async {
    final racer = CdnRacer(logger: (_) {});
    expect(
      await racer.raceThroughput(
        candidates: [cosovHost],
        sampleUrl: akamai,
        group: 'auto',
      ),
      isNull,
    );
  });
}
