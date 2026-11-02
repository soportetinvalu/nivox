# Reconciliación CI de audio + interfaz

## Evidencia previa conservada

- `41485208142`, SHA `3b7c748a546ce09437336634c153db442c4622b4`: Mac real `macOS-15.8.2-arm64`, SDK 15.5. Falló `test_core` con `threaded underruns 2` (límite 1); los siete escenarios simulados no tuvieron underruns ni overflows. Solo se ejecutaron nueve comandos de receta; no llegó al instalador/E2E. El `coreaudio.log` contiene llamadas TCC del sistema, pero no demuestra una denegación TCC de Nivox ni que causara ese fallo. Logs ZIP y artefacto original se conservan en `build/historical-41485208142-*`.
- `41489490904`, SHA `5386031fb481e576e056328b72c419b70cc7aeb9`: la API devuelve run `completed/failure`; Linux `success` y `callaudio / mac-arm64` `cancelled`, sin runner ni pasos. No se conoce quién ni por qué canceló. No es evidencia de la candidata integrada.
- `41494237945`, SHA integrado `e61c5d077b865e517a54522471c6e9f83d53c785`: publicado y verificado por REST. Linux `success`. Runner Mac real asignado y guardas aprobadas; C/C++/driver/herramientas compilados y firmados. `test_core` reprodujo `threaded underruns 4`, límite 1, antes de compilar la app. Los escenarios simulados y conservación de señal pasaron; Swift, instalación y TCC no se ejecutaron. Evidencia en `build/integrated-ci-41494237945-*` y respuestas `build/integrated-*.json`.

## Ajuste de arnés, sin bajar umbrales

El ensayo temporal usa dos pthreads ordinarios, no callbacks CoreAudio. El cambio previo de `nanosleep` a `mach_wait_until` no resolvió el fallo de esta candidata. La evidencia antigua no mide jitter: no permite atribuir con certeza su causa al scheduler ni certificar un fallo del motor.

Se añade a esos dos hilos de prueba Darwin un contrato Mach de tiempo real (periodo original de cada flujo, cómputo 1 ms, restricción 5 ms, preemptible). No cambia el scheduler de la aplicación, del driver ni del host. Se exige retorno exitoso; no se acepta una configuración rechazada como verde. Se conserva la espera absoluta incluso si recibe `KERN_ABORTED`, se registran retraso máximo, tiempo de trabajo, progreso y códigos de política. Los hilos y sus buffers siguen siendo independientes y concurrentes.

Se conserva `threaded underruns <= 1`, cero overflows, misma duración, bloques, target, deriva, denoise, umbrales de señal y escenarios. Se añade contraprueba explícita: detener reiteradamente al productor 150 ms debe exceder el mismo límite y fallar; su JSON debe informar los underruns, no un error de compilación.

La receta compila/verifica la app antes de ejecutar la prueba temporal del núcleo para que este fallo no oculte errores Swift. Ningún fallo se ignora: sigue impidiendo empaquetado/E2E. PKG directo continúa deshabilitado y no se modifican cuarentena, instalador ni guardas.

## Verificación local del ajuste

- `build/integrated-timing-fix-linux-v1/report.json`: release, ASan/UBSan y TSan aprobados; cero underruns/overflows temporales. La contraprueba produjo cuatro underruns, cero overflows y `ok=false`.
- `build/integrated-timing-fix-control-v1/report.json`: controlador 15 casos y contrato backend con HAL sustituido 6 casos en release y ASan/UBSan, más mutaciones; no ABI Apple ni TCC.
- Se conservó el primer fallo de entrega `build/integrated-timing-fix-delivery-v1/report.json`: manifiesto obsoleto tras cambiar dos fuentes. Se actualizaron solo sus hashes, sin relajar la igualdad de bytes.

La compilación y ejecución Apple del ajuste deben acreditarse con un nuevo SHA exacto y API de Actions; este documento no las anticipa. El dispatch usa el workflow registrado `build-speex-arm64.yml`, `callaudio=true`, `--ref integrate/callaudio-ui-safe`: el workflow nuevo de audio aún no está registrado directamente (404), y no se modifica main para registrarlo.

## Límites

Un entitlement `audio-input` firmado no es consentimiento TCC. Logs TCC genéricos no son una denegación demostrada. Este workflow no ejecuta la UI ni una llamada humana real y no acredita permisos concedidos/denegados/revocados, respuesta tardía, hardware USB/Bluetooth, App Nap ni conversación Zoom/Meet/Teams. La prueba virtual, aun aprobada, no permite llamar terminado al producto. No instalar en una Mac de usuario ni retirar cuarentena.
