import Foundation

/// Human labels for module types, models and parameter words, matched
/// against screenshots of preset 0A in the official app (see docs/RESEARCH-NOTES.md,
/// "Labels"). Type names are confirmed for 01…09. Parameter labels come from
/// the one model of each type seen in 0A and may differ for other models of
/// the same type. Model names are known only where we've seen them.
///
/// The app shows the model as wire value + 1 (wire 1 → "02 Flex Boost").
enum ModuleCatalog {
    enum ParamKind {
        /// 0…100 knob.
        case knob
        /// Milliseconds; a set high bit (0x8000) means tempo-synced and the
        /// low bits are a `syncNotes` index.
        case milliseconds
        case hertz
        /// Cab Low/High Cut: raw value is Hz, 0 = OFF (0D: 300/20000, 2A: 60/8300).
        case offOrValue
        /// EQ band gain: dB = (raw − 32) / 2, confirmed on 7 bands across 5 presets.
        case eqGain
        /// EQ band Q: (raw + 2) / 10 (8 → 1.0, 11 → 1.3, 48 → 5.0, 50 → 5.2).
        case eqQ
        /// 0…100 knob; 101 + n means tempo-synced to `syncNotes[n]` (111 = "1/4").
        case rate
        /// Pitch Shift semitones: raw / 10 − 12 (189 → 6.9, 70 → −5.0).
        case pitch
        /// Guitar Sim Low/Mid/High: dB = (raw − 120) / 10 (80 → −4.0, 130 → 1.0).
        case simGain
        /// Guitar Sim Mid Shift: Hz = raw + 200 (649 → 849).
        case midShift
        case unknown
    }

    struct Param {
        let label: String
        let kind: ParamKind
        /// Raw slider range when it isn't implied by `kind` (see `sliderRange`).
        var range: ClosedRange<Double>? = nil
        /// Has a tempo-sync button (Delay Time, Mod Rate).
        var syncable: Bool { kind == .rate || (kind == .milliseconds && label == "Time") }
    }

    static let typeNames: [UInt8: String] = [
        0x01: "Drive",
        0x02: "Pre-Amp",
        0x03: "Cab",
        0x04: "Dyna",
        0x05: "Filter",
        0x06: "Mod",
        0x07: "Delay",
        0x08: "Reverb",
        0x09: "EQ",
        // Not used by any factory preset; codes confirmed from a user-saved preset (docs/RESEARCH-NOTES.md, "Batch 9").
        0x0a: "Guitar Sim",
        0x0b: "Entire Amp",
    ]

    /// Keyed by type, then by wire model value (app number − 1). Collected
    /// from screenshots of every factory preset (0A–19D) in the official app,
    /// plus the app's full Cab list and a walk through unused model numbers.
    static let modelNames: [UInt8: [UInt16: String]] = [
        0x01: [0: "Pure Boost", 1: "Flex Boost", 2: "Tube DR", 3: "808", 4: "Gold Clon", 5: "D-Drive", 6: "Jimmy OD",
               7: "Full DR", 8: "Beebee Pre", 9: "Beebee+", 10: "Black Rat", 11: "Grey Faze", 12: "Muffy",
               13: "Full DS", 14: "Shred", 15: "Riet", 16: "MTL Zone", 17: "MTL Master", 18: "Obsessive Dist",
               19: "Rumble Drive", 20: "Solo", 21: "Blues Mood", 22: "Blues Crab", 23: "Blade", 24: "TS9 Boost",
               25: "ROD881", 26: "Tethys 21", 27: "TS Drive", 28: "Blackglass X8"],
        0x02: [0: "65 US DLX", 1: "65 US TW", 2: "59 US Bass", 3: "US Sonic", 4: "US Blues CL", 5: "US Blues OD",
               6: "E650 CL", 7: "Powerbell CL", 8: "Blacknight CL", 9: "Mark III CL", 10: "Mark V CL", 11: "Tri Rec CL",
               12: "Rockvrb CL", 13: "Dr Zee 18 JR", 14: "Dr Zee Reck", 15: "Jet 100H CL", 16: "Jazz 120", 17: "UK 30 CL",
               18: "UK 30 OD", 19: "HWT 103", 20: "PV5050 CL", 21: "Regal Tone CL", 22: "Regal Tone OD1", 23: "Carol CL",
               24: "Cardeff", 25: "EV 5050 CL", 26: "HT Club CL", 27: "Hugen CL", 28: "Koche OD", 29: "J800",
               30: "J900", 31: "PLX 100", 32: "E650 DS", 33: "Powerbell DS", 34: "Blacknight DS", 35: "Mark III DS",
               36: "Mark V DS", 37: "Tri Rec DS", 38: "Rockvrb DS", 39: "Citrus 30", 40: "Citrus 50", 41: "Slow 100 CR",
               42: "Slow 100 DS", 43: "Jet 100H OD", 44: "PV 5050 DS", 45: "Regal Tone OD2", 46: "Carol OD", 47: "EV 5050 DS",
               48: "HT Club DS", 49: "Hugen OD", 50: "Hugen DS", 51: "Koche DS", 52: "AMPOG 2OD", 53: "AMPOG B18 CL",
               54: "AMPOG SVT 4", 55: "AMPOG SVT VALVE", 56: "MVRKBASS 500", 57: "MVRKBASS 501", 58: "AKUILA 750 CL", 59: "AKUILA 750 DS",
               60: "AKUILA 751", 61: "Basser Crunch"],
        0x03: [0: "Regal Tone 110", 1: "US DLX 112", 2: "Sonic 112", 3: "Blues 112", 4: "Mark 112", 5: "Dr Zee 112",
               6: "Cardeff 112", 7: "US TW 212", 8: "Citrus 212", 9: "Dr Zee 212", 10: "Jazz 212", 11: "UK 212",
               12: "Tow Stones 212", 13: "US Bass 410", 14: "1960 412", 15: "Eagle P412", 16: "Eagle S412", 17: "Rec 412",
               18: "Citrus 412", 19: "Slow 412", 20: "HWT 412", 21: "PV 5050 412", 22: "EV 5050 412", 23: "HT 412",
               24: "Diesel 412", 25: "CT-SupBMK112", 26: "CT-FendS212", 27: "CT-FendTW212", 28: "CT-Fend67212", 29: "CT-BritJV212",
               30: "CT-Brit412", 31: "CT-BritJ412", 32: "CT-Bogie212", 33: "CT-BogieLS212", 34: "CT-BogOS412", 35: "CT-Vocs212",
               36: "CT-Barb212", 37: "CT-Fram212", 38: "CT-Kox212", 39: "CT-Mgan212", 40: "CT-Edd412", 41: "CT-Fried412",
               42: "CT-Gas412", 43: "CT-Hess212", 44: "CT-Hess412", 45: "CT-HW412", 46: "CT-HK412", 47: "CT-OR412",
               48: "CT-Pvyln212", 49: "CT-Pvy50412", 50: "CT-Revo412", 51: "CT-River412", 52: "CT-Sold412", 53: "CT-VTH412",
               54: "CT-Win412", 55: "SV810 U47", 56: "SV810 121", 57: "SV810 7B", 58: "HK U47", 59: "HK 7B",
               60: "HK 121", 61: "HK 421", 62: "Akuila U47", 63: "Akuila 7B", 64: "Akuila 121"],
        0x04: [0: "Red Comp", 1: "Yellow Comp", 2: "Noise Killer", 3: "Intel Reducer"],
        0x05: [0: "Touch Wah", 1: "Auto Wah", 2: "Q-Filter"],
        0x06: [0: "Phaser", 1: "Step Phaser", 2: "Flanger", 3: "Jet Flanger", 4: "Tremolo", 5: "Stutter", 6: "Vibrato", 7: "Rotary",
               8: "Ring", 9: "Pitch Shift", 10: "Ana Chorus", 11: "Tri Chorus", 12: "Lofi", 13: "Slow Gear"],
        0x07: [0: "Digital", 1: "Analog", 2: "Real Echo", 3: "Tape", 4: "Mod", 5: "Reverse", 6: "PingPong", 7: "Crystal", 8: "Rainbow"],
        0x08: [0: "Room", 1: "Hall", 2: "Plate", 3: "Spring", 4: "Mod", 5: "Shimmer"],
        0x09: [0: "5 Bands EQ", 1: "Custom EQ"],
        // Guitar Sim: the app's five subgroups (Acoustic Electric / Piezo /
        // Silent, Classical Piezo / Silent) flattened in list order; unconfirmed.
        0x0a: [0: "Ac Electric GBS 45 a", 1: "Ac Electric GBS 45 b", 2: "Ac Electric GBS 45 c", 3: "Ac Electric GBS SJ200 a",
               4: "Ac Electric GBS SJ200 b", 5: "Ac Electric GBS SJ200 c", 6: "Ac Electric GLD D55 a", 7: "Ac Electric GLD D55 b",
               8: "Ac Electric GLD D55 c", 9: "Ac Electric MTN D45 a", 10: "Ac Electric MTN D45 b", 11: "Ac Electric MTN D45 c",
               12: "Ac Electric TLR 614a", 13: "Ac Electric TLR 614b", 14: "Ac Electric TLR 614c", 15: "Ac Piezo GBS 45 a",
               16: "Ac Piezo GBS 45 b", 17: "Ac Piezo GBS 45 c", 18: "Ac Piezo GBS SJ200 a", 19: "Ac Piezo GBS SJ200 b",
               20: "Ac Piezo GBS SJ200 c", 21: "Ac Piezo GLD D55 a", 22: "Ac Piezo GLD D55 b", 23: "Ac Piezo GLD D55 c",
               24: "Ac Piezo MTN D45 a", 25: "Ac Piezo MTN D45 b", 26: "Ac Piezo MTN D45 c", 27: "Ac Piezo TLR 614a",
               28: "Ac Piezo TLR 614b", 29: "Ac Piezo TLR 614c", 30: "Ac Silent GBS 45 a", 31: "Ac Silent GBS 45 b",
               32: "Ac Silent GBS 45 c", 33: "Ac Silent GBS SJ200 a", 34: "Ac Silent GBS SJ200 b", 35: "Ac Silent GBS SJ200 c",
               36: "Ac Silent GLD D55 a", 37: "Ac Silent GLD D55 b", 38: "Ac Silent GLD D55 c", 39: "Ac Silent MTN D45 a",
               40: "Ac Silent MTN D45 b", 41: "Ac Silent MTN D45 c", 42: "Ac Silent TLR 614a", 43: "Ac Silent TLR 614b",
               44: "Ac Silent TLR 614c", 45: "Cl Piezo Corba GK a", 46: "Cl Piezo Corba GK b", 47: "Cl Piezo Corba GK c",
               48: "Cl Piezo YMH 42S a", 49: "Cl Piezo YMH 42S b", 50: "Cl Piezo YMH 42S c", 51: "Cl Silent Corba GK a",
               52: "Cl Silent Corba GK b", 53: "Cl Silent Corba GK c", 54: "Cl Silent YMH 42S a", 55: "Cl Silent YMH 42S b",
               56: "Cl Silent YMH 42S c"],
        0x0b: [0: "55 US TD", 1: "59 US Bass", 2: "UK30 CL", 3: "UK30 CR", 4: "UK30 DS", 5: "ODS 100 CL",
               6: "ODS 100 CR", 7: "ODS 100 DS", 8: "Dividers CL", 9: "Dividers DS", 10: "CAA OD100 CH1", 11: "CAA OD100 CH2",
               12: "CAA OD100 CH3", 13: "Rock Vrb CL", 14: "Rock Vrb DS", 15: "J800 CL", 16: "J800 CR", 17: "J800 DS",
               18: "PLX 100 CL", 19: "PLX 100 DS", 20: "EV 5050 CH1", 21: "EV 5050 CH2", 22: "EV 5050 CH3", 23: "Cali BLD CL",
               24: "Cali BLD CR", 25: "Cali BLD DS"],
    ]

    /// Models whose parameters differ from their type's default list.
    static let modelParams: [UInt8: [UInt16: [Param]]] = {
        func p(_ label: String, _ kind: ParamKind = .knob, _ range: ClosedRange<Double>? = nil) -> Param {
            Param(label: label, kind: kind, range: range)
        }
        return [
            0x04: [0: [p("Sensitivity"), p("Level")],
                   2: [p("Threshold"), p("Level")]],
            // Touch Wah word order assumed from the app's display order (not yet checked against raw words).
            0x05: [0: [p("Peak"), p("Sensitivity"), p("Level")],
                   2: [p("Rate"), p("Q"), p("Mix")]],
            0x06: [0: [p("Rate", .rate), p("Level"), p("Depth")],
                   1: [p("Rate", .rate), p("Level"), p("Depth")],
                   2: [p("Rate", .rate), p("Mix"), p("Feedback")],
                   3: [p("Rate", .rate), p("Mix"), p("Feedback")],
                   4: [p("Rate", .rate), p("Mix"), p("Tone")],
                   5: [p("Rate", .rate), p("Mix"), p("Tone")],
                   6: [p("Rate", .rate), p("Mix"), p("Tone")],
                   7: [p("Rate", .rate), p("Mix"), p("Tone")],
                   8: [p("Rate", .rate), p("Mix"), p("Tone")],
                   9: [p("Mix"), p("Tone"), p("Pitch", .pitch)],
                   12: [p("Sample"), p("Mix"), p("Bit")],
                   13: [p("Rise"), p("Level")]],
            // Delay words 4–5 are hidden in the official app except on these models.
            0x07: [7: [p("Level"), p("Feedback"), p("Time", .milliseconds, 40...2500), p("Depth"), p("Rate")],
                   8: [p("Level"), p("Feedback"), p("Time", .milliseconds, 40...2500), p("Depth"), p("Rate")]],
            // Words after Level (50 … 2000 50) are not shown in the official app.
            0x09: [0: ["100Hz", "250Hz", "630Hz", "1.6kHz", "4kHz"].map { p($0, .eqGain) } + [p("Level")]],
        ]
    }()

    /// Parameters in word order (after enabled + model).
    static let params: [UInt8: [Param]] = {
        func p(_ label: String, _ kind: ParamKind = .knob, _ range: ClosedRange<Double>? = nil) -> Param {
            Param(label: label, kind: kind, range: range)
        }
        // Band frequency ranges in Hz, from the official app.
        let bands: [(String, ClosedRange<Double>)] = [
            ("Low", 40...600), ("Low Mid", 200...2000), ("High Mid", 600...6000), ("High", 1500...20000),
        ]
        var eq: [Param] = []
        for (band, frequencies) in bands {
            eq += [p("\(band) Gain", .eqGain), p("\(band) Freq", .hertz, frequencies), p("\(band) Q", .eqQ)]
        }
        // Cuts like the Cab's: 0 = OFF, Low Cut up to 600 Hz, High Cut up to 2000 Hz.
        eq += [p("Low Cut", .offOrValue, 0...600), p("High Cut", .offOrValue, 0...2000), p("Level")]
        return [
            0x01: [p("Gain"), p("Tone"), p("Vol")],
            0x02: [p("Gain"), p("Bass"), p("Mid"), p("Treble"), p("Presence"), p("Master")],
            // Cuts: 0 = OFF; Low Cut up to 600 Hz, High Cut up to 2000 Hz (official
            // app, per user). Stored values above (2A 8300, 0D 20000) keep a number field.
            0x03: [p("Gain"), p("Low Cut", .offOrValue, 0...600), p("High Cut", .offOrValue, 0...2000)],
            0x04: [p("Attack"), p("Ratio"), p("Threshold"), p("Level")],
            0x05: [p("Rate"), p("Peak"), p("Range"), p("Level")],
            0x06: [p("Rate", .rate), p("Mix"), p("Tone"), p("Depth")],
            0x07: [p("Level"), p("Feedback"), p("Time", .milliseconds, 40...2500)],
            0x08: [p("Pre Delay", .milliseconds, 0...200), p("Level"), p("Decay"), p("Tone")],
            0x09: eq,
            // 9 more words follow that the official app doesn't show (they look like DSP coefficients).
            0x0a: [p("Gain"), p("Blend"), p("Low", .simGain), p("Mid", .simGain), p("Mid Shift", .midShift), p("High", .simGain)],
            // Same layout as a Pre-Amp (1D: 17 33 50 59 69 91 = Gain … Master).
            0x0b: [p("Gain"), p("Bass"), p("Mid"), p("Treble"), p("Presence"), p("Master")],
        ]
    }()

    /// Categories in the official app's picker order.
    static let categoryOrder: [UInt8] = [0x0a, 0x01, 0x02, 0x03, 0x0b, 0x04, 0x05, 0x09, 0x06, 0x07, 0x08]

    /// Slots the editor offers (9, as in the official app).
    static let maxModules = 9

    /// Wire model values with names, in order.
    static func models(of type: UInt8) -> [UInt16] {
        (modelNames[type] ?? [:]).keys.sorted()
    }

    /// True when the module's type or model has no name in the tables yet.
    static func isUnnamed(_ module: PrimeModule) -> Bool {
        guard !module.isEmpty else { return false }
        guard typeNames[module.type] != nil, let model = module.model else { return true }
        return modelNames[module.type]?[model] == nil
    }

    /// Tempo-sync notes in the official app's slider order (left → right)
    /// with their stored index, recorded by stepping Rainbow's Time slider
    /// over USB. Delay (0x8000 | index) and Mod Rate (101 + index) share it.
    static let syncNoteOrder: [(index: UInt16, label: String)] = [
        (2, "1/32"), (4, "1/32D"), (0, "1/32T"), (5, "1/16"), (7, "1/16D"), (1, "1/16T"),
        (8, "1/8"), (9, "1/8D"), (3, "1/8T"), (10, "1/4"), (12, "1/4D"), (6, "1/4T"),
        (13, "1/2"), (14, "1/2D"), (11, "1/2T"), (15, "1/1"),
    ]
    static let syncNotes: [UInt16: String] = Dictionary(uniqueKeysWithValues: syncNoteOrder.map { ($0.index, $0.label) })

    static func syncLabel(_ index: UInt16) -> String {
        "♩ " + (syncNotes[index] ?? "sync #\(index)")
    }

    /// The sync-note index when a Delay Time (`.milliseconds`, high bit) or
    /// Mod Rate (`.rate`, 101 + index) value is tempo-synced, else nil.
    static func syncIndex(_ kind: ParamKind, value: UInt16) -> UInt16? {
        switch kind {
        case .milliseconds: return value & 0x8000 != 0 ? value & 0x7fff : nil
        case .rate: return value > 100 ? value - 101 : nil
        default: return nil
        }
    }

    static func syncedValue(_ kind: ParamKind, index: UInt16) -> UInt16 {
        kind == .rate ? 101 + index : 0x8000 | index
    }

    static func typeName(_ type: UInt8) -> String {
        typeNames[type] ?? String(format: "Type 0x%02x", type)
    }

    /// "02 Flex Boost", or just "07" when the name isn't known yet.
    static func modelLabel(type: UInt8, model: UInt16) -> String {
        let number = String(format: "%02d", Int(model) + 1)
        guard let name = modelNames[type]?[model] else { return number }
        return "\(number) \(name)"
    }

    /// `paramIndex` counts from 0 at the first word after enabled + model.
    static func param(type: UInt8, model: UInt16?, paramIndex: Int) -> Param {
        let list = model.flatMap { modelParams[type]?[$0] } ?? params[type]
        if let list, paramIndex < list.count { return list[paramIndex] }
        return Param(label: "P\(paramIndex + 1)", kind: .unknown)
    }

    static func format(_ value: UInt16, kind: ParamKind) -> String {
        switch kind {
        case .knob, .unknown:
            return "\(value)"
        case .milliseconds:
            return value & 0x8000 != 0 ? syncLabel(value & 0x7fff) : "\(value) ms"
        case .hertz:
            return "\(value) Hz"
        case .offOrValue:
            return value == 0 ? "OFF" : "\(value) Hz"
        case .eqGain:
            return String(format: "%+.1f dB", (Double(value) - 32) / 2)
        case .eqQ:
            return String(format: "%.1f", (Double(value) + 2) / 10)
        case .rate:
            return value > 100 ? syncLabel(value - 101) : "\(value)"
        case .pitch:
            return String(format: "%.1f", Double(value) / 10 - 12)
        case .simGain:
            return String(format: "%+.1f dB", (Double(value) - 120) / 10)
        case .midShift:
            return "\(Int(value) + 200) Hz"
        }
    }

    /// Whether a 0…100 slider is the right control for this word.
    /// Raw range for a slider, or nil when the word gets a number field
    /// (also when the stored value lies outside the known range). Ranges are
    /// from the official app: Guitar Sim Low/Mid/High −12…+12 dB (raw 0…240),
    /// Mid Shift 200…2000 Hz (raw 0…1800), EQ gains −16…+16 dB (raw 0…64),
    /// EQ Q 0.2…10.0 (raw 0…98), Pitch Shift −12…+12 (raw 0…240), Reverb
    /// Pre Delay 0…200 ms, Delay Time 40…2500 ms; per-parameter ranges sit in
    /// `Param.range`.
    static func sliderRange(_ param: Param, value: UInt16) -> ClosedRange<Double>? {
        let range: ClosedRange<Double>?
        switch param.kind {
        case .knob, .rate, .unknown: range = param.range ?? 0...100
        case .simGain: range = 0...240
        case .midShift: range = 0...1800
        case .eqGain: range = 0...64
        case .eqQ: range = 0...98
        case .pitch: range = 0...240     // −12.0…+12.0 semitones
        // Delay Time 40…2500 ms; tempo-synced values (0x8000 | note) fall
        // outside the range and keep a number field.
        case .milliseconds, .hertz, .offOrValue: range = param.range
        }
        guard let range, range.contains(Double(value)) else { return nil }
        return range
    }
}
