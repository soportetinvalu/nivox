# Interfaz de llamadas: prueba reproducible en Mac efímera

## Ejecución

El workflow manual `build-callaudio-arm64.yml` compila la aplicación y las
bibliotecas reales y ejecuta:

```sh
python3 tests/test_call_ui_mac.py --build build/callaudio-mac --out build/ui-mac-absent --driver absent
# Después de instalar y verificar el driver únicamente en la VM de GitHub:
python3 tests/test_call_ui_mac.py --build build/callaudio-mac --out build/ui-mac-present --driver present
```

Se exige Darwin arm64, un directorio nuevo y las bibliotecas del build real.
Linux se rechaza; no hay sustituto simulado. Cada comando tiene timeout de 120 s.

## Corrección de diseño y regresión visual

Los avisos de routing, permiso pendiente, pérdida de audio y seguridad conservan
su texto completo. Cada texto largo usa tamaño vertical intrínseco; el contenido
se desplaza cuando no cabe. La altura mínima se aplica al contenedor de pestañas,
no a su hijo: la barra de pestañas no consume parte de una altura fija del hijo.
No se encogen fuentes ni se eliminan advertencias para conseguir un verde.

La prueba aloja la vista de llamadas real **y `NivoxContent`, el contenedor de
pestañas usado por `NivoxApp`**, en ventanas AppKit de 780×560 y 1000×560 puntos.
También comprueba el contenedor de pestañas a 1000×900. La ventana no puede crecer
silenciosamente para acomodar el texto. Se recorren posiciones solapadas del
`NSScrollView` real y se guardan capturas del viewport, no del documento oculto.

Vision OCR reconoce los píxeles de cada captura. Se exige que aparezca cada tramo
consecutivo de tres palabras de **todo** el contenido de routing, cierre/permiso,
pérdida de audio, pie de seguridad, estado actual y (si procede) instalación.
Solo se normalizan mayúsculas, acentos y puntuación; no se toleran palabras
omitidas ni se aceptan etiquetas de accesibilidad como prueba de visibilidad.
Las capturas solapadas permiten verificar párrafos partidos entre viewports.
Un error OCR falla de forma cerrada y conserva el texto reconocido para diagnóstico;
no equivale automáticamente a un defecto de la aplicación.

La contraprueba recompila una **copia desechable** de `CallAudio.swift` que cambia
los avisos de tamaño intrínseco a `.lineLimit(1)`. Ejecuta el mismo renderizador y
oráculo: debe fallar específicamente con `significant text not visible` y guardar
tramos ausentes. Un fallo de compilación, HAL, OCR o viewport no cuenta como una
contraprueba válida. El candidato de producción nunca usa esa copia.

## Otras aserciones y evidencia

- Se compilan los cuerpos Swift de producción. La única transformación del
  archivo de entrada es quitar `@main` de una copia de `NivoxApp` bajo `build/`.
- `CallAudioModel` usa `nv_call_create_mac` y su cola real. Su inventario se coteja
  contra otra enumeración HAL real; los endpoints virtuales no son opciones físicas.
- Abrir el modelo no selecciona dispositivos ni habilita captura. Iniciar sin
  selección se rechaza. El backend rechaza UIDs inexistentes con diagnóstico.
- Suspensión y salida se ejercitan por métodos reales, esperando parada asíncrona.
  Esto no equivale a suspender hardware ni cerrar una llamada activa.
- TCC y dispositivos predeterminados deben quedar sin cambios. Se registra el
  estado TCC inicial numérico; **puede estar autorizado previamente**. El paso
  se llama `TCC unchanged`, no `no TCC grant`: no demuestra un permiso denegado.
- La fase contraria del driver debe fallar específicamente por discrepancia HAL.

Los artefactos `ui-mac-absent/` y `ui-mac-present/` guardan `runtime.json`,
`report.json`, `commands.log`, PNG por viewport/desplazamiento, `*-text.json` con
OCR completo y `truncation-counterprobe/` con el fallo intencional. El informe
conserva hashes de entradas y capturas. `ok` solo es true si pasan el candidato
y ambas contrapruebas. Un fallo positivo mantiene evidencia y `ok: false`.

El upload de CI conserva también todos los archivos nombrados por los manifiestos
de build `SHA256SUMS`: los seis binarios/objetos Mac antes omitidos y los cuatro
ejecutables Linux. El manifiesto de build no pretende cubrir los diagnósticos
que se crean después. Tras descargar, se puede ejecutar `shasum -a 256 -c
SHA256SUMS` desde `callaudio-mac/` (o `sha256sum -c` desde `callaudio-linux/`).

## Límites obligatorios

Es un arnés con las vistas/modelo y el contenedor de producción, **no lanzamiento
del bundle entregado ni automatización de clics**. No solicita/modifica TCC,
no inyecta backend/permiso, no usa AppleScript/Accessibility para eludir permisos,
ni relaja firma, instalador o backend. Mantiene hardened runtime y entitlement
de audio. Solo usa la VM GitHub-hosted efímera; no toca una Mac personal ni crea
una release. La CI pendiente no acredita que la UI Mac haya pasado.

Siguen pendientes: diálogo TCC real del bundle ad hoc y persistencia al actualizar;
concesión/denegación/revocación; clics de selectores/menús; audio con micrófono y
auriculares físicos; USB/Bluetooth; sueño/despertar de hardware; routing en
Zoom/Meet/Teams; calidad, latencia, estabilidad prolongada; Gatekeeper e instalación.
Las rutas virtuales de CI no sustituyen esos criterios ni validan aislamiento
de hablante o notarización.
