import Foundation

/* The words in front of the uploader on the result toast (ResultToast). The card picks its heading
   per result (crate.html: "The exact version playing", "The closest version we found", "The
   recording this clip was cut from"), so the page sends its own short form in the native result
   post as exact.label, for example "Exact version", "Closest version" or "Source recording".
   A label the toast cannot use (missing, not a string, blank, more than maxLength characters, or
   holding a line break) falls back to "Closest version". Foundation only, so the host tests run it. */
enum ResultLabel {
    static let fallback = "Closest version"
    static let maxLength = 24

    static func toast(_ raw: Any?) -> String {
        guard let s = (raw as? String)?.trimmingCharacters(in: .whitespacesAndNewlines),
              !s.isEmpty, s.count <= maxLength,
              s.rangeOfCharacter(from: CharacterSet.newlines.union(.controlCharacters)) == nil else {
            return fallback
        }
        return s
    }
}
