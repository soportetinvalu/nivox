// SPDX-License-Identifier: MIT
import SwiftUI
import AppKit
import AVFoundation
import NoiseProbe
import NivoxCallAudio

enum ProbeBuild {
    static let canCapture = true
    static let subtitle = "SpeexDSP 1.2.1 · mezcla suave 0–25 % · sin aislamiento de voz · experimental"
    static let windowTitle = "Nivox · audio local"
    static let instructions = "Elige micrófono físico y canal. Graba hasta 15 s y compara original/filtrado. Solo 48 kHz; sin monitorización ni cambios globales."
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
    @Published var microphone = ""
    @Published var channel = 0
    @Published private(set) var devices: [ProbeInputDevice] = []
    @Published var copyGainDB = 0.0
    @Published var filterMix = 0.25
    @Published var listenVolume = 0.35
    @Published private(set) var levelsText = ""
    @Published private(set) var capturedRoute = ""
    private var capturedDevice: ProbeInputDevice?
    private var confirmedOutputUID: String?
    private var preparedRaw: [Float] = []
    private var preparedClean: [Float] = []
    var selectedDevice: ProbeInputDevice? { devices.first { $0.id == microphone } }
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
    private var permissionGeneration: UInt64 = 0

    init() {
        refreshInputs()
        sleepObserver = NSWorkspace.shared.notificationCenter.addObserver(
            forName: NSWorkspace.willSleepNotification, object: nil, queue: .main
        ) { [weak self] _ in
            Task { @MainActor in
                self?.stop(discard: true, reason: "Suspensión: captura descartada.")
                self?.stopPlayback()
            }
        }
    }
    func refreshInputs() {
        guard !recording, !requesting else { return }
        devices = ProbeInput.devices()
        if selectedDevice == nil { microphone = ""; channel = 0 }
    }
    func start() {
        guard !recording && !requesting else { return }
        guard let d = selectedDevice, channel >= 0, channel < d.channels else {
            status = "Selecciona explícitamente un micrófono físico y su canal."; return
        }
        permissionGeneration &+= 1
        let token = permissionGeneration
        stopPlayback()
        requesting = true
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .authorized:
            requesting = false; beginCapture()
        case .notDetermined:
            AVCaptureDevice.requestAccess(for: .audio) { [weak self] allowed in
                Task { @MainActor in
                    guard let self = self, self.requesting, self.permissionGeneration == token else { return }
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
            guard let choice = selectedDevice, let d = ProbeInput.device(choice.deviceID),
                  d.id == choice.id, channel >= 0, channel < d.channels else {
                throw ProbeError.message("Entrada no disponible: actualiza y selecciona micrófono/canal.")
            }
            try ProbeInput.bind(input, to: d, input: true)
            let f = input.outputFormat(forBus: 0)
            let selectedChannel = channel
            guard f.sampleRate == 48000, Int(f.channelCount) > selectedChannel,
                  f.commonFormat == .pcmFormatFloat32, !f.isInterleaved else {
                throw ProbeError.message("Entrada no compatible: \(f). Esta fase exige Float32 no intercalado a 48 kHz. No cambia el dispositivo ni su tasa.")
            }
            let s = try CaptureSession()
            raw = []; clean = []; hasAudio = false; seconds = 0
            preparedRaw = []; preparedClean = []; levelsText = ""
            capturedDevice = d
            capturedRoute = "\(d.name) · UID \(d.id) · canal \(selectedChannel + 1) · bus \(Int(f.sampleRate)) Hz / \(f.channelCount) canales · HAL \(Int(d.rate)) Hz"
            engine = e; session = s
            // Only bounded copy/validation in C here: no DSP, file I/O, UI or dispatch.
            input.installTap(onBus: 0, bufferSize: 1024, format: f) { buffer, time in
                guard buffer.format.sampleRate == 48000,
                      buffer.format.commonFormat == .pcmFormatFloat32,
                      !buffer.format.isInterleaved,
                      Int(buffer.format.channelCount) > selectedChannel,
                      time.isSampleTimeValid, time.sampleRate == 48000,
                      let channel = buffer.floatChannelData?[selectedChannel] else {
                    np_abort(s.handle); return
                }
                _ = np_push_timed(s.handle, channel, Int(buffer.frameLength), time.sampleTime)
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
            status = "Capturando \(d.name), canal \(selectedChannel + 1), 48 kHz; máximo 15 s. Sin monitorización."
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
        guard let d = capturedDevice,
              let current = ProbeInput.device(d.deviceID), current.id == d.id,
              current.rate == d.rate, current.channels == d.channels else {
            stop(discard: true, reason: "Entrada desconectada o cambiada: toma descartada. No se usa otro micrófono."); return
        }
        seconds = Double(np_received(s.handle)) / 48000
        if np_error(s.handle) != 0 {
            stop(discard: true, reason: "Captura inválida (discontinuidad temporal, cola llena, muestras fuera de rango o cambio de formato). No se ofrece audio incompleto.")
        } else if np_received(s.handle) >= 720000 { stop() }
        else if ProcessInfo.processInfo.systemUptime - startTime > 18 {
            stop(discard: true, reason: "Tiempo de espera/cortes de captura. Grabación descartada.")
        }
    }
    func stop(discard: Bool = false, reason: String? = nil) {
        permissionGeneration &+= 1
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
        engine = nil; session = nil; recording = false; capturedDevice = nil
        if hasAudio { preparePair() }
        else { preparedRaw = []; preparedClean = []; levelsText = "" }
        if let reason = reason { status = reason }
        else if result == 0 {
            status = "A/B en RAM: \(String(format: "%.2f", seconds)) s. Filtro suave; no aísla personas ni garantiza inteligibilidad. Captura fuente sin ganancia conservada."
        } else if !discard { status = "No se obtuvo una grabación válida." }
    }
    func stopPlayback() {
        if let observer = playbackObserver { NotificationCenter.default.removeObserver(observer) }
        playbackObserver = nil
        player?.stop(); playback?.stop(); player = nil; playback = nil
    }
    func confirmHeadphones(_ enabled: Bool) {
        stopPlayback()
        confirmedOutputUID = enabled ? ProbeInput.physicalOutput()?.id : nil
        if enabled && confirmedOutputUID == nil {
            headphones = false
            status = "No hay salida física verificable. Selecciona auriculares antes de confirmar."
        }
    }
    func preparePair() {
        stopPlayback()
        guard hasAudio, !recording, !requesting else { return }
        var a = [Float](repeating: 0, count: raw.count), b = a
        var levels = np_ab_levels()
        let result = raw.withUnsafeBufferPointer { r in clean.withUnsafeBufferPointer { w in
            a.withUnsafeMutableBufferPointer { ap in b.withUnsafeMutableBufferPointer { bp in
                np_prepare_ab(r.baseAddress, w.baseAddress, raw.count, copyGainDB, filterMix,
                              ap.baseAddress, bp.baseAddress, &levels)
            }}
        }}
        guard result == 0 else {
            preparedRaw = []; preparedClean = []
            levelsText = "Parámetros de nivel no válidos; no se reproduce/exporta la copia."; return
        }
        preparedRaw = a; preparedClean = b
        levelsText = String(format: "Captura RMS %.1f dBFS · filtro Δ %.1f dB · ganancia común aplicada %+.1f dB · pico copia %.1f dBFS.",
                            levels.raw_rms_dbfs, levels.filtered_rms_dbfs - levels.raw_rms_dbfs,
                            levels.applied_gain_db, levels.peak_dbfs)
        if levels.raw_rms_dbfs < -40 { levelsText += " Entrada muy baja: comprueba canal, colocación y nivel del receptor; ganar volumen también sube ruido." }
        if levels.full_scale_samples > 0 { levelsText += " Entrada a escala completa: posible clipping previo, no reparable con ganancia digital." }
        if levels.gain_limited != 0 { levelsText += " Ganancia acotada por margen de pico; no se recortan muestras." }
        if levels.bypassed != 0 { levelsText += " Filtro omitido por silencio o correlación negativa." }
    }
    func play(cleaned: Bool) { playSamples(cleaned ? preparedClean : preparedRaw) }
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
            guard let output = ProbeInput.physicalOutput() else {
                throw ProbeError.message("Selecciona una salida física en macOS. Se rechazan salidas virtuales para evitar bucles.")
            }
            guard confirmedOutputUID == output.id else {
                headphones = false
                throw ProbeError.message("La salida cambió desde la confirmación. Revisa auriculares y volumen bajo antes de escuchar.")
            }
            try ProbeInput.bind(e.outputNode, to: output, input: false)
            e.attach(p); e.connect(p, to: e.mainMixerNode, format: b.format)
            guard listenVolume.isFinite, (0...1).contains(listenVolume) else { return }
            p.volume = Float(listenVolume)
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
            status = "Reproducción local por \(output.name). Volumen de app 0–100 %; no cambia volumen del sistema ni envía a llamadas."
        } catch { stopPlayback(); status = error.localizedDescription }
    }
    func export(cleaned: Bool, source: Bool = false) {
        guard !recording, !requesting, let b = buffer(source ? raw : (cleaned ? preparedClean : preparedRaw)) else { return }
        let panel = NSSavePanel()
        panel.nameFieldStringValue = source ? "captura-sin-ganancia.wav" : (cleaned ? "prueba-filtrada.wav" : "prueba-original.wav")
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
        preparedRaw = []; preparedClean = []; capturedRoute = ""; levelsText = ""
        status = "Buffers de la prueba liberados; no implica borrado seguro de memoria ni de WAV exportados."
    }
}

@MainActor final class NivoxAppDelegate: NSObject, NSApplicationDelegate {
    // App-owned, not window-owned: closing/recreating a view cannot destroy HAL.
    let probe = AudioProbe()
    let calls = CallAudioModel()
    private var quitting = false
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if quitting { return .terminateLater }
        guard confirmRoutingLoss(title: "¿Salir de Nivox?", action: "Detener audio y salir") else { return .terminateCancel }
        quitting = true
        probe.stop(discard: true, reason: "Saliendo: prueba descartada.")
        probe.stopPlayback()
        calls.prepareToQuit { sender.reply(toApplicationShouldTerminate: true) }
        return .terminateLater
    }
}

@main @MainActor struct NivoxApp: App {
    @NSApplicationDelegateAdaptor(NivoxAppDelegate.self) private var delegate
    var body: some Scene {
        // Window (singular) can quit on close. WindowGroup + the delegate's
        // explicit last-window policy keep this app alive for ongoing calls.
        WindowGroup(ProbeBuild.windowTitle, id: "nivox-main") {
            NivoxContent(probe: delegate.probe, calls: delegate.calls)
        }
        .commands {
            CommandGroup(replacing: .newItem) { }
            CommandGroup(replacing: .appTermination) {
                Button("Salir de Nivox…") { NSApp.terminate(nil) }.keyboardShortcut("q")
            }
        }
        MenuBarExtra {
            CallMenuView(audio: delegate.calls, probe: delegate.probe)
        } label: {
            CallMenuLabel(audio: delegate.calls)
        }
    }
}

struct NivoxContent: View {
    @ObservedObject var probe: AudioProbe
    @ObservedObject var calls: CallAudioModel
    @State private var tab = 0
    var body: some View {
        TabView(selection: $tab) {
            CallAudioView(audio: calls).tabItem { Text("Llamadas") }.tag(0)
            probeView.disabled(calls.busy || calls.enabled).tabItem { Text("Prueba A/B") }.tag(1)
        }
        .frame(minWidth: 780, minHeight: 560)
        .onChange(of: tab) { _ in
            calls.handleLifecycle(NV_CALL_UI_TAB_CHANGED)
            probe.stop(discard: true, reason: "Cambio de pestaña: prueba detenida.")
            probe.stopPlayback()
        }
        .onDisappear { closeSurface() }
        .onReceive(NotificationCenter.default.publisher(for: NSWindow.willCloseNotification)) { notification in
            // Do not rely only on SwiftUI disappearance to invalidate a TCC reply.
            guard let window = notification.object as? NSWindow,
                  window.title == ProbeBuild.windowTitle else { return }
            closeSurface()
        }
    }
    private func closeSurface() {
        calls.handleLifecycle(NV_CALL_UI_WINDOW_CLOSED)
        probe.stop(discard: true, reason: "Ventana cerrada: captura detenida.")
        probe.stopPlayback()
    }
    private var probeView: some View {
            ScrollView { VStack(alignment: .leading, spacing: 12) {
                Text("Nivox").font(.largeTitle.bold())
                Text(ProbeBuild.subtitle).foregroundColor(.secondary)
                Text(ProbeBuild.instructions)
                HStack {
                    Picker("Micrófono A/B", selection: $probe.microphone) {
                        Text("Elegir entrada física…").tag("")
                        ForEach(probe.devices) { d in Text("\(d.name) · \(d.channels) ch · \(Int(d.rate)) Hz").tag(d.id) }
                    }.onChange(of: probe.microphone) { _ in probe.channel = 0 }
                    Picker("Canal", selection: $probe.channel) {
                        ForEach(0..<max(1, probe.selectedDevice?.channels ?? 1), id: \.self) { i in Text("\(i + 1)").tag(i) }
                    }.frame(width: 110)
                    Button("Actualizar") { probe.refreshInputs() }
                }.disabled(probe.recording || probe.requesting)
                HStack {
                    Button("Grabar prueba") { probe.start() }.disabled(!ProbeBuild.canCapture || probe.recording || probe.requesting || probe.selectedDevice == nil)
                    Button("Finalizar captura") { probe.stop() }.disabled(!probe.recording)
                    Text(String(format: "%.1f / 15 s", probe.seconds)).monospacedDigit()
                }
                Text(probe.capturedRoute).font(.caption).textSelection(.enabled)
                VStack(alignment: .leading) {
                    HStack {
                        Text(String(format: "Ganancia de copias A/B: +%.0f dB", probe.copyGainDB))
                        Slider(value: $probe.copyGainDB, in: 0...24, step: 1)
                            .onChange(of: probe.copyGainDB) { _ in probe.preparePair() }
                    }
                    HStack {
                        Text(String(format: "Filtro suave: %.0f %%", probe.filterMix * 100))
                        Slider(value: $probe.filterMix, in: 0...0.25, step: 0.05)
                            .onChange(of: probe.filterMix) { _ in probe.preparePair() }
                    }
                    Text("0 % = bypass. 25 % conserva 75 % del original; no garantiza conservar cada palabra. Ganancia compartida con margen −1 dBFS, sin AGC. La captura fuente no cambia.").font(.caption)
                }.disabled(probe.recording || probe.requesting)
                Text(probe.levelsText).font(.caption).textSelection(.enabled)
                Toggle("Uso auriculares y he bajado el volumen de salida", isOn: $probe.headphones)
                    .onChange(of: probe.headphones) { enabled in probe.confirmHeadphones(enabled) }
                HStack {
                    Text(String(format: "Volumen de app: %.0f %%", probe.listenVolume * 100))
                    Slider(value: $probe.listenVolume, in: 0...1, step: 0.05)
                        .onChange(of: probe.listenVolume) { _ in probe.stopPlayback() }
                    Text("Cambiar detiene el sonido; vuelve a pulsar Escuchar.").font(.caption)
                }
                HStack {
                    Button("Tono suave 1 s") { probe.tone() }.disabled(probe.recording || probe.requesting || !probe.headphones)
                    Button("Escuchar original") { probe.play(cleaned: false) }.disabled(!probe.hasAudio || probe.recording || probe.requesting || !probe.headphones)
                    Button("Escuchar filtrado") { probe.play(cleaned: true) }.disabled(!probe.hasAudio || probe.recording || probe.requesting || !probe.headphones)
                    Button("Parar sonido") { probe.stopPlayback() }
                }
                HStack {
                    Button("Exportar original…") { probe.export(cleaned: false) }
                    Button("Exportar filtrado…") { probe.export(cleaned: true) }
                    Button("Exportar captura fuente…") { probe.export(cleaned: false, source: true) }
                    Button("Descartar prueba") { probe.clear() }
                }.disabled(!probe.hasAudio || probe.recording || probe.requesting)
                Divider()
                Text(probe.status).textSelection(.enabled).frame(minHeight: 70, alignment: .topLeading)
                Text("Sin red, pesos, grabación automática, AGC ni cancelación de eco. Audio temporal en RAM; WAV solo al exportar. Calidad limitada con ráfagas. No usar aún en llamadas.")
                    .font(.footnote).foregroundColor(.secondary)
            }.padding(24).frame(minWidth: 780, minHeight: 390) }
    }
}
