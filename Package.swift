// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "AgentQR",
    platforms: [.macOS(.v13)],
    products: [.executable(name: "agentqr", targets: ["AgentQR"])],
    targets: [
        .target(name: "AgentQRCore"),
        .executableTarget(name: "AgentQR", dependencies: ["AgentQRCore"]),
        .testTarget(name: "AgentQRCoreTests", dependencies: ["AgentQRCore"])
    ]
)
