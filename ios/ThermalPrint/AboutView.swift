//
//  AboutView.swift
//  ThermalPrint
//
//  The iOS counterpart of the macOS About panel: app identity, author credit,
//  contact / website / GitHub links, and a support link. Presented as a sheet
//  from the nav-bar info button.
//

import SwiftUI

struct AboutView: View {
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List {
                Section {
                    VStack(spacing: 12) {
                        Image(uiImage: appIcon)
                            .resizable()
                            .frame(width: 84, height: 84)
                            .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
                            .shadow(radius: 3, y: 2)
                        Text(AppInfo.name).font(.title2.bold())
                        Text("Version \(AppInfo.displayVersion)")
                            .font(.subheadline).foregroundStyle(.secondary)
                        Text("Print photos to an MXW01 Bluetooth thermal printer.")
                            .font(.callout)
                            .multilineTextAlignment(.center)
                            .foregroundStyle(.secondary)
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 8)
                    .listRowBackground(Color.clear)
                }

                Section {
                    LabeledContent("Author", value: AppInfo.author)
                    Link(destination: URL(string: "mailto:\(AppInfo.contactEmail)")!) {
                        LabeledContent("Contact") { Text(AppInfo.contactEmail) }
                    }
                    Link(destination: AppInfo.websiteURL) {
                        LabeledContent("Website") { Text("thermalprint.sklar.app") }
                    }
                    Link(destination: AppInfo.githubURL) {
                        LabeledContent("Source") { Text("GitHub") }
                    }
                }

                Section {
                    Link(destination: AppInfo.supportURL) {
                        Label("Support this project ☕", systemImage: "cup.and.saucer")
                    }
                }

                Section {
                    Text("© 2026 \(AppInfo.author)")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                        .frame(maxWidth: .infinity, alignment: .center)
                        .listRowBackground(Color.clear)
                }
            }
            .navigationTitle("About")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
        }
    }

    /// The app's own icon, for the header. Falls back to an SF Symbol render.
    private var appIcon: UIImage {
        if let name = (Bundle.main.infoDictionary?["CFBundleIcons"] as? [String: Any])
            .flatMap({ ($0["CFBundlePrimaryIcon"] as? [String: Any]) })
            .flatMap({ ($0["CFBundleIconFiles"] as? [String])?.last }),
           let icon = UIImage(named: name) {
            return icon
        }
        return UIImage(systemName: "printer.fill") ?? UIImage()
    }
}

#Preview {
    AboutView()
}
