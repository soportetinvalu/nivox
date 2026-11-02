// SPDX-License-Identifier: MIT
import SwiftUI
import AppKit
import AVFoundation
import NivoxCallAudio

struct CallDevice: Identifiable, Sendable {
    let id: String
    let name: String
    let input: Bool
    let output: Bool
}
private struct CallSnapshot: Sendable {
    var devices: [CallDevice] = []
    var state = Int(NV_CALL_STOPPED)
    var driver = false
    var detail = ""
    var gaps: UInt64 = 0
}

// Ownership and all HAL control operations stay on this queue, never the UI or
// RT callbacks. Audio samples never cross the Swift/UI boundary.
private final class CallEngine: @unchecked Sendable {
    enum Operation: Sendable { case refresh, start(String, String), stop, poll }
    private let queue = DispatchQueue(label: "local.nivox.call-control", qos: .userInitiated)
    private let handle = nv_call_create_mac()
    deinit {
        let h = handle
        queue.async { nv_call_destroy(h) }
    }
    func perform(_ operation: Operation, completion: @escaping @Sendable (CallSnapshot) -> Void) {
        queue.async { [self] in
            guard let h = handle else {
                completion(CallSnapshot(state: Int(NV_CALL_ERROR), detail: "No se pudo crear el motor local de llamadas."))
                return
            }
            switch operation {
            case .refresh: _ = nv_call_refresh(h)
            case let .start(mic, output):
                _ = nv_call_start(h, mic, output, ProcessInfo.processInfo.systemUptime)
            case .stop: nv_call_stop(h)
            case .poll: nv_call_poll(h, ProcessInfo.processInfo.systemUptime)
            }
            var result = CallSnapshot(state: Int(nv_call_state(h)), driver: nv_call_driver_ready(h) != 0,
                                      detail: String(cString: nv_call_detail(h)), gaps: nv_call_underruns(h))
            for i in 0..<nv_call_device_count(h) {
                guard let d = nv_call_device_at(h, i), d.pointee.physical != 0,
                      d.pointee.alive != 0, d.pointee.rate >= 8000 else { continue }
                result.devices.append(CallDevice(id: String(cString: nv_call_device_uid(d)),
                                                  name: String(cString: nv_call_device_name(d)),
                                                  input: d.pointee.inputs > 0, output: d.pointee.outputs > 0))
            }
            completion(result)
        }
    }
}

@MainActor final class CallAudioModel: ObservableObject {
    @Published var microphone = ""
    @Published var output = ""
    @Published private(set) var devices: [CallDevice] = []
    @Published private(set) var enabled = false
    @Published private(set) var busy = false
    @Published private(set) var driver = false
    @Published private(set) var status = "Procesamiento apagado. No se captura audio al abrir."
    @Published private(set) var detail = ""
    @Published private(set) var gaps: UInt64 = 0
    private let engine = CallEngine()
    private var generation: UInt64 = 0
    private var polling = false
    private var requestingPermission = false
    private var timer: Timer?
    private var sleepObserver: NSObjectProtocol?
    var inputs: [CallDevice] { devices.filter(\.input) }
    var outputs: [CallDevice] { devices.filter(\.output) }

    init() {
        refresh()
        timer = Timer.scheduledTimer(withTimeInterval: 0.5, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.poll() }
        }
        sleepObserver = NSWorkspace.shared.notificationCenter.addObserver(
            forName: NSWorkspace.willSleepNotification, object: nil, queue: .main
        ) { [weak self] _ in Task { @MainActor in self?.stop(reason: "Suspensión: procesamiento detenido. Vuelve a encenderlo al regresar.") } }
    }
    deinit {
        timer?.invalidate()
        if let o = sleepObserver { NSWorkspace.shared.notificationCenter.removeObserver(o) }
    }
    private func submit(_ op: CallEngine.Operation, token: UInt64, finished: (@MainActor () -> Void)? = nil) {
        engine.perform(op) { [weak self] snapshot in
            Task { @MainActor in
                guard let self else { return }
                if token == self.generation { self.apply(snapshot); finished?() }
            }
        }
    }
    private func apply(_ snapshot: CallSnapshot) {
        devices = snapshot.devices; driver = snapshot.driver; detail = snapshot.detail; gaps = snapshot.gaps
        switch snapshot.state {
        case Int(NV_CALL_RUNNING):
            enabled = true; busy = false
            status = "Procesando micrófono y salida · actividad de ambas rutas confirmada."
        case Int(NV_CALL_STARTING):
            enabled = true; busy = true
            status = "Iniciando: esperando actividad de captura y reproducción…"
        case Int(NV_CALL_ERROR):
            enabled = false; busy = false; status = "Audio detenido por un error."
        default:
            enabled = false; busy = false; status = "Procesamiento apagado. Las rutas virtuales no transmiten audio."
        }
    }
    func refresh() {
        guard !enabled, !busy else { return }
        busy = true
        submit(.refresh, token: generation)
    }
    func start() {
        guard !enabled, !busy else { return }
        generation &+= 1
        let token = generation
        busy = true; enabled = true; detail = ""; gaps = 0
        status = "Comprobando permiso de micrófono…"
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .authorized: begin(token: token)
        case .notDetermined:
            requestingPermission = true
            AVCaptureDevice.requestAccess(for: .audio) { [weak self] allowed in
                Task { @MainActor in
                    guard let self, self.generation == token, self.enabled else { return }
                    if allowed { self.begin(token: token) } else { self.permissionDenied() }
                }
            }
        default: permissionDenied()
        }
    }
    private func begin(token: UInt64) {
        requestingPermission = false
        status = "Abriendo las dos rutas de audio…"
        submit(.start(microphone, output), token: token)
    }
    private func permissionDenied() {
        stop(reason: "Micrófono denegado o restringido. Abre Ajustes del Sistema › Privacidad y seguridad › Micrófono, permite Nivox y vuelve a encender. No se elude TCC.")
    }
    func stop(reason: String? = nil) {
        generation &+= 1 // invalidate outstanding permission and worker replies
        requestingPermission = false
        enabled = false; busy = true
        status = "Deteniendo captura y reproducción…"
        submit(.stop, token: generation) { [weak self] in
            if let reason { self?.status = reason }
        }
    }
    private func poll() {
        guard enabled, !polling, !requestingPermission else { return }
        if AVCaptureDevice.authorizationStatus(for: .audio) != .authorized { permissionDenied(); return }
        polling = true
        let token = generation
        engine.perform(.poll) { [weak self] snapshot in
            Task { @MainActor in
                guard let self else { return }
                self.polling = false
                if token == self.generation { self.apply(snapshot) }
            }
        }
    }
    func openPrivacy() {
        if let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone") {
            NSWorkspace.shared.open(url)
        }
    }
}

struct CallAudioView: View {
    @ObservedObject var audio: CallAudioModel
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Audio para llamadas").font(.title2.bold())
            Text("Procesamiento local continuo · SpeexDSP 1.2.1 · sin red ni grabación")
                .foregroundColor(.secondary)
            GroupBox("Dispositivos físicos") {
                VStack(alignment: .leading, spacing: 12) {
                    Picker("Micrófono (canal 1)", selection: $audio.microphone) {
                        Text("Selecciona un micrófono").tag("")
                        ForEach(audio.inputs) { Text($0.name).tag($0.id) }
                        if !audio.microphone.isEmpty && !audio.inputs.contains(where: { $0.id == audio.microphone }) {
                            Text("Micrófono seleccionado no disponible").tag(audio.microphone)
                        }
                    }
                    Picker("Salida (mono/estéreo)", selection: $audio.output) {
                        Text("Selecciona una salida").tag("")
                        ForEach(audio.outputs) { Text($0.name).tag($0.id) }
                        if !audio.output.isEmpty && !audio.outputs.contains(where: { $0.id == audio.output }) {
                            Text("Salida seleccionada no disponible").tag(audio.output)
                        }
                    }
                    Button("Actualizar dispositivos") { audio.refresh() }
                }.padding(8).disabled(audio.enabled || audio.busy)
            }
            Text(audio.driver ? "Driver Nivox detectado" : "Driver Nivox no disponible: instala NivoxAudio.driver con el instalador y actualiza. Requiere administrador; esta app no instala nada.")
                .foregroundColor(audio.driver ? .secondary : .orange)
            Toggle("Procesar micrófono y salida de llamadas", isOn: Binding(
                get: { audio.enabled }, set: { if $0 { audio.start() } else { audio.stop() } }
            ))
            .disabled(!audio.enabled && (audio.busy || !audio.driver || audio.microphone.isEmpty || audio.output.isEmpty))
            Text(audio.status).font(.headline).accessibilityIdentifier("call-audio-status")
            if !audio.detail.isEmpty { Text(audio.detail).foregroundColor(.orange).textSelection(.enabled) }
            if audio.gaps > 0 {
                Text("Aviso: \(audio.gaps) cortes o desbordamientos acumulados en el motor. Si persisten, apaga y revisa los dispositivos.")
                    .foregroundColor(.orange)
            }
            Button("Abrir permiso de Micrófono…") { audio.openPrivacy() }
            Divider()
            Text("En Zoom, Meet o Teams selecciona «Nivox Microphone» como entrada y «Nivox Speaker» como salida. Nivox no cambia los dispositivos predeterminados del sistema ni la configuración de esas apps.")
            Text("Al apagar, cerrar la ventana o suspender la Mac, ambas rutas se detienen. Las apps que sigan usando los dispositivos virtuales quedarán sin audio: selecciona tus dispositivos físicos para continuar sin Nivox.")
            Text("Usa auriculares y baja el volumen antes de activar. Sin cancelación de eco (AEC). La salida se mezcla a mono antes de duplicarse en estéreo. Actividad del motor no confirma que tu app de llamadas esté usando Nivox. Pendiente de validación en una Mac real.")
                .font(.footnote).foregroundColor(.secondary)
        }.padding(24).frame(minWidth: 780, minHeight: 560)
    }
}
