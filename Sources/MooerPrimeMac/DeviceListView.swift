import SwiftUI
import Combine

/// Plain ObservableObject instead of @State, which needs the SwiftUIMacros
/// plugin that only ships inside full Xcode.app — see README.md.
final class DeviceListFilter: ObservableObject {
    /// Show only devices whose name contains "prime" (case-insensitive).
    @Published var primeOnly = true
}

struct DeviceListView: View {
    @EnvironmentObject var bluetooth: BluetoothManager
    @StateObject private var filter = DeviceListFilter()

    private var visibleDevices: [DiscoveredPeripheral] {
        bluetooth.discovered
            .filter { !filter.primeOnly || $0.name.localizedCaseInsensitiveContains("prime") }
            .sorted(by: { $0.rssi > $1.rssi })
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("Nearby Bluetooth devices")
                    .font(.headline)
                Spacer()
                Button(bluetooth.connectionState == .scanning ? "Stop" : "Scan") {
                    bluetooth.connectionState == .scanning ? bluetooth.stopScan() : bluetooth.startScan()
                }
                .disabled(!bluetooth.isPoweredOn)
            }

            Toggle("Only devices named “Prime”", isOn: Binding(
                get: { filter.primeOnly },
                set: { filter.primeOnly = $0 }
            ))
            .toggleStyle(.checkbox)
            .font(.callout)

            if !bluetooth.isPoweredOn {
                Text("Bluetooth is off or not authorized. Enable it in System Settings.")
                    .foregroundStyle(.secondary)
                    .font(.callout)
            }

            List(visibleDevices) { item in
                HStack {
                    VStack(alignment: .leading) {
                        Text(item.name).bold()
                        Text(item.id.uuidString).font(.caption).foregroundStyle(.secondary)
                    }
                    Spacer()
                    Text("\(item.rssi) dBm").font(.caption).foregroundStyle(.secondary)
                    Button("Connect") {
                        bluetooth.connect(item)
                    }
                    // One peripheral at a time: disconnect before connecting elsewhere.
                    .disabled(bluetooth.connectionState != .disconnected && bluetooth.connectionState != .scanning)
                }
            }
            .frame(minHeight: 160)
            .overlay {
                if visibleDevices.isEmpty && !bluetooth.discovered.isEmpty {
                    Text("\(bluetooth.discovered.count) device(s) hidden by the Prime filter")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }

            Divider()

            Text("Connection: \(bluetooth.connectionState.rawValue)\(bluetooth.connectedName.map { " — \($0)" } ?? "")")
                .font(.subheadline)

            if bluetooth.connectionState != .disconnected {
                Button("Disconnect") { bluetooth.disconnect() }
            }
        }
        .padding()
    }
}
