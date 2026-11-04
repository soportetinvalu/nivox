// SPDX-License-Identifier: MIT
// Headless native checks of the production A/B capture path (App/ProbeInput.swift).
// No UI, no network, no device writes, no driver. Layout/time/guard cases run on any Mac;
// hardware sections run ONLY if a physical input already exists and TCC already allows it.
import AVFoundation
import CoreAudio
import NoiseProbe

final class Blocks {
    let list: UnsafeMutableAudioBufferListPointer
    private var memory: [UnsafeMutablePointer<Float>] = []
    init(layout: [Int], frames: Int, value: (_ buffer: Int, _ channel: Int, _ frame: Int) -> Float) {
        list = AudioBufferList.allocate(maximumBuffers: layout.count)
        for (i, n) in layout.enumerated() {
            let p = UnsafeMutablePointer<Float>.allocate(capacity: max(1, frames * n))
            for f in 0..<frames { for c in 0..<n { p[f * n + c] = value(i, c, f) } }
            memory.append(p)
            list[i] = AudioBuffer(mNumberChannels: UInt32(n), mDataByteSize: UInt32(frames * n * 4),
                                  mData: UnsafeMutableRawPointer(p))
        }
    }
    deinit { memory.forEach { $0.deallocate() }; free(list.unsafeMutablePointer) }
}

final class EngineChanges { var count = 0; var firstAt = 0.0; var text = "not_observed" }

@main struct ProbeCaptureSmoke {
    static var passed: [String] = []
    static var failures: [String] = []
    static func check(_ ok: Bool, _ name: String) {
        guard ok else {
            FileHandle.standardError.write(Data(("FAILED: " + name + "\n").utf8))
            exit(1)
        }
        passed.append(name)
    }
    static func deliver(_ s: OpaquePointer, _ b: Blocks, time: Double, channel: Int, valid: Bool = true) -> Bool {
        var stamp = AudioTimeStamp()
        stamp.mSampleTime = time
        stamp.mFlags = valid ? .sampleTimeValid : []
        return HALCapture.deliver(s, UnsafePointer(b.list.unsafeMutablePointer), &stamp, channel: channel)
    }
    static func finished(_ s: OpaquePointer) -> [Float] {
        precondition(np_pump(s) == 0)
        np_close(s)
        precondition(np_finish(s) == 0)
        guard let p = np_raw(s) else { return [] }
        return Array(UnsafeBufferPointer(start: p, count: np_count(s)))
    }
    static func snapshot(uid: String = "U", rate: Double = 48000, channels: Int = 2, layout: [Int] = [2],
                         streams: [ProbeStreamInfo]? = nil) -> ProbeDeviceSnapshot {
        ProbeDeviceSnapshot(uid: uid, rate: rate, channels: channels, layout: layout,
                            streams: streams ?? [ProbeStreamInfo(sampleRate: 48000, channels: channels,
                                                                  isFloat32: true, nonInterleaved: false)])
    }

    static func layoutAndTimeCases() {
        // Interleaved stereo: selected channel only, never a mix of both.
        do {
            guard let session = try? CaptureSession() else { fatalError("session") }
            let s = session.handle
            let b = Blocks(layout: [2], frames: 480) { _, c, f in c == 0 ? 0.25 : 0.1 + Float(f) * 1e-5 }
            check(deliver(s, b, time: 1000, channel: 1), "interleaved block accepted")
            check(deliver(s, b, time: 1480, channel: 1), "contiguous interleaved block accepted")
            check(np_received(s) == 960 && np_error(s) == 0 && np_blocks(s) == 2, "interleaved counts")
            let raw = finished(s)
            check(raw.count == 960 && (0..<480).allSatisfy { raw[$0] == 0.1 + Float($0) * 1e-5 },
                  "channel 2 samples are exactly the selected interleaved channel")
        }
        // Planar: one buffer per channel.
        do {
            guard let session = try? CaptureSession() else { fatalError("session") }
            let s = session.handle
            let b = Blocks(layout: [1, 1], frames: 480) { i, _, _ in i == 0 ? 0.4 : 0.2 }
            check(deliver(s, b, time: 77, channel: 1), "planar block accepted")
            let raw = finished(s)
            check(raw.count == 480 && raw.allSatisfy { $0 == 0.2 }, "planar channel 2 selected from second buffer")
        }
        // Mixed layout: third channel lives in the second buffer.
        do {
            guard let session = try? CaptureSession() else { fatalError("session") }
            let s = session.handle
            let b = Blocks(layout: [2, 1], frames: 100) { i, c, _ in i == 1 ? 0.3 : 0.05 * Float(c + 1) }
            check(deliver(s, b, time: 5, channel: 2), "mixed layout block accepted")
            let raw = finished(s)
            check(raw.count == 100 && raw.allSatisfy { $0 == 0.3 }, "channel index spans buffers")
        }
        // Block larger than the internal scratch is chunked, order preserved.
        do {
            guard let session = try? CaptureSession() else { fatalError("session") }
            let s = session.handle
            let b = Blocks(layout: [2], frames: 10000) { _, c, f in c == 0 ? Float(f % 1000) * 1e-4 : 0 }
            check(deliver(s, b, time: 0, channel: 0), "10000-frame block accepted")
            check(np_error(s) == 0 && np_received(s) == 10000, "chunked block fully queued")
            let raw = finished(s)
            check(raw.count == 10000 && (0..<10000).allSatisfy { raw[$0] == Float($0 % 1000) * 1e-4 },
                  "chunked block preserves order and values")
        }
        // Empty callback is not an error.
        do {
            guard let session = try? CaptureSession() else { fatalError("session") }
            let b = Blocks(layout: [2], frames: 0) { _, _, _ in 0 }
            check(deliver(session.handle, b, time: 10, channel: 0) && np_error(session.handle) == 0,
                  "empty callback is neutral")
        }
        // Counter-tests: each real defect must be sticky and fail closed.
        func rejected(_ name: String, layout: [Int] = [2], channel: Int = 0, second: Double = 1480,
                      secondChannel: Int? = nil, valid: Bool = true, first: Double = 1000,
                      value: Float = 0.1, expectFalse: Bool = false) {
            guard let session = try? CaptureSession() else { fatalError("session") }
            let s = session.handle
            let b = Blocks(layout: layout, frames: 480) { _, _, _ in value }
            _ = deliver(s, b, time: first, channel: channel, valid: valid)
            if np_error(s) == 0 { _ = deliver(s, b, time: second, channel: secondChannel ?? channel) }
            check(np_error(s) != 0, name)
            // Sticky: a later perfectly valid block cannot resurrect the take.
            _ = deliver(s, b, time: second + 480, channel: channel)
            check(np_error(s) != 0, name + " stays sticky")
        }
        rejected("gap in device sample time rejected", second: 1481)
        rejected("repeated device sample time rejected", second: 1479)
        rejected("fractional device sample time rejected", first: 1000.5)
        rejected("missing sample-time flag rejected", valid: false)
        rejected("channel outside buffer list rejected", layout: [1], channel: 1)
        rejected("NaN sample rejected", value: Float.nan)
        rejected("out of range sample rejected", value: 1.5)
    }

    static func guardCases() {
        let base = snapshot()
        check(ProbeGuard.difference(from: base, to: snapshot()) == nil,
              "false positive: notification with unchanged UID/rate/channels/format is not a route change")
        check(ProbeGuard.difference(from: base, to: nil) != nil, "disconnect/dead device discards")
        check(ProbeGuard.difference(from: base, to: snapshot(uid: "V")) != nil, "different UID discards")
        check(ProbeGuard.difference(from: base, to: snapshot(rate: 44100)) != nil, "HAL rate change discards")
        check(ProbeGuard.difference(from: base, to: snapshot(channels: 1, layout: [1])) != nil, "channel count change discards")
        check(ProbeGuard.difference(from: base, to: snapshot(layout: [1, 1])) != nil, "buffer layout change discards")
        let int16 = [ProbeStreamInfo(sampleRate: 48000, channels: 2, isFloat32: false, nonInterleaved: false)]
        check(ProbeGuard.difference(from: base, to: snapshot(streams: int16)) != nil, "stream format change discards")
        let planar = [ProbeStreamInfo(sampleRate: 48000, channels: 2, isFloat32: true, nonInterleaved: true)]
        check(ProbeGuard.difference(from: base, to: snapshot(streams: planar)) != nil, "interleaving change discards")
        check(ProbeGuard.captureProblem(base, channel: 1) == nil, "48 kHz Float32 stereo channel 2 accepted")
        check(ProbeGuard.captureProblem(snapshot(rate: 44100), channel: 0) != nil, "44.1 kHz refused, not resampled")
        check(ProbeGuard.captureProblem(snapshot(streams: int16), channel: 0) != nil, "non-Float32 refused")
        check(ProbeGuard.captureProblem(base, channel: 2) != nil, "channel beyond device refused")
        check(ProbeGuard.captureProblem(snapshot(streams: []), channel: 0) != nil, "no streams refused")
    }

    static func live(_ d: ProbeInputDevice) -> [String: Any] {
        var out: [String: Any] = ["name": d.name, "uid": d.id, "channels": d.channels, "rate": Int(d.rate)]
        guard let snap = ProbeInput.snapshot(d.deviceID) else { out["status"] = "SNAPSHOT_UNAVAILABLE"; return out }
        out["snapshot"] = snap.text
        if let problem = ProbeGuard.captureProblem(snap, channel: 0) { out["status"] = "NOT_ELIGIBLE: " + problem; return out }
        // New path: IOProc on this explicit device.
        guard let session = try? CaptureSession() else { fatalError("session") }
        let hal = HALCapture(device: d, channel: 0, session: session)
        let notified = EngineChanges()
        // Same drain the app runs: producer never meets a full queue.
        let worker = DispatchQueue(label: "smoke.pump")
        let pump = DispatchSource.makeTimerSource(queue: worker)
        let handle = session.handle
        pump.schedule(deadline: .now(), repeating: .milliseconds(5))
        pump.setEventHandler { _ = np_pump(handle) }
        pump.resume()
        do { try hal.start { notified.count += 1 } } catch {
            pump.cancel(); out["status"] = "HAL_START_FAILED: \(error.localizedDescription)"
            failures.append(d.name + ": HAL start failed"); return out
        }
        RunLoop.current.run(until: Date().addingTimeInterval(1.5))
        let stillSame = ProbeGuard.difference(from: snap, to: ProbeInput.snapshot(d.deviceID))
        hal.stop(); pump.cancel(); worker.sync {}
        let blocks = np_blocks(handle), frames = np_received(handle), error = Int(np_error(handle))
        out["hal"] = ["blocks": blocks, "frames": frames, "error": error, "hal_notifications": notified.count,
                      "state_difference": stillSame ?? "none"] as [String: Any]
        // Real device, real clock: contiguous device sample times, ~1.5 s of frames, no discard.
        if blocks == 0 || error != 0 || frames < 48000 || stillSame != nil {
            failures.append("\(d.name): live HAL capture blocks=\(blocks) frames=\(frames) error=\(error) difference=\(stillSame ?? "none")")
        }
        // Historical path, diagnostic only: AVAudioEngine bound to the same device.
        let engine = AVAudioEngine(), changes = EngineChanges(), taps = EngineChanges()
        let begin = ProcessInfo.processInfo.systemUptime
        let observer = NotificationCenter.default.addObserver(forName: .AVAudioEngineConfigurationChange,
                                                              object: engine, queue: .main) { _ in
            if changes.count == 0 {
                changes.firstAt = ProcessInfo.processInfo.systemUptime - begin
                changes.text = ProbeGuard.difference(from: snap, to: ProbeInput.snapshot(d.deviceID)) ?? "none"
            }
            changes.count += 1
        }
        let bound = (try? ProbeInput.bind(engine.inputNode, to: d, input: true)) != nil
        let bus = engine.inputNode.outputFormat(forBus: 0)
        engine.inputNode.installTap(onBus: 0, bufferSize: 1024, format: bus) { _, _ in taps.count += 1 }
        engine.prepare()
        let started = (try? engine.start()) != nil
        RunLoop.current.run(until: Date().addingTimeInterval(2))
        engine.stop(); engine.inputNode.removeTap(onBus: 0)
        NotificationCenter.default.removeObserver(observer)
        out["legacy_engine"] = ["bound": bound, "started": started, "bus_channels": Int(bus.channelCount),
                                "bus_rate": Int(bus.sampleRate), "hal_channels": d.channels,
                                "config_changes": changes.count, "first_change_after_s": changes.firstAt,
                                "tap_buffers": taps.count,
                                "state_difference_at_first_change": changes.text] as [String: Any]
        out["status"] = "LIVE_HARDWARE_EXERCISED"
        return out
    }

    static func hardware() -> [String: Any] {
        let devices = ProbeInput.devices()
        let auth = AVCaptureDevice.authorizationStatus(for: .audio)
        var out: [String: Any] = ["physical_inputs": devices.map { "\($0.name) · \($0.channels) ch · \(Int($0.rate)) Hz" },
                                  "tcc_audio_status_raw": auth.rawValue]
        if devices.isEmpty { out["status"] = "NO_PHYSICAL_INPUT_ON_THIS_MAC"; return out }
        out["devices"] = devices.map { live($0) }
        out["status"] = "PHYSICAL_INPUTS_TRIED"
        return out
    }

    static func main() {
        layoutAndTimeCases()
        guardCases()
        let report: [String: Any] = ["probe_capture_smoke": true, "cases_passed": passed.count,
                                     "cases": passed, "hardware": hardware()]
        guard let data = try? JSONSerialization.data(withJSONObject: report, options: [.sortedKeys]),
              let text = String(data: data, encoding: .utf8) else { fatalError("report") }
        print(text)
        if !failures.isEmpty {
            FileHandle.standardError.write(Data(("FAILED hardware: " + failures.joined(separator: "; ") + "\n").utf8))
            exit(1)
        }
    }
}
