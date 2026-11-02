# Nivox · audio de llamadas (micrófono y altavoz virtuales)

Estado: **implementación inicial en curso, no es una entrega final**. El núcleo portable está probado en Linux. El driver, el motor CoreAudio y la prueba E2E están escritos, pero **no se han compilado ni ejecutado en macOS** (ver «Bloqueos»).

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
- **Distribuir a otras Macs sin avisos de Gatekeeper** requiere el Apple Developer Program (99 USD/año). Con él se obtienen Developer ID Application (driver, app y CLI), Developer ID Installer (pkg), hardened runtime y notarización con `notarytool` + `stapler`. **Es un coste extra, así que queda bloqueado por la condición «sin costos extra».** Sin esa cuenta, el pkg sin firmar descargado exige «Abrir igualmente» en Ajustes › Privacidad y seguridad. No se recomienda desactivar Gatekeeper.
- **Instalar el driver** requiere ser administrador: se copia a `/Library/Audio/Plug-Ins/HAL` y se reinicia coreaudiod, lo que corta el audio 1–2 s. No hace falta reiniciar la Mac, aprobar extensiones ni instalar un daemon. Se usa el ZIP y `sudo bash install-driver.sh`. La instalación prepara y verifica el candidato antes de mover el driver anterior a `/Library/Audio/.NivoxAudio-history/transaction.*/previous.driver` (solo root), sin borrarlo. No elimina atributos de cuarentena: los rechaza sin cambiarlos. La reversa es `sudo bash restore-driver.sh <ruta del previous.driver indicada en el log>`; conserva también el candidato desplazado. `sudo bash uninstall-driver.sh` retira a un respaldo recuperable, no borra. Las operaciones se serializan con un candado y restauran el anterior ante errores detectados. El PKG directo está desactivado porque sobreescribía antes de poder respaldar. Los respaldos no se purgan automáticamente.
- **TCC**: el proceso Nivox necesita permiso de **Micrófono** tanto para el micrófono físico como para leer «Nivox Speaker Return», que también es un dispositivo de entrada. Una app empaquetada necesita `NSMicrophoneUsageDescription` y el entitlement `audio-input`. Si se ejecuta la CLI, el permiso se atribuye a la app de terminal. Las apps de llamadas piden su propio permiso para leer «Nivox Microphone».

## Pruebas

Linux (`python3 callaudio/tools/build_callaudio.py --target linux --out build/<nuevo>`):

- `nv_clock`: monotonía, coherencia entre host y muestra, 24 h.
- `nv_ring`: ventanas válidas, huecos sin audio viejo, escrituras antiguas, NaN/Inf e invalidación. Además, un escritor y dos lectores concurrentes deben devolver 0 tramas rotas.
- `nv_bridge`: simulación de 600 s por caso con deriva (±300, −150, +500, +80, 1500 y +40 ppm con bloques iguales) y tasas 48/44,1/16 kHz. Se exige 0 underruns/overflows, 0 glitches, residual de sinusoide < −60 dB, ppm efectivo igual al esperado y **0 asignaciones de memoria en push/pull** (`--wrap=malloc`). También comprueba la reducción de ruido blanco (> 6 dB), la latencia de impulso y dos hilos reales.
- Las mismas pruebas en modo rápido con ASan+UBSan y con TSan (`setarch -R` solo para ese proceso).

macOS: `.github/workflows/build-callaudio-arm64.yml` (manual) compila y firma ad hoc. Después instala el driver **solo en la VM efímera del runner**, ejecuta `nv-e2e` y una prueba de humo de la CLI, y sube el ZIP, el pkg sin firmar y la evidencia. **No se ha ejecutado** porque requiere push.

## Bloqueos y pendientes

1. **Sin compilación ni ejecución Apple**: el host es Linux, sin SDK. El driver, el motor CoreAudio y el E2E no se han compilado nunca, así que pueden aparecer errores de compilación. Para resolverlo hace falta autorizar un push de esta rama y lanzar el workflow, o una Mac.
2. En el runner, TCC/micrófono para capturar dispositivos virtuales es probable, pero no está garantizado (el runner preautoriza al agente de provisión). Si `nv-e2e` captura silencio, lo indicará.
3. Ni micrófono, ni altavoces, ni Zoom/Meet/Teams reales, ni deriva física se pueden probar en CI: requieren la Mac del usuario.
4. Producto: falta integrar el motor en Nivox.app (barra de menús, selector de dispositivos, inicio de sesión vía `SMAppService`, sin daemon), un instalador con UX, AEC (SpeexDSP `mdf` usando la ruta de altavoz como referencia) y un motor ML opcional.
5. Distribución firmada/notarizada: requiere una cuenta de pago (ver arriba).
