// SPDX-License-Identifier: MIT
import SwiftUI
import AppKit
import AVFoundation
import NoiseProbe

private enum ProbeBuild {
    static let canCapture = true
    static let subtitle = "SpeexDSP 1.2.1 · supresión −15 dB · sin pesos · experimental"
    static let windowTitle = "Nivox · prototipo experimental"
    static let instructions = "Graba hasta 15 segundos y compara original/limpio. Solo 48 kHz; canal 1. No crea un micrófono virtual ni modifica dispositivos."
}

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

@MainActor final class AudioProbe: ObservableObject {
    @Published var status = "Prueba experimental local. Nada se graba al abrir."
    @Published var recording = false
    @Published var requesting = false
    @Published var seconds = 0.0
    @Published var hasAudio = false
    @Published var headphones = false
    private var engine: AVAudioEngine?
    private var session: CaptureSession?
    private let worker = DispatchQueue(label: "local.nivox.dsp", qos: .userInitiated)
    private var pump: DispatchSourceTimer?
    private var ticker: Timer?
    private var routeObserver: NSObjectProtocol?
    private var sleepObserver: NSObjectProtocol?
    private var startTime = ProcessInfo.processInfo.systemUptime
    private var raw: [Float] = []
    private var clean: [Float] = []
    private var playback: AVAudioEngine?
    private var player: AVAudioPlayerNode?
    private var playbackObserver: NSObjectProtocol?

    init() {
        sleepObserver = NSWorkspace.shared.notificationCenter.addObserver(
            forName: NSWorkspace.willSleepNotification, object: nil, queue: .main
        ) { [weak self] _ in
            Task { @MainActor in
                self?.stop(discard: true, reason: "Suspensión: captura descartada.")
                self?.stopPlayback()
            }
        }
    }
    func start() {
        guard !recording && !requesting else { return }
        stopPlayback()
        requesting = true
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .authorized:
            requesting = false; beginCapture()
        case .notDetermined:
            AVCaptureDevice.requestAccess(for: .audio) { [weak self] allowed in
                Task { @MainActor in
                    guard let self = self, self.requesting else { return }
                    self.requesting = false
                    if allowed { self.beginCapture() }
                    else { self.status = "Permiso de micrófono denegado. No hay captura." }
                }
            }
        default:
            requesting = false
            status = "Micrófono denegado/restringido. Revisar Privacidad y seguridad; no se elude TCC."
        }
    }
    private func beginCapture() {
        do {
            let e = AVAudioEngine()
            let input = e.inputNode
            let f = input.outputFormat(forBus: 0)
            guard f.sampleRate == 48000, f.channelCount >= 1,
                  f.commonFormat == .pcmFormatFloat32, !f.isInterleaved else {
                throw ProbeError.message("Entrada no compatible: \(f). Esta fase exige Float32 no intercalado a 48 kHz. No cambia el dispositivo ni su tasa.")
            }
            let s = try CaptureSession()
            raw = []; clean = []; hasAudio = false; seconds = 0
            engine = e; session = s
            // Only bounded copy/validation in C here: no DSP, file I/O, UI or dispatch.
            input.installTap(onBus: 0, bufferSize: 1024, format: f) { buffer, _ in
                guard buffer.format.sampleRate == 48000,
                      buffer.format.commonFormat == .pcmFormatFloat32,
                      !buffer.format.isInterleaved,
                      let channel = buffer.floatChannelData?[0] else {
                    np_abort(s.handle); return
                }
                _ = np_push(s.handle, channel, Int(buffer.frameLength))
            }
            routeObserver = NotificationCenter.default.addObserver(
                forName: .AVAudioEngineConfigurationChange, object: e, queue: .main
            ) { [weak self] _ in
                Task { @MainActor in
                    self?.stop(discard: true, reason: "Cambio de ruta/formato: captura descartada. Iniciar otra prueba.")
                }
            }
            let timer = DispatchSource.makeTimerSource(queue: worker)
            timer.schedule(deadline: .now(), repeating: .milliseconds(5))
            timer.setEventHandler { _ = np_pump(s.handle) }
            pump = timer; timer.resume()
            e.prepare()
            try e.start()
            recording = true; startTime = ProcessInfo.processInfo.systemUptime
            status = "Capturando entrada predeterminada, canal 1, 48 kHz; máximo 15 s. Sin monitorización."
            ticker = Timer.scheduledTimer(withTimeInterval: 0.1, repeats: true) { [weak self] _ in
                Task { @MainActor in self?.tick() }
            }
        } catch {
            stop(discard: true)
            status = error.localizedDescription
        }
    }
    private func tick() {
        guard let s = session, recording else { return }
        seconds = Double(np_received(s.handle)) / 48000
        if np_error(s.handle) != 0 {
            stop(discard: true, reason: "Captura inválida (cola llena, muestras fuera de rango o cambio de formato). No se ofrece audio incompleto.")
        } else if np_received(s.handle) >= 720000 { stop() }
        else if ProcessInfo.processInfo.systemUptime - startTime > 18 {
            stop(discard: true, reason: "Tiempo de espera/cortes de captura. Grabación descartada.")
        }
    }
    func stop(discard: Bool = false, reason: String? = nil) {
        requesting = false // an outstanding TCC reply must not restart after window close/sleep
        ticker?.invalidate(); ticker = nil
        if let observer = routeObserver { NotificationCenter.default.removeObserver(observer) }
        routeObserver = nil
        if let s = session { np_close(s.handle) }
        engine?.stop()
        if engine != nil { engine?.inputNode.removeTap(onBus: 0) }
        pump?.cancel(); pump = nil
        var result: Int32 = -1
        if let s = session {
            worker.sync { result = discard ? -1 : np_finish(s.handle) }
            if result == 0, let a = np_raw(s.handle), let b = np_clean(s.handle) {
                let n = np_count(s.handle)
                raw = Array(UnsafeBufferPointer(start: a, count: n))
                clean = Array(UnsafeBufferPointer(start: b, count: n))
                hasAudio = n > 0; seconds = Double(n) / 48000
            } else {
                raw = []; clean = []; hasAudio = false
            }
        }
        engine = nil; session = nil; recording = false
        if let reason = reason { status = reason }
        else if result == 0 {
            status = "A/B disponible en RAM: \(String(format: "%.2f", seconds)) s. Procesado: SpeexDSP −15 dB, alineado 960 muestras; no garantiza mejora perceptual."
        } else if !discard { status = "No se obtuvo una grabación válida." }
    }
    func stopPlayback() {
        if let observer = playbackObserver { NotificationCenter.default.removeObserver(observer) }
        playbackObserver = nil
        player?.stop(); playback?.stop(); player = nil; playback = nil
    }
    func play(cleaned: Bool) { playSamples(cleaned ? clean : raw) }
    func tone() {
        let samples = (0..<48000).map { Float(sin(2 * Double.pi * 440 * Double($0) / 48000)) * 0.05 }
        playSamples(samples)
    }
    private func buffer(_ samples: [Float]) -> AVAudioPCMBuffer? {
        guard !samples.isEmpty,
              let format = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 48000, channels: 1, interleaved: false),
              let b = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(samples.count)),
              let p = b.floatChannelData?[0] else { return nil }
        b.frameLength = AVAudioFrameCount(samples.count)
        samples.withUnsafeBufferPointer { source in
            if let start = source.baseAddress { p.update(from: start, count: samples.count) }
        }
        return b
    }
    private func playSamples(_ samples: [Float]) {
        guard !recording, !requesting, headphones, let b = buffer(samples) else { return }
        stopPlayback()
        do {
            let e = AVAudioEngine(), p = AVAudioPlayerNode()
            e.attach(p); e.connect(p, to: e.mainMixerNode, format: b.format)
            p.volume = 0.35
            playback = e; player = p
            try e.start()
            playbackObserver = NotificationCenter.default.addObserver(
                forName: .AVAudioEngineConfigurationChange, object: e, queue: .main
            ) { [weak self] _ in
                Task { @MainActor in
                    self?.stopPlayback(); self?.headphones = false
                    self?.status = "Cambió la salida: sonido detenido. Reconfirmar auriculares antes de escuchar."
                }
            }
            p.scheduleBuffer(b); p.play()
            status = "Reproducción local a volumen inicial reducido. No se envía a ninguna llamada."
        } catch { stopPlayback(); status = error.localizedDescription }
    }
    func export(cleaned: Bool) {
        guard !recording, !requesting, let b = buffer(cleaned ? clean : raw) else { return }
        let panel = NSSavePanel()
        panel.nameFieldStringValue = cleaned ? "prueba-limpia.wav" : "prueba-original.wav"
        panel.title = "Exportación explícita de audio privado; no se sube a la red"
        guard panel.runModal() == .OK, let url = panel.url else { return }
        guard !FileManager.default.fileExists(atPath: url.path) else {
            status = "No se sobreescriben archivos. Elige un nombre nuevo."; return
        }
        do {
            let file = try AVAudioFile(forWriting: url, settings: b.format.settings, commonFormat: .pcmFormatFloat32, interleaved: false)
            try file.write(from: b)
            status = "WAV guardado por solicitud: \(url.path)"
        } catch { status = "Exportación fallida; comprobar posible archivo parcial: \(error.localizedDescription)" }
    }
    func clear() {
        guard !recording, !requesting else { return }
        stopPlayback(); raw = []; clean = []; hasAudio = false; seconds = 0
        status = "Buffers de la prueba liberados; no implica borrado seguro de memoria ni de WAV exportados."
    }
}

@main @MainActor struct NivoxApp: App {
    @StateObject private var probe = AudioProbe()
    var body: some Scene {
        WindowGroup(ProbeBuild.windowTitle) {
            VStack(alignment: .leading, spacing: 16) {
                Text("Nivox").font(.largeTitle.bold())
                Text(ProbeBuild.subtitle).foregroundColor(.secondary)
                Text(ProbeBuild.instructions)
                HStack {
                    Button("Grabar prueba") { probe.start() }.disabled(!ProbeBuild.canCapture || probe.recording || probe.requesting)
                    Button("Finalizar captura") { probe.stop() }.disabled(!probe.recording)
                    Text(String(format: "%.1f / 15 s", probe.seconds)).monospacedDigit()
                }
                Toggle("Uso auriculares y he bajado el volumen de salida", isOn: $probe.headphones)
                    .onChange(of: probe.headphones) { enabled in if !enabled { probe.stopPlayback() } }
                HStack {
                    Button("Tono suave 1 s") { probe.tone() }.disabled(probe.recording || probe.requesting || !probe.headphones)
                    Button("Escuchar original") { probe.play(cleaned: false) }.disabled(!probe.hasAudio || probe.recording || probe.requesting || !probe.headphones)
                    Button("Escuchar limpio") { probe.play(cleaned: true) }.disabled(!probe.hasAudio || probe.recording || probe.requesting || !probe.headphones)
                    Button("Parar sonido") { probe.stopPlayback() }
                }
                HStack {
                    Button("Exportar original…") { probe.export(cleaned: false) }
                    Button("Exportar limpio…") { probe.export(cleaned: true) }
                    Button("Descartar prueba") { probe.clear() }
                }.disabled(!probe.hasAudio || probe.recording || probe.requesting)
                Divider()
                Text(probe.status).textSelection(.enabled).frame(minHeight: 70, alignment: .topLeading)
                Text("Sin red, pesos, grabación automática, AGC ni cancelación de eco. Audio temporal en RAM; WAV solo al exportar. Calidad limitada con ráfagas. No usar aún en llamadas.")
                    .font(.footnote).foregroundColor(.secondary)
            }.padding(24).frame(minWidth: 780, minHeight: 390)
                .onDisappear {
                    probe.stop(discard: true, reason: "Ventana cerrada: captura detenida.")
                    probe.stopPlayback()
                }
        }
    }
}
