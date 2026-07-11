//
//  ThermalPrintApp.swift
//  ThermalPrint
//
//  App entry point. A single-window SwiftUI app whose one scene is the printer
//  screen. Images shared into the app (Photos share sheet, Files, drag & drop
//  onto the icon) arrive as URLs and are loaded into the same screen.
//

import SwiftUI

@main
struct ThermalPrintApp: App {
    @StateObject private var printer = PrinterManager()

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(printer)
                .onOpenURL { url in
                    NotificationCenter.default.post(name: .openImageURL, object: url)
                }
        }
    }
}

extension Notification.Name {
    /// Posted when the OS hands us an image to open (share sheet, Files, etc.).
    static let openImageURL = Notification.Name("ThermalPrint.openImageURL")
}
