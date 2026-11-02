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
- No se captura al abrir. El permiso de Micrófono se solicita al encender;
  una respuesta TCC tardía no puede arrancar después de apagar/cerrar/suspender.
- Desconexión, cambio de tasa/canales, pérdida del driver, muestras rechazadas,
  fallo de consulta HAL o falta de progreso durante 5 s detienen ambas rutas.
  No hay reinicio automático. Underruns/overflows se muestran como aviso acumulado.
- Error de apertura conserva el detalle del motor (`OSStatus` cuando existe)
  y ofrece indicaciones sobre permisos, conexión y formato.
- Apagar, cerrar ventana, cambiar de pestaña o suspender cancela la captura.
  La pestaña A/B queda deshabilitada hasta terminar el cierre del motor.
  La prueba A/B anterior se conserva separada, sin afirmar que prueba llamadas.

## Construcción sin instalar nada

```sh
# Linux: control de ciclo de vida con backend inyectado + ASan/UBSan + contraprueba
python3 tests/test_call_audio.py --out build/call-ui-control
# Linux: receta original DSP + integración + nuevo controlador
python3 build.py --target linux --out build/call-ui-linux
# Mac arm64 real: Nivox.app enlazada con CoreAudio y el motor (sin instalar driver)
python3 build.py --target macapp --out build/call-ui-mac
# Mac arm64 real: app + driver + CLI/E2E, ZIP y pkg solo del driver; no instala
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
avisos de cortes y destrucción en activo. La contraprueba fuerza `RUNNING`
sin actividad y exige que falle la aserción de estado.

**No** demuestra compilación Swift/CoreAudio, permisos TCC, interacción de
SwiftUI, audio físico ni compatibilidad con llamadas reales. El host de esta
integración es Linux sin `swiftc` ni SDK de Apple. Estos controles quedan
pendientes de la siguiente aceptación en Mac, no se sustituyen con mocks.

## Verificación realizada en Linux

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

Los informes locales están en `build/` (ignorados por Git). No se instalaron
paquetes ni driver, no se modificó infraestructura y no se hizo push.

## Aceptación manual pendiente en Mac

1. Construir y verificar firma ad hoc. Instalar el driver por el procedimiento
   existente, con consentimiento de administrador. Abrir Nivox: no debe haber
   captura, reproducción ni petición TCC automática.
2. Confirmar que los selectores no incluyen los dispositivos Nivox ni otro
   dispositivo virtual/agregado. Elegir micrófono físico y auriculares.
3. Encender con TCC denegado: no hay rutas activas y aparece orientación útil.
   Repetir autorizando; cancelar la solicitud y cerrar/suspender antes de
   responder: nunca debe arrancar después.
4. En una app de llamada elegir Nivox Microphone/Nivox Speaker. Confirmar voz
   procesada hacia el interlocutor y audio remoto en los auriculares físicos.
   Verificar que el estado solo confirma el motor, no la conexión de esa app.
5. Apagar: ambos flujos deben detenerse. Elegir hardware físico en la app de
   llamada para continuar sin Nivox. No debe modificarse el default del sistema.
6. Desconectar auriculares durante una llamada: detener ambas rutas y mostrar
   error, **sin pasar automáticamente a los altavoces**. Reconectar no debe
   reanudar hasta pulsar encendido. Repetir cambio de formato y pérdida de driver.
7. Cerrar ventana, suspender/reanudar y alternar A/B: nunca captura simultánea,
   reinicio involuntario, audio antiguo ni transmisión tras apagar.
8. Verificar permisos de micrófono revocados y errores de dispositivo ocupado,
   confirmar errores útiles con detalle original, y una sesión larga con
   Bluetooth/USB. Escuchar calidad y cortes con dispositivos reales.

Sin AEC: usar auriculares. Sin modelo ML. Firma/notarización de distribución,
instalador gráfico, inicio automático y barra de menús no forman parte de este
cambio. No se presenta como producto final validado hasta pasar la aceptación.
