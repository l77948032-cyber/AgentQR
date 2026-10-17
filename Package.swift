// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "ScanBridge",
    platforms: [.macOS(.v13)],
    products: [.executable(name: "scanbridge", targets: ["ScanBridge"])],
    targets: [
        .target(name: "ScanBridgeCore"),
        .executableTarget(name: "ScanBridge", dependencies: ["ScanBridgeCore"]),
        .testTarget(name: "ScanBridgeCoreTests", dependencies: ["ScanBridgeCore"])
    ]
)
