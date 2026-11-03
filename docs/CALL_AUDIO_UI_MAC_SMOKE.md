# Interfaz de llamadas: prueba reproducible en Mac efímera

## Ejecución

El workflow manual `build-callaudio-arm64.yml` ejecuta, tras compilar las
bibliotecas reales de producción:

```sh
python3 tests/test_call_ui_mac.py --build build/callaudio-mac --out build/ui-mac-absent --driver absent
# Después de instalar y verificar el driver únicamente en la VM de GitHub:
python3 tests/test_call_ui_mac.py --build build/callaudio-mac --out build/ui-mac-present --driver present
```

Se exige Darwin arm64, un directorio de salida nuevo y las bibliotecas estáticas
del build de la aplicación. Linux se rechaza explícitamente; no hay sustituto
simulado. El proceso tiene un timeout de 120 segundos por comando.

## Qué acredita una ejecución verde

- Se compilan todos los cuerpos Swift de producción. La única transformación
  es quitar `@main` de `NivoxApp` en una copia bajo `build/`, para usar la entrada
  del arnés. No se modifica código de producción ni se inyecta backend/permiso.
- Se instancia `CallAudioModel`, que usa `nv_call_create_mac` y su cola real.
  Su inventario debe coincidir con una enumeración independiente del HAL real,
  filtrada con las mismas propiedades físicas documentadas.
- Abrir el modelo no selecciona dispositivos ni habilita captura. Iniciar sin
  selección se rechaza. El controlador real rechaza UIDs físicos inexistentes
  con estado de error y diagnóstico, sin abrir ninguna ruta física.
- Se aloja `CallAudioView` real en una ventana AppKit. Se conserva un PNG de su
  renderizado, se exigen dimensiones mínimas y diversidad de píxeles. Esto
  detecta una vista vacía; **no certifica legibilidad ni ausencia de clipping**.
- Se ejercitan suspensión y salida mediante métodos del modelo real, esperando
  la confirmación asíncrona de parada. No equivale a suspender la VM ni a cerrar
  una llamada activa.
- Se verifica que el estado de autorización de micrófono y los dispositivos
  predeterminados del sistema no cambian.
- Una segunda ejecución del mismo binario con la fase contraria debe fallar
  específicamente por discrepancia del driver. No basta cualquier código de error.

Los artefactos de Actions incluyen `ui-mac-absent/` y `ui-mac-present/` con
`runtime.json`, `report.json`, `commands.log` y `production-call-view.png`.
El informe conserva hashes de fuentes y PNG. Se conserva evidencia fallida;
`ok` solo cambia a true al completar todas las aserciones y la contraprueba.

## Límites obligatorios

Es un arnés que aloja vista/modelo de producción, **no lanzamiento del bundle
entregado ni automatización de clics**. No solicita ni concede TCC, no modifica
su base de datos, no usa AppleScript/Accessibility para eludir permisos y no
relaja firma, instalador ni guardas del backend. La firma del arnés conserva
hardened runtime y el entitlement de audio de producción. No hay release.

Requieren una Mac física y consentimiento del usuario: diálogo TCC real del
bundle ad hoc y su persistencia tras actualizar; concesión/denegación/revocación;
clics de selectores y menús; captura/reproducción con micrófono y auriculares
reales; desconexión USB/Bluetooth; sueño/despertar de hardware; routing real en
Zoom/Meet/Teams; calidad perceptual, latencia y estabilidad prolongada; Gatekeeper
y experiencia de instalación. No hay modelos de aislamiento de hablante ni
notarización. La CI de rutas virtuales existente no sustituye esos criterios.

## Evidencia local de desarrollo

En Linux se verificó rechazo explícito del arnés Mac, sintaxis Python y
`git diff --check`. La suite `tests/test_call_audio.py --out build/ui-new-control`
pasó release y ASan/UBSan (15 casos de controlador y 6 de glue por variante),
con contrapruebas rechazadas. Esa suite usa I/O inyectada: **no acredita la UI
Mac**. El resultado remoto debe consultarse por el SHA exacto publicado;
un workflow pendiente no es una prueba verde.
