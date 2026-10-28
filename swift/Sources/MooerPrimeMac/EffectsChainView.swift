import SwiftUI
import AppKit
import Combine

/// Plain ObservableObject (not @State/@Observable) so this view compiles
/// without the SwiftUIMacros plugin, which isn't present outside a full
/// Xcode.app install — see README.md.
final class SavePresetForm: ObservableObject {
    @Published var name = ""
    @Published var targetIndex = 1
}

/// Effects-chain editor for the Prime M2, driven by `PrimeM2Device`: preset
/// list (select), the active preset's chain as 9 slots (any category and
/// model per slot, live edit in the pedal's edit buffer) and save-to-slot.
struct EffectsChainView: View {
    @EnvironmentObject var bluetooth: BluetoothManager
    @EnvironmentObject var device: PrimeM2Device
    @StateObject private var saveForm = SavePresetForm()

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            statusBar
            Divider()
            if device.isReady {
                HSplitView {
                    presetList.frame(minWidth: 200, idealWidth: 230, maxWidth: 320)
                    presetEditor.frame(minWidth: 400)
                }
            } else {
                Text(device.link != nil
                     ? "Syncing with the pedal over \(device.link?.rawValue ?? "")…"
                     : "Plug the pedal in by USB, or connect to “M2Prime M2” in the device list.")
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        // Fill the save form when a preset loads, and also when the view
        // appears with one already loaded (onChange alone missed that case).
        .onChange(of: device.preset?.index) { _ in fillSaveForm() }
        .onAppear { fillSaveForm() }
    }

    private func fillSaveForm() {
        guard let preset = device.preset else { return }
        saveForm.name = preset.name
        saveForm.targetIndex = preset.index
    }

    private var statusBar: some View {
        HStack(spacing: 16) {
            if let firmware = device.firmware { Text("Firmware \(firmware)") }
            if let battery = device.battery { Text("Battery \(battery)%") }
            if let tempo = device.tempo { Text("Tempo \(tempo) BPM") }
            if let serial = device.serial { Text(serial).foregroundStyle(.secondary) }
            if let link = device.link {
                Label("via \(link.rawValue)", systemImage: link == .usb ? "cable.connector" : "antenna.radiowaves.left.and.right")
                    .help(link == .usb
                          ? "Controlling the pedal over USB (preferred while plugged in)"
                          : "Controlling the pedal over Bluetooth")
            }
            Spacer()
            if device.isReady, device.presetCache.count < 80 {
                Text("Scanning presets \(device.presetCache.count)/80")
                    .foregroundStyle(.secondary)
            }
            // Fixed slot so showing/hiding the spinner never shifts the buttons.
            ProgressView().controlSize(.small)
                .frame(width: 16, height: 16)
                .opacity(device.isBusy ? 1 : 0)
            Button("Copy raw dump") { copyRawDump() }
                .disabled(device.presetCache.isEmpty)
                .help("Copies every scanned preset's raw module words, for decoding against the official app")
        }
        .font(.caption)
        .padding(.horizontal)
        .padding(.vertical, 6)
    }

    private var presetList: some View {
        // Native list selection: a single click selects, and the highlight
        // follows the pedal's active preset (also when changed on the pedal).
        List(1...80, id: \.self, selection: Binding(
            get: { device.displayedPresetIndex },
            set: { index in
                if let index, index != device.displayedPresetIndex { device.selectPreset(index) }
            }
        )) { index in
            HStack(spacing: 4) {
                unnamedMarker(for: index)
                Text(PrimeM2Names.slotLabel(index))
                    .font(.system(.body, design: .monospaced))
                    .frame(width: 34, alignment: .leading)
                Text(device.presetNames[index] ?? "—")
                Spacer()
            }
            .fontWeight(index == device.displayedPresetIndex ? .bold : .regular)
            .tag(index)
        }
    }

    /// One line per module: "0D FX 04 | 4 Cab 56 SV810 U47 | 1 55 50 12 34".
    private func copyRawDump() {
        let lines = device.presetCache.keys.sorted().flatMap { index -> [String] in
            guard let preset = device.presetCache[index] else { return [] }
            let label = "\(PrimeM2Names.slotLabel(index)) \(preset.name)"
            return preset.modules.filter { !$0.isEmpty }.map { module in
                let model = ModuleCatalog.modelLabel(type: module.type, model: module.model ?? 0)
                let words = module.words.map(String.init).joined(separator: " ")
                return "\(label) | \(module.slot) \(module.typeName) \(model) | \(words)"
            }
        }
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(lines.joined(separator: "\n"), forType: .string)
    }

    /// Orange "?" when a preset uses a model we have no name for yet; empty
    /// space of the same width otherwise (and while the preset isn't scanned).
    @ViewBuilder
    private func unnamedMarker(for index: Int) -> some View {
        let unnamed = device.presetCache[index]?.modules.filter(ModuleCatalog.isUnnamed) ?? []
        if unnamed.isEmpty {
            Color.clear.frame(width: 16, height: 1)
        } else {
            Image(systemName: "questionmark.circle.fill")
                .foregroundStyle(.orange)
                .frame(width: 16)
                .help("Unnamed: " + unnamed.map { module in
                    "\(module.typeName) \(ModuleCatalog.modelLabel(type: module.type, model: module.model ?? 0))"
                }.joined(separator: ", "))
        }
    }

    @ViewBuilder
    private var presetEditor: some View {
        if let preset = device.preset {
            VStack(alignment: .leading) {
                Text("\(PrimeM2Names.slotLabel(preset.index))  \(preset.name)")
                    .font(.title3).bold()
                    .padding([.top, .horizontal])

                if let notice = device.notice {
                    Label(notice, systemImage: "exclamationmark.triangle.fill")
                        .foregroundStyle(.orange)
                        .font(.callout)
                        .padding(.horizontal)
                }

                // Used slots in chain order, then empty ones up to 9. Effects
                // fill from the front, so an empty row always adds at the end.
                let chain = preset.modules.filter { !$0.isEmpty }
                let nextFree = UInt8(chain.count + 1)
                List {
                    ForEach(chain) { module in
                        ModuleRow(module: module,
                                  onChange: { device.updateModule($0) },
                                  onPlace: { type, model in device.placeModule(slot: module.slot, type: type, model: model) },
                                  onRemove: { device.deleteModule(slot: module.slot) })
                    }
                    if chain.count < ModuleCatalog.maxModules {
                        ForEach(chain.count..<ModuleCatalog.maxModules, id: \.self) { position in
                            EmptySlotRow(number: position + 1, isNext: position == chain.count) { type in
                                device.placeModule(slot: nextFree, type: type, model: ModuleCatalog.models(of: type).first ?? 0)
                            }
                        }
                    }
                }

                saveBar
                    .padding()
            }
        } else {
            Text("Loading preset…")
                .foregroundStyle(.secondary)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
    }

    private var saveBar: some View {
        HStack {
            Text("Save edits as")
                .help("Edits play on the pedal right away; Save stores them in a preset slot")
            TextField("Name", text: Binding(
                get: { saveForm.name },
                set: { saveForm.name = String($0.prefix(PrimePreset.nameLength)) }
            ))
            .frame(maxWidth: 200)
            Picker("to", selection: Binding(
                get: { saveForm.targetIndex },
                set: { saveForm.targetIndex = $0 }
            )) {
                ForEach(1...80, id: \.self) { index in
                    Text("\(PrimeM2Names.slotLabel(index))  \(device.presetNames[index] ?? "")").tag(index)
                }
            }
            .frame(maxWidth: 220)
            Button("Save") {
                device.savePreset(to: saveForm.targetIndex, name: saveForm.name)
            }
            .disabled(saveForm.name.isEmpty)
        }
        .font(.callout)
    }
}

/// One used slot: on/off, category and model pickers, remove, and every
/// parameter word, labelled via `ModuleCatalog`. Knobs get a 0…100 slider;
/// ms/Hz/EQ words (and anything already above 100) get a raw number field
/// plus the formatted value. Picking a category or model goes through
/// `2d` (pedal defaults, DSP check); sliders and on/off through `25`.
private struct ModuleRow: View {
    let module: PrimeModule
    let onChange: (PrimeModule) -> Void
    let onPlace: (_ type: UInt8, _ model: UInt16) -> Void
    let onRemove: () -> Void

    // Parameters are always shown: a collapsible section (DisclosureGroup,
    // then an expand button) never opened once the header held menus.
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            header
            parameters
                .padding(.leading, 28)
            Text(module.words.map(String.init).joined(separator: " "))
                .font(.system(.caption2, design: .monospaced))
                .foregroundStyle(.secondary)
                .textSelection(.enabled)
                .padding(.leading, 28)
        }
        .padding(.vertical, 4)
    }

    private var header: some View {
        HStack {
            Toggle(isOn: Binding(
                get: { module.isEnabled },
                set: { enabled in
                    var updated = module
                    updated.isEnabled = enabled
                    onChange(updated)
                }
            )) {
                Text("\(module.slot).").bold().monospacedDigit()
            }
            CategoryMenu(title: module.typeName) { type in
                if type != module.type { onPlace(type, ModuleCatalog.models(of: type).first ?? 0) }
            }
            .frame(width: 120)
            Menu(module.model.map { ModuleCatalog.modelLabel(type: module.type, model: $0) } ?? "—") {
                ForEach(ModuleCatalog.models(of: module.type), id: \.self) { model in
                    Button(ModuleCatalog.modelLabel(type: module.type, model: model)) {
                        if model != module.model { onPlace(module.type, model) }
                    }
                }
            }
            .frame(maxWidth: 230)
            Spacer()
            Button(role: .destructive, action: onRemove) {
                Image(systemName: "trash")
            }
            .buttonStyle(.borderless)
            .help("Remove this effect (later effects move up). Save to keep the change")
        }
    }

    /// Labelled parameters only. Unlabelled words (P7… on Guitar Sim, delay
    /// words 4–5, …) are values the official app hides too; some are DSP data
    /// the pedal computes, so they're shown read-only in the raw line instead.
    private var visibleParameterIndices: [Int] {
        module.parameterIndices.filter {
            ModuleCatalog.param(type: module.type, model: module.model, paramIndex: $0 - 2).kind != .unknown
        }
    }

    private var parameters: some View {
        ForEach(visibleParameterIndices, id: \.self) { wordIndex in
            let param = ModuleCatalog.param(type: module.type, model: module.model, paramIndex: wordIndex - 2)
            HStack {
                HStack(spacing: 4) {
                    Text(param.label)
                    Spacer(minLength: 0)
                    if param.syncable { syncButton(param, wordIndex) }
                }
                .frame(width: 110, alignment: .leading)
                if param.syncable, let index = ModuleCatalog.syncIndex(param.kind, value: module.words[wordIndex]) {
                    // Note slider in the official app's order; stores the note's index.
                    let order = ModuleCatalog.syncNoteOrder
                    let position = order.firstIndex { $0.index == index } ?? 0
                    Slider(value: Binding(
                        get: { Double(position) },
                        set: { set(wordIndex, ModuleCatalog.syncedValue(param.kind, index: order[Int($0.rounded())].index)) }
                    ), in: 0...Double(order.count - 1), step: 1)
                    Text(ModuleCatalog.format(module.words[wordIndex], kind: param.kind))
                        .font(.system(.caption, design: .monospaced))
                        .frame(width: 70, alignment: .trailing)
                } else if let range = ModuleCatalog.sliderRange(param, value: module.words[wordIndex]) {
                    Slider(value: Binding(
                        get: { Double(module.words[wordIndex]) },
                        set: { set(wordIndex, UInt16($0.rounded())) }
                    ), in: range)
                    Text(ModuleCatalog.format(module.words[wordIndex], kind: param.kind))
                        .font(.system(.caption, design: .monospaced))
                        .frame(width: 70, alignment: .trailing)
                } else {
                    TextField("", value: Binding(
                        get: { Int(module.words[wordIndex]) },
                        set: { set(wordIndex, UInt16(clamping: $0)) }
                    ), format: .number)
                    .frame(width: 80)
                    Text(ModuleCatalog.format(module.words[wordIndex], kind: param.kind))
                        .font(.system(.caption, design: .monospaced))
                        .foregroundStyle(.secondary)
                    Spacer()
                }
            }
        }
    }

    /// ♩ toggles tempo sync: on → 1/4, off → 500 ms (Delay Time) or 50 (Mod Rate).
    private func syncButton(_ param: ModuleCatalog.Param, _ wordIndex: Int) -> some View {
        let synced = ModuleCatalog.syncIndex(param.kind, value: module.words[wordIndex]) != nil
        return Button {
            set(wordIndex, synced ? (param.kind == .rate ? 50 : 500) : ModuleCatalog.syncedValue(param.kind, index: 10))
        } label: {
            Image(systemName: "music.note")
                .foregroundStyle(synced ? Color.accentColor : Color.secondary)
        }
        .buttonStyle(.borderless)
        .help(synced ? "Synced to the tempo. Click to set a free time" : "Click to sync to the tempo")
    }

    private func set(_ wordIndex: Int, _ value: UInt16) {
        guard module.words[wordIndex] != value else { return }
        var updated = module
        updated.words[wordIndex] = value
        onChange(updated)
    }
}

/// Category picker shared by used and empty slots, in the official app's order.
private struct CategoryMenu: View {
    let title: String
    let onPick: (UInt8) -> Void

    var body: some View {
        Menu(title) {
            ForEach(ModuleCatalog.categoryOrder, id: \.self) { type in
                Button(ModuleCatalog.typeName(type)) { onPick(type) }
            }
        }
    }
}

/// An unused slot. Only the first one can take an effect, since the pedal
/// keeps effects packed from slot 1; the rest are placeholders.
private struct EmptySlotRow: View {
    let number: Int
    let isNext: Bool
    let onAdd: (UInt8) -> Void

    var body: some View {
        HStack {
            Text("\(number).").bold().monospacedDigit().foregroundStyle(.secondary)
                .padding(.leading, 0)
            if isNext {
                CategoryMenu(title: "Add effect…", onPick: onAdd)
                    .frame(width: 140)
            } else {
                Text("Empty").foregroundStyle(.tertiary)
            }
            Spacer()
        }
    }
}
