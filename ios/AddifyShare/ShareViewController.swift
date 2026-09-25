import UIKit
import UniformTypeIdentifiers

/* ---------- the icon in the share sheet ----------
   This target is what puts Addify in the TikTok / Instagram "Share to" row. It is a plain
   UIViewController rather than SLComposeServiceViewController on purpose: the compose
   sheet asks the user to type a caption and tap Post, and a share is already the user
   saying "this one". Nothing visible is drawn; the extension grabs the link, parks it in
   the app group, opens the app and closes itself.

   Delivery is two-layered because the direct open is not guaranteed:
   1. SharedInbox.write(link)          - always works (app group)
   2. open addify://scan?url=<link>    - responder-chain trick, best effort
   The app drains the inbox on every activation, so if step 2 fails the scan starts the
   next time the user opens Addify. */
final class ShareViewController: UIViewController {

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
        extractLink { [weak self] link in
            guard let self = self else { return }
            guard let link = link, SharedInbox.isScannable(link) else {
                self.finish(error: true)
                return
            }
            SharedInbox.write(link)
            var comps = URLComponents()
            comps.scheme = "addify"
            comps.host = "scan"
            comps.queryItems = [URLQueryItem(name: "url", value: link)]
            if let u = comps.url { _ = self.openHostApp(u) }
            /* Do not complete the request the instant the open is fired. completeRequest
               tears this extension process down, and on current iOS that teardown can land
               before the system has routed the addify:// open to the app, so the app never
               comes forward and the whole thing reads as "white flash, nothing happened". A
               short beat lets the launch take hold. If the open is blocked anyway the inbox
               still delivers the link the next time Addify is opened. */
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) {
                self.finish(error: false)
            }
        }
    }

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
       extensions, only .shared is not. The inbox stays as the backup path. */
    @objc private func openHostApp(_ url: URL) -> Bool {
        var responder: UIResponder? = self
        while let r = responder {
            if let application = r as? UIApplication {
                application.open(url, options: [:], completionHandler: nil)
                return true
            }
            responder = r.next
        }
        return false
    }

    private func finish(error: Bool) {
        if error {
            /* No link in the share: cancel so the host app is not left waiting. */
            extensionContext?.cancelRequest(withError: NSError(domain: "com.addify.share", code: 1,
                                                                userInfo: [NSLocalizedDescriptionKey: "No TikTok or Instagram link in the share"]))
        } else {
            extensionContext?.completeRequest(returningItems: nil, completionHandler: nil)
        }
    }
}
