import SwiftUI

struct ContentView: View {
    @EnvironmentObject private var bridge: EngineBridge

    var body: some View {
        GeometryReader { geo in
        ZStack(alignment: .top) {
            Color(red: 0.090, green: 0.078, blue: 0.122).ignoresSafeArea()

            EngineWebView(bridge: bridge)
                .ignoresSafeArea()

            /* A long press on the status-bar strip opens Settings. The page owns the
               whole screen and has its own header, so the native control has to sit
               somewhere the page does not use for taps. Documented in README.

               IT MUST COVER THE STATUS BAR ONLY (2026-09-26). It used to be a 44 pt strip
               laid out inside the safe area, which put it just BELOW the status bar, right
               over the page's header row: the result screen's back chevron and share
               button sat under an invisible hit-testable view, so a real tap never reached
               them (Konnor: "the back button in the top left doesn't do anything", asked
               5+ times; every web-side fix passed in a desktop browser, where this native
               strip does not exist). Now it is exactly the status bar's height and pushed
               up into it, where the page draws nothing tappable. */
            /* Laid out at the top of the safe area, then moved up by exactly the status
               bar height: .offset moves the hit area with it, so the strip covers
               0..statusBar and nothing below. (.ignoresSafeArea on a fixed-height view
               would GROW it upward instead of moving it, and it would still reach the
               header.)
               DEBUG ONLY (2026-09-29, App Store review): a hidden long press that opens a
               server-address form is a hidden feature (2.3.1) and developer UI (2.1), so
               Release builds (TestFlight and the App Store) do not have it. */
            #if DEBUG
            Color.clear
                .frame(height: max(geo.safeAreaInsets.top, 20))
                .contentShape(Rectangle())
                .onLongPressGesture(minimumDuration: 1.2) { bridge.showSettings = true }
                .offset(y: -max(geo.safeAreaInsets.top, 20))
            #endif

            if bridge.unreachable {
                UnreachableView()
                    .transition(.opacity)
            }

            if let t = bridge.toast {
                ResultToast(payload: t) {
                    if let u = t.exactURL { bridge.openExternal(u) }
                    withAnimation { bridge.toast = nil }
                }
                .padding(.top, 8)
                .transition(.move(edge: .top).combined(with: .opacity))
                .zIndex(2)
            }
        }
        }
        .sheet(isPresented: $bridge.showSettings) {
            SettingsView()
                .environmentObject(bridge)
        }
    }
}

/* Shown instead of a blank web view when the engine does not answer. Release builds show
   plain user copy and one Try again button (which re-resolves the address first); the raw
   engine address and the Settings button are developer UI and exist in Debug only. */
struct UnreachableView: View {
    @EnvironmentObject private var bridge: EngineBridge

    var body: some View {
        VStack(spacing: 18) {
            Spacer()
            Image(systemName: "waveform.slash")
                .font(.system(size: 44, weight: .semibold))
                .foregroundStyle(Color(red: 0.36, green: 0.29, blue: 0.91))
            /* CALL-RETRY: "check your internet" only when the phone said it has none. An engine
               that did not answer while the Wi-Fi is fine is not the user's connection
               (Konnor: "it says you're not on Wi-Fi, but we are"). */
            Text(bridge.offline ? "You're offline" : "Can't reach Addify")
                .font(.title2.weight(.semibold))
            #if DEBUG
            Text(EngineConfig.baseURL.absoluteString)
                .font(.footnote.monospaced())
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 28)
            #endif
            Text(bridge.offline ? "Check your internet connection and try again."
                                : "Addify didn't answer. Try again in a moment.")
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 36)
            HStack(spacing: 12) {
                /* Resolve THEN reload. A plain reload re-requests a hostname that rotated
                   away, so it fails every time until the app is force quit. */
                Button("Try again") { bridge.retryResolvingFirst() }
                    .buttonStyle(.borderedProminent)
                    .tint(Color(red: 0.36, green: 0.29, blue: 0.91))
                #if DEBUG
                Button("Settings") { bridge.showSettings = true }
                    .buttonStyle(.bordered)
                #endif
            }
            Spacer()
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color(red: 0.090, green: 0.078, blue: 0.122).ignoresSafeArea())
        .foregroundStyle(.white)
    }
}
