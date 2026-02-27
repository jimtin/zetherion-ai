import Foundation

struct PendingApproval: Identifiable, Decodable {
    let project_id: String
    let first_seen_at: String
    let last_prompted_at: String?
    let prompt_count: Int
    let status: String

    var id: String { project_id }
}

struct PendingResponse: Decodable {
    let pending: [PendingApproval]
}
