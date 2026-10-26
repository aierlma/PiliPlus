import AVKit
import Flutter
import UIKit

/// AVKit owns the AirPlay picker, receiver connection and video playback.
/// The receiver gets the CDN URL, never the phone's loopback BTR URL.
final class AirPlayPlugin: NSObject, FlutterPlugin, AVRoutePickerViewDelegate {
  static func register(with registrar: FlutterPluginRegistrar) {
    let channel = FlutterMethodChannel(
      name: "com.example.piliplus/airplay",
      binaryMessenger: registrar.messenger()
    )
    let instance = AirPlayPlugin()
    registrar.addMethodCallDelegate(instance, channel: channel)
  }

  private var controller: AirPlayViewController?
  private weak var presenter: UIViewController?
  private var routePicker: AVRoutePickerView?
  private var pickerOpened = false
  private var completion: FlutterResult?
  private var statusObservation: NSKeyValueObservation?
  private var resolutionObservation: NSKeyValueObservation?
  private var preparationTimer: Timer?
  private var initialPositionMs = 0
  private var failure: String?
  private var started = false
  private var positionLoaded = false
  private var enableSubtitles = false
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
      let host = url.host,
      url.scheme == "https" || (args["localNetwork"] as? Bool == true
        && url.scheme == "http" && isPrivateAddress(host)),
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
    let title = (args["title"] as? String).flatMap { $0.isEmpty ? nil : $0 } ?? "PiliPlus BTR"
    setTitle(title, for: item)
    let player = AVPlayer(playerItem: item)
    player.allowsExternalPlayback = true
    let viewController = AirPlayViewController()
    viewController.player = player
    viewController.allowsPictureInPicturePlayback = false
    viewController.updatesNowPlayingInfoCenter = true
    viewController.modalPresentationStyle = .fullScreen
    viewController.onClose = { [weak self] in self?.finish() }
    controller = viewController
    self.presenter = presenter
    completion = result
    initialPositionMs = max(0, (args["positionMs"] as? NSNumber)?.intValue ?? 0)
    failure = nil
    started = false
    positionLoaded = false
    enableSubtitles = args["subtitles"] as? Bool == true
    resolutionObservation = item.observe(\.presentationSize, options: [.initial, .new]) {
      [weak self] item, _ in
      let size = item.presentationSize
      guard size.width > 0, size.height > 0 else { return }
      DispatchQueue.main.async {
        guard let self, self.controller?.player?.currentItem === item else { return }
        // Read the loaded video dimensions rather than the requested quality tier.
        let resolution = "\(Int(size.width.rounded()))×\(Int(size.height.rounded()))"
        self.setTitle("\(resolution) · \(title)", for: item)
      }
    }
    showRoutes(from: presenter)
  }

  private func showRoutes(from presenter: UIViewController) {
    let picker = AVRoutePickerView(frame: CGRect(x: 0, y: 0, width: 44, height: 44))
    picker.prioritizesVideoDevices = true
    picker.delegate = self
    picker.isHidden = true
    presenter.view.addSubview(picker)
    picker.layoutIfNeeded()
    routePicker = picker
    pickerOpened = false
    // Forward the user's Flutter AirPlay tap to AVKit's route button.
    // AVKit owns the device list; no private selector or routing API is used.
    guard let button = picker.subviews.compactMap({ $0 as? UIButton }).first else {
      fail("无法打开 AirPlay 设备列表，请重试")
      return
    }
    button.sendActions(for: .touchUpInside)
    DispatchQueue.main.asyncAfter(deadline: .now() + 3) { [weak self, weak picker] in
      guard let self, self.routePicker === picker, !self.pickerOpened else { return }
      self.fail("无法打开 AirPlay 设备列表，请重试")
    }
  }

  func routePickerViewWillBeginPresentingRoutes(_ routePickerView: AVRoutePickerView) {
    if routePicker === routePickerView { pickerOpened = true }
  }

  func routePickerViewDidEndPresentingRoutes(_ routePickerView: AVRoutePickerView) {
    guard routePicker === routePickerView else { return }
    routePickerView.removeFromSuperview()
    routePicker = nil
    // Let the system picker finish dismissing before showing playback controls.
    DispatchQueue.main.async { [weak self] in
      guard let self, self.completion != nil,
        let presenter = self.presenter, let controller = self.controller,
        let player = controller.player, let item = player.currentItem
      else { return }
      let selectedTV = AVAudioSession.sharedInstance().currentRoute.outputs
        .contains { $0.portType == .airPlay }
      guard selectedTV else {
        self.finish()
        return
      }
      presenter.present(controller, animated: true) { [weak self] in
        self?.prepare(item, player: player)
      }
    }
  }

  private func setTitle(_ title: String, for item: AVPlayerItem) {
    let metadata = AVMutableMetadataItem()
    metadata.identifier = .commonIdentifierTitle
    metadata.value = title as NSString
    metadata.extendedLanguageTag = "und"
    item.externalMetadata = [metadata]
  }

  private func isPrivateAddress(_ host: String) -> Bool {
    let parts = host.split(separator: ".").compactMap { Int($0) }
    guard parts.count == 4, parts.allSatisfy({ (0...255).contains($0) }) else { return false }
    return parts[0] == 10 || (parts[0] == 172 && (16...31).contains(parts[1]))
      || (parts[0] == 192 && parts[1] == 168)
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
          if self.enableSubtitles {
            Task { @MainActor [weak self] in
              guard let group = try? await item.asset.loadMediaSelectionGroup(for: .legible),
                let option = group.options.first, self?.controller?.player === player
              else { return }
              item.select(option, in: group)
            }
          }
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
    if let controller, controller.presentingViewController != nil {
      controller.dismiss(animated: true) { [weak self] in self?.finish() }
    } else {
      finish()
    }
  }

  private func finish() {
    guard let completion else { return }
    self.completion = nil
    preparationTimer?.invalidate()
    preparationTimer = nil
    statusObservation = nil
    resolutionObservation = nil
    routePicker?.delegate = nil
    routePicker?.removeFromSuperview()
    routePicker = nil
    let player = controller?.player
    let seconds = player.map { CMTimeGetSeconds($0.currentTime()) } ?? .nan
    let positionMs = positionLoaded && seconds.isFinite && seconds >= 0
      ? Int(seconds * 1000) : initialPositionMs
    player?.pause()
    player?.replaceCurrentItem(with: nil)
    controller?.onClose = nil
    controller = nil
    presenter = nil
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
