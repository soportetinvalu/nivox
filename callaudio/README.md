# Nivox · audio de llamadas (micrófono y altavoz virtuales)

Estado: **experimental, no es una entrega final**. El núcleo portable se prueba en Linux con sanitizers y contraprueba de denoiser mudo. La evidencia Mac requiere una ejecución del workflow sobre el SHA exacto y su reporte E2E: la existencia del código o un job Linux verde no demuestra audio CoreAudio funcional.

## Arquitectura mínima

```
 Zoom/Meet/Teams                       Nivox (proceso de usuario, sin daemon)
 ──────────────                        ─────────────────────────────────────
 entrada: "Nivox Microphone" ◄─ring─ "Nivox Mic Feed" (oculto) ◄─ AUHAL out ◄─ nv_bridge ◄─ AUHAL in ◄─ micrófono físico
 salida:  "Nivox Speaker"   ──ring─► "Nivox Speaker Return" (oculto) ─► AUHAL in ─► nv_bridge ─► AUHAL out ─► altavoz/auricular físico
                 └──────── NivoxAudio.driver (AudioServerPlugIn dentro de coreaudiod) ────────┘
```

- **Driver HAL** (`driver/NivoxAudioDriver.cpp`, libASPL 3.1.2 MIT). Plug-in de usuario (no kext ni DriverKit) con cuatro dispositivos a 48 kHz Float32: dos visibles y dos ocultos. Los cuatro comparten **un solo reloj** (`nv_clock`: mismo ancla, periodo 16384 y clock domain `NVXO`). Así, una muestra escrita en el instante *t* por el dispositivo oculto se lee en ese mismo *t* desde el visible, sin deriva ni latencia añadida por el driver. El driver no procesa audio, solo mueve tramas. Los dispositivos ocultos no aparecen en las apps de llamadas ni pueden ser predeterminados, y la app los abre por UID.
- **Anillo de bucle** (`core/nv_driver_core.c`, `nv_ring`): está indexado por tiempo de muestra y usa un protocolo tipo seqlock con atómicos relajados. No usa locks ni asignaciones. Los huecos se rellenan con ceros para no repetir audio antiguo. Las tramas aún no escritas, demasiado viejas o sobrescritas durante la lectura se devuelven como silencio.
- **Puente de tiempo real** (`core/nv_bridge.c`): bloque de captura → mono → SRC fijo a 48 kHz → SpeexDSP denoise (`src/speex_noise.c`, preset existente de −15 dB) → FIFO SPSC → **SRC adaptativo** (resampler SpeexDSP) → bloque de reproducción. Un controlador PI sobre el nivel de la FIFO compensa la deriva entre los relojes del dispositivo físico y del virtual, hasta ±2000 ppm. Las marcas de tiempo de host eliminan la componente en diente de sierra entre bloques.
- **Interfaz Nivox.app** (`App/CallAudio.swift`, `control/`, `mac/nv_call_mac.c`): integra `nv_path` en el mismo proceso mediante una cola de control. Selección explícita de dispositivos físicos, arranque de ambas rutas, confirmación de callbacks, parada/rollback y errores útiles. Sin instalación automática ni cambio de dispositivos predeterminados. La pestaña A/B se conserva separada. **Swift/CoreAudio aún sin compilar en Mac**; alcance y aceptación: [`docs/CALL_AUDIO_UI.md`](../docs/CALL_AUDIO_UI.md).
- **Motor macOS** (`mac/`): `nv_hal.c` (AUHAL y propiedades HAL), `nv_path.c` (una ruta entrada→puente→salida) y `nivox_callaudio.c`, una CLI sin interfaz que monta ambas rutas, rehace la ruta si cambia o desaparece un dispositivo y escribe estadísticas JSON en stderr. Nunca elige un dispositivo Nivox como extremo físico, para evitar bucles.
- **E2E** (`mac/nv_e2e.c`): comprueba la topología, el bucle crudo de ambos pares (nivel, frecuencia, glitches y latencia por host time), el motor completo en seco y la reducción de ruido. Usa solo los dispositivos virtuales, así que no necesita micrófono ni altavoces.

## Alternativas evaluadas (licencia / viabilidad)

| Opción | Licencia / coste | Veredicto |
|---|---|---|
| **AudioServerPlugIn propio + libASPL** | MIT (incluye partes de ejemplo de Apple con licencia permisiva, en `LICENSE.apple2012/2020`) | **Elegida**: sin coste, redistribuible y con dispositivos propios y ocultos |
| Ejemplo NullAudio de Apple | Licencia de código de ejemplo de Apple (permisiva) | Viable, pero con ~4000 líneas de boilerplate en C; libASPL ya lo encapsula |
| BlackHole | GPL-3.0; licencia comercial de pago; prohíbe usar su marca en builds modificados | Solo viable distribuyendo el driver como GPL y renombrado, o con licencia de pago. Descartada |
| Background Music / eqMac | GPL | Descartada por el mismo motivo |
| AudioDriverKit (dext) | Requiere entitlement DriverKit concedido por Apple y cuenta de pago | No viable sin coste y sobredimensionado para dispositivos virtuales |
| Core Audio Process Taps (macOS 14.2+) | API de sistema; TCC «grabación de audio del sistema» | **Alternativa futura solo para el lado altavoz**: captura y silencia la app de llamadas sin un altavoz virtual. El micrófono sigue necesitando driver |
| SDK comerciales (Krisp, etc.) | De pago | Excluidos |
| Motor ML (RNNoise BSD-3, DeepFilterNet MIT/Apache-2.0) | Libre | Mejora futura de calidad: se integraría tras la interfaz `cn_engine`. SpeexDSP es sustracción espectral clásica y **no** alcanza la calidad de Krisp/Neep |

## Firma, instalación y permisos (requisitos reales)

- **Apple Silicon** exige firma en todo el código. Una firma ad hoc (`codesign -s -`, la que produce este build) basta para ejecutarlo en la Mac donde se instala. Que coreaudiod cargue un plug-in ad hoc está **pendiente de comprobar en CI**.
- **Distribuir a otras Macs sin avisos de Gatekeeper** requiere el Apple Developer Program (99 USD/año). Con él se obtienen Developer ID Application (driver, app y CLI), Developer ID Installer (pkg), hardened runtime y notarización con `notarytool` + `stapler`. **Es un coste extra, así que queda bloqueado por la condición «sin costos extra».** Esta candidata no genera PKG. La firma ad hoc del ZIP no garantiza aceptación por Gatekeeper ni carga del driver. No se recomienda desactivar Gatekeeper ni quitar cuarentena.
- **Instalar el driver** requiere ser administrador: se copia a `/Library/Audio/Plug-Ins/HAL` y se solicita reiniciar coreaudiod, lo que puede interrumpir **todo el audio** de la Mac; no se garantiza una duración. No probar durante una llamada. Se usa el ZIP completo y `sudo bash install-driver.sh`; ahora exige también el ejecutable firmado `nv-driver-state` del mismo paquete (no mezclar scripts nuevos con binarios antiguos). Prepara y verifica el candidato antes de mover el driver anterior a `/Library/Audio/.NivoxAudio-history/transaction.*/previous.driver` (solo root), sin borrarlo. Rechaza cuarentena sin cambiarla. Solo anuncia `Completed` si `killall coreaudiod` termina en 0 y una sonda de solo lectura confirma los cuatro UID vivos (instalar/restaurar), o los cuatro ausentes (desinstalar), con hasta 40 consultas separadas por un segundo. Un error de consulta nunca cuenta como ausencia. **Esto no prueba audio sano, hardware/defaults ni la versión efectivamente cargada.** Ante fallo, conserva el candidato retirado como `failed.driver`, copia el respaldo anterior de vuelta sin consumirlo y comprueba por separado reinicio/presencia de la recuperación. Si eso falla, informa `RECOVERY UNVERIFIED` y devuelve error; no da por recuperado el audio. La reversa explícita es `sudo bash restore-driver.sh <ruta del previous.driver indicada en el log>`; conserva también el candidato desplazado. `sudo bash uninstall-driver.sh` retira a un respaldo recuperable, no borra. Mantiene candado, guardas de plataforma/root/rutas, firma y cuarentena; el PATH de herramientas está fijado al sistema. PKG directo y purga automática siguen desactivados. Detalle y límites: [`docs/INSTALLER_TRANSACTION_FIX.md`](../docs/INSTALLER_TRANSACTION_FIX.md).
- **TCC**: el proceso Nivox necesita permiso de **Micrófono** tanto para el micrófono físico como para leer «Nivox Speaker Return», que también es un dispositivo de entrada. Una app empaquetada necesita `NSMicrophoneUsageDescription` y el entitlement `audio-input`. Si se ejecuta la CLI, el permiso se atribuye a la app de terminal. Las apps de llamadas piden su propio permiso para leer «Nivox Microphone».

## Pruebas

Regresión Swift/C del ciclo de vida (Linux o Darwin, con `swiftc` real):
`python3 tests/test_call_lifecycle_swift.py --out build/lifecycle-swift`.
Acepta `--swiftc <ruta>` si el compilador no está en `PATH`. Compila el método de
producción extraído contra el módulo C real, ejecuta la matriz de eventos y exige
que la firma antigua y la ausencia de conversión fallen por el diagnóstico de
tipos esperado. No compila SwiftUI/AppKit ni acredita una compilación Mac.

Linux (`python3 callaudio/tools/build_callaudio.py --target linux --out build/<nuevo>`):

- `nv_clock`: monotonía, coherencia entre host y muestra, 24 h.
- `nv_ring`: ventanas válidas, huecos sin audio viejo, escrituras antiguas, NaN/Inf e invalidación. Además, un escritor y dos lectores concurrentes deben devolver 0 tramas rotas.
- `nv_bridge`: simulación de 600 s por caso con deriva (±300, −150, +500, +80, 1500 y +40 ppm con bloques iguales) y tasas 48/44,1/16 kHz. Se exige 0 underruns/overflows, 0 glitches, residual de sinusoide < −60 dB, ppm efectivo igual al esperado y **0 asignaciones de memoria en push/pull** (`--wrap=malloc`). También comprueba la reducción de ruido blanco (> 6 dB), la latencia de impulso y dos hilos reales.
- Las mismas pruebas en modo rápido con ASan+UBSan y con TSan (`setarch -R` solo para ese proceso).

macOS: `.github/workflows/build-callaudio-arm64.yml` compila en un runner Apple Silicon `macos-15`, verifica firma ad hoc y entitlement `audio-input`, y prueba lecturas concurrentes del reloj ya inicializado. Después instala el driver **solo en la VM efímera GitHub-hosted**, prueba rechazo de candidato inválido, sustitución, respaldo/restauración y desinstalación protegida. Ejecuta `nv-e2e`, conservación de señal modulada con denoise activo, rechazo explícito de silencio y recuperación de la CLI tras reiniciar coreaudiod. Retiene ZIP y logs incluso si falla; un artefacto retenido no significa E2E verde. No genera PKG.

Si el workflow nuevo aún no está registrado en la rama predeterminada, se invoca sin modificarla mediante el workflow existente, con el input optativo añadido solo en la rama de trabajo:

```sh
gh workflow run build-speex-arm64.yml --ref fix/callaudio-safe-ci -f callaudio=true
```

Las pruebas del instalador (`python3 callaudio/tests/test_installer.py --evidence build/installer-sandbox.json`) usan sandbox y comandos de sistema sustituidos: prueban su transacción, fallos de reinicio/carga y reversa A → B → A → B con hashes distintos, **no** carga HAL ni firma Apple real. `python3 callaudio/tests/test_driver_state.py` compila la sonda productiva contra una API sintética para probar sus decisiones; no usa el SDK Apple. Las pruebas del monitor verifican cambios de ID, tasa, canales, buffer y formato físico; el hardware físico y los cambios reales de formato quedan fuera del CI virtual.

Regresión del verificador de recuperación: `python3 callaudio/tests/test_cli_recovery.py`.
Usa stderr original del run `41694069376`, job `126120695677`, artefacto
`13262290198`: el motor se reabrió y sus callbacks avanzaron, pero el parser
anterior falló con `JSONDecodeError` ante la advertencia VAD de SpeexDSP.
`tools/verify_cli_recovery.py` conserva esa advertencia conocida en el reporte;
rechaza otras líneas no JSON y exige arranque previo, reinicio, reapertura y
dos muestras posteriores con ambos contadores crecientes, priming y cero
underruns/overflows. Las contrapruebas eliminan esos requisitos o congelan los
contadores y deben fallar. Esto verifica el arnés sobre evidencia Apple histórica;
no sustituye una ejecución Mac nueva ni demuestra contenido de audio tras reiniciar.

## Bloqueos y pendientes

1. **Solo cuenta evidencia Apple real**: el host de desarrollo es Linux, sin SDK. Consultar la ejecución manual de la rama y su SHA exacto; sus fallos no se sustituyen por comprobaciones de sintaxis ni stubs.
2. En el runner, TCC/micrófono para capturar dispositivos virtuales es probable, pero no está garantizado (el runner preautoriza al agente de provisión). Si `nv-e2e` captura silencio, lo indicará.
3. Ni micrófono, ni altavoces, ni Zoom/Meet/Teams reales, ni deriva física se pueden probar en CI: requieren la Mac del usuario.
4. Producto: el motor ya está conectado a la ventana de Nivox.app con selector/encendido/estado/errores, **pendiente de compilación y aceptación en Mac**. La barra de menús también está implementada, aún sin ejecución Apple. Faltan inicio de sesión vía `SMAppService`, instalador con UX, AEC (SpeexDSP `mdf` usando la ruta de altavoz como referencia) y motor ML opcional. No se afirma que estas funciones existan.
5. Distribución firmada/notarizada: requiere una cuenta de pago (ver arriba).
