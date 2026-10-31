import XCTest
import UIKit

/* =====================================================================================
   REVIEW-SIM, DEVICE-PATH LANE (2026-10-31)
   Every path a reviewer can take through build 20, on the iPhone 17 Pro Max and the
   iPad Air 11-inch (M3) simulators (the iPhone app in iPad compatibility mode).

   The app is pointed at the TEST box, never the live server: each launch passes the app's own
   address override (-engineBaseURL, see sim.sh) and the workflow blocks the live host in
   /etc/hosts. Scans are budgeted (MR_SCAN_BUDGET per walk) and wait until the test box reports
   no scan running, so this lane never scans in parallel with another lane.

   Feedback and report controls are never tapped (they ping the owners).

   Every step writes a screenshot and a line to steps.jsonl / steps.log. A failed step is
   logged and the walk carries on; no XCUIElement is touched without an existence check, so a
   missing element can no longer end the walk (run 41184130948 died that way).
   ===================================================================================== */

extension ReviewerUITests {

    // =================================================================================
    // MARK: - Walks (each one is called by the workflow with -only-testing)
    // =================================================================================

    /// Fresh install, no scans: launch, onboarding (later branch), every paste path that costs no
    /// scan, Search, Trending and its chips, Finds empty and after a save, every Profile row,
    /// the paywall with no StoreKit products, Restore, the legal links, Export finds, listen mode
    /// with the microphone denied, light and dark, the page theme, a relaunch.
    @objc func testPathFresh() {
        log = StepLog(cfg: cfg, phase: "fresh")
        liveBlockedCheck()
        launchPointed("launch")
        onboardingLater()
        layoutAndZoom("after-onboarding")
        pasteInvalidText()
        pasteEmptyClipboard()
        pasteDenied()
        typedInvalid()
        searchTab()
        trendingScreen()
        findsTab(expectEmpty: true)
        profileRows()
        paywallNoProducts()
        legalRows()
        confirmRows()
        saveFromTrending()
        findsTab(expectEmpty: false)
        exportFindsWithSongs()
        listenDenied()
        appearanceAndTheme()
        relaunchToHome()
        finish()
    }

    /// Fresh install: Set it up -> Try it on a reel (Safari) -> back -> screen 3 -> Try it on a reel
    /// again from screen 3 -> back -> Paste a link instead.
    @objc func testPathTryReel() {
        log = StepLog(cfg: cfg, phase: "tryreel")
        launchPointed("launch")
        guard tapButton("Set it up", step: "onb-set-it-up", expect: ["Pin Addify"]) else { finish(); return }
        tryReelRoundTrip("onb2-try-reel", expectBack: ["Not set up yet", "You're all set"])
        if sees("Not set up yet") {
            tryReelRoundTrip("onb3-try-reel", expectBack: ["Not set up yet", "You're all set"])
        }
        if tapButton("Paste a link instead", step: "onb3-paste-instead", expect: ["Clip link"]) {
            layoutAndZoom("home-after-try-reel")
        }
        finish()
    }

    /// Fresh install: fast double taps on every onboarding button (XCUIElement.doubleTap).
    @objc func testPathDoubleTaps() {
        log = StepLog(cfg: cfg, phase: "doubletap")
        launchPointed("launch")
        fastTapWalk(mode: "doubleTap")
        finish()
    }

    /// Fresh install: two separate taps ~0.15 s apart on every onboarding button.
    @objc func testPathFastTaps() {
        log = StepLog(cfg: cfg, phase: "fasttap")
        launchPointed("launch")
        fastTapWalk(mode: "twoTaps")
        finish()
    }

    /// The largest accessibility text size (set by the host before launch): boot screen,
    /// onboarding, home, Profile, paywall, and the Dynamic Type audit.
    @objc func testPathLargeText() {
        log = StepLog(cfg: cfg, phase: "largetext")
        launchPointed("launch")
        let s1 = shot("onboarding-1")
        log.line("largetext-onboarding", "INFO", shot: s1, "first screen at the largest accessibility text size")
        audit("largetext-onboarding", types: [.dynamicType, .textClipped])
        ensureHome()
        dismissKeyboard()
        let s2 = shot("home")
        log.line("largetext-home", "INFO", shot: s2, "home at the largest text size")
        audit("largetext-home", types: [.dynamicType, .textClipped])
        if goTab("You") {
            let s3 = shot("profile")
            log.line("largetext-profile", "INFO", shot: s3, "Profile at the largest text size")
            audit("largetext-profile", types: [.dynamicType, .textClipped])
            if tapButton("Upgrade", step: "largetext-upgrade", expect: ["Restore purchases"]) {
                Thread.sleep(forTimeInterval: 2)
                log.line("largetext-paywall", "INFO", shot: shot("paywall"), "paywall at the largest text size")
                closePaywall()
            }
        }
        finish()
    }

    /// Rotation. On iPad the iPhone app sits in a compatibility window; rotate through
    /// landscape and back and check nothing is cut off. On iPhone it is portrait-only.
    @objc func testPathRotation() {
        log = StepLog(cfg: cfg, phase: "rotate")
        launchPointed("launch")
        ensureHome()
        dismissKeyboard()
        let dev = XCUIDevice.shared
        let plan: [(UIDeviceOrientation, String)] = [(.landscapeLeft, "landscape-left"), (.portrait, "portrait"),
                                                      (.landscapeRight, "landscape-right"), (.portraitUpsideDown, "upside-down"),
                                                      (.portrait, "portrait-again")]
        for (o, name) in plan {
            dev.orientation = o
            Thread.sleep(forTimeInterval: 2.5)
            let w = app.windows.firstMatch
            let f = w.exists ? w.frame : .zero
            let sf = springboard.windows.firstMatch.exists ? springboard.windows.firstMatch.frame : .zero
            let s = shot("rotate-\(name)")
            let tabs = waitForElement("You", exact: true, types: [.button], timeout: 2)
            let tabOK = tabs.map { $0.frame.maxX <= f.maxX + 1 && $0.frame.minX >= f.minX - 1 && $0.frame.maxY <= f.maxY + 1 } ?? false
            log.line("rotate-\(name)", tabOK ? "PASS" : "WARN", shot: s,
                     "app window \(Int(f.minX)),\(Int(f.minY)) \(Int(f.width))x\(Int(f.height)) in screen \(Int(sf.width))x\(Int(sf.height)); 'You' tab \(tabs == nil ? "not found" : (tabOK ? "inside the window" : "outside the window"))")
            if name == "landscape-left" {
                if goTab("You"), tapButton("Upgrade", step: "rotate-paywall-landscape", expect: ["Restore purchases"]) {
                    Thread.sleep(forTimeInterval: 1.5)
                    log.line("rotate-paywall", "INFO", shot: shot("rotate-paywall-landscape"), "paywall while the device is in landscape")
                    closePaywall()
                }
                _ = goTab("Home")
            }
        }
        dev.orientation = .portrait
        finish()
    }

    /// Scans on the test box (budgeted): paste a TikTok link, paste an Instagram link, then a
    /// scan that is backgrounded halfway and brought back. Finds after the saves.
    @objc func testPathScans() {
        log = StepLog(cfg: cfg, phase: "scans")
        launchPointed("launch")
        ensureHome()
        dismissKeyboard()
        if let tt = sample("tiktok"), scanGate("paste-tiktok", tt) {
            if pasteToScan(step: "paste-tiktok", link: tt) {
                let out = waitScanOutcome("scan-tiktok")
                if out == "result" { resultScreenChecks("tiktok") }
            }
            backHome()
        }
        findsTab(expectEmpty: false)
        if let ig = sample("instagram"), scanGate("paste-instagram", ig) {
            if pasteToScan(step: "paste-instagram", link: ig) { _ = waitScanOutcome("scan-instagram") }
            backHome()
        }
        if let long = sample("long"), scanGate("background-mid-scan", long) {
            backgroundMidScan(long)
            backHome()
        }
        findsTab(expectEmpty: false)
        finish()
    }

    /// The Share Extension from Safari's share sheet, the way a reviewer without TikTok
    /// installed would do it: open a TikTok link in Safari, Share, Addify.
    @objc func testPathShareSafari() {
        log = StepLog(cfg: cfg, phase: "share")
        launchPointed("launch")
        ensureHome()
        dismissKeyboard()
        guard let link = sample("share") else { log.line("share", "SKIP", shot: nil, "no share sample"); finish(); return }
        guard scanGate("share-from-safari", link) else { finish(); return }
        shareFromSafari(link)
        finish()
    }

    /// Listen mode with the microphone allowed (the permission is reset first).
    @objc func testPathListenAllowed() {
        log = StepLog(cfg: cfg, phase: "listen")
        app.resetAuthorizationStatus(for: .microphone)
        launchPointed("launch")
        ensureHome()
        dismissKeyboard()
        guard scanGate("listen-allowed", "(microphone)") else { finish(); return }
        listenAllowed()
        finish()
    }

    /// Profile > Delete account > OK, and what the reviewer lands on afterwards.
    @objc func testPathDeleteData() {
        log = StepLog(cfg: cfg, phase: "delete")
        launchPointed("launch")
        ensureHome()
        dismissKeyboard()
        guard goTab("You"), let d = waitForElement("Delete account", exact: true, types: [.button], timeout: 5) else {
            log.line("delete-data", "FAIL", shot: shot("no-delete-row"), "no Delete account row on Profile")
            finish(); return
        }
        let pill0 = firstLabel(containing: "scans left") ?? firstLabel(containing: "Unlimited") ?? "?"
        d.tap()
        let alert = app.alerts.firstMatch
        guard alert.waitForExistence(timeout: 5) else {
            log.line("delete-data", "FAIL", shot: shot("delete-no-confirm"), "tapped Delete account, no confirmation")
            finish(); return
        }
        let text = alert.staticTexts.allElementsBoundByIndex.map { $0.label }.joined(separator: " | ")
        let s1 = shot("delete-confirm")
        if alert.buttons["OK"].exists { alert.buttons["OK"].tap() } else { alert.buttons.element(boundBy: alert.buttons.count - 1).tap() }
        Thread.sleep(forTimeInterval: 6)
        let after = waitForText(["Find the exact song", "Clip link", "reach Addify"], timeout: 20)
        let s2 = shot("after-delete")
        let pill1 = firstLabel(containing: "scans left") ?? "?"
        log.line("delete-data", after != nil && after != "reach Addify" ? "PASS" : "FAIL", shot: s2,
                 "confirm said '\(text)' (\(s1)); after OK the page reloaded to '\(after ?? "nothing")'; scan counter before '\(pill0)', after '\(pill1)'")
        finish()
    }

    // =================================================================================
    // MARK: - Launch, boot timeline, live host check
    // =================================================================================

    /// The live server must be unreachable from inside the simulator (the workflow blocks it in
    /// /etc/hosts). One GET /health, the read-only call the rules allow, proves it.
    func liveBlockedCheck() {
        guard let url = URL(string: "https://\(cfg.liveHost)/health") else { return }
        var req = URLRequest(url: url)
        req.timeoutInterval = 8
        let sem = DispatchSemaphore(value: 0)
        var outcome = "no answer"
        URLSession.shared.dataTask(with: req) { _, resp, err in
            if let h = resp as? HTTPURLResponse { outcome = "HTTP \(h.statusCode)" }
            else if let e = err as NSError? { outcome = "error \(e.code) \(e.localizedDescription)" }
            sem.signal()
        }.resume()
        _ = sem.wait(timeout: .now() + 10)
        let blocked = !outcome.hasPrefix("HTTP")
        log.line("live-blocked", blocked ? "PASS" : "FAIL", shot: nil,
                 blocked ? "the live host is unreachable from the simulator (\(outcome)); every request goes to \(cfg.engine)"
                         : "the live host ANSWERED from inside the simulator (\(outcome)); the app still uses the override \(cfg.engine)")
    }

    /// Launch com.addify.app with the address override and record what the screen shows every
    /// half second until the page is up: the native boot screen, a blank screen, the page, or
    /// "Can't reach Addify". A blank stretch is the Oct 30 failure mode.
    @discardableResult
    func launchPointed(_ step: String) -> TimeInterval? {
        app.launchArguments = ["-engineBaseURL", cfg.engine]
        let t0 = Date()
        app.launch()
        var timeline: [String] = []
        var last = ""
        var pageAt: TimeInterval? = nil
        var blank: TimeInterval = 0
        var lastT: TimeInterval = 0
        var shots: [String] = []
        while Date().timeIntervalSince(t0) < 40 {
            let t = Date().timeIntervalSince(t0)
            let text = screenText()
            var state = "blank"
            if sees("reach Addify", in: text) || sees("You're offline", in: text) { state = "unreachable" }
            else if sees("Find the exact song", in: text) || sees("Clip link", in: text) || sees("Pin Addify", in: text) { state = "page" }
            else if app.activityIndicators.firstMatch.exists || sees("Still connecting", in: text) { state = "boot" }
            if state == "blank" && last == "blank" { blank += t - lastT }
            if state != last {
                timeline.append(String(format: "%.1fs %@", t, state))
                if shots.count < 4 { shots.append(shot("\(step)-\(state)")) }
                last = state
            }
            lastT = t
            if state == "page" { pageAt = t; break }
            if state == "unreachable" { break }
            Thread.sleep(forTimeInterval: 0.5)
        }
        let s = shot(step)
        let result: String
        if let p = pageAt { result = blank > 1.5 ? "WARN" : "PASS"; _ = p } else { result = "FAIL" }
        log.line(step, result, shot: s,
                 "timeline: \(timeline.joined(separator: " -> ")); blank for \(String(format: "%.1f", blank)) s; page \(pageAt.map { String(format: "after %.1f s", $0) } ?? "never") (engine \(cfg.engine)); shots \(shots.joined(separator: ","))")
        return pageAt
    }

    // =================================================================================
    // MARK: - Onboarding
    // =================================================================================

    func onboardingLater() {
        // The page opens onboarding a moment after it loads (crate.html: after SP.finishLogin()).
        _ = waitForText(["Set it up", "Pin Addify", "Not set up yet"], timeout: 10)
        guard sees("Set it up") else {
            log.line("onb-1", sees("Clip link") ? "WARN" : "FAIL", shot: shot("onb-missing"),
                     sees("Clip link") ? "no onboarding on a fresh install, home showed at once" : "no onboarding and no home")
            return
        }
        audit("onb-1")
        guard tapButton("Set it up", step: "onb-1-set-it-up", expect: ["Pin Addify"]) else { return }
        audit("onb-2")
        guard tapButton("do this later", step: "onb-2-later", expect: ["Not set up yet", "Paste a link instead"], exact: false) else { return }
        audit("onb-3")
        guard tapButton("Paste a link instead", step: "onb-3-paste-instead", expect: ["Clip link"]) else { return }
        Thread.sleep(forTimeInterval: 1)
        let pill = firstLabel(containing: "scans left") ?? "(no counter)"
        log.line("home", "PASS", shot: shot("home"), "home is up; counter '\(pill)'; keyboard \(app.keyboards.firstMatch.exists ? "up" : "down")")
    }

    /// Gets past onboarding by the quickest honest route, or does nothing if home is already up.
    func ensureHome() {
        _ = waitForText(["Set it up", "Pin Addify", "Not set up yet", "You're all set"], timeout: 6)
        for _ in 0..<5 {
            if sees("Set it up") { _ = tapButton("Set it up", step: "quick-onb-1", expect: ["Pin Addify"], quiet: true); continue }
            if sees("Pin Addify") { _ = tapButton("do this later", step: "quick-onb-2", expect: ["Not set up yet", "Paste a link instead"], exact: false, quiet: true); continue }
            if sees("Not set up yet") { _ = tapButton("Paste a link instead", step: "quick-onb-3", expect: ["Clip link"], quiet: true); continue }
            if sees("You're all set") { _ = tapButton("Find my first song", step: "quick-onb-done", expect: ["Clip link"], quiet: true); continue }
            break
        }
        let ok = waitForElement("Clip link", exact: true, types: [.textField], timeout: 8) != nil
        if !ok { log.line("ensure-home", "FAIL", shot: shot("no-home"), "could not reach home") }
    }

    func tryReelRoundTrip(_ step: String, expectBack: [String]) {
        guard let tr = waitForElement("Try it on a reel", exact: false, types: [.button], timeout: 6) else {
            log.line(step, "FAIL", shot: shot(step + "-missing"), "no 'Try it on a reel' button")
            return
        }
        tr.tap()
        let inSafari = safari.wait(for: .runningForeground, timeout: 10)
        Thread.sleep(forTimeInterval: 4)
        let sa = shot(step + "-away")
        let words = ocr(XCUIScreen.main.screenshot(), screenWidth: springboard.frame.width).map { $0.text }.joined(separator: " ")
        let tiktok = words.lowercased().contains("tiktok")
        log.line(step + "-away", inSafari ? "INFO" : "WARN", shot: sa,
                 inSafari ? "left Addify for Safari (\(tiktok ? "TikTok page on screen" : "no TikTok text read")): this device has no TikTok app"
                          : "Safari did not come to the front in 10 s (state \(stateName(safari.state)))")
        // Back the way a person does it: the status-bar breadcrumb, else the app switcher.
        let crumb = springboard.buttons.matching(NSPredicate(format: "label CONTAINS[c] 'Addify'")).firstMatch
        if crumb.exists && crumb.isHittable { crumb.tap() } else { app.activate() }
        _ = app.wait(for: .runningForeground, timeout: 10)
        let back = waitForText(expectBack, timeout: 8)
        log.line(step + "-back", back != nil ? "PASS" : "FAIL", shot: shot(step + "-back"),
                 back != nil ? "back in Addify on '\(back!)'" : "back in Addify but the onboarding never moved on")
    }

    /// mode doubleTap: XCUIElement.doubleTap(). mode twoTaps: two coordinate taps 0.15 s apart.
    func fastTapWalk(mode: String) {
        let steps: [(String, String)] = [("Set it up", "set-it-up"), ("do this later", "later"), ("Paste a link instead", "paste-instead")]
        for (label, name) in steps {
            guard let b = waitForElement(label, exact: label != "do this later", types: [.button], timeout: 6) else {
                log.line("\(mode)-\(name)", "SKIP", shot: shot("\(mode)-\(name)-absent"), "'\(label)' is not on screen (screen: \(onbScreenName()))")
                continue
            }
            let before = onbScreenName()
            let c = b.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5))
            if mode == "doubleTap" { b.doubleTap() } else { c.tap(); Thread.sleep(forTimeInterval: 0.15); c.tap() }
            Thread.sleep(forTimeInterval: 2.5)
            let away = safari.state == .runningForeground
            let s = shot("\(mode)-\(name)")
            if away {
                log.line("\(mode)-\(name)", "FAIL", shot: s, "\(mode) on '\(label)' (screen \(before)) threw the user out of Addify into Safari (TikTok)")
                app.activate(); _ = app.wait(for: .runningForeground, timeout: 10); Thread.sleep(forTimeInterval: 2.5)
            } else {
                let after = onbScreenName()
                let skipped = (before == "1" && after != "2") || (before == "2" && after != "3") || (before.hasPrefix("3") && after != "home")
                log.line("\(mode)-\(name)", skipped ? "WARN" : "PASS", shot: s, "\(mode) on '\(label)': screen \(before) -> \(after)")
            }
        }
        log.line("\(mode)-end", "INFO", shot: shot("\(mode)-end"), "ended on screen \(onbScreenName())")
    }

    func onbScreenName() -> String {
        let t = screenText()
        if sees("Set it up", in: t) { return "1" }
        if sees("Pin Addify", in: t) { return "2" }
        if sees("Not set up yet", in: t) { return "3-not-yet" }
        if sees("You're all set", in: t) { return "3-all-set" }
        if sees("Clip link", in: t) { return "home" }
        if safari.state == .runningForeground { return "safari" }
        return "unknown"
    }

    // =================================================================================
    // MARK: - Layout, zoom
    // =================================================================================

    /// iOS zooms a web page in when an input under 16 px takes focus. The link box is 14 px
    /// (crate.html .form input) and the viewport has no maximum-scale, so focusing it zooms and
    /// pans the page. Measured against the app window, with the box focused and after the
    /// keyboard closes (the zoom stays).
    func layoutAndZoom(_ step: String) {
        let win = app.windows.firstMatch.frame
        var parts: [String] = []
        var cut: [String] = []
        for (label, exact, types) in [("scans left", false, [XCUIElement.ElementType.staticText, .button]),
                                      ("Clip link", true, [.textField]),
                                      ("Paste link from clipboard", true, [.button]),
                                      ("You", true, [.button]), ("Home", true, [.button])] as [(String, Bool, [XCUIElement.ElementType])] {
            guard let e = waitForElement(label, exact: exact, types: types, timeout: 1) else { continue }
            let f = e.frame
            parts.append("\(label) x \(Int(f.minX))-\(Int(f.maxX))")
            if f.maxX > win.maxX + 1 || f.minX < win.minX - 1 { cut.append(label) }
        }
        let s1 = shot(step + "-focused")
        log.line("zoom-\(step)-focused", cut.isEmpty ? "PASS" : "FAIL", shot: s1,
                 (cut.isEmpty ? "nothing outside the window" : "outside the \(Int(win.width)) pt window: \(cut.joined(separator: ", "))") + "; " + parts.joined(separator: "; "))
        dismissKeyboard()
        Thread.sleep(forTimeInterval: 1)
        var cut2: [String] = []
        for label in ["You", "Home", "Search", "Finds"] {
            if let e = waitForElement(label, exact: true, types: [.button], timeout: 1) {
                let f = e.frame
                if f.maxX > win.maxX + 1 || f.minX < win.minX - 1 { cut2.append("\(label) x \(Int(f.minX))-\(Int(f.maxX))") }
            }
        }
        log.line("zoom-\(step)-keyboard-closed", cut2.isEmpty ? "PASS" : "FAIL", shot: shot(step + "-keyboard-closed"),
                 cut2.isEmpty ? "tab bar inside the window with the keyboard closed" : "page still zoomed after the keyboard closed: \(cut2.joined(separator: "; "))")
    }

    // =================================================================================
    // MARK: - Paste paths
    // =================================================================================

    /// Taps the page's Paste button and answers iOS's Paste callout. allow=false dismisses it
    /// instead. While the callout is up the web content process is frozen, so until it is
    /// answered this only takes screenshots, reads them with on-device OCR and taps by position.
    /// Returns (callout seen, system alert text).
    @discardableResult
    func pasteViaCallout(step: String, clip: String?, allow: Bool) -> (Bool, String?) {
        if let c = clip { UIPasteboard.general.string = c } else { UIPasteboard.general.items = [] }
        dismissKeyboard()
        guard let pbtn = waitForElement("Paste link from clipboard", exact: true, types: [.button], timeout: 5),
              let field = waitForElement("Clip link", exact: true, types: [.textField], timeout: 2) else {
            log.line(step, "FAIL", shot: shot(step + "-no-paste-button"), "no Paste button or link box on home")
            return (false, nil)
        }
        let fieldFrame = field.frame
        let btnFrame = pbtn.frame
        let win = app.windows.firstMatch.frame
        let sw = springboard.frame.width
        tapScreen(CGPoint(x: btnFrame.midX, y: btnFrame.midY))
        Thread.sleep(forTimeInterval: 1.6)
        var seen = false
        var calloutShot = ""
        for attempt in 1...3 {
            let img = XCUIScreen.main.screenshot()
            calloutShot = saveShot(img, "\(step)-callout-\(attempt)")
            let words = ocr(img, screenWidth: sw)
            let bubbles = words.filter { $0.text.trimmingCharacters(in: .whitespaces) == "Paste" && $0.rect.midY < fieldFrame.minY - 2 }
            if let b = bubbles.first {
                seen = true
                if allow {
                    tapScreen(CGPoint(x: b.rect.midX, y: b.rect.midY))
                } else {
                    // An empty spot: the left margin of the caption line above the link box.
                    tapScreen(CGPoint(x: win.minX + 10, y: max(win.minY + 90, fieldFrame.minY - 34)))
                }
                break
            }
            Thread.sleep(forTimeInterval: 1.0)
        }
        Thread.sleep(forTimeInterval: 1.2)
        var alertText: String? = nil
        if let a = systemAlertInfo(timeout: 1.5) {
            alertText = a
            let sa = shot(step + "-system-alert")
            log.line(step + "-system-alert", "INFO", shot: sa, "iOS asked before pasting: \(a)")
            tapAlertButton(preferring: allow ? ["Allow Paste", "Allow"] : ["Don’t Allow Paste", "Don't Allow Paste", "Don’t Allow", "Don't Allow"])
            Thread.sleep(forTimeInterval: 1)
        }
        log.line(step + "-callout", "INFO", shot: calloutShot,
                 seen ? "iOS Paste callout appeared and was \(allow ? "tapped" : "dismissed by tapping outside it")"
                      : "no Paste callout appeared after tapping Paste (clipboard \(clip == nil ? "empty" : "'\(clip!.prefix(40))'"))")
        if !seen {
            // Whatever is up (a callout OCR missed), close it without touching the SCAN dial.
            tapScreen(CGPoint(x: win.minX + 10, y: max(win.minY + 90, fieldFrame.minY - 34)))
            Thread.sleep(forTimeInterval: 1)
        }
        return (seen, alertText)
    }

    /// Reads the page's error line (#err, shown for 4.5 s).
    func pageError() -> String? {
        for key in ["TikTok or Instagram link", "does not look like", "Couldn't", "Could not"] {
            if let l = firstLabel(containing: key), !l.isEmpty { return l }
        }
        return nil
    }

    func pasteInvalidText() {
        let (seen, _) = pasteViaCallout(step: "paste-invalid", clip: "hello from the review simulator", allow: true)
        let err = pageError()
        let scanning = sees("Finding song") || sees("Reading the clip")
        let s = shot("paste-invalid-result")
        let ok = (err ?? "").contains("No TikTok or Instagram link") && !scanning
        log.line("paste-invalid", ok ? "PASS" : (seen ? "FAIL" : "WARN"), shot: s,
                 "clipboard held plain text; page said '\(err ?? "(nothing)")'\(scanning ? "; a scan started" : "")")
        responsiveCheck("paste-invalid")
    }

    func pasteEmptyClipboard() {
        let (seen, _) = pasteViaCallout(step: "paste-empty", clip: nil, allow: true)
        let err = pageError()
        let s = shot("paste-empty-result")
        log.line("paste-empty", err != nil ? "PASS" : "WARN", shot: s,
                 "empty clipboard: callout \(seen ? "appeared" : "did not appear"); page said '\(err ?? "(nothing)")'\(err == nil ? " (a tap that does nothing visible)" : "")")
        responsiveCheck("paste-empty")
    }

    func pasteDenied() {
        let (seen, alert) = pasteViaCallout(step: "paste-denied", clip: "deny test plain text", allow: false)
        Thread.sleep(forTimeInterval: 0.8)
        let err = pageError()
        let scanning = sees("Finding song") || sees("Reading the clip")
        let s = shot("paste-denied-result")
        log.line("paste-denied", scanning ? "FAIL" : (seen ? "PASS" : "WARN"), shot: s,
                 "callout \(seen ? "dismissed" : "never appeared")\(alert != nil ? ", system alert answered Don't Allow" : ""); page said '\(err ?? "(nothing)")'; scan started: \(scanning)")
        responsiveCheck("paste-denied")
    }

    /// After a callout the page must answer the accessibility tree again (a frozen web
    /// content process is what hung run 41177602972 for 6 minutes).
    func responsiveCheck(_ step: String) {
        let t0 = Date()
        let ok = waitForElement("Clip link", exact: true, types: [.textField], timeout: 12) != nil
        let secs = Date().timeIntervalSince(t0)
        if !ok || secs > 8 {
            log.line(step + "-responsive", "FAIL", shot: shot(step + "-frozen"), "the page did not answer for \(Int(secs)) s after the paste prompt")
        }
        dismissKeyboard()
    }

    func typedInvalid() {
        guard let field = waitForElement("Clip link", exact: true, types: [.textField], timeout: 5) else {
            log.line("typed-invalid", "FAIL", shot: shot("typed-invalid-no-box"), "no link box"); return
        }
        field.tap()
        Thread.sleep(forTimeInterval: 0.6)
        field.typeText("not a link")
        Thread.sleep(forTimeInterval: 0.5)
        let go = waitForElement("Find it", exact: true, types: [.button], timeout: 3)
        if let g = go {
            let kb = app.keyboards.firstMatch
            if kb.exists && kb.frame.intersects(g.frame) { dismissKeyboard() }
            if g.exists { g.tap() }
        } else {
            field.typeText("\n")
        }
        Thread.sleep(forTimeInterval: 1.0)
        let err = pageError()
        let s = shot("typed-invalid")
        log.line("typed-invalid", (err ?? "").contains("does not look like") ? "PASS" : "FAIL", shot: s,
                 "typed 'not a link' and \(go != nil ? "tapped Find it" : "pressed return"); page said '\(err ?? "(nothing)")'")
        clearField(field)
        dismissKeyboard()
    }

    /// Pastes a real link with the page's Paste button (the scan starts on its own).
    func pasteToScan(step: String, link: String) -> Bool {
        let (seen, _) = pasteViaCallout(step: step, clip: link, allow: true)
        let started = waitForText(["Finding song", "Reading the clip", "Find another song", "reading audio", "Match found", "Song identified"], timeout: 8)
        let s = shot(step + "-started")
        if started != nil {
            log.line(step, "PASS", shot: s, "paste started a scan ('\(started!)')")
            return true
        }
        log.line(step, seen ? "FAIL" : "WARN", shot: s, "paste did not start a scan (callout \(seen ? "tapped" : "not seen")); typing the link instead")
        guard let field = waitForElement("Clip link", exact: true, types: [.textField], timeout: 3) else { return false }
        return typeAndSubmitLink(field, link)
    }

    // =================================================================================
    // MARK: - Scans: gate, outcome, result screen, background mid-scan
    // =================================================================================

    func sample(_ key: String) -> String? {
        let v = cfg.samples[key] ?? ""
        return v.isEmpty ? nil : v
    }

    /// Budget + the test box must be idle (no scan from any lane running) before a scan starts.
    func scanGate(_ what: String, _ link: String) -> Bool {
        guard cfg.scans else {
            log.line("gate-\(what)", "SKIP", shot: nil, "scans are off for this walk")
            return false
        }
        let usedURL = cfg.out.appendingPathComponent("scans-used.txt")
        let used = Int((try? String(contentsOf: usedURL, encoding: .utf8))?.trimmingCharacters(in: .whitespacesAndNewlines) ?? "0") ?? 0
        guard used < cfg.scanBudget else {
            log.line("gate-\(what)", "SKIP", shot: nil, "scan budget spent (\(used) of \(cfg.scanBudget))")
            return false
        }
        let t0 = Date()
        var idle = 0
        var last = "?"
        while Date().timeIntervalSince(t0) < 300 {
            if let g = testBoxGate() {
                last = "busy \(g["busy"] ?? -1) running \(g["running"] ?? -1) reserved \(g["reserved"] ?? -1) queue \(g["queue"] ?? -1)"
                if (g["busy"] ?? 1) == 0 && (g["running"] ?? 1) == 0 && (g["queue"] ?? 1) == 0 && (g["reserved"] ?? 1) == 0 {
                    idle += 1
                    if idle >= 2 { break }
                } else { idle = 0 }
            } else { last = "health unreadable"; idle = 0 }
            Thread.sleep(forTimeInterval: 4)
        }
        guard idle >= 2 else {
            log.line("gate-\(what)", "SKIP", shot: nil, "the test box stayed busy for 5 min (\(last)); not starting a scan in parallel with another lane")
            return false
        }
        try? String(used + 1).write(to: usedURL, atomically: true, encoding: .utf8)
        let admitted = serverAdmitted().map(String.init) ?? "?"
        let rec = "{\"device\":\"\(cfg.device)\",\"phase\":\"\(log.phase)\",\"what\":\"\(what)\",\"link\":\"\(link)\",\"at\":\"\(ISO8601DateFormatter().string(from: Date()))\",\"admitted_before\":\"\(admitted)\",\"n\":\(used + 1)}\n"
        log.append(rec, to: "scan-ledger.jsonl")
        log.line("gate-\(what)", "INFO", shot: nil, "scan \(used + 1) of \(cfg.scanBudget) on \(cfg.engine) (test box idle: \(last)); link \(link)")
        return true
    }

    /// server.gate on the test box's GET /health.
    func testBoxGate() -> [String: Int]? {
        guard let url = URL(string: cfg.engine + "/health") else { return nil }
        var req = URLRequest(url: url)
        req.timeoutInterval = 10
        let sem = DispatchSemaphore(value: 0)
        var out: [String: Int]? = nil
        URLSession.shared.dataTask(with: req) { data, _, _ in
            if let d = data, let j = try? JSONSerialization.jsonObject(with: d) as? [String: Any],
               let s = j["server"] as? [String: Any], let g = s["gate"] as? [String: Any] {
                var o: [String: Int] = [:]
                for k in ["busy", "running", "reserved", "queue"] { o[k] = g[k] as? Int }
                out = o
            }
            sem.signal()
        }.resume()
        _ = sem.wait(timeout: .now() + 12)
        return out
    }

    /// Waits for the scan to end. Also watches for the onboarding "You're all set" card, which
    /// the page raises when it mistakes a pasted link for a share-sheet arrival (crate.html
    /// onbNoteRun: no tap in the 1.5 s before run(), and the iOS Paste callout tap is not a page
    /// event).
    @discardableResult
    func waitScanOutcome(_ step: String) -> String {
        let before = serverAdmitted()
        let t0 = Date()
        let marks: [TimeInterval] = [5, 15, 30, 60, 90, 120]
        var next = 0
        var outcome = "timeout"
        var headline = ""
        var cardAt: TimeInterval? = nil
        var cardShot = ""
        while Date().timeIntervalSince(t0) < cfg.resultTimeout {
            let text = screenText()
            let el = Date().timeIntervalSince(t0)
            if cardAt == nil && sees("You're all set", in: text) && sees("Find my first song", in: text) {
                cardAt = el
                cardShot = shot("\(step)-allset-card")
            }
            if sees("Find another song", in: text) { outcome = "result"; break }
            if sees("Use listen mode instead", in: text) || sees("Back to home", in: text) || sees("No match", in: text) { outcome = "no-match"; break }
            if sees("Couldn't read", in: text) || sees("not readable", in: text) { outcome = "link-unreadable"; break }
            if sees("reach Addify", in: text) { outcome = "unreachable"; break }
            if let h = ["Finding song", "Reading the clip", "Checking", "Matching", "Listening", "Match found", "Song identified"].first(where: { sees($0, in: text) }) { headline = h }
            if next < marks.count && el >= marks[next] {
                log.line("\(step)-wait", "INFO", shot: shot("\(step)-\(Int(marks[next]))s"), "t=\(Int(el)) s, screen says '\(headline)'")
                next += 1
            }
            Thread.sleep(forTimeInterval: 1.5)
        }
        let secs = Int(Date().timeIntervalSince(t0))
        Thread.sleep(forTimeInterval: 1.5)
        let text = screenText()
        if cardAt == nil && sees("You're all set", in: text) && sees("Find my first song", in: text) {
            cardAt = Double(secs); cardShot = shot("\(step)-allset-card")
        }
        let s = shot(step)
        let ax = dumpAX(step)
        let after = serverAdmitted()
        let server = "test box admitted \(before.map(String.init) ?? "?") -> \(after.map(String.init) ?? "?")"
        let title = resultTitle()
        switch outcome {
        case "result":
            log.line(step, "PASS", shot: s, "result after \(secs) s: '\(title)'; \(server); ax=\(ax)")
        case "no-match":
            log.line(step, "WARN", shot: s, "scan ended with no song after \(secs) s; \(server); ax=\(ax)")
        case "link-unreadable":
            log.line(step, "WARN", shot: s, "'Couldn't read that link' after \(secs) s (the test box has no Instagram/YouTube sessions); \(server); ax=\(ax)")
        case "unreachable":
            log.line(step, "FAIL", shot: s, "'Couldn't reach Addify' during the scan after \(secs) s; \(server); ax=\(ax)")
        default:
            log.line(step, "FAIL", shot: s, "no outcome after \(secs) s; last headline '\(headline)'; \(server); ax=\(ax)")
        }
        if let c = cardAt {
            log.line("\(step)-allset-card", "FAIL", shot: cardShot,
                     "the onboarding card 'You're all set / Find my first song' covered the scan \(Int(c)) s in, although nothing was shared from the share sheet")
            if let b = waitForElement("Find my first song", exact: true, types: [.button], timeout: 2) {
                b.tap()
                Thread.sleep(forTimeInterval: 1.5)
                log.line("\(step)-allset-dismissed", "INFO", shot: shot("\(step)-under-card"), "tapped 'Find my first song'; screen now: \(sees("Find another song") ? "the result" : (sees("Clip link") ? "home" : "other"))")
            }
        }
        return outcome
    }

    func resultTitle() -> String {
        for key in ["Match found", "Song identified", "No match"] where sees(key) { return key }
        return "?"
    }

    func resultScreenChecks(_ tag: String) {
        audit("result-\(tag)")
        let save = waitForElement("Save to Found with Addify", exact: false, types: [.button, .staticText], timeout: 2) != nil
        let openIn = sees("OPEN IN") || sees("Open in")
        log.line("result-\(tag)-screen", "INFO", shot: shot("result-\(tag)"),
                 "save button \(save ? "present" : "absent"); 'Open in' tiles \(openIn ? "present" : "absent")")
    }

    func backHome() {
        for label in ["Find another song", "Back to home", "Back"] {
            if let b = waitForElement(label, exact: true, types: [.button], timeout: 1.5) { b.tap(); break }
        }
        Thread.sleep(forTimeInterval: 1)
        _ = goTab("Home")
        dismissKeyboard()
    }

    /// Starts a scan by typing a link, sends the app to the background 5 s in, waits 20 s,
    /// brings it back and records how the scan ends (or what it shows).
    func backgroundMidScan(_ link: String) {
        guard let field = waitForElement("Clip link", exact: true, types: [.textField], timeout: 5) else {
            log.line("bg-scan", "FAIL", shot: shot("bg-no-box"), "no link box"); return
        }
        UIPasteboard.general.string = ""
        guard typeAndSubmitLink(field, link) else { return }
        Thread.sleep(forTimeInterval: 5)
        let s0 = shot("bg-before-home")
        XCUIDevice.shared.press(.home)
        Thread.sleep(forTimeInterval: 20)
        let s1 = shot("bg-springboard")
        let alertWhileAway = systemAlertInfo(timeout: 1)
        app.activate()
        _ = app.wait(for: .runningForeground, timeout: 10)
        Thread.sleep(forTimeInterval: 1)
        var promptNote = "no system prompt"
        if let a = systemAlertInfo(timeout: 3) {
            promptNote = "system prompt on return: \(a)"
            log.line("bg-scan-prompt", "INFO", shot: shot("bg-prompt"), promptNote)
            tapAlertButton(preferring: ["Don’t Allow", "Don't Allow", "Not Now"])
        }
        log.line("bg-scan-away", "INFO", shot: s1, "backgrounded 5 s into the scan (\(s0)), back after 20 s; \(alertWhileAway.map { "alert while away: \($0)" } ?? "no alert while away"); \(promptNote)")
        let out = waitScanOutcome("bg-scan")
        log.line("bg-scan-outcome", out == "result" || out == "no-match" ? "PASS" : "FAIL", shot: nil, "after the round trip to the home screen the scan ended as '\(out)'")
    }

    func typeAndSubmitLink(_ field: XCUIElement, _ link: String) -> Bool {
        field.tap()
        Thread.sleep(forTimeInterval: 0.6)
        field.typeText(link)
        Thread.sleep(forTimeInterval: 0.5)
        guard let go = waitForElement("Find it", exact: true, types: [.button], timeout: 3) else {
            log.line("find-it", "FAIL", shot: shot("no-find-it"), "no Find it button after typing"); return false
        }
        let kb = app.keyboards.firstMatch
        if kb.exists && kb.frame.intersects(go.frame) { dismissKeyboard() }
        guard go.exists else { return false }
        go.tap()
        let started = waitForText(["Finding song", "Reading the clip", "reading audio", "Match found"], timeout: 8)
        log.line("find-it", started != nil ? "PASS" : "FAIL", shot: shot("find-it"), started != nil ? "scan started" : "no scan screen 8 s after Find it")
        return started != nil
    }

    // =================================================================================
    // MARK: - Share Extension from Safari
    // =================================================================================

    func shareFromSafari(_ link: String) {
        XCUIDevice.shared.press(.home)
        Thread.sleep(forTimeInterval: 1.5)
        guard let url = URL(string: link) else { return }
        XCUIDevice.shared.system.open(url)
        let up = safari.wait(for: .runningForeground, timeout: 15)
        Thread.sleep(forTimeInterval: 7)
        if let a = systemAlertInfo(timeout: 1) {   // e.g. an "Open in TikTok?" prompt
            log.line("share-safari-alert", "INFO", shot: shot("share-safari-alert"), "alert in Safari: \(a)")
            tapAlertButton(preferring: ["Cancel", "Not Now", "Close"])
        }
        let s0 = shot("share-safari-page")
        log.line("share-safari-open", up ? "PASS" : "FAIL", shot: s0, up ? "Safari opened \(link)" : "Safari did not open the link")
        guard up else { return }
        // Safari's Share button: on the toolbar, or inside the page menu on newer Safari.
        var shareBtn = safari.buttons.matching(NSPredicate(format: "label ==[c] 'Share' OR identifier ==[c] 'ShareButton'")).firstMatch
        if !shareBtn.exists {
            let menu = safari.buttons.matching(NSPredicate(format: "label CONTAINS[c] 'Page Menu' OR label CONTAINS[c] 'More' OR label ==[c] 'Menu' OR identifier CONTAINS[c] 'PageMenu'")).firstMatch
            if menu.exists { menu.tap(); Thread.sleep(forTimeInterval: 1.5) }
            shareBtn = safari.descendants(matching: .any).matching(NSPredicate(format: "label ==[c] 'Share'")).firstMatch
        }
        guard shareBtn.exists else {
            try? safari.debugDescription.write(to: cfg.out.appendingPathComponent("\(cfg.device)-share-safari.ax.txt"), atomically: true, encoding: .utf8)
            log.line("share-sheet", "FAIL", shot: shot("share-no-button"), "could not find Safari's Share button (Safari tree saved)")
            return
        }
        shareBtn.tap()
        Thread.sleep(forTimeInterval: 2.5)
        let s1 = shot("share-sheet")
        var addify: XCUIElement? = nil
        for attempt in 0..<5 {
            for root in [safari, springboard] {
                let q = root.descendants(matching: .any).matching(NSPredicate(format: "label ==[c] 'Addify'"))
                let e = q.firstMatch
                if e.exists && e.isHittable { addify = e; break }
            }
            if addify != nil { break }
            // Scroll the app row left: it lists new extensions at its end.
            let row = safari.descendants(matching: .any).matching(NSPredicate(format: "label ==[c] 'Messages' OR label ==[c] 'Mail' OR label ==[c] 'Notes' OR label ==[c] 'Reminders'")).firstMatch
            if row.exists {
                let start = row.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).withOffset(CGVector(dx: 160, dy: 0))
                let end = row.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).withOffset(CGVector(dx: -140, dy: 0))
                start.press(forDuration: 0.05, thenDragTo: end)
                Thread.sleep(forTimeInterval: 1)
            }
            if attempt == 3 {
                // Last resort: the row's own "More" list.
                let more = safari.descendants(matching: .any).matching(NSPredicate(format: "label ==[c] 'More'")).firstMatch
                if more.exists && more.isHittable { more.tap(); Thread.sleep(forTimeInterval: 1.5) }
            }
        }
        guard let target = addify else {
            try? safari.debugDescription.write(to: cfg.out.appendingPathComponent("\(cfg.device)-share-sheet.ax.txt"), atomically: true, encoding: .utf8)
            log.line("share-sheet-addify", "FAIL", shot: shot("share-no-addify"), "Addify is not in Safari's share sheet (\(s1); tree saved)")
            return
        }
        log.line("share-sheet-addify", "PASS", shot: shot("share-addify-found"), "Addify is in the share sheet")
        target.tap()
        let t0 = Date()
        var arrived = false
        var notes: [String] = []
        while Date().timeIntervalSince(t0) < 20 {
            if let a = systemAlertInfo(timeout: 0.5) {
                notes.append("alert: \(a)")
                log.line("share-open-alert", "INFO", shot: shot("share-open-alert"), "iOS asked: \(a); tapped Open")
                tapAlertButton(preferring: ["Open"])
            }
            if app.state == .runningForeground { arrived = true; break }
            let card = safari.descendants(matching: .any).matching(NSPredicate(format: "label CONTAINS[c] 'Open Addify'")).firstMatch
            if card.exists {
                notes.append("the extension showed its fallback card")
                log.line("share-card", "WARN", shot: shot("share-card"), "the extension could not open Addify by itself and showed 'Open Addify'; tapping it")
                if card.isHittable { card.tap() }
            }
            Thread.sleep(forTimeInterval: 1)
        }
        let s2 = shot("share-arrived")
        log.line("share-open-app", arrived ? "PASS" : "FAIL", shot: s2,
                 arrived ? "Addify came to the front \(Int(Date().timeIntervalSince(t0))) s after the tap\(notes.isEmpty ? "" : " (" + notes.joined(separator: "; ") + ")")"
                         : "Addify did not come to the front within 20 s (\(notes.joined(separator: "; ")))")
        if !arrived {
            app.activate()
            _ = app.wait(for: .runningForeground, timeout: 10)
            log.line("share-inbox", "INFO", shot: shot("share-after-activate"), "opened Addify by hand: the inbox should start the scan now")
        }
        let out = waitScanOutcome("share-scan")
        log.line("share-scan-outcome", out == "result" || out == "no-match" ? "PASS" : "FAIL", shot: nil, "the shared link ended as '\(out)'")
    }

    // =================================================================================
    // MARK: - Listen mode
    // =================================================================================

    func listenDenied() {
        _ = goTab("Home")
        dismissKeyboard()
        guard let orb = waitForElement("Scan", exact: true, types: [.button], timeout: 5) else {
            log.line("listen-denied", "FAIL", shot: shot("no-scan-dial"), "no SCAN dial on home"); return
        }
        if let f = waitForElement("Clip link", exact: true, types: [.textField], timeout: 2), let v = f.value as? String, !v.isEmpty, v != f.placeholderValue {
            clearField(f); dismissKeyboard()
        }
        orb.tap()
        if let a = systemAlertInfo(timeout: 8) {
            log.line("mic-prompt", "INFO", shot: shot("mic-prompt"), "\(a)")
            tapAlertButton(preferring: ["Don’t Allow", "Don't Allow"])
            Thread.sleep(forTimeInterval: 2.0)
            let msg = firstLabel(containing: "Microphone") ?? firstLabel(containing: "microphone") ?? firstLabel(containing: "mic") ?? "(no message)"
            let s = shot("mic-denied")
            Thread.sleep(forTimeInterval: 5)
            let still = firstLabel(containing: "icrophone") != nil
            log.line("mic-denied", msg.hasPrefix("(") ? "FAIL" : "PASS", shot: s,
                     "after Don't Allow the page says '\(msg)'; 5 s later the message is \(still ? "still up" : "gone") (\(shot("mic-denied-7s")))")
            if let orb2 = waitForElement("Scan", exact: true, types: [.button], timeout: 3) {
                orb2.tap()
                Thread.sleep(forTimeInterval: 2.5)
                let again = systemAlertInfo(timeout: 1)
                let msg2 = firstLabel(containing: "icrophone") ?? "(no message)"
                log.line("mic-denied-again", msg2.hasPrefix("(") ? "WARN" : "PASS", shot: shot("mic-denied-again"),
                         "second SCAN tap with the mic denied: \(again != nil ? "system prompt \(again!)" : "no prompt"); page says '\(msg2)'")
                if again != nil { tapAlertButton(preferring: ["Don’t Allow", "Don't Allow", "Cancel", "OK"]) }
            }
        } else {
            log.line("mic-prompt", "WARN", shot: shot("listen-no-prompt"), "tapped SCAN, no microphone prompt in 8 s")
        }
        if let b = waitForElement("Cancel", exact: true, types: [.button], timeout: 1) { b.tap() }
        _ = goTab("Home")
    }

    func listenAllowed() {
        guard let orb = waitForElement("Scan", exact: true, types: [.button], timeout: 5) else {
            log.line("listen-allowed", "FAIL", shot: shot("no-scan-dial"), "no SCAN dial on home"); return
        }
        orb.tap()
        if let a = systemAlertInfo(timeout: 8) {
            log.line("mic-prompt", "INFO", shot: shot("mic-prompt"), "\(a); tapped Allow")
            tapAlertButton(preferring: ["Allow", "OK"])
        } else {
            log.line("mic-prompt", "WARN", shot: shot("listen-no-prompt"), "no microphone prompt after the reset")
        }
        Thread.sleep(forTimeInterval: 3)
        log.line("listen-3s", "INFO", shot: shot("listen-3s"), "listening screen: '\(firstLabel(containing: "Listen") ?? "")'")
        let t0 = Date()
        var outcome = "timeout"
        while Date().timeIntervalSince(t0) < 70 {
            let t = screenText()
            if sees("Find another song", in: t) { outcome = "result"; break }
            if sees("No match", in: t) || sees("Couldn't hear", in: t) || sees("didn't catch", in: t) || sees("Nothing matched", in: t) || sees("Back to home", in: t) { outcome = "no-match"; break }
            if sees("icrophone", in: t) && sees("off", in: t) { outcome = "mic-error"; break }
            if sees("reach Addify", in: t) { outcome = "unreachable"; break }
            Thread.sleep(forTimeInterval: 2)
        }
        let s = shot("listen-outcome")
        let ax = dumpAX("listen-outcome")
        log.line("listen-allowed", outcome == "result" || outcome == "no-match" ? "PASS" : (outcome == "mic-error" ? "WARN" : "FAIL"), shot: s,
                 "microphone allowed; listen mode ended as '\(outcome)' after \(Int(Date().timeIntervalSince(t0))) s (the CI runner has no real microphone, so silence is expected); ax=\(ax)")
        backHome()
    }

    // =================================================================================
    // MARK: - Tabs: Search, Trending, Finds
    // =================================================================================

    @discardableResult
    func goTab(_ name: String) -> Bool {
        dismissKeyboard()
        guard let b = waitForElement(name, exact: true, types: [.button], timeout: 5) else {
            log.line("tab-\(name)", "FAIL", shot: shot("no-tab-\(name)"), "no '\(name)' tab button")
            return false
        }
        b.tap()
        Thread.sleep(forTimeInterval: 1.2)
        return true
    }

    func searchTab() {
        guard goTab("Search") else { return }
        audit("search")
        guard let f = waitForElement("Search by lyric, vibe or creator", exact: false, types: [.searchField, .textField], timeout: 5) else {
            log.line("search", "FAIL", shot: shot("search-no-field"), "no search field"); return
        }
        for (q, name) in [("slowed", "search-slowed"), ("zzqxv nothing 0941", "search-nonsense")] {
            f.tap()
            Thread.sleep(forTimeInterval: 0.5)
            if let v = f.value as? String, !v.isEmpty, v != f.placeholderValue {
                f.typeText(String(repeating: XCUIKeyboardKey.delete.rawValue, count: v.count + 2))
            }
            let base = app.buttons.count
            f.typeText(q + "\n")
            let t0 = Date()
            var got = ""
            while Date().timeIntervalSince(t0) < 20 {
                let t = screenText()
                if sees("Nothing close", in: t) { got = "Nothing close"; break }
                if sees("Couldn't", in: t) || sees("Could not", in: t) { got = "error"; break }
                if sees("These match words", in: t) || app.buttons.count >= base + 3 { got = "results"; break }
                Thread.sleep(forTimeInterval: 1.2)
            }
            let s = shot(name)
            let ok = (name == "search-slowed" && got == "results") || (name == "search-nonsense" && got == "Nothing close")
            log.line(name, ok ? "PASS" : (got.isEmpty ? "FAIL" : "WARN"), shot: s,
                     "query '\(q)': \(got.isEmpty ? "nothing after 20 s" : got) in \(Int(Date().timeIntervalSince(t0))) s")
        }
        dismissKeyboard()
    }

    func trendingScreen() {
        guard goTab("Home") else { return }
        dismissKeyboard()
        guard let see = waitForElement("See all", exact: true, types: [.button], timeout: 10) else {
            let empty = sees("Your finds will land here")
            log.line("trending", "WARN", shot: shot("trending-no-see-all"), empty ? "no Trending teaser on home (no chart data on this engine): 'Your finds will land here'" : "no 'See all' on home")
            return
        }
        see.tap()
        Thread.sleep(forTimeInterval: 2)
        let ok = waitForText(["Trending songs"], timeout: 5) != nil
        log.line("trending-open", ok ? "PASS" : "FAIL", shot: shot("trending"), ok ? "Trending screen is up" : "See all did not open Trending")
        audit("trending")
        for chip in ["Slowed", "Sped up", "Remix", "All"] {
            guard let c = waitForElement(chip, exact: true, types: [.button], timeout: 3) else {
                log.line("chip-\(chip)", "FAIL", shot: shot("chip-\(chip)-missing"), "no '\(chip)' chip"); continue
            }
            c.tap()
            Thread.sleep(forTimeInterval: 1.2)
            let empty = firstLabel(containing: "sounds right now")
            let rows = app.buttons.matching(NSPredicate(format: "label MATCHES '(?s)^[0-9]+ .*'")).count
            log.line("chip-\(chip)", empty != nil ? "FAIL" : "PASS", shot: shot("chip-\(chip)"),
                     empty != nil ? "'\(chip)' shows an empty list: '\(empty!)'" : "'\(chip)' lists rows (\(rows) numbered rows read)")
        }
        if let back = waitForElement("Back to Home", exact: true, types: [.button], timeout: 3) {
            back.tap()
            Thread.sleep(forTimeInterval: 1)
        } else { _ = goTab("Home") }
    }

    func findsTab(expectEmpty: Bool) {
        guard goTab("Finds") else { return }
        Thread.sleep(forTimeInterval: 1)
        let empty = sees("Nothing saved yet")
        let sub = firstLabel(containing: "song") ?? ""
        let s = shot(expectEmpty ? "finds-empty" : "finds-after-save")
        if expectEmpty {
            log.line("finds-empty", empty ? "PASS" : "WARN", shot: s, empty ? "empty Finds says 'Nothing saved yet'" : "Finds is not empty on a fresh install ('\(sub)')")
            audit("finds-empty")
        } else {
            log.line("finds-after-save", empty ? "FAIL" : "PASS", shot: s, empty ? "Finds is still empty after a save" : "Finds lists the saved song(s): '\(sub)'")
        }
    }

    /// The "+" on a Trending row saves the song without a scan.
    func saveFromTrending() {
        guard goTab("Home") else { return }
        dismissKeyboard()
        guard let see = waitForElement("See all", exact: true, types: [.button], timeout: 8) else {
            log.line("save-trending", "SKIP", shot: shot("save-no-trending"), "no Trending teaser, so no song to save without a scan"); return
        }
        see.tap()
        Thread.sleep(forTimeInterval: 2)
        let rows = app.buttons.matching(NSPredicate(format: "label MATCHES '(?s)^1[^0-9].*'"))
        let row = rows.firstMatch.exists ? rows.firstMatch : app.buttons.matching(NSPredicate(format: "label MATCHES '(?s)^[0-9]+ .*'")).firstMatch
        guard row.exists else {
            log.line("save-trending", "FAIL", shot: shot("save-no-row"), "no Trending row to save"); return
        }
        let f = row.frame
        let plus = app.descendants(matching: .any).matching(NSPredicate(format: "label ==[c] 'Save to playlist'")).firstMatch
        if plus.exists && plus.isHittable { plus.tap() }
        else { tapScreen(CGPoint(x: f.maxX - 25, y: f.midY)) }   // the 28 pt + sits 11 pt from the row's right edge
        Thread.sleep(forTimeInterval: 1.5)
        let leftApp = safari.state == .runningForeground
        log.line("save-trending", leftApp ? "FAIL" : "PASS", shot: shot("save-trending"),
                 leftApp ? "the tap on + opened the song outside Addify instead of saving it" : "tapped + on '\(row.label.prefix(50))'")
        if leftApp { app.activate(); _ = app.wait(for: .runningForeground, timeout: 10) }
        if let back = waitForElement("Back to Home", exact: true, types: [.button], timeout: 3) { back.tap() }
    }

    // =================================================================================
    // MARK: - Profile, paywall without products, legal rows, confirmations, export
    // =================================================================================

    func profileRows() {
        guard goTab("You") else { return }
        Thread.sleep(forTimeInterval: 1)
        let rows = ["Get Addify Plus", "Upgrade", "How to add Addify", "Auto-save to playlist", "Export finds", "Clear finds", "Privacy policy", "Support", "Delete account", "Manage subscription"]
        var present: [String] = []
        var absent: [String] = []
        let pageText = screenText()
        for r in rows {
            if sees(r, in: pageText) { present.append(r) } else { absent.append(r) }
        }
        let s = shot("profile")
        _ = dumpAX("profile")
        log.line("profile", sees("Get Addify Plus") && sees("Upgrade") ? "PASS" : "FAIL", shot: s,
                 "rows present: \(present.joined(separator: ", ")); absent: \(absent.joined(separator: ", ")) (Manage subscription shows only with a subscription)")
        audit("profile")
        // Export finds with an empty library.
        if let ex = waitForElement("Export finds", exact: false, types: [.button], timeout: 3) {
            ex.tap()
            Thread.sleep(forTimeInterval: 2.5)
            let stillHere = waitForElement("You", exact: true, types: [.button], timeout: 1) != nil
            let sheet = app.otherElements["ActivityListView"].exists || app.sheets.firstMatch.exists
            log.line("export-empty", sheet ? "PASS" : "FAIL", shot: shot("export-empty"),
                     sheet ? "a share sheet opened" : (stillHere ? "tapped Export finds with no finds and nothing happened (a dead button to a reviewer)" : "the page navigated away"))
        }
        // How to add Addify replays screen 2.
        if let how = waitForElement("How to add Addify", exact: true, types: [.button], timeout: 3) {
            how.tap()
            let up = waitForText(["Pin Addify"], timeout: 5) != nil
            let s2 = shot("howto")
            var closed = false
            if up, let later = waitForElement("do this later", exact: false, types: [.button], timeout: 3) {
                later.tap()
                Thread.sleep(forTimeInterval: 1)
                closed = !sees("Pin Addify")
            }
            log.line("howto", up && closed ? "PASS" : "FAIL", shot: s2, up ? "How to add Addify shows the share-sheet steps; 'I'll do this later' \(closed ? "closes it" : "did not close it")" : "How to add Addify opened nothing")
        }
    }

    func closePaywall() {
        if let x = waitForElement("Close", exact: true, types: [.button], timeout: 3) {
            x.tap()
            Thread.sleep(forTimeInterval: 1)
        }
    }

    func paywallNoProducts() {
        guard goTab("You"), let up = waitForElement("Upgrade", exact: true, types: [.button], timeout: 5) else {
            log.line("paywall-open", "FAIL", shot: shot("no-upgrade"), "no Upgrade button"); return
        }
        up.tap()
        Thread.sleep(forTimeInterval: 1.5)
        let opened = sees("Restore purchases")
        log.line("paywall-open", opened ? "PASS" : "FAIL", shot: shot("paywall"), opened ? "paywall is up" : "Upgrade opened nothing")
        guard opened else { return }
        let t0 = Date()
        while Date().timeIntervalSince(t0) < 25 && sees("Loading prices") { Thread.sleep(forTimeInterval: 1.5) }
        let text = screenText()
        let s = shot("paywall-prices")
        audit("paywall")
        if sees("not available right now", in: text) {
            let retry = waitForElement("Try again", exact: false, types: [.button], timeout: 1) != nil
            log.line("paywall-no-products", retry ? "INFO" : "FAIL", shot: s,
                     "StoreKit returned no products (this Release simulator build has no StoreKit configuration, like a build whose subscriptions are not approved or not attached): the sheet says 'Subscriptions are not available right now. Try again later.' with \(retry ? "a Try again button" : "no plans and no Try again button: a dead end")")
        } else if sees("Monthly", in: text) || sees("Yearly", in: text) {
            log.line("paywall-no-products", "INFO", shot: s, "plans listed")
        } else {
            log.line("paywall-no-products", "FAIL", shot: s, "still '\(sees("Loading prices", in: text) ? "Loading prices..." : "unknown")' after 25 s")
        }
        restoreAndRecord(step: "restore-none")
        // Case-sensitive: Profile's own "Privacy policy" row sits under the paywall in the tree.
        openSafariLink("Terms of Use", step: "paywall-terms", caseSensitive: true)
        openSafariLink("Privacy Policy", step: "paywall-privacy", caseSensitive: true)
        if sees("Restore purchases") { closePaywall() }
        log.line("paywall-close", sees("Restore purchases") ? "FAIL" : "PASS", shot: shot("paywall-closed"), sees("Restore purchases") ? "the X did not close the paywall" : "closed with X")
    }

    func restoreAndRecord(step: String) {
        guard let r = waitForElement("Restore purchases", exact: true, types: [.button], timeout: 4) else {
            log.line(step, "FAIL", shot: shot(step + "-missing"), "no Restore purchases button"); return
        }
        r.tap()
        let t0 = Date()
        var message = ""
        var prompts: [String] = []
        while Date().timeIntervalSince(t0) < 40 {
            if let a = systemAlertInfo(timeout: 0.5) {
                prompts.append(a)
                log.line(step + "-prompt", "INFO", shot: shot(step + "-prompt"), "system prompt during Restore: \(a); tapped Cancel")
                tapAlertButton(preferring: ["Cancel", "Not Now", "Close", "OK"])
            }
            let t = screenText()
            if sees("No active subscription", in: t) { message = "No active subscription found on this Apple ID."; break }
            if sees("Could not reach the App Store", in: t) { message = "Could not reach the App Store. Try again."; break }
            if !sees("Restore purchases", in: t) { message = "(the paywall closed: restored)"; break }
            Thread.sleep(forTimeInterval: 1.5)
        }
        let secs = Int(Date().timeIntervalSince(t0))
        log.line(step, message.isEmpty ? "FAIL" : "PASS", shot: shot(step),
                 message.isEmpty ? "no answer after \(secs) s ('Checking your Apple ID...' never ended)" : "after \(secs) s the page said '\(message)'\(prompts.isEmpty ? "" : "; prompts: " + prompts.joined(separator: " / "))")
    }

    /// target=_blank links go to Safari (EngineWebView createWebViewWith -> openExternal).
    func openSafariLink(_ label: String, step: String, caseSensitive: Bool = false) {
        var found: XCUIElement? = nil
        if caseSensitive {
            let t0 = Date()
            repeat {
                for t in [XCUIElement.ElementType.link, .button] {
                    let e = app.descendants(matching: t).matching(NSPredicate(format: "label == %@", label)).firstMatch
                    if e.exists { found = e; break }
                }
                if found != nil { break }
                Thread.sleep(forTimeInterval: 0.7)
            } while Date().timeIntervalSince(t0) < 4
        } else {
            found = waitForElement(label, exact: true, types: [.link, .button], timeout: 4)
        }
        guard let l = found else {
            log.line(step, "FAIL", shot: shot(step + "-missing"), "no '\(label)' link"); return
        }
        l.tap()
        let inSafari = safari.wait(for: .runningForeground, timeout: 10)
        Thread.sleep(forTimeInterval: 3.5)
        let s = shot(step)
        var page = "?"
        if inSafari {
            let d = safari.debugDescription
            try? d.write(to: cfg.out.appendingPathComponent("\(cfg.device)-\(log.phase)-\(step).safari.ax.txt"), atomically: true, encoding: .utf8)
            let host = URL(string: cfg.engine)?.host ?? "?"
            let loaded = d.contains("Support") || d.contains("Privacy") || d.contains("Terms")
            page = (d.contains(host) ? "the engine host " + host : "another host") + (loaded ? ", page text loaded" : ", no page text read")
            if d.contains("Can’t Open Page") || d.contains("Can't Open Page") || d.contains("cannot be found") { page += ", SAFARI ERROR PAGE" }
        }
        log.line(step, inSafari ? (page.contains("ERROR") ? "FAIL" : "PASS") : "FAIL", shot: s,
                 inSafari ? "'\(label)' opened in Safari (\(page)); the reviewer has left the app" : "tapped '\(label)', Safari did not come up in 10 s")
        app.activate()
        _ = app.wait(for: .runningForeground, timeout: 8)
        Thread.sleep(forTimeInterval: 1)
    }

    func legalRows() {
        guard goTab("You") else { return }
        openSafariLink("Privacy policy", step: "profile-privacy")
        openSafariLink("Support", step: "profile-support")
    }

    /// Clear finds and Delete account raise JavaScript confirms (native alerts). Cancel both.
    func confirmRows() {
        guard goTab("You") else { return }
        for (row, step) in [("Clear finds", "clear-finds"), ("Delete account", "delete-account")] {
            guard let b = waitForElement(row, exact: false, types: [.button], timeout: 4) else {
                log.line(step, "FAIL", shot: shot(step + "-missing"), "no '\(row)' row"); continue
            }
            b.tap()
            let alert = app.alerts.firstMatch
            if alert.waitForExistence(timeout: 5) {
                let text = alert.staticTexts.allElementsBoundByIndex.map { $0.label }.joined(separator: " | ")
                let buttons = alert.buttons.allElementsBoundByIndex.map { $0.label }
                log.line(step, "PASS", shot: shot(step + "-confirm"), "'\(row)' asks '\(text)' with \(buttons); tapped Cancel")
                if alert.buttons["Cancel"].exists { alert.buttons["Cancel"].tap() } else { alert.buttons.firstMatch.tap() }
            } else {
                log.line(step, "FAIL", shot: shot(step + "-nothing"), "tapped '\(row)', no confirmation")
            }
            Thread.sleep(forTimeInterval: 1)
        }
    }

    /// Export finds with songs: the page clicks a blob: link with a download attribute.
    /// EngineWebView has no download handling and allows blob: in the main frame.
    func exportFindsWithSongs() {
        guard goTab("You"), let ex = waitForElement("Export finds", exact: false, types: [.button], timeout: 4) else {
            log.line("export-songs", "FAIL", shot: shot("export-songs-missing"), "no Export finds row"); return
        }
        let finds = firstLabel(containing: "in your Finds") ?? firstLabel(containing: "scans left") ?? ""
        ex.tap()
        Thread.sleep(forTimeInterval: 3)
        let s = shot("export-songs")
        let ax = dumpAX("export-songs")
        let tabs = waitForElement("You", exact: true, types: [.button], timeout: 1) != nil
        let sheet = app.otherElements["ActivityListView"].exists || app.sheets.firstMatch.exists
        let alert = systemAlertInfo(timeout: 1) ?? (app.alerts.firstMatch.exists ? app.alerts.firstMatch.label : nil)
        if !tabs {
            log.line("export-songs", "FAIL", shot: s, "after Export finds the app page is gone (no tab bar): the web view navigated to the text file and there is no way back but force quit. ax=\(ax)")
            app.terminate()
            launchPointed("relaunch-after-export")
            ensureHome()
        } else if sheet {
            log.line("export-songs", "PASS", shot: s, "a share or save sheet opened. ax=\(ax)")
            app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.1)).tap()
        } else {
            log.line("export-songs", "FAIL", shot: s, "with songs saved, Export finds did nothing visible\(alert != nil ? " (alert: \(alert!))" : "") \(finds). ax=\(ax)")
        }
    }

    // =================================================================================
    // MARK: - Appearance, theme, relaunch
    // =================================================================================

    func appearanceAndTheme() {
        _ = goTab("Home")
        dismissKeyboard()
        let dev = XCUIDevice.shared
        dev.appearance = .light
        Thread.sleep(forTimeInterval: 2)
        log.line("appearance-light", "INFO", shot: shot("system-light"), "system set to Light: Info.plist forces Dark and the page pins data-theme=dark, so the app should look the same")
        dev.appearance = .dark
        Thread.sleep(forTimeInterval: 1.5)
        log.line("appearance-dark", "INFO", shot: shot("system-dark"), "system set to Dark")
        // The page's own theme switch sits on the Profile header.
        _ = goTab("You")
        if let t = waitForElement("Switch theme", exact: true, types: [.button], timeout: 3) {
            t.tap()
            Thread.sleep(forTimeInterval: 1.5)
            log.line("page-theme-light-profile", "INFO", shot: shot("page-theme-light-profile"), "the page's own theme switch, light, on Profile")
            _ = goTab("Home")
            dismissKeyboard()
            log.line("page-theme-light-home", "INFO", shot: shot("page-theme-light-home"), "home in the page's light theme (the SCAN dial)")
            audit("home-light-theme", types: [.contrast])
            _ = goTab("You")
            if let t2 = waitForElement("Switch theme", exact: true, types: [.button], timeout: 2) { t2.tap() }
            Thread.sleep(forTimeInterval: 1)
        } else {
            log.line("page-theme", "WARN", shot: shot("no-theme-switch"), "no 'Switch theme' button on Profile")
        }
        _ = goTab("Home")
    }

    func relaunchToHome() {
        app.terminate()
        Thread.sleep(forTimeInterval: 1)
        launchPointed("relaunch")
        let t = screenText()
        let onb = sees("Find the exact song", in: t) && sees("Set it up", in: t)
        log.line("relaunch-home", onb ? "WARN" : (sees("Clip link", in: t) ? "PASS" : "FAIL"), shot: shot("relaunch-home"),
                 onb ? "onboarding showed again on the second launch" : "second launch opens on home")
    }

    // =================================================================================
    // MARK: - Small helpers
    // =================================================================================

    /// Taps a button found by label and waits for any of the expected texts. No tap happens
    /// unless the element exists, so a missing button is a logged FAIL, never a crash.
    @discardableResult
    func tapButton(_ label: String, step: String, expect: [String], exact: Bool = true, quiet: Bool = false) -> Bool {
        guard let b = waitForElement(label, exact: exact, types: [.button, .link], timeout: 8) else {
            log.line(step, "FAIL", shot: shot(step + "-missing"), "no '\(label)' button (screen: \(onbScreenName()))")
            return false
        }
        b.tap()
        let got = waitForText(expect, timeout: 8)
        if !quiet || got == nil {
            log.line(step, got != nil ? "PASS" : "FAIL", shot: shot(step), got != nil ? "tapped '\(label)', now '\(got!)'" : "tapped '\(label)', none of \(expect) appeared")
        }
        return got != nil
    }

    func auditName(_ t: XCUIAccessibilityAuditType) -> String {
        switch t {
        case .contrast: return "contrast"
        case .elementDetection: return "elementDetection"
        case .hitRegion: return "hitRegion"
        case .sufficientElementDescription: return "description"
        case .dynamicType: return "dynamicType"
        case .textClipped: return "textClipped"
        case .trait: return "trait"
        default: return "type\(t.rawValue)"
        }
    }

    /// XCUIApplication.performAccessibilityAudit on the current screen. Issues are logged (and
    /// written in full to a file), never turned into XCTest failures, so the walk goes on.
    func audit(_ screen: String, types: XCUIAccessibilityAuditType = .all) {
        var issues: [String] = []
        do {
            try MainActor.assumeIsolated {
                try app.performAccessibilityAudit(for: types) { issue in
                    let el = issue.element.map { "\($0.elementType.rawValue) '\($0.label.prefix(40))'" } ?? "-"
                    issues.append("\(self.auditName(issue.auditType)): \(issue.compactDescription) [\(el)]")
                    return true
                }
            }
            let file = "\(cfg.device)-\(log.phase)-a11y-\(screen).txt"
            try? issues.joined(separator: "\n").write(to: cfg.out.appendingPathComponent(file), atomically: true, encoding: .utf8)
            var counts: [String: Int] = [:]
            for i in issues { let k = String(i.split(separator: ":").first ?? "?"); counts[k, default: 0] += 1 }
            log.line("a11y-\(screen)", issues.isEmpty ? "PASS" : "INFO", shot: nil,
                     "\(issues.count) audit issue(s) \(counts.sorted { $0.key < $1.key }.map { "\($0.key) \($0.value)" }.joined(separator: ", ")); first: \(issues.first ?? "-"); all in \(file)")
        } catch {
            log.line("a11y-\(screen)", "WARN", shot: nil, "the audit did not run: \(error)")
        }
    }
}
