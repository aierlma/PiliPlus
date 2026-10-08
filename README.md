# PiliPlus BTR — aierlma

个人维护的 [PiliPlus](https://github.com/bggRGjQaUbCoE/PiliPlus) + BTR 分支，保留 [nishuodedui1145-del/PiliPlus](https://github.com/nishuodedui1145-del/PiliPlus) 的本地 HTTP 代理、多 Range 并发、CDN 选择/竞速、BTR 设置与日志，同时由 GitHub Actions 定期检查并合并 BTR 作者的 `btr` 与 PiliPlus 官方的 `main`。

下载自己的[正式未签名 IPA](https://github.com/aierlma/PiliPlus/releases/latest)，通过 [AltGallery](https://github.com/aierlma/AltGallery) 更新。BTR 开关默认关闭，在「我的 → 设置 → 音视频设置」开启；播放页的「更多 → BTR」提供快设与日志。应用内更新检查也指向本仓库。

## 同步与发布

默认分支为 `btr`。`BTR and PiliPlus sync and iOS release` 在 GitHub 每天检查一次（cron `17 11 * * *`，纽约夏令时 07:17、冬令时 06:17），也可从 Actions 手动运行。GitHub 计划任务可能延迟；每次检查当前最新提交，不积压逐个版本构建。

1. Ubuntu 分别检查 BTR 作者 `nishuodedui1145-del/PiliPlus/btr`、PiliPlus 官方 `bggRGjQaUbCoE/PiliPlus/main` 和当前个人分支的 SHA，并与最近正式 IPA 对应的源码比较。两种更新都已纳入且应用输入没有变化时直接结束；个人分支的每一个维护提交无需另有 release。任何一个来源有新提交，都独立触发候选合并，无需等待另一方更新。
2. macOS 先普通合并 BTR 作者的新提交，再普通合并官方新提交。BTR 作者落后于官方时，只合并其新增历史，不把新版官方代码覆盖回旧版。任一来源冲突会回滚整个临时候选；个人 README 和 `.github/workflows/` 保留个人配置，两个来源的 README 分别保存为 `README-BTR-upstream.md` 和 `README-upstream.md`。
3. 检查 BTR 接线、独立 bundle ID 和个人更新地址；生成最新测试镜像，运行自包含 BTR 测试、完整 `flutter analyze`，再构建未签名 iOS IPA。
4. 解析实际 IPA，验证版本、构建号和最低系统要求；仅通过全部门槛后才快进 `btr`。发布脚本再比较最近正式版的应用输入：应用变化才发布；纯维护变化只同步历史，不上传或公开另一个 IPA。若运行期间分支已变化，停止推进，避免覆盖用户提交。

发布按应用输入去重，忽略根目录 `README*.md`、`docs/`、`test/`、`tool/`、`.github/`、`.vscode/` 以及 `.gitattributes`、`.gitignore`、`analysis_options.yaml` 的维护变化。Dart 代码、资源、平台文件、`lib/scripts/` 中的构建补丁、依赖锁文件、SDK/版本配置及未知路径仍触发完整发布门槛。构建号沿用 Git 历史提交计数，因此数字可能跳跃，并不表示中间发布了多个 App 版本。`retry_blocked` 只重试失败输入，不强制为相同应用内容再发一版。编译命令或打包行为若在 `.github/` 或 `tool/` 中修改，需要维护者同时调整应用构建输入。沿用现有 workflow，新的纯维护上游历史仍完整验证，但只同步、不发版；已同步的个人维护提交在计划阶段直接跳过构建。

例如 5485 到 5488 增加的是三个维护提交，应用代码、资源和依赖相同；按此规则不会再次发布功能相同的 IPA。已有正式附件保留，不回撤用户已安装的包。

个人正式 iOS 包使用 `官方主版本.官方次版本.个人构建号` 作为 `CFBundleShortVersionString`，例如基于官方 `2.1.6` 的 build 5502 显示为 `2.1.5502`。这符合 iOS 的三段数字版本格式，也让 SideStore 的正式版检查在官方未改 `2.1.6`、但实际源码有更新时识别新包。`CFBundleVersion` 仍为 Git 历史计数；官方原始版本保存在 IPA 的 `PiliPlusUpstreamVersion`、`build-info.json` 的 `upstream_version`、应用内版本说明与 Release 说明中，Tag/附件名继续沿用官方版本与个人构建号。版本写入仅在构建临时工作区发生，不修改上游 `pubspec.yaml` 中的版本策略。AltGallery 读取实际 IPA 的版本，不虚构订阅版本；相同包不会一直提示更新。维护变化仍由上面的应用输入规则排除。

发布前下载上一正式版的 `build-info.json`，要求新 IPA 的三段版本和构建号都严格递增；重复、回退或上游改版本策略导致新版本变低时，在任何 Release 写入和分支推进前阻断。普通快进历史、并发互斥及推进前再次核对远端 SHA 共同防止并行候选发布同一个版本。

在 SideStore 添加个人 BTR 源后，从该源安装或关联 PiliPlus BTR；刷新源后，已安装的旧版会在 My Apps 出现 Update，可直接下载、签名并覆盖更新，无需手动下载 IPA。Refresh 只续签，不能代替 Update。通知横幅还需要 SideStore 通知权限及 iOS 允许的后台检查；GitHub 和订阅不会向设备实时推送。

个人 iOS 版的应用内更新检查按构建号比较正式个人 IPA，并核对 AltGallery 独立 BTR 源中的版本、构建号和下载链接。只有订阅源已经收录的较新包才弹出更新提示；同一个包不会因为 GitHub Release 创建时间晚于编译时间重复提醒。GitHub 已发布而源尚未刷新时，自动检查保持安静，手动检查说明正在等待订阅同步。AltGallery 沿用独立的每 6 小时生成任务，GitHub 可能延迟或丢弃计划任务，因此发布 IPA 与订阅可用并非即时完成；维护者可手动运行 AltGallery 的生成 workflow，无需用户卸载或手动下载 IPA。该检查不改应用标识、签名或用户数据，也不更改其他平台的更新逻辑。

并发上限为 1；一次检查最多进行一次构建，不自动重试相同的个人、官方、BTR 三个 SHA 输入。合并冲突、SDK 补丁失效、测试或构建失败时，GitHub Actions 标红并在本仓库记录一个带源 SHA 的 issue，保持上一正式 IPA。相同三个 SHA 的后续计划检查跳过，避免每天浪费构建；修复后提交新代码，或手动勾选 `retry_blocked` 重试。发生冲突仍需维护者处理，持续检查并不保证两个来源的任何变更都能无冲突合并。

发布流程会先创建 draft release，上传验证过的包，再推进分支并将 release 改为正式；部分失败留下可恢复的 draft，不替换旧正式版。Tag 以版本 + Git 历史构建号 + 源码 SHA 识别，附件名包含版本与构建号，`build-info.json` 记录个人源码、PiliPlus 官方和 BTR 作者三个 SHA、IPA 元数据与 SHA-256。生成过程不执行 IPA。

所有定时工作都在 GitHub，不依赖 Codex 会话、Mac 常驻进程或个人访问令牌。日常同步使用仓库内置的 `GITHUB_TOKEN`。保留自己的 CI 配置意味着两个来源的 workflow 改动需单独检查；源码更新仍走普通 Git 合并。此流程不调用 LLM；冲突被明确阻断，可在有 Copilot 权限时交给 Copilot 提议修复，再重新通过相同验证。

停止：在 Actions 禁用 `BTR and PiliPlus sync and iOS release`。日志位于该 workflow 的运行页，构建产物保留 14 天；失败 issue 是重复运行的阻断记录。GitHub 对长期没有仓库活动的公共计划任务可能暂停，恢复时重新启用该 workflow。

## 构建与测试

Flutter 版本由官方 `pubspec.yaml` 固定，SDK 与 UI 包需要 `lib/scripts/patch.ps1 iOS` 的补丁。标准 macOS GitHub runner 构建，不需要 Apple 签名凭据；产物需自行侧载。

本地构建个人正式 iOS 包时运行 `pwsh lib/scripts/build.ps1 btr-ios`，应用同样的 iOS 版本写入；其他平台沿用原版本生成方式。

```sh
python3 -m unittest discover -s tool/tests -v
dart tool/tests/btr_update_test.dart
python3 tool/check_btr.py
python3 tool/make_btr_mirror.py
flutter test --no-pub test/standalone/btr_bitrate_unit_test.dart test/standalone/btr_cdn_racer_test.dart test/standalone/btr_round22_test.dart test/standalone/btr_round23_test.dart test/standalone/btr_round26_test.dart
```

这些测试自包含；需要真实 B 站临时媒体链接的 `btr_proxy_e2e_test.dart` 不在无人值守测试中，不能将自动验证等同于真机播放验收。

## 来源与许可

官方说明：[README-upstream.md](README-upstream.md)。BTR 作者说明快照：[README-BTR-upstream.md](README-BTR-upstream.md)。BTR 功能与设计：[README-BTR.md](README-BTR.md)、[docs/btr/DESIGN.md](docs/btr/DESIGN.md)。保留原始 [NOTICE](NOTICE) 署名与 [GPL-3.0 LICENSE](LICENSE)。此 fork 不代表 PiliPlus 官方或 Bilibili。
