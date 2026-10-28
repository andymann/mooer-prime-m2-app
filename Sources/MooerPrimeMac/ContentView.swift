import SwiftUI

struct ContentView: View {
    var body: some View {
        NavigationSplitView {
            DeviceListView()
        } detail: {
            EffectsChainView()
        }
        .navigationTitle("Mooer Prime (Mac) — unofficial")
    }
}
