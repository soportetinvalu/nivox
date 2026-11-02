# Candidata UI sobre seguridad · solo local, sin validación Apple

## Procedencia y límites

- Base de seguridad: `5386031fb481e576e056328b72c419b70cc7aeb9`.
- Commits UI originales: `b6a1358208c4ba843cfefe26ee7a64c99cb4ba8c` y
  `ab6d0384452be5e7c67e62f20745f5bb814ccf8b`, aplicados en ese orden.
- Rama local: `integrate/callaudio-ui-safe`, worktree independiente
  `workspace/worktrees/nivox-call-audio-integrated` del canal.
- No se hizo push, dispatch, instalación HAL, retirada de cuarentena ni prueba
  en una Mac de usuario. No se modificaron los dos worktrees de origen.
- La ejecución ya en cola `https://github.com/soportetinvalu/nivox/actions/runs/41489490904`
  pertenece al trabajo de seguridad anterior: **no verifica esta candidata**.
  No se lanzó otra ejecución idéntica ni se consultaron credenciales.
- La evidencia de la antigua rama UI se conserva como historia en
  `CALL_AUDIO_UI_EVIDENCE.json`; no acredita el árbol integrado.

## Conflictos y ajustes semánticos

Los conflictos estaban en el workflow de llamadas y su receta de empaquetado.
Se combinaron las pruebas de instalador y controlador, se conservó `macos-15`
con guardas Darwin/arm64/GitHub-hosted/ausencia de driver previo y todos los
pasos auditados de recuperación, rechazo de candidato, reversa y desinstalación.
El **PKG directo sigue deshabilitado**; no se recuperó la sobreescritura insegura.

Los archivos auditados de instalador, HAL, reloj, monitor de endpoints,
conservación de señal, CLI y E2E conservan bytes de la base de seguridad. Las
excepciones intencionales son README, workflow de llamadas y receta de build:
se revisaron sus deltas sin eliminar guardas ni bajar umbrales.

La UI heredada solo comprobaba tasa/canales. El backend integrado ahora:

- Exige registrar tanto notificaciones de dispositivos como de reinicio del
  servicio antes de abrir rutas. Un fallo conserva OSStatus y no inicia audio.
- Usa una generación atómica con contexto de duración de proceso: una
  notificación tardía no referencia rutas liberadas. Las suscripciones se
  comparten con refcount y mutex fuera de RT; una retirada fallida no deja un
  puntero liberado ni permite duplicar la suscripción al siguiente inicio.
- Resuelve UID nuevamente y compara ID, vivo, tasa, canales, buffer y hash de
  formato físico con `nv_device_state`/`nv_endpoint_changed` auditados, incluidos
  extremos ocultos. Rechaza cambios durante la propia apertura.
- Ante cambio/reinicio, **detiene ambas rutas**, no reconecta por sí solo ni
  sustituye hardware. Esto es deliberadamente distinto de la recuperación
  automática de la CLI. Requiere otro inicio explícito; una notificación global
  de topología puede causar una parada conservadora aunque los UIDs elegidos
  sigan conectados.
- Mantiene permiso TCC explícito y tokens de generación de Swift; ocultar una
  llamada activa no la corta, pero cancela una autorización aún pendiente.
  Estado RUNNING exige callbacks/cebado de ambas rutas, no demuestra señal
  audible ni conexión de Zoom/Meet/Teams.

La app y las herramientas conservan `audio-input`. Se añadió lectura del plist
**firmado**, antes y después de extraer, rechazando valor falso, entero `1`,
entitlement ausente o permisos adicionales. El paquete exterior verifica de
nuevo firma de app/driver/herramientas y bytes exactos tras el round trip.
Estos comandos Apple están preparados, **no ejecutados aquí**.

La exportación por allowlist incluye ahora los headers de vigilancia/señal,
entitlements, instalador transaccional/reversa y pruebas auditadas antes
omitidos, además de las fuentes UI. `source-manifest.json` debe corresponder a
cada byte exportado; no incluye su propio hash. El recibo de evidencia evita
referenciar el hash del manifiesto o del paquete que lo contiene.

## Pruebas Linux y recibos

Resultados reales y hashes de entradas: `CALL_AUDIO_SAFE_EVIDENCE.json`.
Todos los directorios siguientes están bajo `build/` e ignorados por Git.

| Comando ejecutado | Recibo local | Alcance |
|---|---|---|
| `python3 tests/test_call_audio.py --out build/safe-ui-control-v3` | `report.json`, `commands.log` | 15 casos del controlador + 6 del pegamento de backend en release y ASan/UBSan; HAL/path sustituidos, no ABI Apple |
| `python3 callaudio/tools/build_callaudio.py --target linux --out build/safe-ui-core-v1` | `report.json`, `commands.log`, `SHA256SUMS` | 7 escenarios por variante release/ASan+UBSan/TSan, RT allocation guard y contraprueba de denoiser mudo |
| `python3 build.py --target linux --out build/safe-ui-dsp-v2` | `report.json`, `unit-tests.json`, `integration/report.json` | DSP real, 21 pruebas de motor, integración C/CLI y 0 asignaciones callback/worker |
| `python3 build.py --target linux --sanitize --out build/safe-ui-dsp-asan-v2` | `report.json` | Arnés nativo DSP/adaptador/control con ASan/UBSan |
| `python3 callaudio/tests/test_installer.py` | `safe-ui-installer-v1/tests.log` | 5 transacciones en sandbox: respaldo/reversa, rollback, cuarentena, desinstalación recuperable y candado |
| `python3 tests/test_delivery.py --out build/safe-ui-delivery-v2` | `report.json` | Identidad/icono/guardas, parser de entitlement y exactitud de manifiesto; no codesign real |
| `python3 ci/export_sources.py --out build/safe-ui-sources-v2` | `verification.json`, `SHA256SUMS` | Miembros y bytes exactos del tar determinista; no publicación |

Las mutaciones deben fallar por la aserción pertinente, no por fallo de
compilación o crash ajeno: RUNNING sin callbacks, quitar salud de ruta,
aceptar registro fallido de listeners, ignorar reinicio con mismos IDs,
ignorar snapshot de entrada, cortar al ocultar, aceptar permiso tardío y
continuar al suspender. La contraprueba de wiring Swift tampoco compila Swift.

El primer intento `safe-ui-control-v1` pasó los casos reales y sanitizadores,
pero falló al compilar un mutante por falta de `-Icallaudio/mac` en el arnés.
Se corrigió solo esa ruta de prueba y se repitió en `safe-ui-control-v3`,
conservando el informe fallido. No se contó ese error de arnés como rechazo
válido de una mutación. La primera entrega `safe-ui-delivery-v1` también detectó
un XML malformado que se rechazaba con `ExpatError` sin normalizar a `RuntimeError`.
Se corrigió el manejo del parser y se repitieron control y DSP sobre el helper
final; ambos fallos e informes se conservan. La suite larga de núcleo quedó
verde sobre las mismas entradas y no se repitió.

## Pendientes concretos de CI y aceptación

**No hay build Mac de esta candidata.** Tras autorización separada para publicar
su SHA, validar exactamente ese SHA en el workflow manual de llamadas:

1. Compilar C/C++/Swift reales en Darwin arm64 (`macos-15`): resolver símbolos,
   importar el módulo C ampliado y enlazar CoreAudio/AudioToolbox/AVFoundation.
   Los stubs Linux no acreditan tipos/constantes ni ABI Apple.
2. Exigir `app_build.ok`, `mac_compiled`, firmas ad hoc, entitlements firmados
   `audio-input` de app y cada CLI, arquitectura/dependencias y hashes del ZIP
   extraído; comprobar que no hay PKG directo. El éxito de compilación no
   acredita lanzamiento UI ni Gatekeeper.
3. Ejecutar stress del reloj, `nv-e2e` con señal real virtual/no silencio,
   preservación con denoise y contraprueba muda. Si TCC impide captura en el
   runner, registrar el bloqueo sin relajar umbrales.
4. Solo en VM GitHub-hosted efímera: rechazo de candidato inválido sin alterar
   el instalado, sustitución con respaldo, restauración conservando ambas
   versiones, recuperación de la CLI tras `coreaudiod` y uninstall protegido.
5. El workflow actual **no ejecuta la interfaz ni el nuevo backend UI durante
   un reinicio real**: hace falta una sonda Apple específica o aceptación
   interactiva para demostrar parada de ambas rutas tras reinicio con IDs
   reciclados/cambio de formato y ausencia de reanudación automática.
6. Fuera del alcance del CI virtual: TCC autorizado/denegado/revocado y respuesta
   tardía, continuidad al cerrar/cambiar pestaña con menú abierto, App Nap,
   stop/quit/sleep, USB/Bluetooth, no fallback a altavoces y llamada bidireccional
   con voz. Requieren un Mac autorizado más adelante, no se instalaron aquí.

`actionlint` y `shellcheck` no estaban disponibles en el host. La validación
local YAML/Bash y sus guardas se registra separadamente; no se presenta como
un resultado de esas herramientas ni como compatibilidad con Bash 3.2 de Apple.
