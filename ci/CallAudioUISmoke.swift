// SPDX-License-Identifier: MIT
// Real production model/view + HAL. No permission injection, audio or fake devices.
import AppKit
import SwiftUI
import AVFoundation
import CoreAudio
import Vision

// Compare every three-word span, not just labels or a prefix. Overlapping
// viewport captures let a paragraph cross the scroll boundary without skipping
// its middle/end. OCR errors fail closed and preserve pixels + recognized text.
func normalizedWords(_ text: String) -> [String] {
    text.folding(options: [.diacriticInsensitive, .caseInsensitive], locale: Locale(identifier: "es_ES"))
        .components(separatedBy: CharacterSet.alphanumerics.inverted).filter { !$0.isEmpty }
}
func missingTextSpans(_ expected: [String], in recognized: [String]) -> [String] {
    let pages = recognized.map { " " + normalizedWords($0).joined(separator: " ") + " " }
    return expected.flatMap { text -> [String] in
        let words = normalizedWords(text)
        guard words.count >= 3 else { return [text] }
        return (0...(words.count - 3)).compactMap { i in
            let span = words[i..<(i + 3)].joined(separator: " ")
            return pages.contains(where: { $0.contains(" " + span + " ") }) ? nil : span
        }
    }
}
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
    // cacheDisplay captures the transparent hosting view, NOT the NSWindow
    // backing. Vision treats that alpha as black, hiding ordinary black text.
    // Composite only the captured pixels onto the real window background.
    @MainActor static func opaqueSnapshot(_ source: CGImage, background: NSColor) -> CGImage {
        guard let color = background.usingColorSpace(.sRGB),
              let space = CGColorSpace(name: CGColorSpace.sRGB),
              let context = CGContext(data: nil, width: source.width, height: source.height,
                  bitsPerComponent: 8, bytesPerRow: source.width * 4, space: space,
                  bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue) else {
            fatalError("cannot allocate opaque viewport context")
        }
        context.setFillColor(red: color.redComponent, green: color.greenComponent,
                             blue: color.blueComponent, alpha: 1)
        let rect = CGRect(x: 0, y: 0, width: CGFloat(source.width), height: CGFloat(source.height))
        context.fill(rect)
        context.draw(source, in: rect)
        guard let image = context.makeImage() else { fatalError("cannot encode opaque viewport") }
        return image
    }
    @MainActor static func snapshotSelftest() {
        // Transparent black, 50% black, opaque orange: white AND dark backings.
        // This exercises the exact compositor used for OCR, not a Python model.
        let bytes = Data([0, 0, 0, 0, 0, 0, 0, 128, 255, 141, 40, 255])
        let provider = CGDataProvider(data: bytes as CFData)!
        let source = CGImage(width: 3, height: 1, bitsPerComponent: 8, bitsPerPixel: 32,
            bytesPerRow: 12, space: CGColorSpace(name: CGColorSpace.sRGB)!,
            bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.last.rawValue),
            provider: provider, decode: nil, shouldInterpolate: false, intent: .defaultIntent)!
        for (background, level) in [(NSColor.white, 255), (NSColor.black, 0)] {
            let image = opaqueSnapshot(source, background: background)
            require(image.alphaInfo == .noneSkipLast, "viewport still carries transparency")
            let pixels = [UInt8](image.dataProvider!.data! as Data)
            require(Array(pixels[0..<3]) == [UInt8](repeating: UInt8(level), count: 3),
                    "transparent text background was not composited")
            require(abs(Int(pixels[4]) - (level == 255 ? 127 : 0)) <= 1,
                    "antialiased pixel was not alpha blended")
            require(Array(pixels[8..<11]) == [255, 141, 40], "opaque warning pixel changed")
            let sampled = ocrSnapshot(image)
            require(sampled.width == 54 && sampled.height == 50 && sampled.alphaInfo == .noneSkipLast,
                    "OCR sampling geometry/opacity changed")
            let samples = [UInt8](sampled.dataProvider!.data! as Data)
            for x in 0..<3 {
                for dx in 0..<2 {
                    for dy in 0..<2 {
                        let offset = ((24 + dy) * sampled.width + 24 + x * 2 + dx) * 4
                        require(Array(samples[offset..<(offset + 3)]) == Array(pixels[(x * 4)..<(x * 4 + 3)]),
                                "OCR sampling changed a source pixel")
                    }
                }
            }
            require(Array(samples[0..<3]) == [255, 255, 255], "OCR border must be blank")
        }
    }
    // Normalize raster sampling, not words: Vision at 1x dropped the visible E
    // in "En" and c in "cambia" in the genuine incident PNG (run 41706083678).
    // Nearest-neighbour 2x preserves every source pixel; the border supplies
    // image context only. The strict span oracle and real truncation mutation
    // remain unchanged. Keep the exact OCR input as evidence.
    @MainActor static func ocrSnapshot(_ source: CGImage) -> CGImage {
        let scale = 2, border = 24
        let width = source.width * scale + border * 2
        let height = source.height * scale + border * 2
        guard let space = CGColorSpace(name: CGColorSpace.sRGB),
              let context = CGContext(data: nil, width: width, height: height,
                  bitsPerComponent: 8, bytesPerRow: width * 4, space: space,
                  bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue) else {
            fatalError("cannot allocate OCR sampling context")
        }
        context.setFillColor(CGColor(red: 1, green: 1, blue: 1, alpha: 1))
        context.fill(CGRect(x: 0, y: 0, width: width, height: height))
        context.interpolationQuality = .none
        context.draw(source, in: CGRect(x: border, y: border,
                     width: source.width * scale, height: source.height * scale))
        guard let image = context.makeImage() else { fatalError("cannot encode OCR sampling context") }
        return image
    }
    @MainActor static func recognizedText(_ image: CGImage) throws -> String {
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.recognitionLanguages = ["es-ES", "en-US"]
        request.usesLanguageCorrection = false
        try VNImageRequestHandler(cgImage: image).perform([request])
        return (request.results ?? []).compactMap { $0.topCandidates(1).first?.string }.joined(separator: "\n")
    }
    // NSBitmapImageRep decodes bytes; it has no contentsOf: URL initializer.
    @MainActor static func snapshotFixture(_ fixture: URL) -> CGImage? {
        guard let data = try? Data(contentsOf: fixture),
              let bitmap = NSBitmapImageRep(data: data) else { return nil }
        return bitmap.cgImage
    }
    @MainActor static func snapshotIncidentRegression(out: URL) throws {
        // Genuine transparent capture from failed run 41701415863, not a mock.
        let fixture = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
            .appendingPathComponent("tests/fixtures/call-ui-transparent-41701415863.png")
        guard let raw = snapshotFixture(fixture) else {
            throw DiagnosticFailure(message: "missing original transparent capture fixture")
        }
        let expected = [CallAudioCopy.routing, CallAudioCopy.lifecycle,
                        "Reenvío apagado. Para seguir una llamada, selecciona dispositivos físicos en esa app."]
        let rawText = try recognizedText(raw)
        let image = opaqueSnapshot(raw, background: .white) // Fixture was captured in light appearance.
        let nativeText = try recognizedText(image)
        let sampled = ocrSnapshot(image)
        let text = try recognizedText(sampled)
        let missing = missingTextSpans(expected, in: [text])
        let rawMissing = missingTextSpans(expected, in: [rawText])
        let png = NSBitmapImageRep(cgImage: image).representation(using: .png, properties: [:])!
        do {
            try png.write(to: out.appendingPathComponent("snapshot-regression.png"))
            try NSBitmapImageRep(cgImage: sampled).representation(using: .png, properties: [:])!
                .write(to: out.appendingPathComponent("snapshot-regression-ocr.png"))
            let record: [String: Any] = ["recognized": text, "raw_recognized": rawText,
                "native_recognized": nativeText,
                "native_missing_spans": missingTextSpans(expected, in: [nativeText]),
                "ocr_scale": 2, "ocr_border_pixels": 24,
                "missing_spans": missing, "raw_missing_spans": rawMissing]
            try JSONSerialization.data(withJSONObject: record, options: [.prettyPrinted, .sortedKeys])
                .write(to: out.appendingPathComponent("snapshot-regression.json"))
        } catch { throw DiagnosticFailure(message: "snapshot regression evidence write failed: \(error)") }
        if rawMissing.isEmpty {
            throw DiagnosticFailure(message: "original transparent capture did not reproduce OCR failure")
        }
        if !missing.isEmpty {
            throw DiagnosticFailure(message: "opaque original capture still loses significant text: \(missing)")
        }
    }
    @MainActor static func scrollViews(_ view: NSView) -> [NSScrollView] {
        (view as? NSScrollView).map { [$0] } ?? view.subviews.flatMap { scrollViews($0) }
    }
    struct DiagnosticFailure: Error { let message: String }
    static func rect(_ value: NSRect) -> [String: CGFloat] {
        ["x": value.origin.x, "y": value.origin.y, "width": value.width, "height": value.height]
    }
    static func size(_ value: NSSize) -> [String: CGFloat] {
        ["width": value.width, "height": value.height]
    }
    @MainActor static func viewMeasures(_ view: NSView) -> [String: Any] {
        var record: [String: Any] = ["class": String(describing: type(of: view)),
            "frame": rect(view.frame), "bounds": rect(view.bounds), "visible_rect": rect(view.visibleRect),
            "fitting_size": size(view.fittingSize), "intrinsic_content_size": size(view.intrinsicContentSize),
            "hidden": view.isHidden, "translates_autoresizing_mask": view.translatesAutoresizingMaskIntoConstraints,
            "constraints": view.constraints.map { $0.description },
            "subviews": view.subviews.map { viewMeasures($0) }]
        if let scroll = view as? NSScrollView {
            record["clip_frame"] = rect(scroll.contentView.frame)
            record["clip_bounds"] = rect(scroll.contentView.bounds)
            record["document_frame"] = scroll.documentView.map { rect($0.frame) }
            record["document_bounds"] = scroll.documentView.map { rect($0.bounds) }
        }
        return record
    }
    @MainActor static func layoutMeasures(_ window: NSWindow, host: NSView, stage: String) -> [String: Any] {
        ["stage": stage, "window_frame": rect(window.frame),
         "content_rect_for_frame": rect(window.contentRect(forFrameRect: window.frame)),
         "content_layout_rect": rect(window.contentLayoutRect),
         "window_min_size": size(window.minSize), "window_max_size": size(window.maxSize),
         "content_min_size": size(window.contentMinSize), "content_max_size": size(window.contentMaxSize),
         "backing_scale_factor": window.backingScaleFactor,
         "screen_frame": window.screen.map { rect($0.frame) } ?? [:],
         "screen_visible_frame": window.screen.map { rect($0.visibleFrame) } ?? [:],
         "appearance": window.effectiveAppearance.name.rawValue, "host": viewMeasures(host)]
    }
    @MainActor static func render(_ model: CallAudioModel, out: URL, width: Int, height: Int,
                                  tabbed: Bool) async -> [String: Any] {
        await settled(model)
        // The tabbed variant hosts the same root used by NivoxApp, including TabView.
        let root = tabbed ? AnyView(NivoxContent(probe: AudioProbe(), calls: model))
                          : AnyView(CallAudioView(audio: model))
        let host = NSHostingView(rootView: root)
        host.sizingOptions = [] // Do not silently grow the requested viewport to fit.
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: CGFloat(width), height: CGFloat(height)),
                              styleMask: [.titled, .closable, .resizable], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.title = "Nivox production call view · CI"
        let name = "\(tabbed ? "tabbed" : "call")-\(width)x\(height)"
        var record: [String: Any] = ["viewport_width": width, "viewport_height": height,
            "tabbed_production_root": tabbed, "ok": false]
        var failures: [String] = []
        var stages = [layoutMeasures(window, host: host, stage: "created")]
        var captures: [[String: Any]] = []
        var pages: [String] = []
        func checkpoint() {
            record["layout_stages"] = stages
            record["captures"] = captures
            record["failures"] = failures
            do {
                try JSONSerialization.data(withJSONObject: record, options: [.prettyPrinted, .sortedKeys])
                    .write(to: out.appendingPathComponent("\(name)-text.json"), options: .atomic)
            } catch {
                let message = "OCR evidence write failed: \(error)"
                failures.append(message)
                fputs("FAIL: \(message)\n", stderr)
            }
        }
        defer { window.close() }
        window.contentView = host
        stages.append(layoutMeasures(window, host: host, stage: "content_attached"))
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        try? await Task.sleep(nanoseconds: 700_000_000)
        host.layoutSubtreeIfNeeded()
        stages.append(layoutMeasures(window, host: host, stage: "settled"))
        let viewportOK = abs(host.bounds.width - CGFloat(width)) < 1 && abs(host.bounds.height - CGFloat(height)) < 1
        record["viewport_preserved"] = viewportOK
        if !viewportOK {
            failures.append("viewport expanded instead of preserving requested dimensions: requested=\(width)x\(height), host.bounds=\(host.bounds), host.frame=\(host.frame), window.frame=\(window.frame), contentLayoutRect=\(window.contentLayoutRect)")
        }
        // A wrong viewport remains a hard failure. Its pixels are diagnostic only.
        checkpoint()
        // Keep initial pixels even when the scroll-container guard fails.
        do {
            guard let bitmap = host.bitmapImageRepForCachingDisplay(in: host.bounds) else {
                throw DiagnosticFailure(message: "initial diagnostic bitmap allocation failed")
            }
            host.cacheDisplay(in: host.bounds, to: bitmap)
            guard let raw = bitmap.cgImage,
                  let rawPNG = bitmap.representation(using: .png, properties: [:]) else {
                throw DiagnosticFailure(message: "initial diagnostic PNG encoding failed")
            }
            let filename = "\(name)-initial-raw.png"
            try rawPNG.write(to: out.appendingPathComponent(filename))
            record["initial_raw_png"] = filename
            var image: CGImage?
            window.effectiveAppearance.performAsCurrentDrawingAppearance {
                image = opaqueSnapshot(raw, background: window.backgroundColor)
            }
            guard let opaque = image,
                  let png = NSBitmapImageRep(cgImage: opaque).representation(using: .png, properties: [:]) else {
                throw DiagnosticFailure(message: "initial opaque diagnostic PNG encoding failed")
            }
            let opaqueFilename = "\(name)-initial.png"
            try png.write(to: out.appendingPathComponent(opaqueFilename))
            record["initial_png"] = opaqueFilename
            record["initial_pixel_size"] = ["width": bitmap.pixelsWide, "height": bitmap.pixelsHigh]
        } catch { failures.append("initial diagnostic capture failed: \(error)") }
        checkpoint()
        let scrolls = scrollViews(host).filter { !$0.isHidden && $0.documentView != nil }
        record["visible_scroll_count"] = scrolls.count
        guard scrolls.count == 1 else {
            failures.append("expected one visible production scroll view, got \(scrolls.count)")
            checkpoint()
            return record
        }
        let scroll = scrolls[0]
        let clip = scroll.contentView
        let maxY = max(0, scroll.documentView!.bounds.height - clip.bounds.height)
        let step = max(1, clip.bounds.height * 0.45)
        let offsets = Array(stride(from: CGFloat(0), to: maxY, by: step)) + [maxY]
        record["scroll_offsets"] = offsets
        if offsets.count > 12 { failures.append("unexpected unbounded document height") }
        // Bound diagnostic capture, but never count an incomplete traversal as passing.
        for (index, y) in offsets.prefix(12).enumerated() {
          do {
            clip.scroll(to: NSPoint(x: 0, y: y))
            scroll.reflectScrolledClipView(clip)
            try? await Task.sleep(nanoseconds: 200_000_000)
            host.layoutSubtreeIfNeeded()
            stages.append(layoutMeasures(window, host: host, stage: "scroll-\(index)"))
            if abs(host.bounds.width - CGFloat(width)) >= 1 || abs(host.bounds.height - CGFloat(height)) >= 1 {
                record["viewport_preserved"] = false
                failures.append("viewport expanded instead of preserving requested dimensions at capture \(index): requested=\(width)x\(height), host.bounds=\(host.bounds)")
            }
            checkpoint()
            guard let bitmap = host.bitmapImageRepForCachingDisplay(in: host.bounds) else {
                throw DiagnosticFailure(message: "production view could not allocate render bitmap")
            }
            host.cacheDisplay(in: host.bounds, to: bitmap)
            let filename = "\(name)-\(index).png"
            guard let rawPNG = bitmap.representation(using: .png, properties: [:]), let rawImage = bitmap.cgImage else {
                throw DiagnosticFailure(message: "PNG encoding failed")
            }
            var composite: CGImage?
            window.effectiveAppearance.performAsCurrentDrawingAppearance {
                composite = opaqueSnapshot(rawImage, background: window.backgroundColor)
            }
            guard let image = composite,
                  let png = NSBitmapImageRep(cgImage: image).representation(using: .png, properties: [:]) else {
                throw DiagnosticFailure(message: "opaque PNG encoding failed")
            }
            if image.alphaInfo != .noneSkipLast { failures.append("OCR viewport must be opaque") }
            let rawFilename = "\(name)-\(index)-raw.png"
            try rawPNG.write(to: out.appendingPathComponent(rawFilename))
            try png.write(to: out.appendingPathComponent(filename))
            let ocrImage = ocrSnapshot(image)
            let ocrFilename = "\(name)-\(index)-ocr.png"
            try NSBitmapImageRep(cgImage: ocrImage).representation(using: .png, properties: [:])!
                .write(to: out.appendingPathComponent(ocrFilename))
            captures.append(["png": filename, "raw_png": rawFilename, "ocr_png": ocrFilename,
                             "ocr_scale": 2, "ocr_border_pixels": 24, "opaque_window_background": true,
                             "scroll_y": y,
                             "pixel_width": bitmap.pixelsWide, "pixel_height": bitmap.pixelsHigh])
            checkpoint() // Preserve paths/pixels even when Vision throws.
            let recognized = try recognizedText(ocrImage)
            pages.append(recognized)
            captures[captures.count - 1]["recognized"] = recognized
          } catch {
            failures.append("capture \(index) failed: \(error)")
          }
          checkpoint()
        }
        var expected = [CallAudioCopy.routing, CallAudioCopy.lifecycle, CallAudioModel.routingWarning,
                        CallAudioCopy.safety, model.status]
        if !model.driver { expected.append(CallAudioCopy.missingDriver) }
        let missing = missingTextSpans(expected, in: pages)
        record["missing_spans"] = missing
        record["expected_paragraphs"] = expected
        if !missing.isEmpty {
            failures.append("significant text not visible in \(name): \(missing.joined(separator: " | "))")
        }
        record["ok"] = failures.isEmpty
        checkpoint()
        // checkpoint itself may fail: never allow an evidence-write error to pass.
        record["ok"] = failures.isEmpty
        record["failures"] = failures
        for failure in failures { fputs("FAIL: \(failure)\n", stderr) }
        return record
    }
    @MainActor static func exercise() async {
        let args = CommandLine.arguments
        require(missingTextSpans(["Uno dos tres cuatro cinco"], in: ["Uno dos tres\ncuatro cinco"]).isEmpty,
                "text oracle rejected line wrapping")
        require(!missingTextSpans(["Uno dos tres cuatro cinco"], in: ["Uno dos tres…"]).isEmpty,
                "text oracle accepted a truncated suffix")
        require(!missingTextSpans(["Uno dos tres cuatro cinco"], in: ["Uno dos cuatro cinco"]).isEmpty,
                "text oracle accepted missing middle words")
        require(missingTextSpans(["Uno dos tres cuatro cinco"], in: ["Uno dos tres cuatro", "dos tres cuatro cinco"]).isEmpty,
                "text oracle rejected overlapping scroll captures")
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
        snapshotSelftest()
        var failures: [String] = []
        var regressionPassed = false
        do {
            try snapshotIncidentRegression(out: out)
            regressionPassed = true
        } catch { failures.append("snapshot incident regression failed: \(error)") }
        var textChecks: [[String: Any]] = []
        // Full Cartesian matrix: both roots, both widths, both heights.
        for width in [780, 1000] {
            for height in [560, 900] {
                for tabbed in [false, true] {
                    textChecks.append(await render(model, out: out, width: width, height: height, tabbed: tabbed))
                    let progress: [String: Any] = ["ok": false, "phase": args[2],
                        "matrix_expected": 8, "matrix_completed": textChecks.count,
                        "text_visibility_checks": textChecks, "failures": failures]
                    do {
                        try JSONSerialization.data(withJSONObject: progress, options: [.prettyPrinted, .sortedKeys])
                            .write(to: out.appendingPathComponent("runtime.json"), options: .atomic)
                    } catch { failures.append("matrix progress write failed: \(error)") }
                }
            }
        }
        model.handleLifecycle(NV_CALL_UI_SLEEP)
        await settled(model)
        require(!model.enabled && model.status.hasPrefix("Suspensión:"), "real model sleep did not complete stop")
        var quitAcknowledged = false
        model.prepareToQuit { quitAcknowledged = true }
        await settled(model)
        require(quitAcknowledged && !model.enabled && !model.canStart, "quit acknowledged before serialized stop")
        require(AVCaptureDevice.authorizationStatus(for: .audio) == permissionBefore, "TCC state changed")
        require(defaultsBefore == [defaultDevice(kAudioHardwarePropertyDefaultInputDevice), defaultDevice(kAudioHardwarePropertyDefaultOutputDevice)], "system defaults changed")
        let textPassed = textChecks.count == 8 && textChecks.allSatisfy { ($0["ok"] as? Bool) == true }
        let ok = textPassed && regressionPassed && failures.isEmpty
        let report: [String: Any] = ["ok": ok, "phase": args[2], "production_view_rendered": true,
            "real_hal_inventory_matched": true, "physical_device_count": physical.count,
            "invalid_backend_uids_rejected": true, "empty_selection_start_refused": true,
            "sleep_stop_completed": true, "quit_stop_completed": true,
            "tcc_unchanged": true, "system_defaults_unchanged": true,
            "tcc_authorization_raw": permissionBefore.rawValue,
            "matrix_expected": 8, "matrix_completed": textChecks.count, "failures": failures,
            "text_visibility_checks": textChecks, "significant_text_visible": textPassed,
            "text_oracle_selftests_passed": true,
            "opaque_capture_selftests_passed": true, "original_capture_regression_passed": regressionPassed,
            "microphone_capture_tested": false,
            "user_clicks_tested": false, "tcc_prompt_tested": false]
        do {
            let data = try JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys])
            try data.write(to: out.appendingPathComponent("runtime.json"))
            print(String(decoding: data, as: UTF8.self))
        } catch { require(false, "report write failed: \(error)") }
        exit(ok ? 0 : 1)
    }
    @MainActor static func main() {
        _ = NSApplication.shared
        NSApp.setActivationPolicy(.regular)
        Task { @MainActor in await exercise() }
        NSApp.run()
    }
}
