// swift-tools-version: 5.10
import PackageDescription

let package = Package(
    name: "ZetherionDevShell",
    platforms: [.macOS(.v14)],
    products: [
        .executable(name: "ZetherionDevShell", targets: ["ZetherionDevShell"]),
    ],
    targets: [
        .executableTarget(
            name: "ZetherionDevShell",
            path: "Sources/ZetherionDevShell"
        ),
    ]
)
