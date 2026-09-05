import SwiftUI

/* ---------- native result toast ----------
   crate.html's finish() posts the landed result through the 'addify' message handler.
   The page already shows the card; the toast exists for the two moments the page cannot
   cover: the haptic tick, and a tap that hands the crowned upload straight to the music
   app. Tap = open the exact edit (or dismiss when there is no crown). */
struct ResultToast: View {
    let payload: ResultPayload
    let onTap: () -> Void

    var body: some View {
        Button(action: onTap) {
            HStack(spacing: 12) {
                Image(systemName: "checkmark.circle.fill")
                    .font(.system(size: 22, weight: .semibold))
                    .foregroundStyle(Color(red: 0.24, green: 0.80, blue: 0.52))
                VStack(alignment: .leading, spacing: 2) {
                    Text("Match found")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(.secondary)
                    Text(payload.line)
                        .font(.subheadline.weight(.semibold))
                        .lineLimit(2)
                        .foregroundStyle(.primary)
                    if let who = payload.uploader, !who.isEmpty {
                        Text("Crowned: \(who)")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .lineLimit(1)
                    }
                }
                Spacer(minLength: 0)
                if payload.exactURL != nil {
                    Image(systemName: "arrow.up.forward.app")
                        .foregroundStyle(.secondary)
                }
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 12)
            .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 16, style: .continuous).strokeBorder(.white.opacity(0.12)))
            .shadow(color: .black.opacity(0.35), radius: 18, y: 8)
            .padding(.horizontal, 12)
        }
        .buttonStyle(.plain)
        .preferredColorScheme(.dark)
    }
}
