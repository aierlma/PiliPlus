import Flutter
import UIKit

/// Moves the existing Flutter renderer, including video, subtitles and danmaku,
/// into the external scene. The phone keeps a separate transport controller.
final class ExternalDisplayPlugin: NSObject, FlutterPlugin {
  static weak var instance: ExternalDisplayPlugin?
  private let channel: FlutterMethodChannel
  private var completion: FlutterResult?
  private weak var phoneWindow: UIWindow?
  private var playerController: UIViewController?
  private var controls: ExternalPlaybackControls?
  private var outputWindow: UIWindow?
  private var savedIdleTimer = false
  private var diagnostics: [String] = []

  static func register(with registrar: FlutterPluginRegistrar) {
    let channel = FlutterMethodChannel(
      name: "com.example.piliplus/external-display", binaryMessenger: registrar.messenger())
    let plugin = ExternalDisplayPlugin(channel: channel)
    instance = plugin
    registrar.addMethodCallDelegate(plugin, channel: channel)
  }

  private init(channel: FlutterMethodChannel) {
    self.channel = channel
    super.init()
  }

  func handle(_ call: FlutterMethodCall, result: @escaping FlutterResult) {
    switch call.method {
    case "present":
      present(call.arguments as? [String: Any] ?? [:], result: result)
    case "update":
      controls?.update(call.arguments as? [String: Any] ?? [:])
      result(nil)
    default:
      result(FlutterMethodNotImplemented)
    }
  }

  private func present(_ args: [String: Any], result: @escaping FlutterResult) {
    guard completion == nil else {
      result(FlutterError(code: "BUSY", message: "电视输出已打开", details: nil))
      return
    }
    guard let phone = UIApplication.shared.connectedScenes
      .compactMap({ $0 as? UIWindowScene })
      .filter({ $0.session.role == .windowApplication && $0.activationState == .foregroundActive })
      .flatMap({ $0.windows }).first(where: { $0.isKeyWindow }),
      let root = phone.rootViewController
    else {
      result(FlutterError(code: "WINDOW", message: "请返回视频页面后重试", details: nil))
      return
    }
    completion = result
    diagnostics = []
    phoneWindow = phone
    playerController = root
    savedIdleTimer = UIApplication.shared.isIdleTimerDisabled
    UIApplication.shared.isIdleTimerDisabled = true
    let controls = ExternalPlaybackControls()
    controls.videoTitle = args["title"] as? String ?? "PiliPlus BTR"
    controls.onCommand = { [weak self] command, value in
      guard let self else { return }
      var args: [String: Any] = ["command": command]
      if let value { args["value"] = value }
      if command == "close" {
        self.channel.invokeMethod("command", arguments: args) { [weak self] _ in self?.finish() }
      } else {
        self.channel.invokeMethod("command", arguments: args)
      }
    }
    self.controls = controls
    // Retain the same FlutterViewController/engine and media texture. Moving
    // the controller changes its viewport without decoding a second video.
    phone.rootViewController = controls
    for scene in UIApplication.shared.connectedScenes.compactMap({ $0 as? UIWindowScene }) {
      if scene.screen !== phone.windowScene?.screen {
        connect(scene)
        break
      }
    }
  }

  func connect(_ scene: UIWindowScene) {
    guard completion != nil, outputWindow == nil, let root = playerController else { return }
    scene.screen.overscanCompensation = .none
    let output = UIWindow(windowScene: scene)
    output.backgroundColor = .black
    output.rootViewController = root
    outputWindow = output
    output.isHidden = false
    root.view.setNeedsLayout()
    root.view.layoutIfNeeded()
    let size = scene.screen.bounds.size
    diagnostics.append("external display \(Int(size.width))x\(Int(size.height)) scale=\(scene.screen.scale)")
    controls?.connected = true
    channel.invokeMethod("command", arguments: ["command": "connected"])
  }

  func disconnect(_ scene: UIScene) {
    guard outputWindow?.windowScene === scene else { return }
    // iOS also disconnects this scene when the app enters the background.
    // Keep the phone controls and renderer ownership so returning can attach
    // a new external scene without restoring Flutter into a background window.
    outputWindow?.rootViewController = nil
    outputWindow?.isHidden = true
    outputWindow?.windowScene = nil
    outputWindow = nil
    controls?.connected = false
    diagnostics.append("external display disconnected; waiting for reconnect")
    channel.invokeMethod("command", arguments: ["command": "disconnected"])
  }

  private func finish() {
    guard let completion else { return }
    self.completion = nil
    controls?.stopExternalDisplay()
    outputWindow?.rootViewController = nil
    outputWindow?.isHidden = true
    outputWindow?.windowScene = nil
    outputWindow = nil
    if let phone = phoneWindow, let root = playerController {
      phone.rootViewController = root
      phone.makeKeyAndVisible()
      root.view.setNeedsLayout()
    }
    playerController = nil
    controls = nil
    phoneWindow = nil
    UIApplication.shared.isIdleTimerDisabled = savedIdleTimer
    completion(["diagnostics": diagnostics])
  }
}

/// Selected by UIKit for an AirPlay/HDMI display, independent of the phone scene.
final class ExternalDisplaySceneDelegate: UIResponder, UIWindowSceneDelegate {
  func scene(_ scene: UIScene, willConnectTo session: UISceneSession,
             options connectionOptions: UIScene.ConnectionOptions) {
    guard let scene = scene as? UIWindowScene else { return }
    ExternalDisplayPlugin.instance?.connect(scene)
  }

  func sceneDidDisconnect(_ scene: UIScene) {
    ExternalDisplayPlugin.instance?.disconnect(scene)
  }
}

private final class ExternalPlaybackControls: UIViewController {
  var videoTitle = "PiliPlus BTR"
  var onCommand: ((String, Double?) -> Void)?
  var connected = false {
    didSet {
      status.text = connected ? "正在电视上播放" : "打开控制中心 → 屏幕镜像 → 选择电视\n连接后会自动显示完整视频、字幕和弹幕"
      transport.isHidden = !connected
    }
  }
  private let status = UILabel()
  private let transport = UIStackView()
  private let playButton = UIButton(type: .system)
  private let danmakuButton = UIButton(type: .system)
  private let subtitleButton = UIButton(type: .system)
  private let slider = UISlider()
  private let time = UILabel()
  private var duration = 0.0
  private var displayRegistration: AnyObject?

  override var supportedInterfaceOrientations: UIInterfaceOrientationMask { .allButUpsideDown }

  override func viewDidLoad() {
    super.viewDidLoad()
    view.backgroundColor = .black
    let title = UILabel()
    title.text = videoTitle
    title.textColor = .white
    title.font = .preferredFont(forTextStyle: .headline)
    title.numberOfLines = 2
    title.textAlignment = .center
    status.textColor = .lightGray
    status.numberOfLines = 0
    status.textAlignment = .center
    status.font = .preferredFont(forTextStyle: .subheadline)
    time.textColor = .lightGray
    time.textAlignment = .center
    time.font = .monospacedDigitSystemFont(ofSize: 14, weight: .regular)
    slider.minimumValue = 0
    slider.maximumValue = 1
    slider.accessibilityLabel = "播放进度"
    slider.addTarget(self, action: #selector(seek), for: [.touchUpInside, .touchUpOutside])
    playButton.addTarget(self, action: #selector(togglePlayback), for: .touchUpInside)
    danmakuButton.addTarget(self, action: #selector(toggleDanmaku), for: .touchUpInside)
    subtitleButton.addTarget(self, action: #selector(nextSubtitle), for: .touchUpInside)
    let backward = button("后退 15 秒", action: #selector(skipBackward))
    let forward = button("前进 15 秒", action: #selector(skipForward))
    let row = UIStackView(arrangedSubviews: [backward, playButton, forward])
    row.distribution = .fillEqually
    row.spacing = 16
    let options = UIStackView(arrangedSubviews: [danmakuButton, subtitleButton])
    options.distribution = .fillEqually
    options.spacing = 16
    transport.axis = .vertical
    transport.spacing = 14
    [row, slider, time, options].forEach { transport.addArrangedSubview($0) }
    let close = button("结束电视输出", action: #selector(closeOutput))
    let stack = UIStackView(arrangedSubviews: [title, status, transport, close])
    stack.axis = .vertical
    stack.spacing = 22
    stack.translatesAutoresizingMaskIntoConstraints = false
    view.addSubview(stack)
    NSLayoutConstraint.activate([
      stack.centerXAnchor.constraint(equalTo: view.safeAreaLayoutGuide.centerXAnchor),
      stack.centerYAnchor.constraint(equalTo: view.safeAreaLayoutGuide.centerYAnchor),
      stack.leadingAnchor.constraint(greaterThanOrEqualTo: view.safeAreaLayoutGuide.leadingAnchor, constant: 24),
      stack.trailingAnchor.constraint(lessThanOrEqualTo: view.safeAreaLayoutGuide.trailingAnchor, constant: -24),
      stack.widthAnchor.constraint(lessThanOrEqualToConstant: 580),
      stack.topAnchor.constraint(greaterThanOrEqualTo: view.safeAreaLayoutGuide.topAnchor, constant: 12),
      stack.bottomAnchor.constraint(lessThanOrEqualTo: view.safeAreaLayoutGuide.bottomAnchor, constant: -12),
    ])
    let width = stack.widthAnchor.constraint(equalTo: view.safeAreaLayoutGuide.widthAnchor, constant: -48)
    width.priority = .defaultHigh
    width.isActive = true
    connected = false
    update([:])
    if #available(iOS 27.0, *) {
      let configuration = UISceneConfiguration(
        name: "PiliPlus TV", sessionRole: .windowExternalDisplayNonInteractive)
      configuration.delegateClass = ExternalDisplaySceneDelegate.self
      configuration.storyboard = nil
      displayRegistration = registerSceneAccessory(
        .externalNonInteractive(sceneConfiguration: configuration))
    }
  }

  func stopExternalDisplay() {
    if #available(iOS 27.0, *),
       let registration = displayRegistration as? UISceneAccessoryRegistration {
      unregisterSceneAccessory(registration)
      displayRegistration = nil
    }
  }

  private func button(_ title: String, action: Selector) -> UIButton {
    let button = UIButton(type: .system)
    button.setTitle(title, for: .normal)
    button.addTarget(self, action: action, for: .touchUpInside)
    return button
  }

  func update(_ state: [String: Any]) {
    loadViewIfNeeded()
    playButton.setTitle(state["playing"] as? Bool == true ? "暂停" : "播放", for: .normal)
    danmakuButton.setTitle(state["danmaku"] as? Bool == true ? "弹幕：开" : "弹幕：关", for: .normal)
    subtitleButton.setTitle("字幕：\(state["subtitle"] as? String ?? "关闭")", for: .normal)
    let position = (state["positionMs"] as? NSNumber)?.doubleValue ?? 0
    duration = (state["durationMs"] as? NSNumber)?.doubleValue ?? 0
    if !slider.isTracking { slider.value = duration > 0 ? Float(position / duration) : 0 }
    slider.isEnabled = duration > 0
    time.text = "\(timestamp(position)) / \(timestamp(duration))"
  }

  private func timestamp(_ milliseconds: Double) -> String {
    let seconds = max(0, Int(milliseconds / 1000))
    return String(format: "%d:%02d", seconds / 60, seconds % 60)
  }
  @objc private func togglePlayback() { onCommand?("togglePlayback", nil) }
  @objc private func toggleDanmaku() { onCommand?("toggleDanmaku", nil) }
  @objc private func nextSubtitle() { onCommand?("nextSubtitle", nil) }
  @objc private func skipBackward() { onCommand?("skip", -15000) }
  @objc private func skipForward() { onCommand?("skip", 15000) }
  @objc private func seek() { onCommand?("seek", Double(slider.value) * duration) }
  @objc private func closeOutput() { onCommand?("close", nil) }
}
