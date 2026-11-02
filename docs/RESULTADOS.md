# Resultados de la entrega integrada

## Ejecutado realmente en Linux

Plataforma Linux x86_64, GCC 13.3.0. Motor **SpeexDSP 1.2.1 real**, compilado desde el tar oficial fijado; no sustitutos, modelos, audio privado ni dispositivos. Comandos completos y hashes de entradas activas en los informes de build.

| Comprobación | Resultado observado |
|---|---|
| Build release + arnés C | RC 0, `ok=true`, `dsp_executed=true`, `mac_compiled=false` |
| Suite de motor original | **21/21 aprobadas**, sin cambiar señales, aserciones ni umbrales |
| Ruta completa `np_*` | **14 casos**: 1/479/480/481/1001/15007/720000 muestras, bloques 127/1024; igualdad con motor real y conservación de finales parciales |
| Impulsos | **3 sondas**, fuerza 0/0.5/1; impulso en 32 → pico en 992, retardo 960 muestras |
| Contrapruebas DSP | Salida muda y desplazada rechazadas; tasa/canales/aborto inválidos rechazados; CLI igual al motor cuantizado y sobrescritura rechazada |
| Atenuación de ruido blanco solo | **6.258321834002474 dB**, supera umbral original >6 dB; no mide inteligibilidad del habla |
| Arnés SPSC | Assertions aprobadas, **240000 muestras** entre productor/consumidor coordinados; no prueba exhaustiva de races |
| Instrumentación de asignaciones GNU | **0 llamadas** en el tramo inicial instrumentado de push/pump/close/finish de 1000 muestras; no extrapolar a toda la UI/captura |
| ASan + UBSan | RC 0, `ok=true`, adaptador/cola/upstream instrumentados; arnés C sin diagnósticos de sanitizador; no suite Python vía ctypes ni benchmark |
| Identidad/icono/guardas/parser | **21/21 aprobadas, 0 omitidas**: 16 controles de entrega y 5 del parser Mach-O (fixtures sintéticas, no herramientas Apple) |
| Icono | Original PNG 1254 × 1254 e ICNS de **11 representaciones**, hashes idénticos al branding vigente; corrupción/CRC/truncado/representación ausente rechazados |
| Exportación | Allowlist de **30 archivos fuente** + manifiesto, conjunto exacto de **31 entradas** y bytes verificados; sin builds/Git/cachés/credenciales/Neep/RNNoise/pesos/audio privado |
| Guardas | Build/export fuera de `build/` o sobre salida existente, tar corrupto, entrada simbólica/parent simbólico y Mac simulado en Linux rechazados |
| `actionlint` | **1.7.12, RC 0** sobre workflow integrado y copia extraída; contraprueba con campo YAML inválido → **RC 1** |
| Preservación | Hashes antes/después de **468 archivos** del prototipo y **1285 archivos** del stage: sin añadidos, retirados ni cambios observados |

No se suman los subcasos a un supuesto total único: son suites y sondas con alcances distintos.

## Autocontención comprobada

Se exportó y extrajo una copia independiente bajo `build/isolated/nivox`, se cotejaron todos los nombres/hashes y se reconstruyó desde su propia raíz. Build release y ASan+UBSan se ejecutaron con entorno mínimo explícito y HOME/TMPDIR locales, sin variables de autenticación heredadas. En esa copia: **21/21 pruebas de motor, 14 casos, 3 impulsos, arnés C y 21/21 pruebas de entrega aprobados**, más `actionlint` RC 0. Reexportación determinista y manifiesto permiten contrastar la entrega final.

La documentación de resultados se completa después de esta ejecución; los hashes de todos los archivos activos de build/audio/icono se contrastan con los probados en la copia independiente. No se presenta una modificación documental posterior como otra medición ni como ejecución de Actions. La exportación final se verifica por nombres y bytes y conserva manifiesto/checksum externos para no introducir una autorreferencia.

## Evidencia local (no exportar en bloque)

- `build/linux-release/{report.json,commands.log,unit-tests.json,integration/report.json,SHA256SUMS}`.
- `build/linux-sanitize/{report.json,commands.log,SHA256SUMS}`.
- `build/delivery-tests/report.json` y resultados equivalentes de la copia independiente.
- `build/evidence/`: inventarios de originales, comparación de integración/diffs, informe de preservación, tabla oficial de runners descargada, resultados/version de actionlint y resumen consolidado.
- Exportación final en `build/public-sources/`, con tar, `source-manifest.json`, `SHA256SUMS` y `verification.json`. Este directorio es distinto de la exportación preliminar usada para reconstrucción; no se borraron evidencias anteriores.

## Avisos y límites reales

Upstream emite `warning: The VAD has been replaced by a hack pending a complete rewrite` incluso con VAD desactivado; queda preservado en logs. No es un diagnóstico de ASan/UBSan. El extractor web inicial no admite extracción con backend ddgs; se recuperó la documentación oficial por HTTPS con urllib (HTTP 200) y hash, sin inventar contenido.

**No compilado ni ejecutado en macOS; no se generó app/binario Apple, no se ejecutó GitHub Actions ni se publicó nada.** La guarda Mac falla explícitamente en Linux. No se usaron credenciales ni se accedió al micrófono o a servidores del proyecto; las consultas HTTPS de documentación y acciones públicas no ejecutaron servicios de inferencia. El workflow manual arm64 está preparado y validado sintácticamente, no ejecutado.

Pendiente: Swift/AVFoundation/GUI, TCC real, hardware/rutas/suspensión, Finder/Dock, audio A/B y exportación en Mac, M4 y versión real de macOS, calidad perceptual/voz, rendimiento y latencia extremo a extremo. No hay micrófono virtual ni integración en llamadas. Firma ad hoc está solo en la receta futura: Developer ID, notarización, stapling y Gatekeeper siguen pendientes. Confirmar derechos públicos del arte aportado, excluido de MIT. Detalle y criterios: `ACEPTACION_MAC.md`.
