import SwiftUI

/* ---------- native result toast ----------
   crate.html's finish() posts the landed result through the 'addify' message handler.
   The page already shows the card; the toast exists for the two moments the page cannot
   cover: the haptic tick and a glanceable line. Tap = dismiss; it never leaves the app. */
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
                    /* Matches the card's own heading, THE CLOSEST VERSION WE FOUND. One line, cut at
                       the end, so a long uploader name is what gets shortened, never the label. */
                    if let who = payload.uploader, !who.isEmpty {
                        Text("Closest version: \(who)")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .lineLimit(1)
                            .truncationMode(.tail)
                    }
                }
                Spacer(minLength: 0)
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
