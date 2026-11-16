import SwiftUI
import UIKit

struct ContentView: View {
    @EnvironmentObject private var bridge: EngineBridge
    /* NATIVE-R6-05 (build 31): with Reduce Motion on, the result banner fades in and out instead of
       sliding down from the top. */
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    /* NATIVE-R6-09 (build 31): which native screen covers the page, if any (VoiceOver is told when it
       changes). */
    private var nativeScreen: Int { bridge.unreachable ? 2 : (bridge.booting ? 1 : 0) }

    /* NATIVE-R6-06 (build 31): the page's light theme paints a near-white strip under the status bar,
       so the app takes the light appearance there and the status bar's time and battery turn dark.
       The loading and error screens are dark, so they keep the dark appearance. */
    private var lightPage: Bool { bridge.pageTheme == "light" && nativeScreen == 0 }

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

            /* BUILD 20: the native loading screen until the engine page has loaded once. App
               Review's iPad (2026-10-30) sat on a blank dark screen for three minutes; now the
               first thing anyone sees is Addify loading, then the app or a clear error. */
            if bridge.booting && !bridge.unreachable {
                BootView()
                    .transition(.opacity)
            }

            if bridge.unreachable {
                UnreachableView()
                    .transition(.opacity)
            }

            if let t = bridge.toast {
                /* Tap only dismisses. The toast sits over the result header, so a tap meant for
                   Back or Share used to open the crowned upload in Safari (2026-11-04 sim, SE and
                   iPad). The card underneath already has every open button. */
                ResultToast(payload: t) {
                    withAnimation { bridge.toast = nil }
                }
                .padding(.top, 8)
                .transition(reduceMotion ? .opacity : .move(edge: .top).combined(with: .opacity))
                .zIndex(2)
            }
        }
        }
        .preferredColorScheme(lightPage ? .light : .dark)
        /* The loading screen, the error screen or the page took over: VoiceOver starts again at the top
           of what is now on screen (the error's title, the page) instead of on what went away. */
        .onChange(of: nativeScreen) { _ in
            DispatchQueue.main.async { UIAccessibility.post(notification: .screenChanged, argument: nil) }
        }
        /* Debug builds only, like every way in: Release builds compile no Settings screen. */
        #if DEBUG
        .sheet(isPresented: $bridge.showSettings) {
            SettingsView()
                .environmentObject(bridge)
        }
        #endif
    }
}

/* BUILD 20: what the app shows while it finds a working engine address and loads the page.
   Same ground and accent as the page and the error screen, so nothing flashes. After 8 s it
   says it is still connecting, so a slow network never looks frozen. NATIVE-R7-02 (build 32): with
   it comes the error screen's Try again, which starts the load over (the load itself now waits up to
   WebKit's 60 s with one address). NATIVE-R7-06 (build 32): VoiceOver hears "Still connecting" when
   it appears, and the empty line before it is not a stop for VoiceOver. */
struct BootView: View {
    @EnvironmentObject private var bridge: EngineBridge
    @State private var slow = false
    /* Each Try again waits its own 8 s before the line and the button come back. */
    @State private var attempt = 0

    var body: some View {
        VStack(spacing: 16) {
            Spacer()
            Image(systemName: "waveform")
                .font(.system(size: 44, weight: .semibold))
                .foregroundStyle(Color(red: 0.36, green: 0.29, blue: 0.91))
                .accessibilityHidden(true)
            Text("Addify")
                .font(.title2.weight(.semibold))
            ProgressView()
                .tint(.white)
                .padding(.top, 4)
            Text(slow ? "Still connecting..." : " ")
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .accessibilityHidden(!slow)
            /* Below the line, over the space under it, so what is above stays where it was. The space is
               given the screen's width and the button its own size (a spacer in a column has no width). */
            Spacer()
                .frame(maxWidth: .infinity)
                .overlay(alignment: .top) {
                    if slow {
                        Button("Try again") {
                            slow = false
                            attempt += 1
                            bridge.retryResolvingFirst()
                        }
                        .buttonStyle(.borderedProminent)
                        .tint(Color(red: 0.36, green: 0.29, blue: 0.91))
                        .fixedSize()
                        .padding(.top, 2)
                    }
                }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color(red: 0.090, green: 0.078, blue: 0.122).ignoresSafeArea())
        .foregroundStyle(.white)
        /* NATIVE-R6-09 (build 31): VoiceOver stays on this screen while it covers the page. */
        .accessibilityElement(children: .contain)
        .accessibilityAddTraits(.isModal)
        .task(id: attempt) {
            try? await Task.sleep(nanoseconds: 8_000_000_000)
            guard !Task.isCancelled else { return }
            slow = true
            UIAccessibility.post(notification: .announcement, argument: "Still connecting")
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
                .accessibilityHidden(true)
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
        /* NATIVE-R6-09 (build 31): VoiceOver stays on this screen while it covers the page. */
        .accessibilityElement(children: .contain)
        .accessibilityAddTraits(.isModal)
    }
}
