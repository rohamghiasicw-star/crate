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
                .onChange(of: scenePhase) { phase in
                    bridge.isActive = (phase == .active)
                    /* The inbox is the guaranteed share path; drain it on every
                       foreground, not just cold launch. */
                    if phase == .active { bridge.drainInbox() }
                }
        }
    }
}
