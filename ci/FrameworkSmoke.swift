// SPDX-License-Identifier: MIT
// Headless: memory buffers + real DSP only. No devices, TCC, network or UI.
import AVFoundation
import NoiseProbe

@main struct FrameworkSmoke {
    static func main() {
        guard let format = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 48000, channels: 1, interleaved: false),
              let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: 1001),
              let samples = buffer.floatChannelData?[0],
              let session = np_create(48000, 1, 1001) else {
            fatalError("AVFoundation buffer or SpeexDSP session creation failed")
        }
        defer { np_destroy(session) }
        buffer.frameLength = 1001
        for i in 0..<1001 { samples[i] = i == 17 ? 0.25 : 0 }
        precondition(np_push(session, samples, 1001) == NP_LIMIT)
        precondition(np_pump(session) == NP_OK)
        np_close(session)
        precondition(np_finish(session) == NP_OK)
        precondition(np_count(session) == 1001 && np_raw(session)?[17] == 0.25)
        guard let clean = np_clean(session) else { fatalError("Missing processed samples") }
        precondition((0..<1001).allSatisfy { clean[$0].isFinite })
        precondition((0..<1001).contains { clean[$0] != 0 })
        precondition(NP_DELAY == 960)
        print("{\"swift_c_import\":true,\"avfoundation_memory_buffer\":true,\"dsp_tested\":true,\"microphone_tested\":false}")
    }
}
