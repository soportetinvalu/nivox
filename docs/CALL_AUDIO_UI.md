# Interfaz de audio de llamadas · integración y aceptación

## Qué está conectado

La pestaña **Llamadas** de `Nivox.app` usa el motor en el mismo proceso, no
una CLI suelta ni una simulación:

`CallAudioView → CallAudioModel → cola call-control → nv_call → nv_call_mac → nv_path → AUHAL/nv_bridge/SpeexDSP`.

- Encender abre las dos rutas existentes: micrófono físico → Nivox Mic Feed;
  Nivox Speaker Return → salida física. No hay bypass mientras está apagado:
  **se cierran las dos rutas**. Las apps que sigan usando Nivox quedarán sin audio.
- Los selectores muestran dispositivos vivos, con UID estable y transporte
  físico. Se excluyen Nivox, transportes virtuales, agregados, ocultos y de
  transporte desconocido. No se eligen dispositivos predeterminados ni se
  sustituye una selección que desaparece. La selección solo dura esta sesión.
- No se cambian las tasas, tamaños de buffer ni dispositivos predeterminados.
  La entrada usa el primer canal físico; la salida se procesa a mono y se
  duplica a estéreo si el dispositivo tiene al menos dos canales.
- `STARTING` solo pasa a `RUNNING` cuando **ambos** puentes tienen callbacks
  de entrada y salida y están cebados. Eso no certifica que Zoom/Meet/Teams
  estén conectados a los dispositivos virtuales, ni que haya señal audible.
- No se captura al abrir ni al reabrir. El permiso de Micrófono se solicita
  solo al iniciar explícitamente; una solicitud pendiente se invalida al
  cerrar la ventana, cambiar de pestaña, detener o suspender. La comprobación
  por generación también impide reutilizar respuestas antiguas en la prueba A/B.
- Desconexión, cambio de ID/tasa/canales/buffer/formato físico, notificación de
  dispositivos/reinicio CoreAudio (aunque conserve IDs), pérdida del driver, muestras rechazadas,
  fallo de consulta HAL o falta de progreso durante 5 s detienen ambas rutas.
  No hay reinicio automático. Underruns/overflows se muestran como aviso acumulado.
- Error de apertura conserva el detalle del motor (`OSStatus` cuando existe)
  y ofrece indicaciones sobre permisos, conexión y formato.
- El motor y la prueba A/B pertenecen al delegado de **la aplicación**, no a
  las vistas. Cerrar la ventana o cambiar de pestaña conserva las dos rutas de
  una llamada ya iniciada, incluso durante STARTING. No inicia nuevas rutas.
  `WindowGroup` y `applicationShouldTerminateAfterLastWindowClosed = false`
  evitan asociar el último cierre con la salida. Se elimina «Nueva ventana».
- La barra de menús permanece disponible: estado de reenvío (no prueba de
  señal audible), detalle del error, cortes acumulados, reabrir dispositivos,
  iniciar con la selección existente, **Detener audio…** y **Salir de Nivox…**.
  Iniciar desde el menú cancela y cierra primero cualquier prueba A/B,
  incluyendo su permiso pendiente. A/B permanece deshabilitada mientras
  llamadas está iniciando, activa o cerrando rutas.
- Una actividad `userInitiatedAllowingIdleSystemSleep` evita App Nap durante
  el audio solicitado, pero no impide la suspensión. La vigilancia de salud
  usa un temporizador en modo `.common`, también con el menú abierto.
- **Detener audio…** y salir por menú, Cmd-Q o Dock piden confirmación y
  explican que no hay bypass ni cambio automático a hardware. Cancelar es
  la opción predeterminada y no detiene una llamada activa. El aviso de
  reconfigurar la app de llamadas sigue visible también después de detener.
  No se conoce qué aplicaciones siguen usando el driver: no se afirma que
  estén desconectadas ni que la llamada esté a salvo por estar Nivox apagado.
- La salida normal devuelve `.terminateLater` y responde a AppKit solo tras
  el cierre serializado de ambas rutas. Invalida permisos pendientes y
  detiene A/B. No hay espera síncrona de HAL en el hilo principal.
- Suspender detiene ambas rutas sin confirmación ni reanudación automática.
  Al volver se requiere iniciar explícitamente. Error, salida forzada, caída
  del proceso y apagado del sistema tampoco ofrecen bypass: una llamada
  configurada con Nivox puede quedarse sin audio. El diálogo de salida no
  puede proteger frente a SIGKILL o una interrupción de energía.

La diferencia de cierre entre `Window` y `WindowGroup` está documentada por
[Apple](https://developer.apple.com/documentation/swiftui/window). La barra
usa `MenuBarExtra` de macOS 13+, sin agente ni cambio de política del Dock.

## Construcción sin instalar nada

```sh
# Linux: control de ciclo de vida con backend inyectado + ASan/UBSan + contraprueba
python3 tests/test_call_audio.py --out build/call-ui-control
# Linux: receta original DSP + integración + nuevo controlador
python3 build.py --target linux --out build/call-ui-linux
# Mac arm64 real: Nivox.app enlazada con CoreAudio y el motor (sin instalar driver)
python3 build.py --target macapp --out build/call-ui-mac
# Mac arm64 real: app + driver + CLI/E2E y ZIP protegido; PKG directo deshabilitado
python3 callaudio/tools/build_callaudio.py --target mac --out build/callaudio-mac
```

Cada `--out` debe ser nuevo. No requiere red, paquetes Python, daemon ni SDK
comercial. El workflow manual `build-callaudio-arm64.yml` ahora construye e
incluye la app en el ZIP. Su paso E2E preexistente instala el driver **solo en
la VM efímera de GitHub**, no en este host ni en una Mac de usuario. No se ha
lanzado ni se ha hecho push como parte de esta integración.

## Pruebas y límite de evidencia

`test_call_control.c` ejecuta el controlador de producción con un backend
inyectado: arranque sin captura automática, catálogo, driver ausente,
selecciones no válidas/virtuales, rollback de cada ruta, actividad requerida
en ambas rutas, apagado idempotente, IO estancado, desconexión, cambios de
formato, pérdida de driver, error de enumeración, muestras rechazadas,
avisos de cortes y destrucción en activo. Ahora añade la política C de
ciclo de vida que importa Swift: conservar STARTING/RUNNING tras 100 cambios
de pestaña/cierres, cancelar permiso pendiente, parar en suspensión sin
reinicio y mantener detección de estancamiento con la ventana cerrada.
Las contrapruebas fuerzan RUNNING sin actividad, parada al ocultar, aceptación
de permiso antiguo y suspensión sin parada; cada una debe fallar en su aserción
concreta. Las guardas de fuente Swift también rechazan reintroducir el
`calls.stop()` en el cambio de pestaña: **no son ejecución de SwiftUI ni TCC**.

**No** demuestra compilación Swift/CoreAudio, permisos TCC, interacción de
SwiftUI, audio físico ni compatibilidad con llamadas reales. El host de esta
integración es Linux sin `swiftc` ni SDK de Apple. Estos controles quedan
pendientes de la siguiente aceptación en Mac, no se sustituyen con mocks.

## Integración sobre seguridad

La candidata actual y sus límites están en [`CALL_AUDIO_SAFE_INTEGRATION.md`](CALL_AUDIO_SAFE_INTEGRATION.md).
Los recibos siguientes son históricos de la rama UI, no verifican esta candidata.

## Evidencia de la integración anterior (conservada)

- `build/call-ui-control-final/report.json`: 10 casos de control en release y
  10 con ASan/UBSan, sin fallos; contraprueba de actividad rechazada.
- `build/call-ui-linux-v1/report.json`: receta DSP original correcta, 21 tests
  de motor, integración C/CLI correcta y 0 asignaciones en callback/worker.
- `build/call-ui-callaudio-linux-v1/report.json`: núcleo de llamadas correcto
  en release, ASan/UBSan y TSan (7 escenarios por variante; release completo,
  sanitizadores en modo rápido). Esto usa relojes/señales de prueba, no hardware.
- `build/call-ui-delivery-v1/report.json`: 21 pruebas de entrega correctas,
  sin omisiones. La exportación allowlist se comprobó por miembros y bytes.
- La receta Mac rechaza explícitamente este host sin crear salida; no se
  presenta el resultado Linux como compilación Apple.

## Verificación de continuidad y barra de menús en Linux

El recibo versionado `docs/CALL_AUDIO_UI_EVIDENCE.json` registra resultados,
hashes de las fuentes probadas y de los informes locales de esta iteración.
Las rutas y comandos completos están en sus informes bajo `build/`.
No acredita compilación ni interacción Apple; véanse los límites anteriores.

Los informes locales están en `build/` (ignorados por Git). No se instalaron
paquetes ni driver, no se modificó infraestructura y no se hizo push.

## Aceptación manual pendiente en Mac

1. Construir y verificar firma ad hoc. Instalar el driver por el procedimiento
   existente, con consentimiento de administrador. Abrir Nivox: no debe haber
   captura, reproducción ni petición TCC automática.
2. Confirmar que los selectores no incluyen los dispositivos Nivox ni otro
   dispositivo virtual/agregado. Elegir micrófono físico y auriculares.
3. Encender con TCC denegado: no hay rutas activas y aparece orientación útil.
   Repetir autorizando; cerrar/cambiar de pestaña/detener/suspender antes de
   responder: nunca debe arrancar después. Repetir cerrando y abriendo una
   nueva solicitud antes de responder a la antigua, tanto en llamadas como A/B.
4. En una app de llamada elegir Nivox Microphone/Nivox Speaker. Confirmar voz
   procesada hacia el interlocutor y audio remoto en los auriculares físicos.
   Verificar que el estado solo confirma el motor, no la conexión de esa app.
5. Detener desde ventana y barra de menús: comprobar advertencia y opción
   predeterminada Cancelar; cancelar no debe cortar audio. Confirmar detiene
   ambos flujos y deja orientación para elegir hardware físico en la app de
   llamadas. La selección/default del sistema debe permanecer idéntica.
6. Desconectar auriculares durante una llamada: detener ambas rutas y mostrar
   error, **sin pasar automáticamente a los altavoces**. Reconectar no debe
   reanudar hasta pulsar encendido. Repetir cambio de formato y pérdida de driver.
7. En llamada bidireccional y durante STARTING, alternar pestañas, cerrar con
   botón rojo/Cmd-W, minimizar y dejar solo el menú durante al menos 10 min:
   comprobar continuidad real con interlocutor y ausencia de App Nap. Abrir
   el menú durante más de 5 s y comprobar que la vigilancia sigue funcionando.
   Reabrir sin duplicar motores. Cambiar a A/B no corta llamadas y A/B permanece
   bloqueada. Tras detener, iniciar A/B y desde el menú iniciar llamadas:
   comprobar cierre completo de A/B y rechazo de cualquier respuesta TCC vieja.
8. Salir por menú, Cmd-Q y Dock: cancelar no corta, confirmar cierra ambas
   rutas antes de terminar el proceso; repetir al iniciar, con permiso pendiente
   y error. Reabrir la app no debe capturar ni restaurar el estado activo.
   Probar suspensión/reanudación y revocación de permiso con ventana cerrada:
   se detiene, muestra aviso de pérdida de rutas y no arranca automáticamente.
9. Verificar permisos de micrófono revocados y errores de dispositivo ocupado,
   confirmar errores útiles con detalle original, y una sesión larga con
   Bluetooth/USB. Escuchar calidad y cortes con dispositivos reales.

Sin AEC: usar auriculares. Sin modelo ML. Firma/notarización de distribución,
instalador gráfico e inicio automático no forman parte de este cambio.
La barra de menús está implementada, no ejecutada en Mac. No se presenta como
producto final validado hasta pasar compilación, TCC, UI y llamadas reales.
