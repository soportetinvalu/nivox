# Aceptación pendiente: Mac, llamadas, permisos y firma

**No ejecutado en Mac ni en GitHub Actions.** Ninguna prueba Linux acredita Swift/SDK, AVFoundation, GUI o hardware. No hay `.app` ni ZIP Apple validados en esta entrega de fuentes.

## Compilación y artefacto

- Ejecutar workflow manual en Darwin arm64 real; conservar versión de macOS/SDK/compilador, arquitectura y logs completos. `macos-26` es el label preparado con guarda `uname`; el runner no es un Mac mini M4. La versión del Mac objetivo debe verificarse al disponer de acceso, sin deducirla del chip.
- Revisar C/Python, smoke Swift sin dispositivos, import C, enlace estático SpeexDSP, app SwiftUI y comportamiento de concurrencia/cancelación real. El parser Linux usa fixtures sintéticas, no Mach-O real.
- Verificar icono original/11 representaciones ICNS, Info.plist, Finder/Dock, recursos/licencias, dependencias `lipo`/`otool`, firma y ZIP reextraído. La receta contiene estos controles, pero no se han ejecutado con herramientas Apple.

## Permisos y privacidad

- `NSMicrophoneUsageDescription` identifica SpeexDSP/procesamiento local. Entitlement único `com.apple.security.device.audio-input=true`. La app no está declarada App Sandbox. Esto no otorga permiso TCC ni demuestra que la petición funcione.
- Probar TCC no determinado/denegado/restringido/permitido/revocado; no capturar sin consentimiento, no volver a iniciar tras una respuesta tardía al cerrar o suspender. Arrancar no debe solicitar micrófono hasta Grabar.
- Mantener bundle ID experimental `local.christian.nivox.speex.experimental`, distinto del prototipo anterior. No trasladar ni asumir permisos del ID anterior; firma/identidad futuras pueden modificar la asociación TCC.
- Pruebas manuales autorizadas: micrófonos de 48 kHz y rechazo de otras tasas/formatos, entrada canal 1, máximo 15 s, corte, cola llena, cambio de ruta, suspensión/reanudación y cierre. A/B original/procesado con auriculares, sonido inicial bajo, exportación WAV explícita sin sobrescritura y verificación de archivo parcial si falla.
- Confirmación de auriculares es un checkbox del usuario, no detección automática. No hay cancelación de eco. No volcar grabaciones privadas a CI/logs/repositorios.

## Llamadas: no implementado

La app **no crea un micrófono virtual**, no publica entrada HAL ni envía la señal limpia a Zoom/Meet/Teams. Grabar/procesar/reproducir buffers no basta para aparecer en sus selectores. No hay ruta de audio de llamada ni interoperabilidad probadas.

Falta elegir e implementar una ruta autorizada: dispositivo virtual/driver separado con revisión de licencia e instalación, o un experimento CoreAudio con APIs compatibles y pruebas por app. Los process taps capturan salida de procesos y no equivalen por sí solos a un micrófono universal; un agregado público es una hipótesis, no solución acreditada. Una app WebRTC propia podría enviar su stream sin crear un dispositivo global; tampoco está implementado. No se incluye BlackHole ni se recomienda instalar nada automáticamente.

Antes de llamadas: consentimiento específico para dispositivos/permisos adicionales, salida limpia separada de tono/original, retorno solo por auriculares sin bucle, selección por UID sin cambiar salida global, reloj/remuestreo/latencia extremo a extremo, mute y protección frente a feedback, limpieza de objetos tras fallo y permisos concedidos/denegados/revocados. Probar cada versión de Zoom/Meet/Teams con reuniones de prueba autorizadas, nunca conversaciones ajenas. No declarar compatibilidad por pasar DSP sintético.

## Firma y distribución

La receta futura genera firma **ad hoc** y la verifica antes/después de ZIP. No hay certificado Developer ID, identidad de firma de producción, perfil, notarización Apple ni stapling. No se han usado credenciales. La firma ad hoc no demuestra aceptación de Gatekeeper de una descarga; probar una descarga real en Mac limpio es un hito distinto y no se deben desactivar sus protecciones.

Para distribución externa faltan: titularidad del icono/marca, política de datos/soporte, revisión de licencias, identidad estable, Developer ID con autorización segura, hardened runtime apropiado, notarización/stapling y prueba Gatekeeper. Un artifact de Actions no es un Release. Este encargo no publica ni habilita estos pasos.

## Calidad

Falta evaluación con voz autorizada, escucha crítica, escenarios de ráfagas y micrófonos reales, inteligibilidad y rendimiento/latencia/CPU en el Mac objetivo. −15 dB es configuración, no mejora garantizada. No se acredita paridad con otros productos ni tiempo real duro.
