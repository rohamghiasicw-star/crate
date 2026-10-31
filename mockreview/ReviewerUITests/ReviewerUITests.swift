import XCTest
import UIKit
import ObjectiveC

/* =====================================================================================
   ADDIFY MOCK APP REVIEW
   Drives the installed com.addify.app the way App Review does on a fresh install: launch,
   onboarding, paste a sample link from the Review Notes, scan, result, Profile, the paywall,
   Restore Purchases, the legal links, listen mode's permission prompt, and the
   addify://scan deep link the Share Extension uses.

   The workflow passes settings as TEST_RUNNER_MR_* environment variables (xcodebuild hands
   them to this process without the TEST_RUNNER_ prefix). Every step writes a screenshot and a
   line to steps.jsonl / steps.log in MR_OUT, which is a folder on the runner's disk.

   Nothing here asserts on wording the page might change tomorrow except the labels a
   reviewer would read. A step that fails is logged and the walk carries on, so one broken
   screen still yields a full report. The test fails at the end if any step failed.
   ===================================================================================== */

struct MRConfig {
    let out: URL
    let device: String
    let scans: Bool
    let sample: String
    let engine: String
    let noIdle: Bool
    let resultTimeout: TimeInterval

    static let current: MRConfig = {
        let e = ProcessInfo.processInfo.environment
        let out = URL(fileURLWithPath: e["MR_OUT"] ?? (NSTemporaryDirectory() + "mockreview"))
        try? FileManager.default.createDirectory(at: out, withIntermediateDirectories: true)
        return MRConfig(out: out,
                        device: e["MR_DEVICE"] ?? "device",
                        scans: e["MR_SCANS"] == "1",
                        sample: e["MR_SAMPLE"] ?? "https://vt.tiktok.com/ZSXWjGrqT/",
                        engine: e["MR_ENGINE"] ?? "https://161-35-185-80.sslip.io",
                        noIdle: (e["MR_NO_IDLE"] ?? "1") == "1",
                        resultTimeout: TimeInterval(e["MR_RESULT_TIMEOUT"] ?? "150") ?? 150)
    }()
}

/* XCUITest waits for the app to go "idle" before every query and tap. A page with CSS
   animations running (the scan dial, the onboarding fades) can hold that wait for a long
   time. Every wait in this file is explicit, so the implicit one is switched off. */
enum Quiescence {
    static var done = false
    static func disable() {
        guard !done, let cls = NSClassFromString("XCUIApplicationProcess") else { return }
        done = true
        let one = NSSelectorFromString("waitForQuiescenceIncludingAnimationsIdle:")
        if let m = class_getInstanceMethod(cls, one) {
            let b: @convention(block) (AnyObject, Bool) -> Void = { _, _ in }
            method_setImplementation(m, imp_implementationWithBlock(b))
        }
        let two = NSSelectorFromString("waitForQuiescenceIncludingAnimationsIdle:isPreEvent:")
        if let m = class_getInstanceMethod(cls, two) {
            let b: @convention(block) (AnyObject, Bool, Bool) -> Void = { _, _, _ in }
            method_setImplementation(m, imp_implementationWithBlock(b))
        }
        print("MR| quiescence waits disabled")
    }
}

final class StepLog {
    let cfg: MRConfig
    let phase: String
    let t0 = Date()
    private(set) var failures: [String] = []

    init(cfg: MRConfig, phase: String) { self.cfg = cfg; self.phase = phase }

    func line(_ step: String, _ result: String, shot: String?, _ note: String) {
        let t = Date().timeIntervalSince(t0)
        if result == "FAIL" { failures.append("\(step): \(note)") }
        let rec: [String: Any] = ["device": cfg.device, "phase": phase, "t": (t * 10).rounded() / 10,
                                  "step": step, "result": result, "shot": shot ?? "", "note": note,
                                  "at": ISO8601DateFormatter().string(from: Date())]
        if let d = try? JSONSerialization.data(withJSONObject: rec), let s = String(data: d, encoding: .utf8) {
            append(s + "\n", to: "steps.jsonl")
        }
        let text = String(format: "%@ | %@ | t+%6.1fs | %-22@ | %-4@ | %@ | %@",
                          cfg.device, phase, t, step as NSString, result as NSString, shot ?? "-", note)
        append(text + "\n", to: "steps.log")
        print("MR| " + text)
    }

    func append(_ s: String, to name: String) {
        let url = cfg.out.appendingPathComponent(name)
        let data = Data(s.utf8)
        if let h = try? FileHandle(forWritingTo: url) {
            h.seekToEndOfFile(); h.write(data); try? h.close()
        } else {
            try? data.write(to: url)
        }
    }
}

final class ReviewerUITests: XCTestCase {
    let cfg = MRConfig.current
    let app = XCUIApplication(bundleIdentifier: "com.addify.app")
    let springboard = XCUIApplication(bundleIdentifier: "com.apple.springboard")
    let safari = XCUIApplication(bundleIdentifier: "com.apple.mobilesafari")
    var log: StepLog!
    var shotN = 0

    override func setUp() {
        super.setUp()
        continueAfterFailure = true
        if cfg.noIdle { Quiescence.disable() }
    }

    // =================================================================================
    // MARK: - The three walks
    // =================================================================================

    /// iPhone: the full reviewer path on a fresh install that the workflow already launched.
    func testIPhoneReviewerPath() {
        log = StepLog(cfg: cfg, phase: "path")
        attach()
        launchCheck()
        onboarding()
        let pasted = pasteAndMaybeScan()
        if cfg.scans {
            if pasted { waitForResult(stepName: "scan-result") }
        } else {
            log.line("scan", "SKIP", shot: nil, "MR_SCANS=0: the link was typed, not submitted, so the server did no work")
        }
        goHome()
        otherTabs()
        profile()
        paywall()
        profileLinks()
        deleteAccountPrompt()
        listenMode()
        exportFinds()
        relaunchCheck()
        finish()
    }

    /// iPad: the launch path. Addify 1.0 is an iPhone-only build, so this is the iPhone app
    /// in iPad compatibility mode, which is how App Review opens it on an iPad.
    func testIPadLaunchPath() {
        log = StepLog(cfg: cfg, phase: "ipad")
        attach()
        launchCheck()
        windowGeometry()
        onboarding()
        if cfg.scans {
            if pasteAndMaybeScan() { waitForResult(stepName: "scan-result") }
            goHome()
        }
        profile()
        paywall(short: true)
        finish()
    }

    /// Deep link: the workflow terminated the app and opened addify://scan?url=<sample>
    /// (what the Share Extension does). This walk only watches what happens next.
    func testDeepLinkResult() {
        log = StepLog(cfg: cfg, phase: "deeplink")
        let st = app.state
        let running = st == .runningForeground
        let s = shot("opened")
        let ax = dumpAX("opened")
        log.line("deeplink-open", running ? "PASS" : "FAIL", shot: s,
                 "app state after openurl: \(stateName(st)); ax=\(ax)")
        if !running { app.activate() }
        if let a = systemAlertInfo(timeout: 2) {
            let s2 = shot("deeplink-alert")
            log.line("deeplink-alert", "INFO", shot: s2, "system alert on open: \(a)")
        }
        waitForResult(stepName: "deeplink-result")
        finish()
    }

    // =================================================================================
    // MARK: - Steps
    // =================================================================================

    func attach() {
        let before = app.state
        app.activate()
        let ok = app.wait(for: .runningForeground, timeout: 20)
        log.line("attach", ok ? "PASS" : "FAIL", shot: nil,
                 "state before activate: \(stateName(before)), after: \(stateName(app.state))")
    }

    /// The Oct 15 rejection: "Can't reach Addify" right after launch on a fresh install.
    func launchCheck() {
        let loaded = waitForText(["Find the exact song", "Clip link", "reach Addify", "offline"], timeout: 30)
        let s = shot("launch-check")
        let ax = dumpAX("launch-check")
        let text = screenText()
        if sees("reach Addify", in: text) || sees("You're offline", in: text) || sees("didn't answer", in: text) {
            log.line("launch-check", "FAIL", shot: s, "unreachable screen is up (the 2.1(a) bug). ax=\(ax)")
        } else if loaded != nil {
            log.line("launch-check", "PASS", shot: s, "page loaded, first screen shows '\(loaded!)'. ax=\(ax)")
        } else {
            log.line("launch-check", "FAIL", shot: s, "no onboarding and no home after 30 s: blank or unknown screen. ax=\(ax)")
        }
    }

    func windowGeometry() {
        let w = app.windows.firstMatch
        let f = w.exists ? w.frame : .zero
        let sb = springboard.windows.firstMatch
        let sf = sb.exists ? sb.frame : .zero
        log.line("ipad-window", "INFO", shot: nil,
                 "app window \(Int(f.width))x\(Int(f.height)) pt inside screen \(Int(sf.width))x\(Int(sf.height)) pt")
    }

    func onboarding() {
        guard let setUp = waitForElement("Set it up", exact: true, types: [.button], timeout: 20) else {
            let s = shot("onboarding-missing")
            let home = sees("Clip link")
            log.line("onboarding-1", home ? "WARN" : "FAIL", shot: s,
                     home ? "no onboarding on a fresh install, home showed straight away" : "no 'Set it up' button and no home")
            return
        }
        let s1 = shot("onboarding-1")
        setUp.tap()
        let next = waitForText(["Pin Addify"], timeout: 8)
        log.line("onboarding-1", next != nil ? "PASS" : "FAIL", shot: s1, "tapped 'Set it up'")

        let s2 = shot("onboarding-2")
        guard let later = waitForElement("do this later", exact: false, types: [.button], timeout: 8) else {
            log.line("onboarding-2", "FAIL", shot: s2, "no 'I'll do this later' button")
            return
        }
        later.tap()
        let third = waitForText(["Not set up yet", "Paste a link instead"], timeout: 8)
        log.line("onboarding-2", third != nil ? "PASS" : "FAIL", shot: s2, "tapped 'I'll do this later'")

        let s3 = shot("onboarding-3")
        guard let paste = waitForElement("Paste a link instead", exact: false, types: [.button], timeout: 8) else {
            log.line("onboarding-3", "FAIL", shot: s3, "no 'Paste a link instead' button")
            return
        }
        paste.tap()
        let field = waitForElement("Clip link", exact: true, types: [.textField], timeout: 8)
        Thread.sleep(forTimeInterval: 1.0)
        let s4 = shot("home")
        let pill = firstLabel(containing: "scans left") ?? firstLabel(containing: "scan left") ?? "(no scan counter)"
        log.line("onboarding-3", field != nil ? "PASS" : "FAIL", shot: s3, "tapped 'Paste a link instead'")
        log.line("home", field != nil ? "PASS" : "FAIL", shot: s4,
                 "link box \(field != nil ? "present" : "missing"); counter '\(pill)'; keyboard \(app.keyboards.firstMatch.exists ? "up" : "down")")
    }

    /// Paste the sample link. With scans on, the page's own Paste button reads the clipboard,
    /// iOS shows its Paste callout, and the page starts the scan the moment it gets a link
    /// (crate.html pastebtn.onclick calls run()). With scans off, the clipboard holds plain
    /// text instead, so the same callout is exercised with no server work, and the link is
    /// typed into the box and left unsubmitted.
    @discardableResult
    func pasteAndMaybeScan() -> Bool {
        guard let field = waitForElement("Clip link", exact: true, types: [.textField], timeout: 10) else {
            log.line("paste", "FAIL", shot: shot("no-link-box"), "no link box on home")
            return false
        }
        let clip = cfg.scans ? cfg.sample : "not a link"
        UIPasteboard.general.string = clip
        guard let pbtn = waitForElement("Paste link from clipboard", exact: true, types: [.button], timeout: 5) else {
            log.line("paste-button", "FAIL", shot: shot("no-paste-button"), "no in-page Paste button next to the link box")
            return typeAndSubmit(field)
        }
        pbtn.tap()
        let callout = waitForCallout(timeout: 6)
        if let c = callout {
            let s = shot("paste-callout")
            c.tap()
            log.line("paste-callout", "PASS", shot: s, "iOS showed its Paste callout and it was tapped (clipboard: '\(clip)')")
        } else {
            let s = shot("paste-no-callout")
            log.line("paste-callout", "FAIL", shot: s, "tapped Paste, no iOS Paste callout appeared within 6 s")
        }
        Thread.sleep(forTimeInterval: 1.5)
        if cfg.scans {
            let started = waitForText(["Finding song", "Reading the clip", "Find another song", "reading audio"], timeout: 6)
            let s = shot("paste-started")
            if started != nil {
                log.line("paste-scan-start", "PASS", shot: s, "the paste started a scan on its own ('\(started!)')")
                return true
            }
            log.line("paste-scan-start", "FAIL", shot: s, "paste did not start a scan; falling back to typing the link and tapping Find it")
            return typeAndSubmit(field)
        } else {
            let err = firstLabel(containing: "No TikTok or Instagram link") ?? "(no message)"
            let s = shot("paste-not-a-link")
            log.line("paste-not-a-link", err.hasPrefix("(") ? "WARN" : "PASS", shot: s,
                     "clipboard held plain text; page said: '\(err)'")
            return typeOnly(field)
        }
    }

    func typeOnly(_ field: XCUIElement) -> Bool {
        field.tap()
        Thread.sleep(forTimeInterval: 0.8)
        field.typeText(cfg.sample)
        Thread.sleep(forTimeInterval: 0.8)
        let v = (field.value as? String) ?? ""
        let s = shot("typed-link")
        let ok = v.contains("tiktok") || v.contains("instagram")
        let findIt = waitForElement("Find it", exact: true, types: [.button], timeout: 2) != nil
        log.line("typed-link", ok ? "PASS" : "FAIL", shot: s,
                 "link box value '\(v.prefix(60))'; 'Find it' button \(findIt ? "visible" : "missing")")
        clearField(field)
        return ok
    }

    func typeAndSubmit(_ field: XCUIElement) -> Bool {
        guard typeOnlyKeep(field) else { return false }
        guard let go = waitForElement("Find it", exact: true, types: [.button], timeout: 4) else {
            log.line("find-it", "FAIL", shot: shot("no-find-it"), "no 'Find it' button after typing the link")
            return false
        }
        let kb = app.keyboards.firstMatch
        if kb.exists && kb.frame.intersects(go.frame) {
            if let done = waitForElement("Done", exact: true, types: [.button], timeout: 2) { done.tap() }
            Thread.sleep(forTimeInterval: 0.8)
        }
        go.tap()
        let started = waitForText(["Finding song", "Reading the clip", "reading audio"], timeout: 8)
        log.line("find-it", started != nil ? "PASS" : "FAIL", shot: shot("find-it-tapped"),
                 started != nil ? "scan started" : "tapped 'Find it', no scan screen within 8 s")
        return started != nil
    }

    func typeOnlyKeep(_ field: XCUIElement) -> Bool {
        field.tap()
        Thread.sleep(forTimeInterval: 0.8)
        field.typeText(cfg.sample)
        let v = (field.value as? String) ?? ""
        let ok = v.contains("tiktok") || v.contains("instagram")
        log.line("typed-link", ok ? "PASS" : "FAIL", shot: shot("typed-link"), "link box value '\(v.prefix(60))'")
        return ok
    }

    func clearField(_ field: XCUIElement) {
        let v = (field.value as? String) ?? ""
        guard !v.isEmpty, v != field.placeholderValue else { return }
        field.tap()
        field.typeText(String(repeating: XCUIKeyboardKey.delete.rawValue, count: v.count + 2))
        if let done = waitForElement("Done", exact: true, types: [.button], timeout: 1) { done.tap() }
    }

    /// Waits up to MR_RESULT_TIMEOUT for the scan to end, with a screenshot at fixed marks.
    func waitForResult(stepName: String) {
        let before = serverAdmitted()
        let marks: [TimeInterval] = [5, 15, 30, 60, 90, 120]
        var nextMark = 0
        let t0 = Date()
        var outcome: String? = nil
        var lastHeadline = ""
        while Date().timeIntervalSince(t0) < cfg.resultTimeout {
            let text = screenText()
            if sees("Find another song", in: text) { outcome = "result"; break }
            if sees("Use listen mode instead", in: text) || sees("Back to home", in: text)
                || sees("Back to TikTok", in: text) || sees("Back to Instagram", in: text) { outcome = "no-match"; break }
            if sees("reach Addify", in: text) { outcome = "unreachable"; break }
            if let h = ["Finding song", "Reading the clip", "Checking", "Matching", "Listening"].first(where: { sees($0, in: text) }) {
                lastHeadline = h
            }
            let el = Date().timeIntervalSince(t0)
            if nextMark < marks.count && el >= marks[nextMark] {
                let s = shot("\(stepName)-\(Int(marks[nextMark]))s")
                log.line("\(stepName)-wait", "INFO", shot: s, "t=\(Int(el)) s, screen says '\(lastHeadline)'")
                nextMark += 1
            }
            Thread.sleep(forTimeInterval: 2)
        }
        let secs = Int(Date().timeIntervalSince(t0))
        Thread.sleep(forTimeInterval: 1.5)
        let s = shot(stepName)
        let ax = dumpAX(stepName)
        let after = serverAdmitted()
        let server = "server admitted \(before.map(String.init) ?? "?") -> \(after.map(String.init) ?? "?")"
        let toast = app.staticTexts.matching(NSPredicate(format: "label CONTAINS[c] 'Exact version'")).firstMatch.exists
        switch outcome {
        case "result":
            log.line(stepName, "PASS", shot: s, "song found in \(secs) s; \(server); native toast \(toast ? "shown" : "not seen"); ax=\(ax)")
        case "no-match":
            log.line(stepName, "FAIL", shot: s, "scan ended with NO song after \(secs) s (a reviewer sees no result for a Review Notes link); \(server); ax=\(ax)")
        case "unreachable":
            log.line(stepName, "FAIL", shot: s, "'Can't reach Addify' during the scan after \(secs) s; \(server); ax=\(ax)")
        default:
            log.line(stepName, "FAIL", shot: s, "no result after \(secs) s (spinner never ended); last headline '\(lastHeadline)'; \(server); ax=\(ax)")
        }
    }

    func goHome() {
        for label in ["Back", "Back to home", "Find another song"] {
            if let b = waitForElement(label, exact: true, types: [.button], timeout: 1) { b.tap(); break }
        }
        Thread.sleep(forTimeInterval: 1.2)
        let tab = waitForElement("Home", exact: true, types: [.button], timeout: 6)
        tab?.tap()
        Thread.sleep(forTimeInterval: 1.0)
        let ok = waitForElement("Clip link", exact: true, types: [.textField], timeout: 6) != nil
        log.line("home-again", ok ? "PASS" : "FAIL", shot: shot("home-again"), ok ? "back on home" : "could not get back to home")
    }

    func otherTabs() {
        for (tab, name) in [("Search", "tab-search"), ("Finds", "tab-finds")] {
            guard let b = waitForElement(tab, exact: true, types: [.button], timeout: 5) else {
                log.line(name, "FAIL", shot: shot(name + "-missing"), "no '\(tab)' tab")
                continue
            }
            b.tap()
            Thread.sleep(forTimeInterval: 2.5)
            let s1 = shot(name)
            Thread.sleep(forTimeInterval: 6)
            let text = screenText()
            let s2 = shot(name + "-8s")
            let stuck = sees("Loading", in: text)
            log.line(name, stuck ? "WARN" : "PASS", shot: s1,
                     "opened; after 8 s: \(s2)\(stuck ? " still says Loading" : "")")
        }
    }

    func profile() {
        guard let you = waitForElement("You", exact: true, types: [.button], timeout: 6) else {
            log.line("profile", "FAIL", shot: shot("no-you-tab"), "no 'You' tab (the Review Notes call it Profile)")
            return
        }
        you.tap()
        let got = waitForText(["Get Addify Plus"], timeout: 8)
        Thread.sleep(forTimeInterval: 1)
        let s = shot("profile")
        let ax = dumpAX("profile")
        log.line("profile", got != nil ? "PASS" : "FAIL", shot: s,
                 got != nil ? "Profile shows 'Get Addify Plus'. ax=\(ax)" : "no 'Get Addify Plus' card on Profile. ax=\(ax)")
    }

    func paywall(short: Bool = false) {
        guard let up = waitForElement("Upgrade", exact: true, types: [.button], timeout: 6) else {
            log.line("paywall-open", "FAIL", shot: shot("no-upgrade"), "no 'Upgrade' button on the Get Addify Plus card")
            return
        }
        up.tap()
        Thread.sleep(forTimeInterval: 2)
        let s1 = shot("paywall-2s")
        let opened = sees("Addify Plus") && sees("Restore purchases")
        log.line("paywall-open", opened ? "PASS" : "FAIL", shot: s1, opened ? "paywall sheet is up" : "tapped Upgrade, no paywall")
        guard opened else { return }
        let t0 = Date()
        while Date().timeIntervalSince(t0) < 20 && sees("Loading prices") { Thread.sleep(forTimeInterval: 1.5) }
        let secs = Int(Date().timeIntervalSince(t0)) + 2
        let text = screenText()
        let s2 = shot("paywall-prices")
        let ax = dumpAX("paywall-prices")
        if sees("not available right now", in: text) {
            log.line("paywall-prices", "WARN", shot: s2,
                     "after \(secs) s the paywall says 'Subscriptions are not available right now. Try again later.' and shows NO plans. Expected in an unsigned simulator build; a reviewer sees the same screen whenever StoreKit returns no products. ax=\(ax)")
        } else if sees("Loading prices", in: text) {
            log.line("paywall-prices", "FAIL", shot: s2, "still 'Loading prices...' after \(secs) s. ax=\(ax)")
        } else if sees("Monthly", in: text) || sees("Yearly", in: text) {
            log.line("paywall-prices", "PASS", shot: s2, "plans listed after \(secs) s. ax=\(ax)")
        } else {
            log.line("paywall-prices", "WARN", shot: s2, "unclear price state after \(secs) s. ax=\(ax)")
        }
        if !short {
            openExternalLink("Terms of Use", step: "paywall-terms")
            restore()
        }
        if let x = waitForElement("Close", exact: true, types: [.button], timeout: 4) {
            x.tap()
            Thread.sleep(forTimeInterval: 1)
            let gone = !sees("Restore purchases")
            log.line("paywall-close", gone ? "PASS" : "FAIL", shot: shot("paywall-closed"), gone ? "closed with X" : "X did not close the paywall")
        } else {
            log.line("paywall-close", "FAIL", shot: shot("paywall-no-x"), "no Close button on the paywall")
        }
    }

    func restore() {
        guard let r = waitForElement("Restore purchases", exact: true, types: [.button], timeout: 4) else {
            log.line("restore", "FAIL", shot: shot("no-restore"), "no 'Restore purchases' button")
            return
        }
        r.tap()
        Thread.sleep(forTimeInterval: 2)
        let s1 = shot("restore-2s")
        var alertNote = "no system prompt"
        let t0 = Date()
        var message = ""
        while Date().timeIntervalSince(t0) < 30 {
            if let a = systemAlertInfo(timeout: 0.5) {
                alertNote = "system prompt: \(a)"
                let sa = shot("restore-system-prompt")
                log.line("restore-prompt", "INFO", shot: sa, alertNote)
                tapAlertButton(preferring: ["Cancel", "Not Now", "Close", "OK"])
                Thread.sleep(forTimeInterval: 1)
            }
            let text = screenText()
            if sees("No active subscription", in: text) { message = "No active subscription found on this Apple ID."; break }
            if sees("Could not reach the App Store", in: text) { message = "Could not reach the App Store. Try again."; break }
            if !sees("Restore purchases", in: text) { message = "(paywall closed itself)"; break }
            Thread.sleep(forTimeInterval: 1.5)
        }
        let secs = Int(Date().timeIntervalSince(t0)) + 2
        let s2 = shot("restore-result")
        log.line("restore", message.isEmpty ? "FAIL" : "INFO", shot: s2,
                 message.isEmpty ? "no answer after \(secs) s ('Checking your Apple ID...' never ended?); \(alertNote); first shot \(s1)"
                                 : "after \(secs) s the page said '\(message)'; \(alertNote); first shot \(s1)")
    }

    func profileLinks() {
        openExternalLink("Privacy policy", step: "profile-privacy")
        openExternalLink("Support", step: "profile-support")
    }

    /// Links with target=_blank go to Safari (EngineWebView createWebViewWith -> openExternal).
    func openExternalLink(_ label: String, step: String) {
        guard let l = waitForElement(label, exact: false, types: [.link, .button], timeout: 5) else {
            log.line(step, "FAIL", shot: shot(step + "-missing"), "no '\(label)' link")
            return
        }
        l.tap()
        let inSafari = safari.wait(for: .runningForeground, timeout: 8)
        Thread.sleep(forTimeInterval: 3)
        let s = shot(step)
        var page = ""
        if inSafari {
            let d = safari.debugDescription
            try? d.write(to: cfg.out.appendingPathComponent("\(cfg.device)-\(log.phase)-\(step).safari.ax.txt"), atomically: true, encoding: .utf8)
            page = d.range(of: "sslip.io") != nil ? "engine page" : "unknown page"
        }
        log.line(step, inSafari ? "PASS" : "FAIL", shot: s,
                 inSafari ? "'\(label)' opened in Safari (\(page)); reviewer leaves the app here" : "tapped '\(label)', Safari did not come up in 8 s")
        app.activate()
        _ = app.wait(for: .runningForeground, timeout: 8)
        Thread.sleep(forTimeInterval: 1)
    }

    func deleteAccountPrompt() {
        guard let d = waitForElement("Delete account", exact: true, types: [.button], timeout: 5) else {
            log.line("delete-account", "WARN", shot: shot("no-delete"), "no 'Delete account' row on Profile")
            return
        }
        d.tap()
        let alert = app.alerts.firstMatch
        if alert.waitForExistence(timeout: 5) {
            let text = alert.staticTexts.allElementsBoundByIndex.map { $0.label }.joined(separator: " | ")
            let buttons = alert.buttons.allElementsBoundByIndex.map { $0.label }
            let s = shot("delete-account-confirm")
            log.line("delete-account", "INFO", shot: s, "confirm says '\(text)', buttons \(buttons); tapped Cancel")
            if alert.buttons["Cancel"].exists { alert.buttons["Cancel"].tap() } else { alert.buttons.firstMatch.tap() }
        } else {
            log.line("delete-account", "FAIL", shot: shot("delete-account-nothing"), "tapped 'Delete account', no confirmation appeared")
        }
        Thread.sleep(forTimeInterval: 1)
    }

    /// The big SCAN dial with an empty link box starts listen mode: the microphone prompt.
    /// Answered "Don't Allow" on purpose: it records the prompt wording and the denied path
    /// without sending audio to the server.
    func listenMode() {
        if let home = waitForElement("Home", exact: true, types: [.button], timeout: 5) { home.tap() }
        Thread.sleep(forTimeInterval: 1)
        if let f = waitForElement("Clip link", exact: true, types: [.textField], timeout: 4) {
            let v = (f.value as? String) ?? ""
            if !v.isEmpty && v != f.placeholderValue {
                log.line("listen", "SKIP", shot: shot("listen-box-not-empty"), "link box still holds '\(v.prefix(40))'; the dial would scan it, so listen was not tapped")
                return
            }
        }
        guard let orb = waitForElement("Scan", exact: true, types: [.button], timeout: 5) else {
            log.line("listen", "FAIL", shot: shot("no-scan-dial"), "no SCAN dial on home")
            return
        }
        orb.tap()
        if let a = systemAlertInfo(timeout: 8) {
            let s = shot("mic-prompt")
            log.line("mic-prompt", "INFO", shot: s, "\(a)")
            tapAlertButton(preferring: ["Don’t Allow", "Don't Allow"])
            Thread.sleep(forTimeInterval: 2.5)
            let msg = firstLabel(containing: "Microphone") ?? firstLabel(containing: "mic") ?? "(no message)"
            let s2 = shot("mic-denied")
            log.line("mic-denied", msg.hasPrefix("(") ? "FAIL" : "PASS", shot: s2, "after Don't Allow the page says '\(msg)'")
        } else {
            let s = shot("listen-no-prompt")
            log.line("mic-prompt", "WARN", shot: s, "tapped SCAN, no microphone prompt within 8 s; screen: '\(firstLabel(containing: "Listen") ?? "")'")
        }
        Thread.sleep(forTimeInterval: 2)
        let s3 = shot("after-listen")
        log.line("after-listen", "INFO", shot: s3, "screen after the listen attempt")
    }

    /// "Export finds" builds a blob: link with a download attribute. The web view has no
    /// download handling, so this records what a reviewer actually gets.
    func exportFinds() {
        if let you = waitForElement("You", exact: true, types: [.button], timeout: 5) { you.tap() }
        Thread.sleep(forTimeInterval: 1.2)
        guard let ex = waitForElement("Export finds", exact: false, types: [.button], timeout: 5) else {
            log.line("export-finds", "WARN", shot: shot("no-export"), "no 'Export finds' row")
            return
        }
        let finds = firstLabel(containing: "in your Finds") ?? ""
        ex.tap()
        Thread.sleep(forTimeInterval: 3)
        let s = shot("export-finds")
        let ax = dumpAX("export-finds")
        let stillApp = waitForElement("You", exact: true, types: [.button], timeout: 1) != nil
        let sheet = app.otherElements["ActivityListView"].exists || app.sheets.firstMatch.exists
        if !stillApp {
            log.line("export-finds", "FAIL", shot: s, "the app page is gone after tapping Export finds (tabs missing): the web view navigated away. ax=\(ax)")
        } else if sheet {
            log.line("export-finds", "PASS", shot: s, "a share or save sheet opened. ax=\(ax)")
        } else {
            log.line("export-finds", "WARN", shot: s, "tapped Export finds and nothing visible happened \(finds). A reviewer reads that as a dead button. ax=\(ax)")
        }
    }

    func relaunchCheck() {
        app.terminate()
        Thread.sleep(forTimeInterval: 1)
        app.launch()
        let loaded = waitForText(["Clip link", "Find the exact song", "reach Addify"], timeout: 30)
        let s = shot("relaunch")
        let text = screenText()
        let bad = sees("reach Addify", in: text)
        let onb = sees("Find the exact song", in: text)
        log.line("relaunch", bad || loaded == nil ? "FAIL" : (onb ? "WARN" : "PASS"), shot: s,
                 bad ? "'Can't reach Addify' on the second launch" : loaded == nil ? "nothing loaded on the second launch" :
                 onb ? "onboarding showed again on the second launch" : "second launch opens on home")
    }

    func finish() {
        let fails = log.failures
        log.line("done", fails.isEmpty ? "PASS" : "FAIL", shot: nil, "\(fails.count) failed step(s)")
        if !fails.isEmpty { XCTFail("Mock review failures: " + fails.joined(separator: " || ")) }
    }

    // =================================================================================
    // MARK: - Helpers
    // =================================================================================

    func stateName(_ s: XCUIApplication.State) -> String {
        switch s {
        case .unknown: return "unknown"
        case .notRunning: return "notRunning"
        case .runningBackgroundSuspended: return "backgroundSuspended"
        case .runningBackground: return "background"
        case .runningForeground: return "foreground"
        @unknown default: return "other"
        }
    }

    func screenText() -> String { app.debugDescription }

    func sees(_ s: String, in text: String? = nil) -> Bool {
        (text ?? screenText()).range(of: s, options: [.caseInsensitive, .diacriticInsensitive]) != nil
    }

    func waitForText(_ any: [String], timeout: TimeInterval) -> String? {
        let t0 = Date()
        repeat {
            let text = screenText()
            if let hit = any.first(where: { sees($0, in: text) }) { return hit }
            Thread.sleep(forTimeInterval: 1)
        } while Date().timeIntervalSince(t0) < timeout
        return nil
    }

    func waitForElement(_ label: String, exact: Bool, types: [XCUIElement.ElementType], timeout: TimeInterval) -> XCUIElement? {
        let p = exact ? NSPredicate(format: "label ==[c] %@", label) : NSPredicate(format: "label CONTAINS[c] %@", label)
        let t0 = Date()
        repeat {
            for t in types {
                let e = app.descendants(matching: t).matching(p).firstMatch
                if e.exists { return e }
            }
            if timeout <= 0 { break }
            Thread.sleep(forTimeInterval: 0.7)
        } while Date().timeIntervalSince(t0) < timeout
        return nil
    }

    func firstLabel(containing s: String) -> String? {
        let p = NSPredicate(format: "label CONTAINS[c] %@", s)
        let e = app.descendants(matching: .any).matching(p).firstMatch
        return e.exists ? e.label : nil
    }

    /// The iOS edit-menu "Paste" that WebKit raises for navigator.clipboard.readText().
    func waitForCallout(timeout: TimeInterval) -> XCUIElement? {
        let t0 = Date()
        repeat {
            for e in [app.menuItems["Paste"], app.buttons["Paste"], springboard.menuItems["Paste"], springboard.buttons["Paste"]] {
                if e.exists { return e }
            }
            Thread.sleep(forTimeInterval: 0.5)
        } while Date().timeIntervalSince(t0) < timeout
        return nil
    }

    func systemAlertInfo(timeout: TimeInterval) -> String? {
        let a = springboard.alerts.firstMatch
        guard a.waitForExistence(timeout: timeout) else { return nil }
        let texts = a.staticTexts.allElementsBoundByIndex.map { $0.label }.filter { !$0.isEmpty }
        let buttons = a.buttons.allElementsBoundByIndex.map { $0.label }
        return "title '\(a.label)'; text '\(texts.joined(separator: " | "))'; buttons \(buttons)"
    }

    func tapAlertButton(preferring labels: [String]) {
        let a = springboard.alerts.firstMatch
        guard a.exists else { return }
        for l in labels where a.buttons[l].exists { a.buttons[l].tap(); return }
        a.buttons.firstMatch.tap()
    }

    @discardableResult
    func shot(_ name: String) -> String {
        shotN += 1
        let file = String(format: "%@-%@-%02d-%@.png", cfg.device, log.phase, shotN, name)
        let s = XCUIScreen.main.screenshot()
        do { try s.pngRepresentation.write(to: cfg.out.appendingPathComponent(file)) }
        catch { print("MR| could not write \(file): \(error)") }
        let a = XCTAttachment(screenshot: s)
        a.name = file
        a.lifetime = .keepAlways
        add(a)
        return file
    }

    @discardableResult
    func dumpAX(_ name: String) -> String {
        let file = String(format: "%@-%@-%02d-%@.ax.txt", cfg.device, log.phase, shotN, name)
        var text = "== com.addify.app, state \(stateName(app.state))\n" + app.debugDescription
        let alert = springboard.alerts.firstMatch
        if alert.exists { text += "\n\n== system alert\n" + alert.debugDescription }
        do { try text.write(to: cfg.out.appendingPathComponent(file), atomically: true, encoding: .utf8) }
        catch { print("MR| could not write \(file): \(error)") }
        return file
    }

    /// GET /health on the live engine, read like any user would. served.admitted counts the
    /// scans the server let in, so a delta proves the scan reached the server.
    func serverAdmitted() -> Int? {
        guard let url = URL(string: cfg.engine + "/health") else { return nil }
        let sem = DispatchSemaphore(value: 0)
        var result: Int? = nil
        var req = URLRequest(url: url)
        req.timeoutInterval = 10
        URLSession.shared.dataTask(with: req) { data, _, _ in
            if let d = data,
               let j = try? JSONSerialization.jsonObject(with: d) as? [String: Any],
               let s = j["server"] as? [String: Any], let g = s["gate"] as? [String: Any],
               let sv = g["served"] as? [String: Any] {
                result = sv["admitted"] as? Int
            }
            sem.signal()
        }.resume()
        _ = sem.wait(timeout: .now() + 12)
        return result
    }
}
