# Corrección transaccional del instalador HAL

## Alcance y estado

Corrección del falso éxito demostrado en `da0c20b`: `killall coreaudiod || true`
permitía confirmar una instalación aunque el reinicio devolviera 7. La nueva
transacción no se confirma hasta que el reinicio devuelva 0 y se observe el
estado HAL requerido. No cambia la UI, el DSP ni los umbrales E2E.

**Esta corrección se verifica en Linux con sandbox/API sintética. No se ha
compilado con el SDK Apple ni ejecutado en Mac. No habilita una instalación
física ni certifica un paquete descargable.** No se despacha/reintenta Actions
para buscar capacidad Mac; los dos intentos previos sin runner siguen siendo un
bloqueo de aceptación nativa. No se crea Release.

## Contrato de éxito y recuperación

- `install`/`restore`: exigir los cuatro UID `nivox.microphone.v1`,
  `nivox.mic-feed.v1`, `nivox.speaker.v1`, `nivox.speaker-return.v1` presentes y
  vivos. `uninstall`: exigir los cuatro ausentes. Una consulta HAL fallida no
  demuestra ausencia. Se usa traducción UID, incluida la variante para ocultos,
  no búsqueda de nombres en una lista de dispositivos visibles.
- `nv-driver-state` es una sonda C de solo lectura. No abre streams, captura,
  cambia defaults ni pide TCC. El build Mac la compila, audita arquitectura y
  dependencias, firma ad hoc y verifica también después de extraer el ZIP.
  No lleva entitlement de captura; las herramientas de captura mantienen el suyo.
- El gestor exige la sonda firmada del mismo paquete, rechaza symlink/cuarentena,
  la copia a la transacción root-only y fija permisos antes de ejecutarla.
  No existe un override productivo por entorno para rutas o resultados.
- Hasta 40 consultas con pausas de un segundo. Es un límite de consultas, no una
  garantía de tiempo total ni de interrupción máxima del audio. Un proceso nuevo
  por consulta evita conservar objetos HAL previos al reinicio.
- Si falla reinicio/estado/firma final, la transacción devuelve no-cero, no emite
  `Completed`, conserva el candidato publicado en `failed.driver` y repone el
  anterior mediante copia desde `previous.driver`: no consume ese respaldo.
- La recuperación intenta su propio reinicio y verificación. Si falla el
  filesystem, el reinicio o la consulta, informa `RECOVERY UNVERIFIED`, conserva
  evidencia/respaldos y no afirma recuperación operativa. Logs de comprobación
  principal y recuperación separados: `transaction-hal-state.log` y
  `recovery-hal-state.log`; stdout/stderr del operador deben conservarse también.
- Restaurar explícitamente usa una copia del respaldo gestionado y respalda a su
  vez la versión desplazada. Desinstalar no destruye el driver.
- Guardas Darwin arm64, root, rutas sin enlaces, historial 0700, candado, firma y
  cuarentena permanecen. PATH de herramientas fijado al sistema. PKG directo
  sigue desactivado. No se eliminan atributos, respaldos ni candados huérfanos.

El estado anterior recuperado se infiere de la existencia del bundle antes de
la transacción; no es un snapshot de audio/defaults. La presencia de UID tras
`killall` tampoco identifica criptográficamente la versión cargada ni demuestra
reinicio de todos los servicios de audio, estabilidad, contenido audible, hardware
físico o aplicaciones de llamada. SIGKILL, disco lleno y caída de energía siguen
fuera de la garantía de traps. Ante recuperación no verificada, inspección manual
antes de cualquier reintento: no forzar ni borrar otros HAL.

## Pruebas reproducibles, sin Mac

```sh
python3 callaudio/tests/test_installer.py --evidence build/installer-sandbox.json
python3 callaudio/tests/test_driver_state.py
```

La primera suite sustituye comandos OS, sonda HAL y raíz `/Library` **solo en
una copia dentro de `build/`**. Prueba reinicio RC7, carga/consulta fallida,
recuperación fallida (filesystem/reinicio/HAL), rechazo de origen/sonda inválida,
cuarentena, symlinks, root/plataforma, candado y restauración gestionada.
La reversa A → B → A → B usa binarios fixture con bytes distintos y compara el
inventario completo de SHA-256 de instalados, respaldos y candidatos fallidos;
no se limita a reinstalar la misma build. La evidencia JSON conserva cada
subproceso, RC, stdout/stderr e inventarios de hashes, aunque el sandbox se borre.

La segunda compila **el C de producción contra una API HAL sintética** y prueba
presentes/vivos, UID faltante, dispositivo muerto, fallback de ocultos, ausencia,
argumentos inválidos y errores de consulta/asignación. No es compilación Apple,
validación ABI, firma real, carga HAL ni prueba de audio.

Para comprobar sensibilidad al defecto histórico, extraer el gestor de la base
a un archivo de evidencia y pasar `--manager-source <archivo>` a la primera suite
con `InstallerTests.test_restart_failure_rolls_back_and_never_completes` y
`InstallerTests.test_load_failure_rolls_back_and_retains_failure_log`.
Se exige que fallen **porque la base devuelve 0 y emite `Completed install`**,
no por un fallo del arnés.

## Aceptación nativa pendiente

En laboratorio Apple expresamente autorizado y con capacidad real: construir el
SHA exacto, verificar Bash 3.2/firma/ZIP con guardas activas; probar carga y retirada
por UID, reversa entre dos versiones nativas distinguibles con hashes, fallo de
reinicio/carga y recuperación; comprobar hardware/defaults y audio después.
No instalar en la Mac del usuario ni mezclar esta fuente con paquetes históricos.
Los fallos UI/E2E históricos no se resuelven ni se dispensan con este cambio.
