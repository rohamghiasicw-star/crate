import XCTest
import StoreKit
import StoreKitTest

/* =====================================================================================
   REVIEW-SIM, DEVICE-PATH LANE: the paywall against StoreKit, in a TEST-ONLY scheme.
   mockreview/store.yml builds the Addify app from ios/ (Debug, the configuration StoreKit
   testing supports) with AddifyReview.storekit attached to the scheme, and this UI test bundle
   targets it (TEST_TARGET_NAME Addify) so SKTestSession can drive the local StoreKit
   environment: buy monthly, buy yearly, the user cancels, the purchase fails, restore with
   nothing bought, restore after a purchase made elsewhere, and StoreKit returning no products.

   The paywall code under test is the same in Debug and Release: crate.html (served by the
   test box) and StoreKitHandler / AddifyStore in EngineWebView.swift. Nothing here ships.
   ===================================================================================== */

extension ReviewerUITests {

    @objc func testStoreFlows() {
        log = StepLog(cfg: cfg, phase: "store")
        let session = storeSession()
        launchPointed("launch")
        ensureHome()
        dismissKeyboard()

        // 1. Products: names, prices, the trial line.
        if openPaywall("store-paywall") { paywallPlansCheck() }

        // 2. The user cancels on Apple's sheet.
        if let s = session { s.disableDialogs = false }
        buyPlan("Monthly", step: "buy-cancel", answer: "cancel")

        // 3. The purchase fails.
        if let s = session {
            let e = blockingAsync { try await s.setSimulatedError(.generic(.unknown), forAPI: .purchase) }
            log.line("store-fail-setup", e == nil ? "INFO" : "WARN", shot: nil, e == nil ? "StoreKit set to fail purchases" : "could not set the purchase error: \(e!)")
            s.disableDialogs = true
        }
        buyPlan("Monthly", step: "buy-fails", answer: "none")
        if let s = session { _ = blockingAsync { try await s.setSimulatedError(nil, forAPI: .purchase) } }

        // 4. Restore with nothing bought.
        restoreAndRecord(step: "store-restore-none")

        // 5. Buy monthly (no dialog), then what a subscriber sees.
        buyPlan("Monthly", step: "buy-monthly", answer: "none")
        subscriberChecks("monthly")

        // 6. The entitlement survives a relaunch.
        app.terminate()
        launchPointed("relaunch-subscribed")
        ensureHome()
        dismissKeyboard()
        let pill = firstLabel(containing: "Unlimited") ?? firstLabel(containing: "scans left") ?? "?"
        log.line("store-relaunch", pill == "Unlimited" ? "PASS" : "FAIL", shot: shot("store-relaunch"), "after a relaunch the counter says '\(pill)'")

        // 7. Restore after a purchase made outside this install (another device / a reinstall).
        if let s = session {
            s.clearTransactions()
            app.terminate()
            launchPointed("relaunch-cleared")
            ensureHome()
            dismissKeyboard()
            if openPaywall("store-paywall-2") {
                let e = blockingAsync { _ = try await s.buyProduct(identifier: "com.addify.app.unlimited.yearly") }
                log.line("store-outside-buy", e == nil ? "INFO" : "WARN", shot: nil, e == nil ? "a yearly purchase was made outside the app (SKTestSession.buyProduct)" : "buyProduct failed: \(e!)")
                restoreAndRecord(step: "store-restore-after-buy")
                let p2 = firstLabel(containing: "Unlimited") ?? firstLabel(containing: "scans left") ?? "?"
                log.line("store-restore-result", p2 == "Unlimited" ? "PASS" : "FAIL", shot: shot("store-after-restore"), "after Restore the counter says '\(p2)'")
            }
        }

        // 8. Buy yearly through Apple's sheet (dialogs on), trial line visible on the sheet.
        if let s = session {
            s.clearTransactions()
            s.disableDialogs = false
            app.terminate()
            launchPointed("relaunch-for-yearly")
            ensureHome()
            dismissKeyboard()
            if openPaywall("store-paywall-3") {
                buyPlan("Yearly", step: "buy-yearly", answer: "confirm")
                subscriberChecks("yearly")
            }
        }

        // 9. StoreKit returns no products (a build whose subscriptions are not approved yet).
        if let s = session {
            s.clearTransactions()
            let e = blockingAsync { try await s.setSimulatedError(.generic(.networkError(URLError(.notConnectedToInternet))), forAPI: .loadProducts) }
            app.terminate()
            launchPointed("relaunch-no-products")
            ensureHome()
            dismissKeyboard()
            if openPaywall("store-paywall-none") {
                let t0 = Date()
                while Date().timeIntervalSince(t0) < 20 && sees("Loading prices") { Thread.sleep(forTimeInterval: 1.5) }
                let none = sees("not available right now")
                let retry = waitForElement("Try again", exact: false, types: [.button], timeout: 1) != nil
                log.line("store-no-products", none ? (retry ? "PASS" : "FAIL") : "WARN", shot: shot("store-no-products"),
                         none ? "products unavailable: 'Subscriptions are not available right now. Try again later.' with \(retry ? "a Try again button" : "no plans and no Try again button")"
                              : "products still listed with the load error set (\(e.map { "\($0)" } ?? "error set"))")
                restoreAndRecord(step: "store-restore-no-products")
                closePaywall()
            }
            _ = blockingAsync { try await s.setSimulatedError(nil, forAPI: .loadProducts) }
        }
        finish()
    }

    // MARK: helpers

    func storeSession() -> SKTestSession? {
        do {
            let s = try SKTestSession(configurationFileNamed: "AddifyReview")
            s.resetToDefaultState()
            s.clearTransactions()
            s.disableDialogs = true
            log.line("store-session", "PASS", shot: nil, "SKTestSession loaded AddifyReview.storekit (monthly + yearly, 3-day trial on yearly)")
            return s
        } catch {
            log.line("store-session", "FAIL", shot: nil, "SKTestSession could not start: \(error). The paywall below runs against whatever StoreKit answers.")
            return nil
        }
    }

    /// Runs an async StoreKitTest call from this synchronous UI test and waits for it.
    func blockingAsync(_ body: @escaping () async throws -> Void) -> Error? {
        let sem = DispatchSemaphore(value: 0)
        var err: Error? = nil
        Task.detached {
            do { try await body() } catch { err = error }
            sem.signal()
        }
        if sem.wait(timeout: .now() + 30) == .timedOut { return NSError(domain: "review-sim", code: 1, userInfo: [NSLocalizedDescriptionKey: "timed out"]) }
        return err
    }

    func openPaywall(_ step: String) -> Bool {
        guard goTab("You") else { return false }
        guard let up = waitForElement("Upgrade", exact: true, types: [.button], timeout: 5) else {
            log.line(step, "FAIL", shot: shot(step + "-no-upgrade"), "no Upgrade button on Profile (counter '\(firstLabel(containing: "scans left") ?? firstLabel(containing: "Unlimited") ?? "?")')")
            return false
        }
        up.tap()
        Thread.sleep(forTimeInterval: 1.5)
        let t0 = Date()
        while Date().timeIntervalSince(t0) < 20 && sees("Loading prices") { Thread.sleep(forTimeInterval: 1.2) }
        let ok = sees("Restore purchases")
        log.line(step, ok ? "PASS" : "FAIL", shot: shot(step), ok ? "paywall is up" : "Upgrade opened nothing")
        return ok
    }

    func paywallPlansCheck() {
        let monthly = app.buttons.matching(NSPredicate(format: "label CONTAINS[c] 'Monthly'")).firstMatch
        let yearly = app.buttons.matching(NSPredicate(format: "label CONTAINS[c] 'Yearly'")).firstMatch
        let m = monthly.exists ? monthly.label.replacingOccurrences(of: "\n", with: " ") : "(none)"
        let y = yearly.exists ? yearly.label.replacingOccurrences(of: "\n", with: " ") : "(none)"
        let ok = monthly.exists && yearly.exists && m.contains("4.99") && y.contains("24.99")
        let trial = y.lowercased().contains("free")
        log.line("store-plans", ok ? "PASS" : "FAIL", shot: shot("store-plans"),
                 "monthly '\(m)'; yearly '\(y)'; trial line on yearly: \(trial ? "yes" : "no")")
        audit("paywall-with-plans")
    }

    /// answer: "cancel" taps Cancel on Apple's sheet, "confirm" subscribes on it, "none" expects
    /// no sheet (dialogs off or an error).
    func buyPlan(_ plan: String, step: String, answer: String) {
        if !sees("Restore purchases") { _ = openPaywall(step + "-reopen") }
        let btn = app.buttons.matching(NSPredicate(format: "label CONTAINS[c] %@", plan)).firstMatch
        guard btn.exists else {
            log.line(step, "FAIL", shot: shot(step + "-no-plan"), "no '\(plan)' plan button on the paywall"); return
        }
        btn.tap()
        Thread.sleep(forTimeInterval: 2.5)
        var sheetNote = "no purchase sheet"
        if answer != "none" {
            let want = answer == "cancel" ? ["Cancel", "Close", "Not Now"] : ["Subscribe", "Confirm", "Try It Free", "Start Free Trial", "Purchase", "Buy", "OK"]
            var tapped = false
            let t0 = Date()
            while !tapped && Date().timeIntervalSince(t0) < 15 {
                for root in [app, springboard] {
                    for l in want {
                        let b = root.buttons.matching(NSPredicate(format: "label ==[c] %@", l)).firstMatch
                        if b.exists && b.isHittable {
                            let s = shot(step + "-sheet")
                            let texts = root.staticTexts.allElementsBoundByIndex.prefix(14).map { $0.label }.filter { !$0.isEmpty }.joined(separator: " | ")
                            sheetNote = "Apple's sheet (\(s)) said: \(texts.prefix(300)); tapped '\(l)'"
                            b.tap(); tapped = true; break
                        }
                    }
                    if tapped { break }
                }
                if !tapped { Thread.sleep(forTimeInterval: 1) }
            }
            if !tapped { sheetNote = "no purchase sheet with \(want) appeared in 15 s" }
        }
        Thread.sleep(forTimeInterval: 3)
        if let a = systemAlertInfo(timeout: 2) {   // e.g. "You're all set" after a purchase
            sheetNote += "; then a system alert: \(a)"
            tapAlertButton(preferring: ["OK", "Done", "Close"])
            Thread.sleep(forTimeInterval: 1.5)
        }
        let open = sees("Restore purchases")
        let msg = firstLabel(containing: "did not go through") ?? firstLabel(containing: "waiting for approval") ?? ""
        let pill = firstLabel(containing: "Unlimited") ?? firstLabel(containing: "scans left") ?? "?"
        let s = shot(step)
        switch step {
        case "buy-cancel":
            log.line(step, open && msg.isEmpty ? "PASS" : "FAIL", shot: s, "\(sheetNote); paywall \(open ? "still open" : "closed"), message '\(msg)', counter '\(pill)'")
        case "buy-fails":
            log.line(step, open && !msg.isEmpty ? "PASS" : "FAIL", shot: s, "\(sheetNote); paywall \(open ? "still open" : "closed"); message '\(msg.isEmpty ? "(none: a silent failure)" : msg)'")
        default:
            let ok = !open && pill == "Unlimited"
            log.line(step, ok ? "PASS" : "FAIL", shot: s, "\(sheetNote); paywall \(open ? "still open" : "closed"), counter '\(pill)', message '\(msg)'")
        }
    }

    func subscriberChecks(_ tag: String) {
        guard goTab("You") else { return }
        Thread.sleep(forTimeInterval: 1)
        let t = screenText()
        let plus = sees("Addify Plus", in: t)
        let card = sees("Get Addify Plus", in: t)
        let manage = waitForElement("Manage subscription", exact: false, types: [.button], timeout: 3)
        log.line("subscriber-\(tag)", plus && !card && manage != nil ? "PASS" : "FAIL", shot: shot("subscriber-\(tag)"),
                 "Profile: 'Addify Plus' \(plus ? "shown" : "missing"); upgrade card \(card ? "still shown" : "hidden"); Manage subscription row \(manage != nil ? "present" : "missing")")
        if let m = manage {
            m.tap()
            Thread.sleep(forTimeInterval: 4)
            let s = shot("manage-\(tag)")
            let inSafari = safari.state == .runningForeground
            let appStore = XCUIApplication(bundleIdentifier: "com.apple.AppStore").state == .runningForeground
            let sheet = app.otherElements.matching(NSPredicate(format: "label CONTAINS[c] 'Subscription'")).firstMatch.exists
            log.line("manage-\(tag)", inSafari || appStore || sheet ? "INFO" : "FAIL", shot: s,
                     inSafari ? "Manage subscription left the app for Safari (apps.apple.com/account/subscriptions; the simulator has no App Store app)"
                              : appStore ? "Manage subscription opened the App Store" : sheet ? "a subscriptions sheet opened in the app" : "Manage subscription did nothing visible")
            if inSafari || appStore { app.activate(); _ = app.wait(for: .runningForeground, timeout: 8) }
        }
    }
}
