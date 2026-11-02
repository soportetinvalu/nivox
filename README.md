# Nivox · SpeexDSP · prototipo local Apple Silicon

Entrega integrada de fuentes: branding Nivox e icono original aportado por el usuario, con **SpeexDSP 1.2.1 real**, estático y sin pesos. No incluye Neep, RNNoise, modelos, audio privado ni credenciales. No instala drivers, servicios ni dispositivos. La inferencia y el build local no necesitan descargar dependencias; GitHub Actions sí necesita red para checkout, herramientas y artifacts.

**Compilación experimental verificada en macOS arm64:** [ejecución de GitHub Actions](https://github.com/soportetinvalu/nivox/actions/runs/41475856569), sobre el código `dccde6fa9ab44ad3bb7f6b841c45bb79fbc72875`. La interfaz y el micrófono aún no se han probado en una Mac de usuario. Los resultados anteriores de Linux figuran en `docs/RESULTADOS.md`.

## Alcance real

- Prueba A/B local de hasta 15 s; no es un micrófono virtual, no se selecciona como entrada en Zoom/Meet/Teams y no envía audio a llamadas.
- Captura solo al pulsar Grabar y previa autorización TCC; no se solicita micrófono al construir ni abrir la ventana. Esta conducta está implementada pero pendiente de prueba Mac.
- Entrada: canal 1, Float32 no intercalado, exactamente 48 kHz; otros formatos se rechazan, sin cambiar tasa/dispositivo. WAV solo al exportar explícitamente; audio temporal en RAM.
- SpeexDSP: frame 480, denoise activado, supresión −15 dB, fuerza 1; AGC/dereverb/VAD desactivados. Conversión interna PCM16. `cn_last_vad` devuelve 0 por compatibilidad, no probabilidad de voz.
- Retardo nominal motor + adaptador: 960 muestras / 20 ms, sin incluir dispositivo, cola, worker ni host. Al terminar se vacía y recorta esa demora. El preset no promete 15 dB de mejora del habla ni calidad perceptual/paridad comercial.
- Callback de captura: copia/validación acotada a cola SPSC; DSP en worker separado. Overflow/formato inválido aborta la grabación. Sin monitorización de micrófono ni cancelación de eco; reproducción posterior requiere confirmación manual de auriculares, no detectados automáticamente.

## Construir y probar en Linux

Requiere Python >=3.12, compilador C, `make`, `ar`; herramientas externas solo para desarrollo: `actionlint`. No requiere `pip`, modelos, instalación global ni acceso a dispositivos. Todos los destinos deben ser nuevos:

```sh
python3 build.py --target linux --out build/linux-release
python3 build.py --target linux --sanitize --out build/linux-sanitize
python3 tests/test_delivery.py --out build/delivery-tests
# Si actionlint está instalado:
actionlint -color .github/workflows/build-speex-arm64.yml
python3 ci/export_sources.py --out build/public-sources
```

El primer build ejecuta DSP real, arnés C, pruebas de motor y ruta completa/CLI con señales sintéticas. ASan+UBSan instrumentan adaptador, cola y upstream; ejecutan el arnés C, no ctypes. Los tests de identidad/guardas/parser son independientes y no simulan una compilación Mac.

CLI opcional, solo sobre un WAV propio PCM16 mono/48 kHz, con nombre de salida nuevo:

```sh
python3 python/christian_noise.py input.wav output-nuevo.wav \
  --library build/linux-release/libNoiseProbe.so --block 127
```

## Apple Silicon / Actions preparado

El contenido de esta carpeta se usa como raíz del repositorio, no el workspace padre. Workflow manual: `.github/workflows/build-speex-arm64.yml`; no publica Releases, no usa secrets, permisos `contents: read`, acciones fijadas por SHA y checkout sin persistir autenticación. Primero pruebas Linux; luego `macos-26` con guardas explícitas Darwin/arm64 y SDK >=13. La tabla oficial de runners GitHub consultada identifica `macos-26` como arm64 (M1); no prueba compatibilidad M4. La guarda real evita depender solo del label.

En un Mac Apple Silicon autorizado con herramientas Apple y Python >=3.12:

```sh
python3 build.py --target macapp --out build/mac-arm64
```

Receta: construir SpeexDSP estático, probar DSP, compilar smoke Swift/AVFoundation solo en RAM y app SwiftUI, validar icono obligatorio, copiar licencias, auditar Mach-O arm64/dependencias, firmar **ad hoc**, crear `Nivox-arm64-experimental.zip`, extraer y cotejar hashes/firma/arquitectura/icono. La ejecución de Actions enlazada arriba produjo `Nivox.app` en Mac arm64, pero no abrió la interfaz ni probó micrófonos. Firma ad hoc no es Developer ID ni notarización, y no garantiza aceptación de Gatekeeper. No desactivar sus protecciones.

## Licencias y faltantes

- Código propio MIT: `LICENSE`. Upstream/archivo fijado: `LICENSES.md`.
- Arte aportado por el usuario: Christian confirmó el derecho a usarlo y compartirlo como parte de Nivox. **MIT no licencia el icono**; no se ha verificado independientemente la titularidad (`App/Assets/BRANDING.md`).
- No hay evaluación nueva de voz humana, escucha validada, STOI/PESQ/WER ni garantía de tiempo real duro. La atenuación de ruido sintético no demuestra inteligibilidad. SpeexDSP puede producir artefactos o atenuar voz; ráfagas siguen siendo un límite conocido, no una calidad nuevamente medida aquí.
- Llamadas, permisos reales, firma y aceptación: `docs/ACEPTACION_MAC.md`. Integración/procedencia: `docs/INTEGRACION.md`.

El tar exportado es exclusivamente de fuentes con manifiesto SHA-256; no es un binario Apple ni una publicación. No subir `build/`, cachés, evidencia local completa o el workspace padre.
