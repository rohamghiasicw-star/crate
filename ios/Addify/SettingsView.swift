import SwiftUI

/* ---------- engine settings ----------
   One field, because the engine address is the one thing that changes under the app:
   the free tunnel hostname rotates on every cloudflared restart. Written to the app
   group so the Share Extension sees the same value. No Settings.bundle on purpose:
   that writes to the app's own defaults, which the extension cannot read. */
struct SettingsView: View {
    @EnvironmentObject private var bridge: EngineBridge
    @Environment(\.dismiss) private var dismiss

    @State private var text = EngineConfig.baseURLString
    @State private var status: Status = .idle

    enum Status: Equatable {
        case idle, testing, ok(String), fail(String)
    }

    var body: some View {
        NavigationView {
            Form {
                Section {
                    TextField("https://engine.example.com", text: $text)
                        .keyboardType(.URL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .font(.body.monospaced())
                    HStack {
                        Button("Test") { test() }
                            .disabled(status == .testing || text.trimmingCharacters(in: .whitespaces).isEmpty)
                        Spacer()
                        statusLabel
                    }
                } header: {
                    Text("Engine address")
                } footer: {
                    Text("The app loads its page and API from this address. Test checks GET /health and accepts only the Addify engine.")
                }

                Section {
                    Button("Reset to default") {
                        text = EngineConfig.defaultBaseURL
                        status = .idle
                    }
                    .disabled(EngineConfig.normalise(text) == EngineConfig.defaultBaseURL)
                } footer: {
                    Text("Default: \(EngineConfig.defaultBaseURL)")
                        .font(.caption.monospaced())
                }

                Section {
                    LabeledContent("App", value: appVersion)
                    LabeledContent("App group", value: AppGroup.id)
                } header: {
                    Text("About")
                }
            }
            .navigationTitle("Addify")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) { Button("Save") { save() } }
            }
        }
    }

    @ViewBuilder private var statusLabel: some View {
        switch status {
        case .idle: EmptyView()
        case .testing: ProgressView()
        case .ok(let b):
            Label(b.isEmpty ? "Engine OK" : "Engine OK, build \(b)", systemImage: "checkmark.circle.fill")
                .foregroundStyle(.green).font(.footnote)
        case .fail(let m):
            Label(m, systemImage: "xmark.octagon.fill")
                .foregroundStyle(.red).font(.footnote).lineLimit(2)
        }
    }

    private var appVersion: String {
        let v = (Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String) ?? "0"
        let b = (Bundle.main.infoDictionary?["CFBundleVersion"] as? String) ?? "0"
        return "\(v) (\(b))"
    }

    private func test() {
        guard let u = URL(string: EngineConfig.normalise(text)) else { status = .fail("Not a valid URL"); return }
        status = .testing
        Task {
            do {
                let h = try await EngineConfig.healthCheck(u)
                await MainActor.run { status = .ok(h.build ?? "") }
            } catch {
                await MainActor.run { status = .fail(error.localizedDescription) }
            }
        }
    }

    private func save() {
        let before = EngineConfig.baseURL
        EngineConfig.setBaseURL(text)
        dismiss()
        /* Reload only when the host changed; the page keeps its state otherwise. */
        if EngineConfig.baseURL != before { bridge.retry() }
    }
}
