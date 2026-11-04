# A/B: entrada explícita y niveles acotados — candidata sin validar en Mac

**No declarar resuelto el problema físico hasta probar esta candidata en el Mac real.**
No hay aislamiento de hablante ni corrección demostrada del Hollyland. No se tocó el Mac.

## Cambios de la ruta

- Selector A/B independiente y explícito por UID, con canal manual (1, 2, …), sin elegir por energía. No sigue el selector de Llamadas ni se cambia la entrada predeterminada del sistema. Se enumeran entradas vivas y físicas; virtuales, agregadas, ocultas y UID `nivox.*` quedan fuera.
- **Captura directa por HAL (corrige el rechazo inmediato).** La toma ya no usa `AVAudioEngine` para entrada: abre un IOProc (`AudioDeviceCreateIOProcIDWithBlock`) sobre el `AudioDeviceID` elegido y copia sólo el canal elegido (`np_push_strided_timed`) con el `mSampleTime` del dispositivo. Sólo lee propiedades, crea el IOProc y hace Start/Stop; no cambia entrada predeterminada, tasa, ganancia ni buffer. `AudioUnitSetProperty(CurrentDevice)` queda sólo para la reproducción local (salida).
- Se exige Float32 a 48 kHz según el formato virtual de cada stream del HAL: tasas o formatos distintos se rechazan, **no se cambia la tasa del accesorio ni se finge remuestreo**. UID, nombre, canal, tasa HAL, estructura de buffers y formato de stream se muestran por toma, más una línea de tiempo de diagnóstico copiable.
- Captura sin monitorización. Reproducción detenida antes de capturar. Huecos/repeticiones de sample time invalidan la toma, además de las guardas históricas de cola/formato/rango. Desconexión/cambio real descarta, no conmuta silenciosamente a otro micrófono. Una notificación HAL por sí sola no es veredicto: se relee el estado (UID, vida, tasa, canales, estructura de buffers, formato de stream) y sólo una diferencia real descarta; el sondeo cada 0,1 s aplica la misma comparación. Sin ningún bloque en 3 s tras `AudioDeviceStart` también se descarta.
- La captura fuente permanece intacta. **Ganancia de copias A/B** explícita 0…24 dB, inicial 0 dB, no ganancia hardware; ambas copias usan el mismo factor constante. Se acota por el mayor pico de las dos al margen −1 dBFS. No se normaliza ruido ni silencio, no AGC ni compresor. Si se pide +24 y no cabe, se aplica menos y se avisa.
- Filtrado: mezcla `0.75 * raw + 0.25 * Speex` por defecto; deslizador 0…25 %, 0=bypass. Speex sigue −15 dB interno; no se modifica Llamadas. Correlación global negativa o fuente digitalmente nula omite el filtro en toda la toma, sin conmutación por bloques.
- Con correlación no negativa, la norma RMS global filtrada no cae por debajo de 0,75 de la fuente antes del factor común. **Esto no garantiza cada palabra, ventana ni inteligibilidad** y se mantiene ese límite en la interfaz. La mezcla conserva también ruido; no equivale a supresión −15 dB ni a aislamiento de voces.
- Volumen del nodo reproductor explícito **0…1**, inicial 0,35, nunca >1. Cambiar nivel detiene el sonido: debe pulsarse Escuchar otra vez. Confirmación manual de auriculares y volumen bajo, más rechazo de salida virtual/agregada. No puede certificar presión sonora segura ni detectar físicamente auriculares. No hay salida durante captura ni audio a llamadas.
- Exportar original/filtrado guarda las copias con ganancia común (no el volumen del reproductor); **Exportar captura fuente** conserva el tap sin ganancia. No se sobrescriben archivos ni se sube audio. Las medidas de pico/RMS son de señal, no de voz/SNR.

## Validación reproducible

```sh
python3 -B build.py --target linux --out build/ab-safe-linux
python3 -B build.py --target linux --sanitize --out build/ab-safe-sanitize
python3 -B tests/test_delivery.py --out build/ab-safe-delivery
# Opcional: corpus real YA disponible localmente, sin red. Nunca usa grabaciones privadas en CI.
python3 -B tests/test_ab_levels.py --library build/ab-safe-linux/libNoiseProbe.so \
  --out build/ab-safe-speech/report.json --speech-dir /ruta/local/al/corpus
```

`test_ab_levels.py`: identidad, ganancia compartida, límites, silencio, antífase,
entrada muy débil, bypass, rechazo de NaN/Inf/rango/parámetros, timestamps, motor C real,
mutantes compilados sin cota de pico y salida muda. La comprobación Swift es **sólo contrato
de fuente**, no compilación. En CI sin corpus, el caso real se omite explícitamente.
El workflow manual integrado compila en macos-26 arm64 y conserva reportes de niveles;
no instala driver ni captura micrófono. No lanzarlo si el ejecutor o el presupuesto lo bloquea.

## Aceptación pendiente en Mac M4 / macOS 26.5.2

1. Tras compilación Apple autorizada, abrir candidata sin instalar driver, con auriculares y salida baja.
2. En A/B seleccionar explícitamente **Wireless microphone** del Hollyland y el canal que realmente tenga voz. No cambiar Ajustes globales. Anotar ruta mostrada y mantener ganancia en 0 dB inicialmente.
3. Grabar 2 s de silencio + 3 s de voz. Ver RMS/pico. Si bajo, comparar canal correcto/colocación; sólo después aumentar gradualmente ganancia de copias, que aumenta también ruido.
4. Escuchar original y filtrado con el mismo volumen; comparar filtro 0 % y 25 %. Exportar localmente captura fuente y ambas copias si hace falta diagnosticar. No enviar a servicios externos. Comparar con QuickTime con la misma entrada física si sigue baja la fuente.
5. Contrapruebas: canal sin voz no debe confundirse con el otro; desconectar entrada debe descartar sin fallback; no escuchar mientras graba; cambiar volumen debe detener; ganancia máxima no debe superar pico −1 dBFS en los WAV copia. Confirmar que entrada/salida/tasa/volumen globales quedaron idénticos.

No atribuir el zumbido histórico de ~60 Hz al Hollyland: la identidad de entrada de esa toma no está demostrada. Las nuevas pruebas descritas por el usuario no estaban disponibles como nuevos archivos en esta carpeta; la evaluación local usa habla pública previamente conservada y la evidencia histórica separada.

## Causa del rechazo inmediato «Cambio de ruta/formato» (vídeo del 4-nov-2026)

**Observado en el vídeo** (5 pulsaciones): Wireless microphone (Hollyland, 2 ch, 48 kHz, UID `AppleUSBAudioEngine:Shenzhen Hollyland Technology Co.,Ltd:Wireless microphone:93Y2252G4EV:3`). Tras cada «Grabar prueba» aparece unos 0,3–0,5 s «Capturando…» (con «Finalizar captura» activo y el contador en 0,0 / 15 s en los fotogramas muestreados) y luego «Cambio de ruta/formato: captura descartada». Es decir, `AVAudioEngine.start()` tuvo éxito y se disparó `AVAudioEngineConfigurationChange`, que el código trataba como cambio real. La línea de ruta mostraba «bus 48000 Hz / 1 canales · HAL 48000 Hz» para un dispositivo de 2 canales.

**Reproducido en macOS (CI, run 41917823742 / 41918404476, runner arm64 con «Apple Virtual Sound Device», 2 ch, 48 kHz):** la ruta histórica (mismo `AudioUnitSetProperty(CurrentDevice)` sobre `inputNode`, mismo `installTap`, mismo observador) emite `AVAudioEngineConfigurationChange` **0,28 s después de `start()`**, tras 20 buffers del tap, con el estado HAL **idéntico** al inicial (`state_difference_at_first_change: none`: mismo UID, tasa, canales, estructura de buffers y formato de stream) y con el bus ya en 2 canales. Por tanto: (1) el motor notifica una reconfiguración interna sin cambio real de dispositivo, (2) la guarda histórica la trataba como cambio de ruta y descartaba la toma, y (3) la discrepancia 1 vs 2 canales del Mac del usuario no es necesaria para provocarlo. La causa interna exacta de esa notificación (reconfiguración del I/O compartido tras fijar el dispositivo, nodo de salida implícito sin salida compatible, u otra) **no está determinada**, y el Hollyland físico no se ha podido probar: que el mecanismo coincida es muy probable por síntoma y temporización, no está demostrado.

Solución: IOProc directo (sin motor, sin formato de bus, sin nodo de salida) + guardas que comparan estado real. En el mismo runner la ruta nueva capturó 155 bloques / 79 360 fotogramas en ~1,6 s con `np_error = 0` (tiempos de muestra contiguos del dispositivo) y sin diferencia de estado. **Esto no es prueba con el Hollyland.** Pruebas que se ejecutan:

- Linux/macOS: `tests/test_ab_levels.py::test_strided_channel_extraction_and_failures` (canal elegido en intercalado, bloques de 10 000 fotogramas, huecos, repeticiones, NaN, rango, argumentos inválidos; los mutantes sin `offset` y sin continuidad fallan).
- Mac CI (`ci/ProbeCaptureSmoke.swift`, compilado con el `ProbeInput.swift` real; 39 casos): `HALCapture.deliver` sobre `AudioBufferList` sintéticas (intercalado, plano, mixto, bloque grande, bloque vacío) y contrapruebas (hueco/repetición/fraccionario/sin bandera de tiempo/canal fuera/NaN/fuera de rango, siempre pegajosas); comparador `ProbeGuard`: notificación sin cambio de estado → **no** descarta (falso positivo), desconexión, UID, tasa, canales, estructura de buffers, formato e intercalado → descartan; 44,1 kHz y no Float32 rechazados; captura real de 1,5 s por cada entrada del runner con vaciado como el de la app (falla si hay error, <1 s de muestras o diferencia de estado) y reproducción de la ruta histórica como diagnóstico (no falla).
- **No probado:** desconexión/cambio real sobre hardware (el runner no permite desenchufar); la guarda se prueba con el comparador sobre estados sintéticos, no con un evento HAL real. Tampoco la interfaz pulsando «Grabar», ni el Hollyland.

### Pasos mínimos para el usuario (Mac M4)

1. Descargar el artefacto de la compilación Mac de esta rama y abrir la app (sin instalar driver).
2. Pestaña «Prueba A/B», elegir «Wireless microphone», canal 1, «Grabar prueba» y hablar 3 s.
3. Éxito: contador > 0 s, «Finalizar captura» y RMS/pico tras finalizar. Fallo: pulsar «Copiar diagnóstico de esta toma» y enviar ese texto (UID, formato de stream, línea de tiempo, bloques/muestras, motivo) junto con una captura de pantalla.
4. Contraprueba de seguridad: durante otra toma, desenchufar el receptor; debe aparecer «Entrada desconectada o cambiada…» sin pasar a otro micrófono.
