import AVKit
import Flutter
import UIKit

/// AVKit owns the AirPlay picker, receiver connection and video playback.
/// The receiver gets the CDN URL, never the phone's loopback BTR URL.
final class AirPlayPlugin: NSObject, FlutterPlugin {
  static func register(with registrar: FlutterPluginRegistrar) {
    let channel = FlutterMethodChannel(
      name: "com.example.piliplus/airplay",
      binaryMessenger: registrar.messenger()
    )
    let instance = AirPlayPlugin()
    registrar.addMethodCallDelegate(instance, channel: channel)
  }

  private var controller: AirPlayViewController?
  private var completion: FlutterResult?
  private var statusObservation: NSKeyValueObservation?
  private var preparationTimer: Timer?
  private var initialPositionMs = 0
  private var failure: String?
  private var started = false
  private var positionLoaded = false
  private var audioConfiguration: (
    AVAudioSession.Category, AVAudioSession.Mode,
    AVAudioSession.RouteSharingPolicy, AVAudioSession.CategoryOptions
  )?

  func handle(_ call: FlutterMethodCall, result: @escaping FlutterResult) {
    let args = call.arguments as? [String: Any] ?? [:]
    switch call.method {
    case "present":
      present(args, result: result)
    case "play":
      controller?.player?.play()
      result(nil)
    case "pause":
      controller?.player?.pause()
      result(nil)
    case "seek":
      let milliseconds = max(0, (args["positionMs"] as? NSNumber)?.int64Value ?? 0)
      controller?.player?.seek(to: CMTime(value: milliseconds, timescale: 1000))
      result(nil)
    default:
      result(FlutterMethodNotImplemented)
    }
  }

  private func present(_ args: [String: Any], result: @escaping FlutterResult) {
    guard completion == nil else {
      result(FlutterError(code: "BUSY", message: "AirPlay 已打开", details: nil))
      return
    }
    guard let rawURL = args["url"] as? String, let url = URL(string: rawURL),
      url.scheme == "https", let host = url.host,
      !["localhost", "127.0.0.1", "::1"].contains(host)
    else {
      result(FlutterError(code: "URL", message: "此播放地址不支持 AirPlay", details: nil))
      return
    }
    let window = UIApplication.shared.connectedScenes
      .compactMap { $0 as? UIWindowScene }
      .filter { $0.activationState == .foregroundActive }
      .flatMap { $0.windows }
      .first { $0.isKeyWindow }
    guard var presenter = window?.rootViewController else {
      result(FlutterError(code: "WINDOW", message: "请返回视频页面后重试", details: nil))
      return
    }
    while let presented = presenter.presentedViewController {
      presenter = presented
    }
    guard !presenter.isBeingDismissed else {
      result(FlutterError(code: "WINDOW", message: "请稍后重试", details: nil))
      return
    }

    let audioSession = AVAudioSession.sharedInstance()
    audioConfiguration = (
      audioSession.category, audioSession.mode,
      audioSession.routeSharingPolicy, audioSession.categoryOptions
    )
    do {
      try audioSession.setCategory(.playback, mode: .moviePlayback, policy: .longFormVideo)
      try audioSession.setActive(true)
    } catch {
      restoreAudioSession()
      result(FlutterError(code: "AUDIO", message: "无法启动投屏音频，请重试", details: nil))
      return
    }

    let item = AVPlayerItem(url: url)
    if let title = args["title"] as? String, !title.isEmpty {
      let metadata = AVMutableMetadataItem()
      metadata.identifier = .commonIdentifierTitle
      metadata.value = title as NSString
      metadata.extendedLanguageTag = "und"
      item.externalMetadata = [metadata]
    }
    let player = AVPlayer(playerItem: item)
    player.allowsExternalPlayback = true
    let viewController = AirPlayViewController()
    viewController.player = player
    viewController.allowsPictureInPicturePlayback = false
    viewController.updatesNowPlayingInfoCenter = true
    viewController.modalPresentationStyle = .fullScreen
    viewController.onClose = { [weak self] in self?.finish() }
    controller = viewController
    completion = result
    initialPositionMs = max(0, (args["positionMs"] as? NSNumber)?.intValue ?? 0)
    failure = nil
    started = false
    positionLoaded = false
    presenter.present(viewController, animated: true) { [weak self] in
      self?.prepare(item, player: player)
    }
  }

  private func prepare(_ item: AVPlayerItem, player: AVPlayer) {
    guard completion != nil, controller?.player === player else { return }
    preparationTimer = Timer.scheduledTimer(withTimeInterval: 30, repeats: false) {
      [weak self] _ in
      self?.fail("投屏视频加载超时，请重试或切换 CDN")
    }
    statusObservation = item.observe(\.status, options: [.initial, .new]) {
      [weak self] item, _ in
      DispatchQueue.main.async {
        guard let self, self.controller?.player === player else { return }
        switch item.status {
        case .readyToPlay:
          guard !self.started else { return }
          self.started = true
          self.preparationTimer?.invalidate()
          self.preparationTimer = nil
          let seconds = CMTimeGetSeconds(item.duration)
          let requested = Double(self.initialPositionMs) / 1000
          let position = seconds.isFinite ? min(requested, max(0, seconds - 0.1)) : requested
          player.seek(to: CMTime(seconds: position, preferredTimescale: 1000)) {
            [weak self] finished in
            DispatchQueue.main.async {
              guard finished, let self, self.controller?.player === player else { return }
              self.positionLoaded = true
              player.play()
            }
          }
        case .failed:
          self.fail("此视频无法通过 AirPlay 播放，请重试或切换 CDN")
        default:
          break
        }
      }
    }
  }

  private func fail(_ message: String) {
    guard completion != nil else { return }
    failure = message
    controller?.dismiss(animated: true) { [weak self] in self?.finish() }
  }

  private func finish() {
    guard let completion else { return }
    self.completion = nil
    preparationTimer?.invalidate()
    preparationTimer = nil
    statusObservation = nil
    let player = controller?.player
    let seconds = player.map { CMTimeGetSeconds($0.currentTime()) } ?? .nan
    let positionMs = positionLoaded && seconds.isFinite && seconds >= 0
      ? Int(seconds * 1000) : initialPositionMs
    player?.pause()
    player?.replaceCurrentItem(with: nil)
    controller?.onClose = nil
    controller = nil
    restoreAudioSession()
    var response: [String: Any] = ["positionMs": positionMs]
    if let failure { response["error"] = failure }
    completion(response)
  }

  private func restoreAudioSession() {
    let audioSession = AVAudioSession.sharedInstance()
    try? audioSession.setActive(false, options: .notifyOthersOnDeactivation)
    if let saved = audioConfiguration {
      try? audioSession.setCategory(saved.0, mode: saved.1, policy: saved.2, options: saved.3)
    }
    audioConfiguration = nil
  }
}

private final class AirPlayViewController: AVPlayerViewController {
  var onClose: (() -> Void)?

  override func viewDidDisappear(_ animated: Bool) {
    super.viewDidDisappear(animated)
    if isBeingDismissed || presentingViewController == nil {
      onClose?()
    }
  }
}
