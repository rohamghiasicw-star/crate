import UIKit
import UniformTypeIdentifiers

/* ---------- the icon in the share sheet ----------
   This target is what puts Addify in the TikTok / Instagram "Share to" row. It is a plain
   UIViewController rather than SLComposeServiceViewController on purpose: the compose
   sheet asks the user to type a caption and tap Post, and a share is already the user
   saying "this one". When the open works nothing is drawn: the extension grabs the link,
   parks it in the app group, opens the app and closes itself. When iOS does not open the
   app, a small dark card says so and offers an Open Addify button, so a share is never
   silently swallowed.

   Delivery is two-layered because the direct open is not guaranteed:
   1. SharedInbox.write(link)          - always works (app group)
   2. open addify://scan?url=<link>    - responder-chain trick, best effort
   The app drains the inbox on every activation, so if step 2 fails the scan starts the
   next time the user opens Addify. */
final class ShareViewController: UIViewController {
    private var started = false
    private var finished = false
    private var link: String?
    private var attempt = 0
    private var answeredAttempt = 0
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
                   anything silent". */
                self.showCard(title: "No TikTok or Instagram link",
                              body: "Addify scans TikTok and Instagram videos, and this share did not include one.",
                              primary: nil)
                DispatchQueue.main.asyncAfter(deadline: .now() + 2.5) { self.finish(error: true) }
                return
            }
            self.link = link
            SharedInbox.write(link)
            self.openAddify(afterTap: false)
        }
    }

    /* Try to bring Addify forward with addify://scan?url=<link>. iOS answers through the
       completion handler: true means the app is on its way, so this card closes. False,
       no application object, or no answer at all within 1.5 s means the open did not
       happen, and the card says so with a button instead of closing silently (which is
       exactly what "I click addify and nothing happens" looked like). The link is already
       in the inbox, so the scan still starts the next time Addify opens. */
    private func openAddify(afterTap: Bool) {
        guard !finished, let link = link else { return }
        var comps = URLComponents()
        comps.scheme = "addify"
        comps.host = "scan"
        comps.queryItems = [URLQueryItem(name: "url", value: link)]
        guard let url = comps.url else { showFallback(afterTap: afterTap); return }
        attempt += 1
        let mine = attempt
        let fired = openHostApp(url) { [weak self] ok in
            DispatchQueue.main.async {
                guard let self = self, !self.finished, mine == self.attempt else { return }
                self.answeredAttempt = mine
                if ok {
                    self.finish(error: false)
                } else {
                    self.showFallback(afterTap: afterTap)
                }
            }
        }
        if !fired { showFallback(afterTap: afterTap); return }
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) { [weak self] in
            guard let self = self, !self.finished, self.answeredAttempt != mine, mine == self.attempt else { return }
            /* If the app already drained the inbox it got the link, whatever iOS said. */
            if !SharedInbox.hasPending { self.finish(error: false) } else { self.showFallback(afterTap: afterTap) }
        }
    }

    /* The user came back to TikTok or Instagram after Addify took over. If the app drained
       the inbox, the share was delivered and this card has nothing left to say. */
    @objc private func hostWillEnterForeground() {
        guard !finished, link != nil else { return }
        if !SharedInbox.hasPending { finish(error: false) }
    }

    private func showFallback(afterTap: Bool) {
        guard !finished else { return }
        if !SharedInbox.hasPending { finish(error: false); return }
        if afterTap {
            showCard(title: "Open Addify to scan it",
                     body: "iOS did not let Addify open from here. Open Addify from your home screen and this video starts scanning right away.",
                     primary: nil)
        } else {
            showCard(title: "Addify has this video",
                     body: "Tap Open Addify to scan it now.",
                     primary: "Open Addify")
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

        let t = UILabel()
        t.text = title
        t.font = .systemFont(ofSize: 19, weight: .semibold)
        t.textColor = .white
        t.numberOfLines = 0

        let b = UILabel()
        b.text = body
        b.font = .systemFont(ofSize: 15)
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
            go.titleLabel?.font = .systemFont(ofSize: 17, weight: .semibold)
            go.setTitleColor(.white, for: .normal)
            go.backgroundColor = purple
            go.layer.cornerRadius = 14
            go.layer.cornerCurve = .continuous
            go.heightAnchor.constraint(equalToConstant: 52).isActive = true
            go.addTarget(self, action: #selector(openTapped), for: .touchUpInside)
            stack.addArrangedSubview(go)
        }
        let done = UIButton(type: .system)
        done.setTitle("Done", for: .normal)
        done.titleLabel?.font = .systemFont(ofSize: 16, weight: .medium)
        done.setTitleColor(UIColor(white: 1, alpha: 0.8), for: .normal)
        done.heightAnchor.constraint(equalToConstant: 44).isActive = true
        done.addTarget(self, action: #selector(doneTapped), for: .touchUpInside)
        stack.addArrangedSubview(done)

        box.addSubview(stack)
        view.addSubview(box)
        NSLayoutConstraint.activate([
            stack.topAnchor.constraint(equalTo: box.topAnchor, constant: 22),
            stack.leadingAnchor.constraint(equalTo: box.leadingAnchor, constant: 20),
            stack.trailingAnchor.constraint(equalTo: box.trailingAnchor, constant: -20),
            stack.bottomAnchor.constraint(equalTo: box.bottomAnchor, constant: -12),
            box.leadingAnchor.constraint(equalTo: view.safeAreaLayoutGuide.leadingAnchor, constant: 12),
            box.trailingAnchor.constraint(equalTo: view.safeAreaLayoutGuide.trailingAnchor, constant: -12),
            box.bottomAnchor.constraint(equalTo: view.safeAreaLayoutGuide.bottomAnchor, constant: -12),
        ])
        card = box
    }

    /* A real tap: the retry runs with a user gesture behind it. */
    @objc private func openTapped() { openAddify(afterTap: true) }
    @objc private func doneTapped() { finish(error: false) }

    /* Walk every attachment of every input item. URL first (Instagram, Safari), then
       plain text with a link inside (TikTok). First hit wins. */
    private func extractLink(_ done: @escaping (String?) -> Void) {
        let items = (extensionContext?.inputItems as? [NSExtensionItem]) ?? []
        let providers = items.flatMap { $0.attachments ?? [] }
        var queue = providers
        func next() {
            guard !queue.isEmpty else { DispatchQueue.main.async { done(nil) }; return }
            let p = queue.removeFirst()
            if p.hasItemConformingToTypeIdentifier(UTType.url.identifier) {
                p.loadItem(forTypeIdentifier: UTType.url.identifier, options: nil) { item, _ in
                    if let u = item as? URL { DispatchQueue.main.async { done(u.absoluteString) } }
                    else if let d = item as? Data, let s = String(data: d, encoding: .utf8), let l = SharedInbox.firstURL(in: s) {
                        DispatchQueue.main.async { done(l) }
                    } else { next() }
                }
            } else if p.hasItemConformingToTypeIdentifier(UTType.plainText.identifier) {
                p.loadItem(forTypeIdentifier: UTType.plainText.identifier, options: nil) { item, _ in
                    var text: String? = item as? String
                    if text == nil, let a = item as? NSAttributedString { text = a.string }
                    if text == nil, let d = item as? Data { text = String(data: d, encoding: .utf8) }
                    if let t = text, let l = SharedInbox.firstURL(in: t) { DispatchQueue.main.async { done(l) } }
                    else { next() }
                }
            } else {
                next()
            }
        }
        next()
    }

    /* Extensions have no UIApplication.shared, so there is no sanctioned way to open the
       host app from a Share Extension. The responder chain still ends in the extension's
       UIApplication object, and asking THAT object to open the URL is the workaround every
       share-to-app extension ships.

       iOS 18 CHANGED THE RULES (2026-09-25, Roham: "the share extension doesn't work").
       The old call here was perform(openURL:) on that object. From iOS 18 the deprecated
       openURL: is a no-op that always returns NO and logs "The caller of
       UIApplication.openURL(_:) needs to migrate to the non-deprecated
       UIApplication.open(_:options:completionHandler:). Force returning false (NO)." So the
       share sheet grabbed the link, parked it in the inbox, and Addify never came forward:
       exactly what testers saw. Casting the responder to UIApplication and calling the
       modern open(_:options:completionHandler:) is the fix; the type is available to
       extensions, only .shared is not. The inbox stays as the backup path.

       Returns false when no application object was found (nothing was attempted), and
       hands iOS's own yes/no to `answer` when the open was attempted. */
    private func openHostApp(_ url: URL, answer: @escaping (Bool) -> Void) -> Bool {
        guard let application = hostApplication() else { return false }
        application.open(url, options: [:], completionHandler: answer)
        return true
    }

    /* The responder chain first. If this controller is not in a window yet the chain stops
       short of the application, so fall back to asking the class for its shared instance
       through the runtime, the route reported working on iOS 18 in Apple Developer Forums
       thread 779644 (April 2025). Same object either way. */
    private func hostApplication() -> UIApplication? {
        var responder: UIResponder? = self
        while let r = responder {
            if let application = r as? UIApplication { return application }
            responder = r.next
        }
        let shared = NSSelectorFromString("sharedApplication")
        guard UIApplication.responds(to: shared),
              let obj = UIApplication.perform(shared)?.takeUnretainedValue() as? UIApplication else { return nil }
        return obj
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
