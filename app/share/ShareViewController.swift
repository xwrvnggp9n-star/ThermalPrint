// Share-extension principal class for ThermalPrint.
//
// The extension is intentionally UI-less: it grabs the first shared image,
// copies it into its sandbox temp directory (readable by the un-sandboxed
// main app), hands it to ThermalPrint.app via Launch Services (which routes
// it through application:openFiles: exactly like a Dock drop), and closes.
//
// Compiled by app/build_release.sh with swiftc into
// ThermalPrint.app/Contents/PlugIns/ThermalPrintShare.appex.

import Cocoa
import UniformTypeIdentifiers

@objc(ShareViewController)
class ShareViewController: NSViewController {

    override func loadView() {
        // No visible UI — the share sheet needs *a* view, so give it an
        // empty one; the request completes before anything can render.
        view = NSView(frame: NSRect(x: 0, y: 0, width: 1, height: 1))
    }

    override func viewDidAppear() {
        super.viewDidAppear()
        handleShare()
    }

    private func handleShare() {
        let providers = (extensionContext?.inputItems as? [NSExtensionItem])?
            .compactMap { $0.attachments }
            .flatMap { $0 } ?? []
        guard let provider = providers.first(where: {
            $0.hasItemConformingToTypeIdentifier(UTType.image.identifier)
        }) else {
            cancel()
            return
        }
        provider.loadFileRepresentation(
            forTypeIdentifier: UTType.image.identifier
        ) { url, _ in
            guard let url = url else {
                self.cancel()
                return
            }
            // The provider deletes its file when this callback returns —
            // copy it to a path that outlives the extension and that the
            // main app can read.
            let dir = FileManager.default.temporaryDirectory
                .appendingPathComponent(
                    "ThermalPrint-share-"
                    + ProcessInfo.processInfo.globallyUniqueString)
            do {
                try FileManager.default.createDirectory(
                    at: dir, withIntermediateDirectories: true)
                let name = url.lastPathComponent.isEmpty
                    ? "shared-image.png" : url.lastPathComponent
                let target = dir.appendingPathComponent(name)
                try FileManager.default.copyItem(at: url, to: target)
                DispatchQueue.main.async { self.openInMainApp(target) }
            } catch {
                self.cancel()
            }
        }
    }

    private func openInMainApp(_ fileURL: URL) {
        // …/ThermalPrint.app/Contents/PlugIns/ThermalPrintShare.appex → the app
        let appURL = Bundle.main.bundleURL
            .deletingLastPathComponent()   // PlugIns
            .deletingLastPathComponent()   // Contents
            .deletingLastPathComponent()   // ThermalPrint.app
        let config = NSWorkspace.OpenConfiguration()
        config.activates = true
        NSWorkspace.shared.open(
            [fileURL], withApplicationAt: appURL, configuration: config
        ) { _, error in
            DispatchQueue.main.async {
                if error != nil {
                    self.cancel()
                } else {
                    self.extensionContext?.completeRequest(
                        returningItems: nil, completionHandler: nil)
                }
            }
        }
    }

    private func cancel() {
        DispatchQueue.main.async {
            self.extensionContext?.cancelRequest(withError: NSError(
                domain: "app.sklar.thermalprint.share", code: 1,
                userInfo: [NSLocalizedDescriptionKey: "No usable image in the shared items."]))
        }
    }
}
