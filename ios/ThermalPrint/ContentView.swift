//
//  ContentView.swift
//  ThermalPrint
//
//  The one and only screen: pick a photo, preview exactly what will print
//  (scaled to 384px and dithered), tune it with Darkness / Brightness /
//  Contrast / Dither, and print over Bluetooth. Mirrors the macOS app's layout
//  and defaults, adapted to iOS (PhotosPicker, touch sliders, a nav bar).
//

import PhotosUI
import SwiftUI

struct ContentView: View {
    @EnvironmentObject private var printer: PrinterManager

    @State private var image: UIImage?
    @State private var fileName: String?
    @State private var settings = RenderSettings()
    @State private var intensity: Double = Double(MXW01.defaultIntensity)   // 175
    @State private var rendered: RenderedBitmap?
    @State private var preview: UIImage?
    @State private var pickerItem: PhotosPickerItem?
    @State private var showAbout = false

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 18) {
                    connectionRow
                    previewCard
                    photoRow
                    controls
                    printButton
                    statusRow
                }
                .padding()
            }
            .navigationTitle("ThermalPrint")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button { showAbout = true } label: {
                        Image(systemName: "info.circle")
                    }
                    .accessibilityLabel("About ThermalPrint")
                }
            }
            .sheet(isPresented: $showAbout) { AboutView() }
        }
        .onChange(of: pickerItem) { _, item in loadPickedPhoto(item) }
        .onChange(of: settings) { _, _ in rerender() }
        .onChange(of: intensity) { _, _ in retint() }
        .onReceive(NotificationCenter.default.publisher(for: .openImageURL)) { note in
            if let url = note.object as? URL { loadImage(from: url) }
        }
        .task {
            #if DEBUG
            // Test seam: preload an image path from the environment so the
            // render pipeline (orientation, flip, rotate/mirror) can be verified
            // in the simulator without driving the photo picker. Never ships.
            if let path = ProcessInfo.processInfo.environment["TP_PREVIEW_IMAGE"],
               let ui = UIImage(contentsOfFile: path) {
                setImage(ui, name: (path as NSString).lastPathComponent)
                let env = ProcessInfo.processInfo.environment
                if let r = env["TP_PREVIEW_ROTATE"], let deg = Int(r) { settings.rotation = deg }
                settings.mirror = env["TP_PREVIEW_MIRROR"] == "1"
            }
            #endif
        }
    }

    // MARK: - Connection

    private var connectionRow: some View {
        HStack(spacing: 10) {
            Circle()
                .fill(printer.connectionOK ? Color.green
                      : (printer.isConnected ? Color.orange : Color.secondary))
                .frame(width: 9, height: 9)
            Text(printer.connectionText)
                .font(.subheadline)
                .foregroundStyle(printer.connectionOK ? Color.green : Color.secondary)
                .lineLimit(1)
            Spacer()
            Button {
                printer.connectAndRefresh()
            } label: {
                Label("Connect", systemImage: "arrow.clockwise")
            }
            .buttonStyle(.bordered)
            .disabled(printer.busy)
        }
    }

    // MARK: - Preview

    private var previewCard: some View {
        ZStack {
            RoundedRectangle(cornerRadius: 16, style: .continuous)
                .fill(Color(.secondarySystemBackground))
            if let preview {
                Image(uiImage: preview)
                    .resizable()
                    .interpolation(.none)
                    .scaledToFit()
                    .padding(10)
            } else {
                VStack(spacing: 12) {
                    Image(systemName: "photo.on.rectangle.angled")
                        .font(.system(size: 44))
                        .foregroundStyle(.tertiary)
                    Text("Choose a photo to print")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                }
            }
        }
        .frame(height: 360)
        .overlay(alignment: .bottomLeading) {
            if image != nil {
                transformButton(system: "rotate.right", label: "Rotate 90° clockwise") { rotate() }
                    .padding(12)
            }
        }
        .overlay(alignment: .bottomTrailing) {
            if image != nil {
                transformButton(system: "arrow.left.and.right", label: "Mirror") {
                    settings.mirror.toggle()
                }
                .padding(12)
            }
        }
    }

    private func transformButton(system: String, label: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: system)
                .font(.system(size: 16, weight: .semibold))
                .frame(width: 40, height: 40)
                .background(.thinMaterial, in: Circle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(label)
    }

    // MARK: - Photo picker

    private var photoRow: some View {
        HStack {
            PhotosPicker(selection: $pickerItem, matching: .images, photoLibrary: .shared()) {
                Label("Choose Photo", systemImage: "photo")
            }
            .buttonStyle(.bordered)
            Spacer()
            if let fileName {
                Text(fileName)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .truncationMode(.middle)
            }
        }
    }

    // MARK: - Controls

    private var controls: some View {
        VStack(spacing: 14) {
            sliderRow(title: "Darkness", value: $intensity, range: 0...255,
                      display: String(Int(intensity)))
            sliderRow(title: "Brightness", value: $settings.brightness, range: 0.3...1.7,
                      display: String(format: "%.2f", settings.brightness))
            sliderRow(title: "Contrast", value: $settings.contrast, range: 0.5...3.0,
                      display: String(format: "%.2f", settings.contrast))
            Toggle("Dither (best for photos)", isOn: $settings.dither)
                .font(.subheadline)
        }
        .padding(16)
        .background(Color(.secondarySystemBackground),
                    in: RoundedRectangle(cornerRadius: 16, style: .continuous))
    }

    private func sliderRow(title: String, value: Binding<Double>,
                           range: ClosedRange<Double>, display: String) -> some View {
        HStack(spacing: 12) {
            Text(title)
                .font(.subheadline)
                .frame(width: 78, alignment: .leading)
            Slider(value: value, in: range)
            Text(display)
                .font(.subheadline.monospacedDigit())
                .foregroundStyle(.secondary)
                .frame(width: 48, alignment: .trailing)
        }
    }

    // MARK: - Print

    private var printButton: some View {
        Button {
            guard let rendered else { return }
            printer.print(rendered, intensity: Int(intensity))
        } label: {
            Label(printer.isConnected ? "Print" : "Connect and Print", systemImage: "printer")
                .frame(maxWidth: .infinity)
        }
        .buttonStyle(.borderedProminent)
        .controlSize(.large)
        .disabled(rendered == nil || printer.busy)
    }

    private var statusRow: some View {
        HStack(spacing: 8) {
            if printer.busy { ProgressView().controlSize(.small) }
            Text(printer.statusLine)
                .font(.footnote)
                .foregroundStyle(.secondary)
            Spacer()
        }
        .frame(minHeight: 20)
    }

    // MARK: - Image loading & rendering

    private func loadPickedPhoto(_ item: PhotosPickerItem?) {
        guard let item else { return }
        Task {
            if let data = try? await item.loadTransferable(type: Data.self),
               let ui = UIImage(data: data) {
                await MainActor.run { setImage(ui, name: nil) }
            }
        }
    }

    private func loadImage(from url: URL) {
        let scoped = url.startAccessingSecurityScopedResource()
        defer { if scoped { url.stopAccessingSecurityScopedResource() } }
        guard let data = try? Data(contentsOf: url), let ui = UIImage(data: data) else { return }
        setImage(ui, name: url.lastPathComponent)
    }

    private func setImage(_ ui: UIImage, name: String?) {
        image = ui
        fileName = name
        settings.rotation = 0        // a fresh image starts unrotated…
        settings.mirror = false      // …and unmirrored
        rerender()
    }

    private func rotate() {
        // Always turn what the user SEES 90° clockwise; under a mirror the
        // underlying rotation must step the other way.
        let step = settings.mirror ? -90 : 90
        settings.rotation = (((settings.rotation + step) % 360) + 360) % 360
    }

    private func rerender() {
        guard let image else { rendered = nil; preview = nil; return }
        rendered = BitmapRenderer.render(image, settings: settings)
        retint()
    }

    private func retint() {
        preview = rendered?.previewImage(intensity: Int(intensity))
    }
}

#Preview {
    ContentView().environmentObject(PrinterManager())
}
