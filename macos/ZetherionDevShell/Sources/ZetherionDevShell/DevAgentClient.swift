import Foundation

enum DevAgentClientError: LocalizedError {
    case missingToken
    case invalidResponse
    case requestFailed(String)

    var errorDescription: String? {
        switch self {
        case .missingToken:
            return "Missing API token. Set ZETHERION_DEV_AGENT_TOKEN in your environment."
        case .invalidResponse:
            return "Invalid response from daemon."
        case let .requestFailed(message):
            return message
        }
    }
}

final class DevAgentClient {
    private let baseURL: URL
    private let token: String

    init() throws {
        let env = ProcessInfo.processInfo.environment
        let base = env["ZETHERION_DEV_AGENT_URL"] ?? "http://127.0.0.1:8787/v1"
        guard let url = URL(string: base) else {
            throw DevAgentClientError.invalidResponse
        }
        guard let token = env["ZETHERION_DEV_AGENT_TOKEN"], !token.isEmpty else {
            throw DevAgentClientError.missingToken
        }
        self.baseURL = url
        self.token = token
    }

    func listPendingApprovals() async throws -> [PendingApproval] {
        var request = URLRequest(url: baseURL.appending(path: "approvals/pending"))
        request.httpMethod = "GET"
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw DevAgentClientError.invalidResponse
        }
        guard http.statusCode == 200 else {
            throw DevAgentClientError.requestFailed("Request failed: HTTP \(http.statusCode)")
        }
        let decoded = try JSONDecoder().decode(PendingResponse.self, from: data)
        return decoded.pending
    }

    func setPolicy(projectID: String, mode: String) async throws {
        var request = URLRequest(url: baseURL.appending(path: "projects").appending(path: projectID).appending(path: "policy"))
        request.httpMethod = "POST"
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let body: [String: String] = [
            "mode": mode,
            "source": "swift_shell",
        ]
        request.httpBody = try JSONSerialization.data(withJSONObject: body)

        let (_, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw DevAgentClientError.invalidResponse
        }
        guard (200 ..< 300).contains(http.statusCode) else {
            throw DevAgentClientError.requestFailed("Policy update failed: HTTP \(http.statusCode)")
        }
    }
}
