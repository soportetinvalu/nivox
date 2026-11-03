// SPDX-License-Identifier: MIT
// Real production model/view + HAL. No permission injection, audio or fake devices.
import AppKit
import SwiftUI
import AVFoundation
import CoreAudio
import NivoxCallAudio

@main struct CallAudioUISmoke {
    @MainActor static func require(_ value: Bool, _ message: String) {
        if !value { fputs("FAIL: \(message)\n", stderr); exit(1) }
    }
    @MainActor static func settled(_ model: CallAudioModel) async {
        for _ in 0..<200 {
            if !model.busy { return }
            try? await Task.sleep(nanoseconds: 50_000_000)
        }
        require(false, "production worker did not settle within 10 seconds")
    }
    static func defaultDevice(_ selector: AudioObjectPropertySelector) -> AudioDeviceID {
        var address = AudioObjectPropertyAddress(mSelector: selector,
            mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
        var device: AudioDeviceID = 0
        var size = UInt32(MemoryLayout<AudioDeviceID>.size)
        let status = AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &size, &device)
        if status != noErr { fputs("FAIL: cannot read CoreAudio defaults\n", stderr); exit(1) }
        return device
    }
    @MainActor static func exercise() async {
        let args = CommandLine.arguments
        require(args.count == 3, "usage: ui-smoke OUTPUT_DIRECTORY absent|present")
        let out = URL(fileURLWithPath: args[1], isDirectory: true)
        let expectedDriver = args[2] == "present"
        require(["absent", "present"].contains(args[2]), "invalid expected driver state")
        let permissionBefore = AVCaptureDevice.authorizationStatus(for: .audio)
        let defaultsBefore = [defaultDevice(kAudioHardwarePropertyDefaultInputDevice), defaultDevice(kAudioHardwarePropertyDefaultOutputDevice)]
        let model = CallAudioModel()
        await settled(model)
        require(model.driver == expectedDriver, "real HAL driver presence disagrees with phase")
        require(!model.enabled && !model.canStart && model.microphone.isEmpty && model.output.isEmpty,
                "opening the model must not select devices or start audio")
        // Cross-check the asynchronous production model against another real HAL controller.
        guard let real = nv_call_create_mac() else { require(false, "HAL controller allocation failed"); return }
        defer { nv_call_destroy(real) }
        require(nv_call_refresh(real) == 0, "real HAL enumeration failed")
        var physical: [String] = []
        for i in 0..<nv_call_device_count(real) {
            guard let d = nv_call_device_at(real, i) else { continue }
            if d.pointee.physical != 0 && d.pointee.alive != 0 && d.pointee.rate >= 8000 {
                physical.append(String(cString: nv_call_device_uid(d)))
            }
        }
        require(model.devices.map(\.id).sorted() == physical.sorted(), "UI inventory differs from real HAL physical inventory")
        require(!model.devices.contains { $0.id.hasPrefix("nivox.") }, "virtual endpoints exposed as physical choices")
        model.start() // Negative: missing explicit selections must be refused before TCC.
        require(!model.enabled && !model.busy, "start without selections was not refused")
        let rejected = nv_call_start(real, "nivox.test.nonexistent.input", "nivox.test.nonexistent.output", ProcessInfo.processInfo.systemUptime)
        require(rejected != 0 && Int(nv_call_state(real)) == Int(NV_CALL_ERROR), "real backend accepted nonexistent physical UIDs")
        require(!String(cString: nv_call_detail(real)).isEmpty, "backend rejection lacks diagnostic")
        nv_call_stop(real)
        require(Int(nv_call_state(real)) == Int(NV_CALL_STOPPED), "backend stop failed")
        // A real AppKit window and unmodified production SwiftUI view, not a fixture view.
        let host = NSHostingView(rootView: CallAudioView(audio: model))
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1000, height: 900),
                              styleMask: [.titled, .closable, .resizable], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.title = "Nivox production call view · CI"
        window.contentView = host
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        try? await Task.sleep(nanoseconds: 700_000_000)
        host.layoutSubtreeIfNeeded()
        guard let bitmap = host.bitmapImageRepForCachingDisplay(in: host.bounds) else {
            require(false, "production view could not allocate render bitmap"); return
        }
        host.cacheDisplay(in: host.bounds, to: bitmap)
        require(bitmap.pixelsWide >= 780 && bitmap.pixelsHigh >= 560, "render smaller than production minimum")
        var colors = Set<String>()
        for y in stride(from: 0, to: bitmap.pixelsHigh, by: 8) {
            for x in stride(from: 0, to: bitmap.pixelsWide, by: 8) {
                if let color = bitmap.colorAt(x: x, y: y) { colors.insert(color.description) }
            }
        }
        require(colors.count > 16, "blank/unrendered production view")
        guard let png = bitmap.representation(using: .png, properties: [:]) else { require(false, "PNG encoding failed"); return }
        do { try png.write(to: out.appendingPathComponent("production-call-view.png")) }
        catch { require(false, "PNG write failed: \(error)") }
        model.handleLifecycle(NV_CALL_UI_SLEEP)
        await settled(model)
        require(!model.enabled && model.status.hasPrefix("Suspensión:"), "real model sleep did not complete stop")
        var quitAcknowledged = false
        model.prepareToQuit { quitAcknowledged = true }
        await settled(model)
        require(quitAcknowledged && !model.enabled && !model.canStart, "quit acknowledged before serialized stop")
        require(AVCaptureDevice.authorizationStatus(for: .audio) == permissionBefore, "TCC state changed")
        require(defaultsBefore == [defaultDevice(kAudioHardwarePropertyDefaultInputDevice), defaultDevice(kAudioHardwarePropertyDefaultOutputDevice)], "system defaults changed")
        let report: [String: Any] = ["ok": true, "phase": args[2], "production_view_rendered": true,
            "real_hal_inventory_matched": true, "physical_device_count": physical.count,
            "invalid_backend_uids_rejected": true, "empty_selection_start_refused": true,
            "sleep_stop_completed": true, "quit_stop_completed": true,
            "tcc_unchanged": true, "system_defaults_unchanged": true,
            "tcc_authorization_raw": permissionBefore.rawValue,
            "pixel_width": bitmap.pixelsWide, "pixel_height": bitmap.pixelsHigh,
            "sampled_colors": colors.count, "microphone_capture_tested": false,
            "user_clicks_tested": false, "tcc_prompt_tested": false]
        do {
            let data = try JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys])
            try data.write(to: out.appendingPathComponent("runtime.json"))
            print(String(decoding: data, as: UTF8.self))
        } catch { require(false, "report write failed: \(error)") }
        window.close()
        exit(0)
    }
    @MainActor static func main() {
        _ = NSApplication.shared
        NSApp.setActivationPolicy(.regular)
        Task { @MainActor in await exercise() }
        NSApp.run()
    }
}
