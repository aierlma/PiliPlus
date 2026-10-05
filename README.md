# PiliPlus BTR — aierlma

个人维护的 [PiliPlus](https://github.com/bggRGjQaUbCoE/PiliPlus) + BTR 分支，保留 [nishuodedui1145-del/PiliPlus](https://github.com/nishuodedui1145-del/PiliPlus) 的本地 HTTP 代理、多 Range 并发、CDN 选择/竞速、BTR 设置与日志，同时由 GitHub Actions 定期检查并合并官方 `main`。

下载自己的[正式未签名 IPA](https://github.com/aierlma/PiliPlus/releases/latest)，通过 [AltGallery](https://github.com/aierlma/AltGallery) 更新。BTR 开关默认关闭，在「我的 → 设置 → 音视频设置」开启；播放页的「更多 → BTR」提供快设与日志。应用内更新检查也指向本仓库。

## 同步与发布

默认分支为 `btr`。`BTR upstream sync and iOS release` 在 GitHub 每天检查一次（cron `17 11 * * *`，纽约夏令时 07:17、冬令时 06:17），也可从 Actions 手动运行。GitHub 计划任务可能延迟；每次检查当前最新提交，不积压逐个版本构建。

1. Ubuntu 检查官方最新 SHA、当前个人分支和对应 release；没有变化就结束。
2. macOS 在临时候选提交中普通合并官方源码，保留 BTR。个人 README 和 `.github/workflows/` 保留个人配置，官方 README 刷新到 `README-upstream.md`。
3. 检查 BTR 接线、独立 bundle ID 和个人更新地址；生成最新测试镜像，运行自包含 BTR 测试、完整 `flutter analyze`，再构建未签名 iOS IPA。
4. 解析实际 IPA，验证版本、构建号和最低系统要求；仅通过全部门槛后才快进 `btr` 并发布。若运行期间分支已变化，停止发布，避免覆盖用户提交。

并发上限为 1；一次检查最多进行一次构建，不自动重试相同失败输入。合并冲突、SDK 补丁失效、测试或构建失败时，GitHub Actions 标红并在本仓库记录一个带源 SHA 的 issue，保持上一正式 IPA。相同输入的后续计划检查跳过，避免每天浪费构建；修复后提交新代码，或手动勾选 `retry_blocked` 重试。发生冲突仍需维护者处理，持续检查并不保证任何官方变更都能无冲突合并。

发布流程会先创建 draft release，上传验证过的包，再推进分支并将 release 改为正式；部分失败留下可恢复的 draft，不替换旧正式版。Tag 和文件名以版本 + Git 历史构建号识别，`build-info.json` 记录源码和官方 SHA、IPA 元数据与 SHA-256。生成过程不执行 IPA。

所有定时工作都在 GitHub，不依赖 Codex 会话、Mac 常驻进程或个人访问令牌。日常同步使用仓库内置的 `GITHUB_TOKEN`。保留自己的 CI 配置意味着官方 workflow 改动需单独检查；源码更新仍走普通 Git 合并。此流程不调用 LLM；冲突被明确阻断，可在有 Copilot 权限时交给 Copilot 提议修复，再重新通过相同验证。

停止：在 Actions 禁用 `BTR upstream sync and iOS release`。日志位于该 workflow 的运行页，构建产物保留 14 天；失败 issue 是重复运行的阻断记录。GitHub 对长期没有仓库活动的公共计划任务可能暂停，恢复时重新启用该 workflow。

## 构建与测试

Flutter 版本由官方 `pubspec.yaml` 固定，SDK 与 UI 包需要 `lib/scripts/patch.ps1 iOS` 的补丁。标准 macOS GitHub runner 构建，不需要 Apple 签名凭据；产物需自行侧载。

```sh
python3 -m unittest discover -s tool/tests -v
python3 tool/check_btr.py
python3 tool/make_btr_mirror.py
flutter test --no-pub test/standalone/btr_bitrate_unit_test.dart test/standalone/btr_cdn_racer_test.dart test/standalone/btr_round22_test.dart test/standalone/btr_round23_test.dart test/standalone/btr_round26_test.dart
```

这些测试自包含；需要真实 B 站临时媒体链接的 `btr_proxy_e2e_test.dart` 不在无人值守测试中，不能将自动验证等同于真机播放验收。

## 来源与许可

官方说明：[README-upstream.md](README-upstream.md)。BTR 功能与设计：[README-BTR.md](README-BTR.md)、[docs/btr/DESIGN.md](docs/btr/DESIGN.md)。保留原始 [NOTICE](NOTICE) 署名与 [GPL-3.0 LICENSE](LICENSE)。此 fork 不代表 PiliPlus 官方或 Bilibili。
