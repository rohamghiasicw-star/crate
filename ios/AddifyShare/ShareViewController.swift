import UIKit
import UniformTypeIdentifiers

/* ---------- the icon in the share sheet ----------
   This target is what puts Addify in the TikTok / Instagram "Share to" row. It is a plain
   UIViewController rather than SLComposeServiceViewController on purpose: the compose
   sheet asks the user to type a caption and tap Post, and a share is already the user
   saying "this one". FALLBACK BUILD (guideline 4.4, parked, ship only on the owners' go): the
   extension never opens the app. It grabs the link, parks it in the app group and shows a
   small dark card that says so, with Done. The app drains the inbox on every activation,
   so the scan starts the next time the user opens Addify. */
final class ShareViewController: UIViewController {
    private var started = false
    private var finished = false
    private var link: String?
    private weak var card: UIView?

    override func viewDidLoad() {
        super.viewDidLoad()
        /* The extension draws nothing of its own, but iOS still presents this controller as
           a card while it does its work. Left on the default light appearance that card
           paints solid white over the TikTok feed for the split second before we dismiss,
           which is the "quick white screen" people are seeing. Forcing dark and keeping the
           view clear lets the host app show through instead of flashing white. */
        overrideUserInterfaceStyle = .dark
        view.backgroundColor = .clear
    }

    override func viewDidAppear(_ animated: Bool) {
        super.viewDidAppear(animated)
        /* viewDidAppear can run again when the host comes back to the front; one share,
           one attempt. */
        guard !started else { return }
        started = true
        NotificationCenter.default.addObserver(self, selector: #selector(hostWillEnterForeground),
                                               name: .NSExtensionHostWillEnterForeground, object: nil)
        extractLink { [weak self] link in
            guard let self = self else { return }
            guard let link = link, SharedInbox.isScannable(link) else {
                /* Say so instead of vanishing. Roham 09-24 20:50: "there shouldn't be
                   anything silent". NATIVE-R6-04 (build 31): the card stays until Done for
                   everyone. It used to close itself after 2.5 s unless VoiceOver was running,
                   which still cut off Switch Control and Voice Control users (iOS can't tell
                   when Voice Control is on), and anyone who reads slowly. */
                self.showCard(title: "No TikTok or Instagram link",
                              body: "Addify scans TikTok and Instagram videos, and this share did not include one.",
                              primary: nil)
                return
            }
            self.link = link
            SharedInbox.write(link)
            self.showSaved()
        }
    }

    /* The user came back to TikTok or Instagram after Addify took over. If the app drained
       the inbox, the share was delivered and this card has nothing left to say. */
    @objc private func hostWillEnterForeground() {
        guard !finished, link != nil else { return }
        if !SharedInbox.hasPending { finish(error: false) }
    }

    /* FALLBACK BUILD: no open from the extension (guideline 4.4). The link is in the inbox, so
       opening Addify by hand scans it (within ten minutes at once, later it asks first). */
    private func showSaved() {
        guard !finished else { return }
        if AppGroup.isShared {
            showCard(title: "Saved to Addify",
                     body: "Open Addify from your home screen to scan this video.",
                     primary: nil)
        } else {
            /* No app group in this build: the inbox stays in the extension, so hand the user
               the link instead of promising a scan that would never start. */
            UIPasteboard.general.string = link
            showCard(title: "Link copied",
                     body: "Open Addify and paste the link on Home to scan it.",
                     primary: nil)
        }
    }

    // MARK: the card (only shown when something needs the user)

    private func showCard(title: String, body: String, primary: String?) {
        card?.removeFromSuperview()
        let ground = UIColor(red: 0.090, green: 0.078, blue: 0.122, alpha: 1)   // #17141F, the app ground
        let purple = UIColor(red: 0.36, green: 0.29, blue: 0.91, alpha: 1)      // the app's accent

        let box = UIView()
        box.backgroundColor = ground
        box.layer.cornerRadius = 22
        box.layer.cornerCurve = .continuous
        box.translatesAutoresizingMaskIntoConstraints = false

        /* NATIVE-R6-04 / A11Y-R6-06 (build 31): every font follows Larger Text. At the default size each
           is the size it always was; the card scrolls when the text outgrows the screen. */
        func scaled(_ size: CGFloat, _ weight: UIFont.Weight, _ style: UIFont.TextStyle) -> UIFont {
            UIFontMetrics(forTextStyle: style).scaledFont(for: .systemFont(ofSize: size, weight: weight))
        }

        let t = UILabel()
        t.text = title
        t.font = scaled(19, .semibold, .title3)
        t.adjustsFontForContentSizeCategory = true
        t.textColor = .white
        t.numberOfLines = 0

        let b = UILabel()
        b.text = body
        b.font = scaled(15, .regular, .subheadline)
        b.adjustsFontForContentSizeCategory = true
        b.textColor = UIColor(white: 1, alpha: 0.72)
        b.numberOfLines = 0

        let stack = UIStackView(arrangedSubviews: [t, b])
        stack.axis = .vertical
        stack.spacing = 8
        stack.setCustomSpacing(18, after: b)
        stack.translatesAutoresizingMaskIntoConstraints = false

        if let primary = primary {
            let go = UIButton(type: .system)
            go.setTitle(primary, for: .normal)
            go.titleLabel?.font = scaled(17, .semibold, .headline)
            go.titleLabel?.adjustsFontForContentSizeCategory = true
            go.titleLabel?.numberOfLines = 0
            go.titleLabel?.textAlignment = .center
            go.setTitleColor(.white, for: .normal)
            go.backgroundColor = purple
            go.layer.cornerRadius = 14
            go.layer.cornerCurve = .continuous
            go.heightAnchor.constraint(greaterThanOrEqualToConstant: 52).isActive = true
            go.addTarget(self, action: #selector(doneTapped), for: .touchUpInside)
            stack.addArrangedSubview(go)
        }
        let done = UIButton(type: .system)
        done.setTitle("Done", for: .normal)
        done.titleLabel?.font = scaled(16, .medium, .callout)
        done.titleLabel?.adjustsFontForContentSizeCategory = true
        done.setTitleColor(UIColor(white: 1, alpha: 0.8), for: .normal)
        done.heightAnchor.constraint(greaterThanOrEqualToConstant: 44).isActive = true
        done.addTarget(self, action: #selector(doneTapped), for: .touchUpInside)
        stack.addArrangedSubview(done)

        /* The stack sits in a scroll view that is exactly as tall as the stack, until the card would
           pass the top of the screen; then it scrolls. */
        let scroll = UIScrollView()
        scroll.translatesAutoresizingMaskIntoConstraints = false
        scroll.alwaysBounceVertical = false
        scroll.showsHorizontalScrollIndicator = false
        scroll.addSubview(stack)
        box.addSubview(scroll)
        view.addSubview(box)
        let fit = scroll.frameLayoutGuide.heightAnchor.constraint(equalTo: stack.heightAnchor)
        fit.priority = .defaultHigh
        NSLayoutConstraint.activate([
            scroll.topAnchor.constraint(equalTo: box.topAnchor, constant: 22),
            scroll.leadingAnchor.constraint(equalTo: box.leadingAnchor, constant: 20),
            scroll.trailingAnchor.constraint(equalTo: box.trailingAnchor, constant: -20),
            scroll.bottomAnchor.constraint(equalTo: box.bottomAnchor, constant: -12),
            stack.topAnchor.constraint(equalTo: scroll.contentLayoutGuide.topAnchor),
            stack.leadingAnchor.constraint(equalTo: scroll.contentLayoutGuide.leadingAnchor),
            stack.trailingAnchor.constraint(equalTo: scroll.contentLayoutGuide.trailingAnchor),
            stack.bottomAnchor.constraint(equalTo: scroll.contentLayoutGuide.bottomAnchor),
            stack.widthAnchor.constraint(equalTo: scroll.frameLayoutGuide.widthAnchor),
            fit,
            box.topAnchor.constraint(greaterThanOrEqualTo: view.safeAreaLayoutGuide.topAnchor, constant: 12),
            box.leadingAnchor.constraint(equalTo: view.safeAreaLayoutGuide.leadingAnchor, constant: 12),
            box.trailingAnchor.constraint(equalTo: view.safeAreaLayoutGuide.trailingAnchor, constant: -12),
            box.bottomAnchor.constraint(equalTo: view.safeAreaLayoutGuide.bottomAnchor, constant: -12),
        ])
        /* VoiceOver and Switch Control stay inside the card while it is up. */
        box.accessibilityViewIsModal = true
        card = box
        /* NATIVE-A-R5-01 (build 30): a card replaces whatever VoiceOver was on (the host app, or
           the card before it), so VoiceOver is told the screen changed and starts at its title. */
        UIAccessibility.post(notification: .screenChanged, argument: t)
    }

    /* Done on the no-link card cancels the request, as its timed close did (finish). */
    @objc private func doneTapped() { finish(error: link == nil) }

    /* Walk every attachment of every input item. URL first (Instagram, Safari), then
       plain text with a link inside (TikTok). First hit wins. GAPS-KIT-R5-01/03 (build 30): the link
       is the one the page itself would scan from the same text (SharedInbox.shareLink). */
    private func extractLink(_ done: @escaping (String?) -> Void) {
        let items = (extensionContext?.inputItems as? [NSExtensionItem]) ?? []
        let providers = items.flatMap { $0.attachments ?? [] }
        var queue = providers
        func next() {
            guard !queue.isEmpty else { DispatchQueue.main.async { done(nil) }; return }
            let p = queue.removeFirst()
            if p.hasItemConformingToTypeIdentifier(UTType.url.identifier) {
                p.loadItem(forTypeIdentifier: UTType.url.identifier, options: nil) { item, _ in
                    if let u = item as? URL {
                        let s = u.absoluteString
                        DispatchQueue.main.async { done(SharedInbox.shareLink(in: s) ?? s) }
                    }
                    else if let d = item as? Data, let s = String(data: d, encoding: .utf8), let l = SharedInbox.shareLink(in: s) {
                        DispatchQueue.main.async { done(l) }
                    } else { next() }
                }
            } else if p.hasItemConformingToTypeIdentifier(UTType.plainText.identifier) {
                p.loadItem(forTypeIdentifier: UTType.plainText.identifier, options: nil) { item, _ in
                    var text: String? = item as? String
                    if text == nil, let a = item as? NSAttributedString { text = a.string }
                    if text == nil, let d = item as? Data { text = String(data: d, encoding: .utf8) }
                    if let t = text, let l = SharedInbox.shareLink(in: t) { DispatchQueue.main.async { done(l) } }
                    else { next() }
                }
            } else {
                next()
            }
        }
        next()
    }

    private func finish(error: Bool) {
        guard !finished else { return }
        finished = true
        NotificationCenter.default.removeObserver(self)
        if error {
            /* No link in the share: cancel so the host app is not left waiting. */
            extensionContext?.cancelRequest(withError: NSError(domain: "com.addify.share", code: 1,
                                                                userInfo: [NSLocalizedDescriptionKey: "No TikTok or Instagram link in the share"]))
        } else {
            extensionContext?.completeRequest(returningItems: nil, completionHandler: nil)
        }
    }
}
