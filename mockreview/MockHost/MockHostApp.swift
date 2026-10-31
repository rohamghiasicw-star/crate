import SwiftUI

/* Host for the ReviewerUITests bundle only. It is never launched by the tests; they drive
   com.addify.app directly. */
@main
struct MockHostApp: App {
    var body: some Scene {
        WindowGroup {
            Text("Addify mock review host")
        }
    }
}
