import SwiftUI

@main
struct AddifyApp: App {
    @StateObject private var bridge = EngineBridge()
    @Environment(\.scenePhase) private var scenePhase

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
                           comes back to a hostname that no longer resolves. */
                        Task { await EngineConfig.resolveFromDirectory() }
                    }
                }
        }
    }
}
