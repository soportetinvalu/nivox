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

## Incidente de captura del run 41701415863

El job Mac `126146365535` compiló correctamente y llegó al primer viewport; el
oráculo declaró 116 tramos ausentes. La captura RGBA contenía 435803 píxeles con
alpha menor que 255 y 80126 píxeles negros con alpha no nulo. El OCR conservaba
los avisos naranjas, pero perdía el texto negro: `cacheDisplay` había capturado
la vista transparente sin el fondo de `NSWindow`. Componer esa captura sobre
blanco para inspección recupera visualmente routing, estado y cierre sin cambiar
el layout. Esto identifica un fallo del arnés, no demuestra aún que pasen todos
los viewports ni descarta otros defectos de la UI.

Se conserva el PNG original, byte a byte, como
`tests/fixtures/call-ui-transparent-41701415863.png` (780×560), SHA-256
`09337c01d803a951d484da32bc774e4e6e8bd3c09f8e3ebd4afd82986ab1f94f`.
Procede de `ui-mac-absent/call-780x560-0.png` del artefacto
`nivox-callaudio-arm64-adhoc-41701415863-1`:
<https://github.com/soportetinvalu/nivox/actions/runs/41701415863/job/126146365535>.
No contiene datos de usuario: es la vista inicial del runner efímero, sin
selecciones de dispositivos ni captura de audio.

El arnés ahora compone los píxeles sobre `window.backgroundColor`, resuelto bajo
la apariencia efectiva de esa ventana, en un bitmap sin alpha antes de OCR.
No añade texto, cambia fuentes ni amplía el viewport. Guarda el PNG transparente
`*-raw.png` y el PNG opaco que se reconoce. Se mantiene el oráculo estricto de
tramos y la contraprueba `.lineLimit(1)` sin cambios.

Antes de los viewports, el código nativo exige:

- Composición correcta de píxeles transparentes, semitransparentes y opacos sobre
  blanco y negro, preservando el naranja opaco.
- Reproducción del fallo OCR con el PNG original transparente y lectura completa
  de routing, cierre y estado al componer ese mismo PNG. Guarda ambos textos,
  sus tramos ausentes y `snapshot-regression.png`; no admite un fallo arbitrario.

Comprobaciones locales de la corrección: inspección alpha y composición con
Pillow sobre el artefacto real; `py_compile` del runner; suite C de control y
contrato con HAL simulado en release y ASan+UBSan, incluidas sus mutaciones.
Ninguna de ellas compila Swift/AppKit ni ejecuta Vision. La regresión nativa y
la visibilidad completa solo quedan acreditadas cuando finalice verde la CI
Mac del commit corrector; no se atribuye ese resultado a la inspección Linux.

## Incidente de compilación del run 41704538403

El job Mac `126157234647`, sobre `51f763aad21119ad56ac2b1350b5e7fdf7510036`,
construyó y empaquetó la aplicación, pero no compiló el arnés UI. El diagnóstico
real se conserva en `ui-mac-absent/commands.log` del artefacto
`nivox-callaudio-arm64-adhoc-41704538403-1`:

```text
ci/CallAudioUISmoke.swift:101:57: error: argument passed to call that takes no arguments
NSBitmapImageRep(contentsOf: fixture)
```

`NSBitmapImageRep` no ofrece ese inicializador de URL. `snapshotFixture` carga
los bytes con `Data(contentsOf:)` y los decodifica con `NSBitmapImageRep(data:)`.
Devuelve `nil` ante un archivo ausente o no decodificable; el oráculo mantiene
su fallo cerrado. No se modifica el PNG original, la composición ni el OCR.

Antes de compilar la UI completa, el runner extrae ese método **literalmente**
y lo compila contra AppKit real con warnings como errores. Ejecuta tres casos:
PNG genuino (780×560 y alpha conservado), ruta inexistente y archivo de texto
no decodificable. También recompila el inicializador defectuoso original y
exige específicamente el mismo diagnóstico, no cualquier fallo. El informe
registra `snapshot_fixture_cases_passed: 3` y
`original_fixture_initializer_rejected: true` solo cuando ambas pruebas pasan.
No usa declaraciones AppKit inventadas ni un decoder simulado.

Comprobaciones locales del corrector: `py_compile`, análisis sintáctico con
Swift 6.2.1 real, regresión de lifecycle Swift/C (14 casos y dos rechazos de
tipos), control C y contrato HAL simulado en release y ASan+UBSan con sus
contrapruebas. Linux no dispone de AppKit: **no acredita compilación semántica
AppKit, ejecución de este nuevo decoder nativo ni visibilidad Mac**. Esos
resultados siguen pendientes de la nueva CI; no se espera su terminación.

## Incidente de ejecución del run 41706083678

Sobre `40dab08926b0b9355adbe77e5b084953a5a91acc`, el job Mac
`126162427503` compiló y firmó `ui-smoke`. El decoder pasó los tres casos y
rechazó el inicializador original con el diagnóstico esperado. El ejecutable
salió con RC1 en `snapshotIncidentRegression`, **antes de renderizar los
viewports actuales**:

```text
FAIL: opaque original capture still loses significant text: ["en zoom meet", "nivox no cambia", "no cambia los", "cambia los dispositivos"]
```

Artefacto: `13265504356`,
`nivox-callaudio-arm64-adhoc-41706083678-1`, SHA-256 del ZIP descargado
`a0cf5842c470608cb83d8d934806ebb93cc733a0740de910a8acf643a3789841`.
Su `snapshot-regression.json` reconoce `n Zoom, Meet…` y `ambia los dispositivos…`,
pero el PNG opaco muestra las letras iniciales completas. Es una omisión de
Vision sobre píxeles presentes; no demuestra truncado de la vista actual. La
composición alpha ya funciona, pero por sí sola no basta para este OCR.

El arnés prepara ahora una entrada OCR 2× con vecino más próximo y un borde
blanco de 24 píxeles. No cambia fuentes, viewport, textos esperados, tolerancias
ni el oráculo; no añade letras ni usa corrección lingüística. Conserva PNG raw,
opaco y `*-ocr.png`, y en la regresión conserva también OCR nativo 1× para
comparación. El mismo tratamiento se aplica al candidato y a `.lineLimit(1)`.
La autoprueba nativa exige que cada píxel RGB de origen se replique exactamente
en cuatro píxeles, con geometría, opacidad y borde correctos, sobre blanco y negro.

**Estado de esta intervención:** la omisión OCR está acreditada con el artefacto
real; el muestreo 2× es una corrección candidata que requiere ejecutar Vision
en la CI Mac. No se declara que haya resuelto el incidente hasta que pasen la
regresión histórica, todos los viewports y la contraprueba de truncado. Las
pruebas Linux de control/lifecycle y el parseo Swift no sustituyen esa ejecución.

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
