// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "MooerPrimeMac",
    platforms: [.macOS(.v13)],
    targets: [
        .executableTarget(
            name: "MooerPrimeMac",
            path: "Sources/MooerPrimeMac",
            exclude: ["Resources/Info.plist"],
            linkerSettings: [
                .unsafeFlags([
                    "-Xlinker", "-sectcreate",
                    "-Xlinker", "__TEXT",
                    "-Xlinker", "__info_plist",
                    "-Xlinker", "Sources/MooerPrimeMac/Resources/Info.plist",
                ])
            ]
        )
    ]
)
