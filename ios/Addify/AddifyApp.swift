import SwiftUI

@main
struct AddifyApp: App {
    @StateObject private var bridge = EngineBridge()
    @Environment(\.scenePhase) private var scenePhase

    /* The engine-address Settings sheet is a developer tool and ships in Debug builds only
       (App Review 2.3.1: no hidden features; 2.1: no placeholder UI). A TestFlight tester
       who typed an address into an older build would otherwise stay pinned to it forever
       with no screen left to clear it, so a Release build drops the manual override and
       goes back to the automatic directory lookup. */
    init() {
        #if !DEBUG
        EngineConfig.setBaseURL("")
        #endif
    }

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(bridge)
                /* addify://scan?url=... from the Share Extension (and any other app
                   that learns the scheme). */
                .onOpenURL { url in bridge.handleIncoming(url) }
                /* The engine hostname rotates while the app is closed, so the value
                   baked into the last launch is usually already dead. Resolve before the
                   web view is asked for anything. */
                .task { await EngineConfig.resolveFromDirectory() }
                .onChange(of: scenePhase) { phase in
                    bridge.isActive = (phase == .active)
                    /* The inbox is the guaranteed share path; drain it on every
                       foreground, not just cold launch. */
                    if phase == .active {
                        bridge.drainInbox()
                        /* Same reason as launch: a phone that sat in a pocket for an hour
                           comes back to a hostname that no longer resolves.
                           CALL-RETRY: coming back to "Can't reach Addify" is the moment to try
                           again, so the user does not have to tap it (resolve, then reload). */
                        if bridge.unreachable { bridge.retryResolvingFirst() }
                        else { Task { await EngineConfig.resolveFromDirectory() } }
                    }
                }
        }
    }
}
