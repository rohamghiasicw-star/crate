import SwiftUI

struct ContentView: View {
    @EnvironmentObject private var bridge: EngineBridge

    var body: some View {
        ZStack(alignment: .top) {
            Color(red: 0.04, green: 0.04, blue: 0.06).ignoresSafeArea()

            EngineWebView(bridge: bridge)
                .ignoresSafeArea()

            /* A long press on the status-bar strip opens Settings. The page owns the
               whole screen and has its own header, so the native control has to sit
               somewhere the page does not use for taps. Documented in README. */
            Color.clear
                .frame(height: 44)
                .contentShape(Rectangle())
                .onLongPressGesture(minimumDuration: 1.2) { bridge.showSettings = true }

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
        .sheet(isPresented: $bridge.showSettings) {
            SettingsView()
                .environmentObject(bridge)
        }
    }
}

/* Shown instead of a blank web view when the engine does not answer. The number one
   reason is the tunnel hostname rotated (free trycloudflare), so Settings is one tap. */
struct UnreachableView: View {
    @EnvironmentObject private var bridge: EngineBridge

    var body: some View {
        VStack(spacing: 18) {
            Spacer()
            Image(systemName: "waveform.slash")
                .font(.system(size: 44, weight: .semibold))
                .foregroundStyle(Color(red: 0.36, green: 0.29, blue: 0.91))
            Text("Engine unreachable")
                .font(.title2.weight(.semibold))
            Text(EngineConfig.baseURL.absoluteString)
                .font(.footnote.monospaced())
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 28)
            Text("Check the engine is running and the address above is the current tunnel.")
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 36)
            HStack(spacing: 12) {
                Button("Retry") { bridge.retry() }
                    .buttonStyle(.borderedProminent)
                    .tint(Color(red: 0.36, green: 0.29, blue: 0.91))
                Button("Settings") { bridge.showSettings = true }
                    .buttonStyle(.bordered)
            }
            Spacer()
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color(red: 0.04, green: 0.04, blue: 0.06).ignoresSafeArea())
        .foregroundStyle(.white)
    }
}
