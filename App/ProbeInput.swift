// SPDX-License-Identifier: MIT
import AVFoundation
import CoreAudio
import AudioToolbox

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
    static func device(_ id: AudioDeviceID, input: Bool = true) -> ProbeInputDevice? {
        guard uint(id, kAudioDevicePropertyDeviceIsAlive) == 1,
              uint(id, kAudioDevicePropertyIsHidden) == 0,
              let transport = uint(id, kAudioDevicePropertyTransportType),
              transport != kAudioDeviceTransportTypeVirtual,
              transport != kAudioDeviceTransportTypeAggregate,
              let uid = string(id, kAudioDevicePropertyDeviceUID), !uid.hasPrefix("nivox."),
              let name = string(id, kAudioObjectPropertyName) else { return nil }
        var a = address(kAudioDevicePropertyStreamConfiguration,
                        input ? kAudioObjectPropertyScopeInput : kAudioObjectPropertyScopeOutput)
        var size: UInt32 = 0
        guard AudioObjectGetPropertyDataSize(id, &a, 0, nil, &size) == noErr,
              Int(size) >= MemoryLayout<AudioBufferList>.size, size <= 1048576 else { return nil }
        let memory = UnsafeMutableRawPointer.allocate(byteCount: Int(size), alignment: MemoryLayout<AudioBufferList>.alignment)
        defer { memory.deallocate() }
        guard AudioObjectGetPropertyData(id, &a, 0, nil, &size, memory) == noErr else { return nil }
        let list = UnsafeMutableAudioBufferListPointer(memory.assumingMemoryBound(to: AudioBufferList.self))
        let channels = list.reduce(0) { $0 + Int($1.mNumberChannels) }
        guard channels > 0 else { return nil }
        a = address(kAudioDevicePropertyNominalSampleRate)
        var rate: Float64 = 0
        size = UInt32(MemoryLayout<Float64>.size)
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
