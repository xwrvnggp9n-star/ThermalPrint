//
//  AppInfo.swift
//  ThermalPrint
//
//  Single source of truth for the app's identity — the iOS counterpart of
//  version.py. The marketing version and build number also live in the target's
//  Info.plist (CFBundleShortVersionString / CFBundleVersion); keep them in sync.
//

import Foundation

enum AppInfo {
    static let name = "ThermalPrint"
    static let version = "1.2.0"
    static let author = "Sandor W. Sklar"
    static let contactEmail = "thermalprint@sklar.app"
    static let websiteURL = URL(string: "https://thermalprint.sklar.app")!
    static let githubURL = URL(string: "https://github.com/xwrvnggp9n-star/ThermalPrint")!

    /// Marketing version from the running bundle, falling back to the constant.
    static var displayVersion: String {
        Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? version
    }
}
