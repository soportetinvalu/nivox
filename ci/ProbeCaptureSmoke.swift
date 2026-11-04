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

final class EngineChanges { var count = 0 }

@main struct ProbeCaptureSmoke {
    static var passed: [String] = []
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

    static func hardware() -> [String: Any] {
        let devices = ProbeInput.devices()
        var out: [String: Any] = ["physical_inputs": devices.map { "\($0.name) · \($0.channels) ch · \(Int($0.rate)) Hz" }]
        guard let d = devices.first else { out["status"] = "NO_PHYSICAL_INPUT_ON_THIS_MAC"; return out }
        guard AVCaptureDevice.authorizationStatus(for: .audio) == .authorized else {
            out["status"] = "PHYSICAL_INPUT_PRESENT_BUT_TCC_NOT_AUTHORIZED"; return out
        }
        guard let snap = ProbeInput.snapshot(d.deviceID), ProbeGuard.captureProblem(snap, channel: 0) == nil else {
            out["status"] = "PHYSICAL_INPUT_NOT_48K_FLOAT32"; return out
        }
        // New path.
        guard let session = try? CaptureSession() else { fatalError("session") }
        let hal = HALCapture(device: d, channel: 0, session: session)
        do { try hal.start {} } catch { out["status"] = "HAL_START_FAILED: \(error.localizedDescription)"; return out }
        RunLoop.current.run(until: Date().addingTimeInterval(1.5))
        hal.stop()
        out["hal_blocks"] = np_blocks(session.handle)
        out["hal_frames"] = np_received(session.handle)
        out["hal_error"] = np_error(session.handle)
        check(np_blocks(session.handle) > 0 && np_error(session.handle) == 0, "live HAL capture delivered contiguous blocks")
        // Historical path, diagnostic only: AVAudioEngine bound to the same device.
        let engine = AVAudioEngine(), changes = EngineChanges()
        let observer = NotificationCenter.default.addObserver(forName: .AVAudioEngineConfigurationChange,
                                                              object: engine, queue: .main) { _ in changes.count += 1 }
        let bound = (try? ProbeInput.bind(engine.inputNode, to: d, input: true)) != nil
        let bus = engine.inputNode.outputFormat(forBus: 0)
        engine.inputNode.installTap(onBus: 0, bufferSize: 1024, format: bus) { _, _ in }
        engine.prepare()
        let started = (try? engine.start()) != nil
        RunLoop.current.run(until: Date().addingTimeInterval(2))
        engine.stop(); engine.inputNode.removeTap(onBus: 0)
        NotificationCenter.default.removeObserver(observer)
        out["legacy_engine"] = ["bound": bound, "started": started, "bus_channels": Int(bus.channelCount),
                                "hal_channels": d.channels, "config_changes": changes.count] as [String: Any]
        out["status"] = "LIVE_HARDWARE_EXERCISED"
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
    }
}
