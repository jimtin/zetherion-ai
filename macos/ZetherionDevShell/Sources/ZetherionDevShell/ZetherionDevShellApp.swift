import AppKit
import SwiftUI

@main
struct ZetherionDevShellApp: App {
    @StateObject private var model = ApprovalModel()

    var body: some Scene {
        MenuBarExtra("Zetherion", systemImage: "wrench.and.screwdriver.fill") {
            Group {
                if let error = model.error {
                    Text(error)
                        .foregroundStyle(.red)
                        .fixedSize(horizontal: false, vertical: true)
                }

                if model.pending.isEmpty {
                    Text("No pending approvals")
                        .foregroundStyle(.secondary)
                } else {
                    ForEach(model.pending) { item in
                        VStack(alignment: .leading, spacing: 6) {
                            Text(item.project_id)
                                .font(.headline)
                            HStack {
                                Button("Approve auto-clean") {
                                    Task { await model.setMode(projectID: item.project_id, mode: "auto_clean") }
                                }
                                Button("Never clean") {
                                    Task { await model.setMode(projectID: item.project_id, mode: "never_clean") }
                                }
                            }
                        }
                        .padding(.vertical, 4)
                    }
                }

                Divider()
                Button("Refresh") {
                    Task { await model.refresh() }
                }
                Button("Quit") {
                    NSApplication.shared.terminate(nil)
                }
            }
            .padding(8)
            .frame(minWidth: 360)
            .task {
                await model.refresh()
            }
        }
    }
}

@MainActor
final class ApprovalModel: ObservableObject {
    @Published var pending: [PendingApproval] = []
    @Published var error: String?

    private let client: DevAgentClient?
    private let initError: Error?

    init() {
        do {
            self.client = try DevAgentClient()
            self.initError = nil
        } catch {
            self.client = nil
            self.initError = error
        }
    }

    func refresh() async {
        if let initError {
            error = initError.localizedDescription
            pending = []
            return
        }
        guard let client else {
            error = "Client unavailable"
            pending = []
            return
        }
        do {
            pending = try await client.listPendingApprovals()
            error = nil
        } catch {
            self.error = error.localizedDescription
            pending = []
        }
    }

    func setMode(projectID: String, mode: String) async {
        guard let client else {
            return
        }
        do {
            try await client.setPolicy(projectID: projectID, mode: mode)
            await refresh()
        } catch {
            self.error = error.localizedDescription
        }
    }
}
