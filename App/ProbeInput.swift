// SPDX-License-Identifier: MIT
import AVFoundation
import CoreAudio
import AudioToolbox
import NoiseProbe

final class CaptureSession: @unchecked Sendable {
    let handle: OpaquePointer
    init() throws {
        guard let p = np_create(48000, 1, 720000) else {
            throw ProbeError.message("No se pudo crear SpeexDSP / cola lock-free.")
        }
        handle = p
    }
    deinit { np_destroy(handle) }
}
enum ProbeError: LocalizedError {
    case message(String)
    var errorDescription: String? { if case let .message(s) = self { return s }; return nil }
}

struct ProbeInputDevice: Identifiable {
    let deviceID: AudioDeviceID
    let id: String // UID, not a potentially recycled numeric ID
    let name: String
    let channels: Int
    let rate: Double
}

// Read-only HAL discovery; the only setter below is on this engine's AudioUnit.
// Never set system default device, device rate, hardware gain or buffer size.
enum ProbeInput {
    private static func address(_ selector: AudioObjectPropertySelector,
                                _ scope: AudioObjectPropertyScope = kAudioObjectPropertyScopeGlobal) -> AudioObjectPropertyAddress {
        AudioObjectPropertyAddress(mSelector: selector, mScope: scope, mElement: kAudioObjectPropertyElementMain)
    }
    private static func uint(_ id: AudioObjectID, _ selector: AudioObjectPropertySelector) -> UInt32? {
        var a = address(selector), value: UInt32 = 0, size = UInt32(MemoryLayout<UInt32>.size)
        guard AudioObjectGetPropertyData(id, &a, 0, nil, &size, &value) == noErr else { return nil }
        return value
    }
    private static func string(_ id: AudioObjectID, _ selector: AudioObjectPropertySelector) -> String? {
        var a = address(selector), value: CFString = "" as CFString
        var size = UInt32(MemoryLayout<CFString>.size)
        guard AudioObjectGetPropertyData(id, &a, 0, nil, &size, &value) == noErr else { return nil }
        return value as String
    }
    /// Channels per HAL buffer, in the order the IOProc receives its AudioBufferList.
    static func channelLayout(_ id: AudioObjectID, input: Bool) -> [Int]? {
        var a = address(kAudioDevicePropertyStreamConfiguration,
                        input ? kAudioObjectPropertyScopeInput : kAudioObjectPropertyScopeOutput)
        var size: UInt32 = 0
        guard AudioObjectGetPropertyDataSize(id, &a, 0, nil, &size) == noErr,
              Int(size) >= MemoryLayout<AudioBufferList>.size, size <= 1048576 else { return nil }
        let memory = UnsafeMutableRawPointer.allocate(byteCount: Int(size), alignment: MemoryLayout<AudioBufferList>.alignment)
        defer { memory.deallocate() }
        guard AudioObjectGetPropertyData(id, &a, 0, nil, &size, memory) == noErr else { return nil }
        let list = UnsafeMutableAudioBufferListPointer(memory.assumingMemoryBound(to: AudioBufferList.self))
        return list.map { Int($0.mNumberChannels) }
    }
    static func device(_ id: AudioDeviceID, input: Bool = true) -> ProbeInputDevice? {
        guard uint(id, kAudioDevicePropertyDeviceIsAlive) == 1,
              uint(id, kAudioDevicePropertyIsHidden) == 0,
              let transport = uint(id, kAudioDevicePropertyTransportType),
              transport != kAudioDeviceTransportTypeVirtual,
              transport != kAudioDeviceTransportTypeAggregate,
              let uid = string(id, kAudioDevicePropertyDeviceUID), !uid.hasPrefix("nivox."),
              let name = string(id, kAudioObjectPropertyName) else { return nil }
        guard let layout = channelLayout(id, input: input) else { return nil }
        let channels = layout.reduce(0, +)
        guard channels > 0 else { return nil }
        var a = address(kAudioDevicePropertyNominalSampleRate)
        var rate: Float64 = 0
        var size = UInt32(MemoryLayout<Float64>.size)
        guard AudioObjectGetPropertyData(id, &a, 0, nil, &size, &rate) == noErr else { return nil }
        return ProbeInputDevice(deviceID: id, id: uid, name: name, channels: channels, rate: rate)
    }
    static func devices() -> [ProbeInputDevice] {
        var a = address(kAudioHardwarePropertyDevices), size: UInt32 = 0
        guard AudioObjectGetPropertyDataSize(AudioObjectID(kAudioObjectSystemObject), &a, 0, nil, &size) == noErr,
              size > 0, size <= 1048576 else { return [] }
        var ids = [AudioDeviceID](repeating: 0, count: Int(size) / MemoryLayout<AudioDeviceID>.size)
        let result = ids.withUnsafeMutableBytes {
            AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &a, 0, nil, &size, $0.baseAddress!)
        }
        guard result == noErr else { return [] }
        return ids.compactMap { device($0) }.sorted { $0.name.localizedStandardCompare($1.name) == .orderedAscending }
    }
    static func physicalOutput() -> ProbeInputDevice? {
        var a = address(kAudioHardwarePropertyDefaultOutputDevice)
        var id: AudioDeviceID = 0, size = UInt32(MemoryLayout<AudioDeviceID>.size)
        guard AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &a, 0, nil, &size, &id) == noErr else { return nil }
        return device(id, input: false)
    }
    static func bind(_ node: AVAudioIONode, to device: ProbeInputDevice, input: Bool) throws {
        guard let current = self.device(device.deviceID, input: input), current.id == device.id,
              let unit = node.audioUnit else {
            throw ProbeError.message("El dispositivo elegido ya no está disponible. Actualiza y selecciónalo de nuevo.")
        }
        var id = device.deviceID
        let result = AudioUnitSetProperty(unit, kAudioOutputUnitProperty_CurrentDevice,
                                          kAudioUnitScope_Global, 0, &id, UInt32(MemoryLayout<AudioDeviceID>.size))
        var actual: AudioDeviceID = 0, size = UInt32(MemoryLayout<AudioDeviceID>.size)
        guard result == noErr,
              AudioUnitGetProperty(unit, kAudioOutputUnitProperty_CurrentDevice,
                                   kAudioUnitScope_Global, 0, &actual, &size) == noErr,
              actual == id, self.device(actual, input: input)?.id == device.id else {
            throw ProbeError.message("No se pudo vincular/verificar el dispositivo elegido (OSStatus \(result)). Sin cambio de entrada predeterminada.")
        }
    }
}


// MARK: - Capture identity, guards and direct HAL capture

/// What the HAL says one input stream delivers to an IOProc (virtual format).
struct ProbeStreamInfo: Equatable {
    let sampleRate: Double
    let channels: Int
    let isFloat32: Bool
    let nonInterleaved: Bool
    var text: String {
        "\(isFloat32 ? "Float32" : "formato no Float32") \(Int(sampleRate)) Hz \(channels) ch \(nonInterleaved ? "no intercalado" : "intercalado")"
    }
}

/// Full identity + format of the selected input at one instant. Equality is the guard.
struct ProbeDeviceSnapshot: Equatable {
    let uid: String
    let rate: Double
    let channels: Int
    let layout: [Int]
    let streams: [ProbeStreamInfo]
    var text: String {
        "HAL \(Int(rate)) Hz · buffers \(layout.map(String.init).joined(separator: "+")) · "
            + (streams.isEmpty ? "sin streams" : streams.map { $0.text }.joined(separator: " | "))
    }
}

enum ProbeGuard {
    /// nil = same device and same format. A HAL/engine notification is only a reason to
    /// look; this comparison of real state is the verdict. Any real difference discards.
    static func difference(from base: ProbeDeviceSnapshot, to now: ProbeDeviceSnapshot?) -> String? {
        guard let now = now else { return "la entrada desapareció, se desconectó o dejó de estar viva" }
        if now.uid != base.uid { return "UID distinto (\(base.uid) → \(now.uid))" }
        if now.rate != base.rate { return "tasa HAL \(Int(base.rate)) → \(Int(now.rate)) Hz" }
        if now.channels != base.channels || now.layout != base.layout {
            return "canales/estructura de buffers \(base.layout) → \(now.layout)"
        }
        if now.streams != base.streams { return "formato de stream \(base.text) → \(now.text)" }
        return nil
    }
    /// nil = this phase can capture it bit-exactly (Float32, exactly 48 kHz, no resampling).
    static func captureProblem(_ s: ProbeDeviceSnapshot, channel: Int) -> String? {
        if s.rate != 48000 { return "tasa HAL \(Int(s.rate)) Hz; esta fase exige 48 kHz y no la cambia" }
        if s.streams.isEmpty { return "el HAL no declara streams de entrada" }
        if let bad = s.streams.first(where: { !$0.isFloat32 || $0.sampleRate != 48000 }) {
            return "stream \(bad.text); esta fase exige Float32 a 48 kHz"
        }
        if s.streams.reduce(0, { $0 + $1.channels }) != s.channels { return "canales de streams y de buffers no coinciden" }
        if channel < 0 || channel >= s.channels { return "canal \(channel + 1) fuera de \(s.channels) canales" }
        return nil
    }
}

extension ProbeInput {
    private static func ids(_ id: AudioObjectID, _ selector: AudioObjectPropertySelector,
                            _ scope: AudioObjectPropertyScope) -> [UInt32]? {
        var a = address(selector, scope), size: UInt32 = 0
        guard AudioObjectGetPropertyDataSize(id, &a, 0, nil, &size) == noErr, size <= 1048576 else { return nil }
        var out = [UInt32](repeating: 0, count: Int(size) / MemoryLayout<UInt32>.size)
        if out.isEmpty { return [] }
        let result = out.withUnsafeMutableBytes { AudioObjectGetPropertyData(id, &a, 0, nil, &size, $0.baseAddress!) }
        return result == noErr ? out : nil
    }
    private static func streamInfo(_ stream: AudioObjectID) -> ProbeStreamInfo? {
        var a = address(kAudioStreamPropertyVirtualFormat)
        var f = AudioStreamBasicDescription()
        var size = UInt32(MemoryLayout<AudioStreamBasicDescription>.size)
        guard AudioObjectGetPropertyData(stream, &a, 0, nil, &size, &f) == noErr else { return nil }
        return ProbeStreamInfo(
            sampleRate: f.mSampleRate, channels: Int(f.mChannelsPerFrame),
            isFloat32: f.mFormatID == kAudioFormatLinearPCM && (f.mFormatFlags & kAudioFormatFlagIsFloat) != 0
                && f.mBitsPerChannel == 32,
            nonInterleaved: (f.mFormatFlags & kAudioFormatFlagIsNonInterleaved) != 0)
    }
    /// Read-only. nil when the physical input is gone, dead, hidden, virtual or unreadable.
    static func snapshot(_ id: AudioDeviceID) -> ProbeDeviceSnapshot? {
        guard let d = device(id), let layout = channelLayout(id, input: true),
              let streamIDs = ids(id, kAudioDevicePropertyStreams, kAudioObjectPropertyScopeInput) else { return nil }
        let streams = streamIDs.compactMap { streamInfo($0) }
        guard streams.count == streamIDs.count else { return nil }
        return ProbeDeviceSnapshot(uid: d.id, rate: d.rate, channels: d.channels, layout: layout, streams: streams)
    }
    /// Addresses whose change should trigger a re-check (never a verdict by itself).
    static func watchedAddresses() -> [AudioObjectPropertyAddress] {
        [address(kAudioDevicePropertyDeviceIsAlive),
         address(kAudioDevicePropertyNominalSampleRate),
         address(kAudioDevicePropertyDeviceHasChanged),
         address(kAudioDevicePropertyStreamConfiguration, kAudioObjectPropertyScopeInput),
         address(kAudioDevicePropertyStreams, kAudioObjectPropertyScopeInput)]
    }
}

/// Captures one explicit channel straight from one explicit AudioDeviceID with an IOProc.
/// No AVAudioEngine, no output node, no implicit default-device format, no device/rate/gain
/// writes: only read properties, create IOProc, Start/Stop. Mirrors the HAL data layout.
final class HALCapture: @unchecked Sendable {
    let device: ProbeInputDevice
    let channel: Int
    private let session: CaptureSession
    private var procID: AudioDeviceIOProcID?
    private var running = false
    private var listener: AudioObjectPropertyListenerBlock?
    private var listened: [(AudioObjectID, AudioObjectPropertyAddress)] = []

    init(device: ProbeInputDevice, channel: Int, session: CaptureSession) {
        self.device = device; self.channel = channel; self.session = session
    }

    /// Real-time body, kept free of allocation/locks/ObjC. Returns false after a sticky
    /// np_abort when the buffer list, time stamp or channel does not match the contract.
    static func deliver(_ handle: OpaquePointer, _ data: UnsafePointer<AudioBufferList>,
                        _ time: UnsafePointer<AudioTimeStamp>, channel: Int) -> Bool {
        guard time.pointee.mFlags.contains(.sampleTimeValid), time.pointee.mSampleTime.isFinite else {
            np_abort(handle); return false
        }
        let t = time.pointee.mSampleTime, r = t.rounded()
        guard abs(t - r) < 1e-3, let sampleTime = Int64(exactly: r) else { np_abort(handle); return false }
        let list = UnsafeMutableAudioBufferListPointer(UnsafeMutablePointer(mutating: data))
        var first = 0
        for buffer in list {
            let n = Int(buffer.mNumberChannels)
            if n > 0, channel >= first, channel < first + n {
                guard let memory = buffer.mData else { np_abort(handle); return false }
                let frames = Int(buffer.mDataByteSize) / (MemoryLayout<Float>.size * n)
                _ = np_push_strided_timed(handle, memory.assumingMemoryBound(to: Float.self),
                                          frames, n, channel - first, sampleTime)
                return true
            }
            first += n
        }
        np_abort(handle); return false
    }

    /// `onSuspect` runs on the main queue after a HAL property notification.
    func start(onSuspect: @escaping @Sendable () -> Void) throws {
        guard !running, procID == nil else { throw ProbeError.message("La captura HAL ya estaba iniciada.") }
        let handle = session.handle, selected = channel
        let block: AudioDeviceIOBlock = { _, input, inputTime, output, _ in
            // A duplex device must never get uninitialised bytes on its output side.
            if Int(bitPattern: output) != 0 { // input-only devices may pass no output list
                for buffer in UnsafeMutableAudioBufferListPointer(output) {
                    if let m = buffer.mData { memset(m, 0, Int(buffer.mDataByteSize)) }
                }
            }
            _ = HALCapture.deliver(handle, input, inputTime, channel: selected)
        }
        var created: AudioDeviceIOProcID?
        let made = AudioDeviceCreateIOProcIDWithBlock(&created, device.deviceID, nil, block)
        guard made == noErr, let proc = created else {
            throw ProbeError.message("No se pudo crear el IOProc de la entrada elegida (OSStatus \(made)). Sin cambio de dispositivo.")
        }
        procID = proc
        let watcher: AudioObjectPropertyListenerBlock = { _, _ in onSuspect() }
        listener = watcher
        for var address in ProbeInput.watchedAddresses() {
            if AudioObjectAddPropertyListenerBlock(device.deviceID, &address, DispatchQueue.main, watcher) == noErr {
                listened.append((device.deviceID, address))
            }
        }
        var systemDevices = AudioObjectPropertyAddress(mSelector: kAudioHardwarePropertyDevices,
                                                       mScope: kAudioObjectPropertyScopeGlobal,
                                                       mElement: kAudioObjectPropertyElementMain)
        let system = AudioObjectID(kAudioObjectSystemObject)
        if AudioObjectAddPropertyListenerBlock(system, &systemDevices, DispatchQueue.main, watcher) == noErr {
            listened.append((system, systemDevices))
        }
        let started = AudioDeviceStart(device.deviceID, proc)
        guard started == noErr else {
            stop()
            throw ProbeError.message("AudioDeviceStart rechazó la entrada elegida (OSStatus \(started)). Sin cambio de dispositivo.")
        }
        running = true
    }

    /// Idempotent. After it returns no IOProc call is in flight.
    func stop() {
        if let watcher = listener {
            for var item in listened {
                _ = AudioObjectRemovePropertyListenerBlock(item.0, &item.1, DispatchQueue.main, watcher)
            }
        }
        listened = []; listener = nil
        if let proc = procID {
            if running { _ = AudioDeviceStop(device.deviceID, proc) }
            _ = AudioDeviceDestroyIOProcID(device.deviceID, proc)
        }
        procID = nil; running = false
    }
    deinit { stop() }
}
