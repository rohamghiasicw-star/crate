import SwiftUI

/* Host for the ObserverUITests bundle only. Never launched by the tests; they read
   com.addify.app by bundle id. */
@main
struct ObserverHostApp: App {
    var body: some Scene {
        WindowGroup {
            Text("Addify launch lab observer")
        }
    }
}
