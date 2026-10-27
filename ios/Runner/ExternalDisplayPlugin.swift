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
    controls.artworkURL = args["artwork"] as? String
    controls.overrideUserInterfaceStyle = args["darkMode"] as? Bool == true ? .dark : .light
    controls.onCommand = { [weak self] command, value in
      guard let self else { return }
      var args: [String: Any] = ["command": command]
      if let value { args["value"] = value }
      if command == "close" || command == "airplay" || command == "dlna" {
        self.channel.invokeMethod("command", arguments: ["command": "close"]) { [weak self] _ in
          self?.finish(nextMode: command == "close" ? nil : command)
        }
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

  private func finish(nextMode: String? = nil) {
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
    var response: [String: Any] = ["diagnostics": diagnostics]
    if let nextMode { response["nextMode"] = nextMode }
    completion(response)
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
  var artworkURL: String?
  var onCommand: ((String, Double?) -> Void)?
  var connected = false {
    didSet { updateConnection() }
  }
  private let status = UILabel()
  private let artwork = UIImageView()
  private let artworkPlaceholder = UIImageView(image: UIImage(systemName: "tv"))
  private let transport = UIStackView()
  private let connectionGuide = UIStackView()
  private let playButton = UIButton(type: .system)
  private let danmakuButton = UIButton(type: .system)
  private let subtitleButton = UIButton(type: .system)
  private let slider = UISlider()
  private let elapsedTime = UILabel()
  private let remainingTime = UILabel()
  private var duration = 0.0
  private var subtitleMenuSignature = ""
  private var artworkTask: URLSessionDataTask?
  private var displayRegistration: AnyObject?

  override var supportedInterfaceOrientations: UIInterfaceOrientationMask { .allButUpsideDown }

  override func viewDidLoad() {
    super.viewDidLoad()
    view.backgroundColor = .systemBackground
    view.tintColor = .label

    let close = symbolButton("chevron.down", label: "结束投屏", action: #selector(closeOutput))
    let more = symbolButton("ellipsis", label: "更多投屏选项", action: nil)
    more.showsMenuAsPrimaryAction = true
    more.menu = UIMenu(children: [
      UIAction(title: "连接帮助", image: UIImage(systemName: "questionmark.circle")) { [weak self] _ in
        self?.showConnectionHelp()
      },
      UIMenu(title: "其他投屏方式", options: .displayInline, children: [
        UIAction(title: "AirPlay 视频", image: UIImage(systemName: "airplayvideo")) { [weak self] _ in
          self?.onCommand?("airplay", nil)
        },
        UIAction(title: "DLNA", image: UIImage(systemName: "tv")) { [weak self] _ in
          self?.onCommand?("dlna", nil)
        },
      ]),
    ])
    let heading = label("AirPlay", style: .headline)
    heading.textAlignment = .center
    let header = UIStackView(arrangedSubviews: [close, heading, more])
    header.alignment = .center
    header.spacing = 16
    header.translatesAutoresizingMaskIntoConstraints = false
    view.addSubview(header)

    let scroll = UIScrollView()
    scroll.showsVerticalScrollIndicator = false
    scroll.translatesAutoresizingMaskIntoConstraints = false
    view.addSubview(scroll)

    artwork.contentMode = .scaleAspectFill
    artwork.backgroundColor = .secondarySystemBackground
    artwork.layer.cornerRadius = 20
    artwork.layer.cornerCurve = .continuous
    artwork.clipsToBounds = true
    artwork.isAccessibilityElement = false
    artworkPlaceholder.contentMode = .scaleAspectFit
    artworkPlaceholder.tintColor = .tertiaryLabel
    artworkPlaceholder.translatesAutoresizingMaskIntoConstraints = false
    artwork.addSubview(artworkPlaceholder)

    let title = UILabel()
    title.text = videoTitle
    title.textColor = .label
    title.font = .preferredFont(forTextStyle: .title3)
    title.adjustsFontForContentSizeCategory = true
    title.numberOfLines = 3
    status.textColor = .secondaryLabel
    status.font = .preferredFont(forTextStyle: .subheadline)
    status.adjustsFontForContentSizeCategory = true
    status.numberOfLines = 0
    let metadata = UIStackView(arrangedSubviews: [title, status])
    metadata.axis = .vertical
    metadata.spacing = 6

    for time in [elapsedTime, remainingTime] {
      time.textColor = .secondaryLabel
      time.font = UIFontMetrics(forTextStyle: .caption1).scaledFont(
        for: .monospacedDigitSystemFont(ofSize: 12, weight: .medium))
      time.adjustsFontForContentSizeCategory = true
    }
    remainingTime.textAlignment = .right
    let times = UIStackView(arrangedSubviews: [elapsedTime, remainingTime])
    times.distribution = .fillEqually
    slider.minimumValue = 0
    slider.maximumValue = 1
    slider.accessibilityLabel = "播放进度"
    slider.addTarget(self, action: #selector(previewSeek), for: .valueChanged)
    slider.addTarget(self, action: #selector(seek), for: [.touchUpInside, .touchUpOutside])
    let timeline = UIStackView(arrangedSubviews: [slider, times])
    timeline.axis = .vertical
    timeline.spacing = 0

    configure(playButton, symbol: "play.fill", pointSize: 30, prominent: true)
    playButton.accessibilityLabel = "播放"
    playButton.addTarget(self, action: #selector(togglePlayback), for: .touchUpInside)
    danmakuButton.addTarget(self, action: #selector(toggleDanmaku), for: .touchUpInside)
    subtitleButton.showsMenuAsPrimaryAction = true
    let backward = symbolButton("gobackward.15", label: "后退 15 秒", action: #selector(skipBackward))
    let forward = symbolButton("goforward.15", label: "前进 15 秒", action: #selector(skipForward))
    backward.configuration = .plain()
    forward.configuration = .plain()
    backward.setImage(UIImage(systemName: "gobackward.15", withConfiguration:
      UIImage.SymbolConfiguration(pointSize: 28, weight: .regular)), for: .normal)
    forward.setImage(UIImage(systemName: "goforward.15", withConfiguration:
      UIImage.SymbolConfiguration(pointSize: 28, weight: .regular)), for: .normal)
    let row = UIStackView(arrangedSubviews: [backward, playButton, forward])
    row.alignment = .center
    row.distribution = .equalSpacing
    row.isLayoutMarginsRelativeArrangement = true
    row.directionalLayoutMargins = NSDirectionalEdgeInsets(top: 8, leading: 24, bottom: 8, trailing: 24)
    let options = UIStackView(arrangedSubviews: [danmakuButton, subtitleButton])
    options.distribution = .fillEqually
    options.spacing = 12
    transport.axis = .vertical
    transport.spacing = 22
    [timeline, row, options].forEach { transport.addArrangedSubview($0) }

    let guideSymbol = UIImageView(image: UIImage(systemName: "rectangle.on.rectangle",
      withConfiguration: UIImage.SymbolConfiguration(pointSize: 34, weight: .light)))
    guideSymbol.tintColor = .secondaryLabel
    guideSymbol.contentMode = .scaleAspectFit
    let guideTitle = label("连接电视", style: .title2)
    let guideText = label("打开控制中心，轻点“屏幕镜像”，\n然后选择电视。", style: .subheadline)
    guideText.textColor = .secondaryLabel
    guideText.textAlignment = .center
    let help = UIButton(type: .system)
    help.setTitle("查看连接步骤", for: .normal)
    help.titleLabel?.font = .preferredFont(forTextStyle: .subheadline)
    help.addTarget(self, action: #selector(showConnectionHelp), for: .touchUpInside)
    connectionGuide.axis = .vertical
    connectionGuide.alignment = .center
    connectionGuide.spacing = 14
    [guideSymbol, guideTitle, guideText, help].forEach { connectionGuide.addArrangedSubview($0) }
    connectionGuide.setCustomSpacing(24, after: guideSymbol)

    let stack = UIStackView(arrangedSubviews: [artwork, metadata, transport, connectionGuide])
    stack.axis = .vertical
    stack.spacing = 28
    stack.setCustomSpacing(20, after: artwork)
    stack.translatesAutoresizingMaskIntoConstraints = false
    scroll.addSubview(stack)
    NSLayoutConstraint.activate([
      header.topAnchor.constraint(equalTo: view.safeAreaLayoutGuide.topAnchor, constant: 8),
      header.leadingAnchor.constraint(equalTo: view.safeAreaLayoutGuide.leadingAnchor, constant: 20),
      header.trailingAnchor.constraint(equalTo: view.safeAreaLayoutGuide.trailingAnchor, constant: -20),
      close.widthAnchor.constraint(equalToConstant: 44),
      more.widthAnchor.constraint(equalToConstant: 44),
      close.heightAnchor.constraint(equalToConstant: 44),
      more.heightAnchor.constraint(equalToConstant: 44),
      scroll.topAnchor.constraint(equalTo: header.bottomAnchor, constant: 24),
      scroll.bottomAnchor.constraint(equalTo: view.safeAreaLayoutGuide.bottomAnchor),
      scroll.leadingAnchor.constraint(equalTo: view.safeAreaLayoutGuide.leadingAnchor),
      scroll.trailingAnchor.constraint(equalTo: view.safeAreaLayoutGuide.trailingAnchor),
      stack.topAnchor.constraint(equalTo: scroll.contentLayoutGuide.topAnchor, constant: 12),
      stack.bottomAnchor.constraint(equalTo: scroll.contentLayoutGuide.bottomAnchor, constant: -32),
      stack.centerXAnchor.constraint(equalTo: scroll.frameLayoutGuide.centerXAnchor),
      stack.leadingAnchor.constraint(equalTo: scroll.contentLayoutGuide.leadingAnchor, constant: 28),
      stack.trailingAnchor.constraint(equalTo: scroll.contentLayoutGuide.trailingAnchor, constant: -28),
      stack.widthAnchor.constraint(lessThanOrEqualToConstant: 520),
      artwork.heightAnchor.constraint(equalTo: artwork.widthAnchor, multiplier: 9.0 / 16),
      artworkPlaceholder.widthAnchor.constraint(equalToConstant: 48),
      artworkPlaceholder.heightAnchor.constraint(equalToConstant: 48),
      artworkPlaceholder.centerXAnchor.constraint(equalTo: artwork.centerXAnchor),
      artworkPlaceholder.centerYAnchor.constraint(equalTo: artwork.centerYAnchor),
      playButton.widthAnchor.constraint(equalToConstant: 80),
      playButton.heightAnchor.constraint(equalToConstant: 80),
      backward.widthAnchor.constraint(equalToConstant: 56),
      backward.heightAnchor.constraint(equalToConstant: 56),
      forward.widthAnchor.constraint(equalToConstant: 56),
      forward.heightAnchor.constraint(equalToConstant: 56),
      danmakuButton.heightAnchor.constraint(greaterThanOrEqualToConstant: 60),
      subtitleButton.heightAnchor.constraint(greaterThanOrEqualToConstant: 60),
      help.heightAnchor.constraint(greaterThanOrEqualToConstant: 44),
    ])
    let width = stack.widthAnchor.constraint(equalTo: scroll.frameLayoutGuide.widthAnchor, constant: -56)
    width.priority = .defaultHigh
    width.isActive = true
    updateConnection()
    update([:])
    loadArtwork()
    if #available(iOS 27.0, *) {
      let configuration = UISceneConfiguration(
        name: "PiliPlus TV", sessionRole: .windowExternalDisplayNonInteractive)
      configuration.delegateClass = ExternalDisplaySceneDelegate.self
      configuration.storyboard = nil
      displayRegistration = registerSceneAccessory(
        .externalNonInteractive(sceneConfiguration: configuration))
    }
  }

  deinit { artworkTask?.cancel() }

  private func updateConnection() {
    guard isViewLoaded else { return }
    status.text = connected ? "正在电视上播放" : "等待连接"
    transport.isHidden = !connected
    connectionGuide.isHidden = connected
  }

  func stopExternalDisplay() {
    if #available(iOS 27.0, *),
       let registration = displayRegistration as? UISceneAccessoryRegistration {
      unregisterSceneAccessory(registration)
      displayRegistration = nil
    }
  }

  private func label(_ text: String, style: UIFont.TextStyle) -> UILabel {
    let label = UILabel()
    label.text = text
    label.font = .preferredFont(forTextStyle: style)
    label.adjustsFontForContentSizeCategory = true
    label.numberOfLines = 0
    return label
  }

  private func configure(_ button: UIButton, symbol: String, pointSize: CGFloat = 17,
                         prominent: Bool = false) {
    var configuration: UIButton.Configuration
    if #available(iOS 26.0, *) {
      configuration = prominent ? .prominentGlass() : .glass()
    } else {
      configuration = prominent ? .filled() : .gray()
    }
    configuration.cornerStyle = .capsule
    configuration.baseBackgroundColor = prominent ? .label : nil
    configuration.baseForegroundColor = prominent ? .systemBackground : .label
    configuration.image = UIImage(systemName: symbol,
      withConfiguration: UIImage.SymbolConfiguration(pointSize: pointSize, weight: .semibold))
    button.configuration = configuration
  }

  private func symbolButton(_ symbol: String, label: String, action: Selector?) -> UIButton {
    let button = UIButton(type: .system)
    configure(button, symbol: symbol)
    button.accessibilityLabel = label
    if let action { button.addTarget(self, action: action, for: .touchUpInside) }
    return button
  }

  private func option(_ button: UIButton, title: String, subtitle: String, symbol: String) {
    var configuration = UIButton.Configuration.gray()
    configuration.title = title
    configuration.subtitle = subtitle
    configuration.image = UIImage(systemName: symbol)
    configuration.imagePadding = 10
    configuration.titlePadding = 3
    configuration.cornerStyle = .large
    configuration.baseForegroundColor = .label
    configuration.contentInsets = NSDirectionalEdgeInsets(top: 12, leading: 14, bottom: 12, trailing: 14)
    button.configuration = configuration
    button.accessibilityLabel = title
    button.accessibilityValue = subtitle
  }

  private func loadArtwork() {
    guard let raw = artworkURL, !raw.isEmpty,
          let url = URL(string: raw.hasPrefix("//") ? "https:\(raw)" : raw),
          ["https", "http"].contains(url.scheme?.lowercased() ?? "") else { return }
    artworkTask = URLSession.shared.dataTask(with: url) { [weak self] data, _, _ in
      guard let data, let image = UIImage(data: data) else { return }
      DispatchQueue.main.async { [weak self] in
        self?.artwork.image = image
        self?.artworkPlaceholder.isHidden = true
      }
    }
    artworkTask?.resume()
  }

  func update(_ state: [String: Any]) {
    loadViewIfNeeded()
    let playing = state["playing"] as? Bool == true
    let danmaku = state["danmaku"] as? Bool == true
    status.text = connected ? (playing ? "正在电视上播放" : "已暂停") : "等待连接"
    playButton.configuration?.image = UIImage(systemName: playing ? "pause.fill" : "play.fill",
      withConfiguration: UIImage.SymbolConfiguration(pointSize: 30, weight: .semibold))
    playButton.accessibilityLabel = playing ? "暂停" : "播放"
    option(danmakuButton, title: "弹幕", subtitle: danmaku ? "已开启" : "已关闭",
           symbol: danmaku ? "text.bubble.fill" : "text.bubble")
    option(subtitleButton, title: "字幕", subtitle: state["subtitle"] as? String ?? "关闭",
           symbol: "captions.bubble")
    let subtitles = state["subtitles"] as? [String] ?? ["关闭"]
    let selected = state["subtitleIndex"] as? Int ?? 0
    let signature = "\(selected):\(subtitles)"
    if signature != subtitleMenuSignature {
      subtitleMenuSignature = signature
      subtitleButton.menu = UIMenu(title: "字幕", children: subtitles.enumerated().map { index, title in
        UIAction(title: title, state: index == selected ? .on : .off) { [weak self] _ in
          self?.onCommand?("selectSubtitle", Double(index))
        }
      })
    }
    let position = (state["positionMs"] as? NSNumber)?.doubleValue ?? 0
    duration = (state["durationMs"] as? NSNumber)?.doubleValue ?? 0
    if !slider.isTracking {
      slider.value = duration > 0 ? Float(position / duration) : 0
      updateTimes(position)
    }
    slider.isEnabled = duration > 0
  }

  private func updateTimes(_ position: Double) {
    elapsedTime.text = timestamp(position)
    remainingTime.text = "−\(timestamp(max(0, duration - position)))"
    slider.accessibilityValue = "\(timestamp(position))，共 \(timestamp(duration))"
  }

  private func timestamp(_ milliseconds: Double) -> String {
    let seconds = max(0, Int(milliseconds / 1000))
    if seconds >= 3600 {
      return String(format: "%d:%02d:%02d", seconds / 3600, seconds / 60 % 60, seconds % 60)
    }
    return String(format: "%d:%02d", seconds / 60, seconds % 60)
  }

  @objc private func showConnectionHelp() {
    let alert = UIAlertController(title: "连接电视", message:
      "1. 从屏幕右上角向下轻扫，打开控制中心。\n2. 轻点“屏幕镜像”，选择电视。\n3. 返回 PiliPlus，视频会自动显示在电视上。\n\n播放期间请保持 PiliPlus 在前台。",
      preferredStyle: .alert)
    alert.addAction(UIAlertAction(title: "好", style: .default))
    present(alert, animated: true)
  }
  @objc private func togglePlayback() { onCommand?("togglePlayback", nil) }
  @objc private func toggleDanmaku() { onCommand?("toggleDanmaku", nil) }
  @objc private func skipBackward() { onCommand?("skip", -15000) }
  @objc private func skipForward() { onCommand?("skip", 15000) }
  @objc private func previewSeek() { updateTimes(Double(slider.value) * duration) }
  @objc private func seek() { onCommand?("seek", Double(slider.value) * duration) }
  @objc private func closeOutput() { onCommand?("close", nil) }
}
