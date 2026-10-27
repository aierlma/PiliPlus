import 'package:material_ui/material_ui.dart';

/// Keeps the source player mounted while the phone shows compact TV controls.
class ExternalPlaybackHeader extends StatelessWidget {
  const ExternalPlaybackHeader({
    super.key,
    required this.connected,
    required this.playing,
    required this.videoSize,
    required this.player,
    required this.overlay,
    required this.onBack,
    required this.onTogglePlayback,
    required this.onShowControls,
  });

  static const compactHeight = 64.0;

  final bool connected;
  final bool playing;
  final Size videoSize;
  final Widget player;
  final Widget overlay;
  final VoidCallback onBack;
  final VoidCallback onTogglePlayback;
  final VoidCallback onShowControls;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = theme.colorScheme;
    return Stack(
      clipBehavior: Clip.none,
      children: [
        // Offstage preserves the decoder and danmaku subscriptions. Keep its
        // layout size too: shrinking the source player can resize its texture.
        Offstage(
          offstage: connected,
          child: OverflowBox(
            alignment: Alignment.topCenter,
            minHeight: connected ? videoSize.height : null,
            maxHeight: connected ? videoSize.height : null,
            child: SizedBox(
              width: videoSize.width,
              height: videoSize.height,
              child: player,
            ),
          ),
        ),
        if (connected)
          Material(
            color: colors.surface,
            child: SizedBox(
              height: compactHeight,
              child: Row(
                children: [
                  IconButton(
                    tooltip: '返回',
                    onPressed: onBack,
                    icon: const Icon(
                      Icons.arrow_back_ios_new_rounded,
                      size: 20,
                    ),
                  ),
                  Expanded(
                    child: InkWell(
                      onTap: onShowControls,
                      borderRadius: BorderRadius.circular(12),
                      child: Padding(
                        padding: const EdgeInsets.symmetric(vertical: 8),
                        child: Row(
                          children: [
                            Icon(Icons.airplay_rounded, color: colors.primary),
                            const SizedBox(width: 10),
                            Expanded(
                              child: Column(
                                mainAxisSize: MainAxisSize.min,
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  Text(
                                    'AirPlay',
                                    style: theme.textTheme.titleSmall,
                                  ),
                                  Text(
                                    playing ? '正在电视上播放' : '已暂停',
                                    style: theme.textTheme.bodySmall?.copyWith(
                                      color: colors.onSurfaceVariant,
                                    ),
                                  ),
                                ],
                              ),
                            ),
                          ],
                        ),
                      ),
                    ),
                  ),
                  IconButton.filledTonal(
                    tooltip: playing ? '暂停' : '播放',
                    onPressed: onTogglePlayback,
                    icon: Icon(
                      playing ? Icons.pause_rounded : Icons.play_arrow_rounded,
                    ),
                  ),
                  IconButton(
                    tooltip: '投屏选项',
                    onPressed: onShowControls,
                    icon: const Icon(Icons.more_horiz_rounded),
                  ),
                  const SizedBox(width: 4),
                ],
              ),
            ),
          )
        else
          overlay,
      ],
    );
  }
}
