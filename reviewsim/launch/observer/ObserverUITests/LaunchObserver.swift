import XCTest
import ObjectiveC

/* =====================================================================================
   DEVICE-LAUNCH LAB: accessibility observer
   The host-side launch loop (reviewsim/launch/scripts/launchloop.py) uninstalls, installs
   and launches com.addify.app with simctl, and drops a request file into $RS_OUT/axreq
   whenever it wants to know what is on screen. This test answers each request with the
   app's state and its full accessibility tree in $RS_OUT/axres/<id>.txt. It never taps,
   types or activates anything, so it cannot change what the app does.
   Settings arrive as TEST_RUNNER_RS_* environment variables (xcodebuild strips the prefix).
   ===================================================================================== */

enum Quiescence {
    static var report: [String] = []
    /* XCUITest waits for the app to go idle before every query. A page with CSS animations
       (or a spinner) can hold that wait for a long time, which would stall a dump. */
    static func disable() {
        guard let cls = NSClassFromString("XCUIApplicationProcess") else {
            report.append("XCUIApplicationProcess missing"); return
        }
        let yes: @convention(block) (AnyObject) -> Bool = { _ in true }
        var patched = false
        for name in ["shouldSkipPreEventQuiescence", "shouldSkipPostEventQuiescence"] {
            if let m = class_getInstanceMethod(cls, NSSelectorFromString(name)) {
                method_setImplementation(m, imp_implementationWithBlock(yes))
                report.append("\(name) -> YES"); patched = true
            }
        }
        if !patched {
            for (sel, arity) in [("waitForQuiescenceIncludingAnimationsIdle:", 1),
                                 ("waitForQuiescenceIncludingAnimationsIdle:isPreEvent:", 2)] {
                guard let m = class_getInstanceMethod(cls, NSSelectorFromString(sel)) else { continue }
                if arity == 1 {
                    let b: @convention(block) (AnyObject, Bool) -> Void = { _, _ in }
                    method_setImplementation(m, imp_implementationWithBlock(b))
                } else {
                    let b: @convention(block) (AnyObject, Bool, Bool) -> Void = { _, _, _ in }
                    method_setImplementation(m, imp_implementationWithBlock(b))
                }
                report.append("\(sel) -> no-op")
            }
        }
    }
}

final class LaunchObserver: XCTestCase {
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

    func testServe() {
        continueAfterFailure = true
        Quiescence.disable()
        let env = ProcessInfo.processInfo.environment
        let out = URL(fileURLWithPath: env["RS_OUT"] ?? NSTemporaryDirectory())
        let reqDir = out.appendingPathComponent("axreq")
        let resDir = out.appendingPathComponent("axres")
        let fm = FileManager.default
        try? fm.createDirectory(at: reqDir, withIntermediateDirectories: true)
        try? fm.createDirectory(at: resDir, withIntermediateDirectories: true)
        let maxSecs = Double(env["RS_MAX_SECS"] ?? "21000") ?? 21000
        let t0 = Date()
        try? "ready \(ISO8601DateFormatter().string(from: t0)) patch: \(Quiescence.report.joined(separator: ", "))\n"
            .write(to: out.appendingPathComponent("ax-ready.txt"), atomically: true, encoding: .utf8)
        print("RS| ax server ready; " + Quiescence.report.joined(separator: ", "))
        var served = 0
        while Date().timeIntervalSince(t0) < maxSecs {
            if fm.fileExists(atPath: reqDir.appendingPathComponent("STOP").path) { break }
            let names = ((try? fm.contentsOfDirectory(atPath: reqDir.path)) ?? []).filter { $0.hasSuffix(".req") }.sorted()
            for name in names {
                let id = String(name.dropLast(4))
                let started = Date()
                let app = XCUIApplication(bundleIdentifier: "com.addify.app")
                let st = app.state
                var text = "state=\(stateName(st))\n"
                if st == .runningForeground || st == .runningBackground {
                    text += app.debugDescription
                }
                let sb = XCUIApplication(bundleIdentifier: "com.apple.springboard")
                let alert = sb.alerts.firstMatch
                if alert.exists { text += "\n== springboard alert\n" + alert.debugDescription }
                text += "\n== dump took \(String(format: "%.2f", Date().timeIntervalSince(started))) s\n"
                try? text.write(to: resDir.appendingPathComponent(id + ".txt"), atomically: true, encoding: .utf8)
                try? fm.removeItem(at: reqDir.appendingPathComponent(name))
                served += 1
            }
            Thread.sleep(forTimeInterval: 0.1)
        }
        print("RS| ax server stopped after \(served) dumps")
    }
}
