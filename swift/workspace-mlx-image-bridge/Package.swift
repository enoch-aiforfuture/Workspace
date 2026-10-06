// swift-tools-version: 6.2
import PackageDescription

let package = Package(
    name: "workspace-mlx-image-bridge",
    platforms: [.macOS(.v26)],
    products: [
        .executable(name: "workspace-mlx-inpaint", targets: ["WorkspaceMLXInpaint"]),
        .executable(name: "workspace-mlx-colorize", targets: ["WorkspaceMLXColorize"]),
    ],
    dependencies: [
        .package(url: "https://github.com/xocialize/mlx-lama-swift", branch: "main"),
        .package(url: "https://github.com/xocialize/mlx-ddcolor-swift", branch: "main"),
    ],
    targets: [
        .executableTarget(
            name: "WorkspaceMLXInpaint",
            dependencies: [
                .product(name: "LaMa", package: "mlx-lama-swift"),
                .product(name: "MIGAN", package: "mlx-lama-swift"),
            ]
        ),
        .executableTarget(
            name: "WorkspaceMLXColorize",
            dependencies: [
                .product(name: "DDColor", package: "mlx-ddcolor-swift"),
            ]
        ),
    ]
)
