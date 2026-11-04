# Corrección transaccional del instalador HAL

## Alcance y estado

Corrección proporcionada de los defectos auditados en `fca9f9e`: señales durante
la reversa, sonda bloqueada, copia de recuperación fallida y aceptación falsa de
una actualización basada únicamente en los mismos UID. No cambia UI, DSP,
umbrales E2E ni firma. No instala nada en la Mac del usuario ni publica Releases.

**Verificación local: Linux con shell real en sandbox y C productivo contra una
API HAL sintética. No es compilación con SDK Apple, firma real ni aceptación
HAL/audio.** La ejecución anterior `41762825876` de `fca9f9e` terminó con Linux
verde y fallos Mac (UI y E2E); no acredita esta candidata. Una CI nueva puede
construir/probar el SHA nuevo en un runner GitHub-hosted efímero; su resultado
no se anticipa ni dispensa los fallos anteriores.

## Sustituciones bloqueadas, sin inventar identidad

- `install` y `restore` **rechazan una ruta instalada existente**, incluso si
  candidato y respaldo tienen firma válida o parecen ser la misma versión.
  Mensaje: `Replacement refused: loaded driver version cannot be verified;
  installed driver was not changed`.
- El rechazo ocurre antes de crear historial/candado, copiar, verificar firmas,
  reiniciar CoreAudio o ejecutar la sonda. Se repite la guarda bajo candado antes
  de desplazar/publicar, por si otro instalador terminó desde el preflight.
- Los cuatro UID estables y `DeviceIsAlive` **no identifican el binario cargado**;
  `killall` es asíncrono y puede dejar consultar temporalmente el daemon anterior.
  No se añade una propiedad HAL inventada ni se compara una versión del disco
  como si fuera la versión cargada. Actualización/downgrade in situ quedan
  deshabilitados hasta disponer de una prueba de identidad real revisada.
- La instalación inicial y restauración gestionada a una **ruta ausente** siguen
  disponibles; desinstalar conserva un respaldo. No se automatiza desinstalar →
  instalar como atajo para actualizar. La CI retira y restaura únicamente la
  misma build para probar esa operación, no una migración entre versiones.

## Contrato acotado de presencia y plazo

- `install`/`restore` a ruta ausente exigen reinicio RC0 y los cuatro UID
  `nivox.microphone.v1`, `nivox.mic-feed.v1`, `nivox.speaker.v1`,
  `nivox.speaker-return.v1` presentes/vivos. `uninstall` exige los cuatro ausentes.
  Un error de consulta nunca demuestra ausencia. Un proceso nuevo por consulta
  evita conservar objetos HAL del cliente, pero no prueba identidad del servidor.
- `nv-driver-state` sigue siendo de solo lectura, sin streams, captura, defaults
  ni solicitud TCC. Debe viajar firmado en el paquete completo; no mezclar esta
  fuente con una sonda histórica. Se rechaza helper symlink/cuarentena y se copia
  a una transacción root-only con permisos restringidos antes de usarlo.
- La sonda arma `alarm(5)` **antes de cualquier llamada HAL**. El manejador usa
  solo `write`/`_exit(124)`, incluso con una consulta IPC bloqueada. RC124 aborta
  la fase inmediatamente y provoca reversa; no se repite 40 veces una sonda
  colgada. No depende de una utilidad `timeout` ausente en macOS.
- El polling tiene además presupuesto transcurrido de 40 s (`SECONDS`) y máximo
  de 40 consultas; la última consulta iniciada puede usar hasta 5 s adicionales.
  La reversa tiene su propio presupuesto. Esto no es un plazo global de la
  transacción: copia, firma, reinicio, arranque previo a `main`, fallos del kernel
  o planificación no tienen aquí un watchdog independiente. No se promete una
  duración máxima de interrupción del audio.

## Reversa e interrupciones

- El trap EXIT desactiva solo EXIT e **ignora HUP/INT/TERM** hasta terminar la
  reversa y liberar el candado. Los hijos de recuperación heredan esa disposición:
  otra señal al shell o al grupo de primer plano no mata la copia ni silencia
  los avisos finales. Se conserva el RC original no-cero; ninguna reversa emite
  `Completed`. Marcar intención antes de mover el original evita la ventana entre
  el `mv` y la actualización del estado del shell.
- Normalmente se copia `previous.driver` a `recovery.driver` y se publica con
  renombrado, conservando el respaldo original. Ante fallo de copia **o de su
  publicación**, se intenta rescatar el original por renombrado directo al HAL,
  sin necesitar espacio para una segunda copia y sin publicar el parcial.
- Si funciona ese rescate, **se consume `previous.driver`**: los bytes originales
  están ahora en la ruta instalada. Se avisa explícitamente
  `original rescued by rename; previous.driver backup consumed`; el parcial queda
  retenido como evidencia, no se borra nada para hacer sitio.
- Si también falla ese renombrado, se conserva el respaldo y se informa
  `RECOVERY UNVERIFIED`. La ruta instalada puede quedar ausente; no se afirma
  recuperación. Tampoco se afirma si falla el reinicio o la consulta posterior.
- Logs de fase principal y reversa separados (`transaction-hal-state.log`,
  `recovery-hal-state.log`). Conservar stdout/stderr completos del operador.
  Que se repongan bytes y presencia **no prueba audio sano, defaults, dispositivos
  físicos ni versión cargada**. SIGKILL, pérdida de energía y fallo de disco que
  impida también renombrar siguen fuera de la garantía de traps. Ante candado
  huérfano/recuperación no verificada: inspección manual, no borrado automático.

Guardas Darwin arm64/root/rutas, historial 0700, candado, PATH de herramientas
fijo, firma y cuarentena permanecen. PKG directo y purga automática desactivados.

## Pruebas reproducibles, sin Mac

```sh
python3 callaudio/tests/test_installer.py --evidence build/installer-sandbox.json
python3 callaudio/tests/test_driver_state.py --evidence build/driver-state-synthetic.json
bash -n callaudio/installer/manage-driver.sh
```

- **25 pruebas de gestor**: primer install; rechazo sin mutación de install y
  restore, incluido respaldo válido; retirada/restauración de la misma versión;
  reinicio/carga/firma fallidos; copia parcial por ENOSPC; fallo de publicar la
  copia; rescate correcto y rescate fallido; RC124; presupuesto temporal; señales
  HUP/INT/TERM repetidas al padre y al grupo durante copia; doble Ctrl-C durante
  consulta principal/reversa; señal inmediatamente después de mover el original;
  plataforma/root/rutas/cuarentena/helper/candado. Inventarios SHA-256 completos.
- **6 pruebas de sonda C**, incluyendo bloqueos infinitos en consulta de servicio,
  UID y estado vivo: el proceso real sale con RC124 y mensaje a los cinco segundos.
  HAL/SDK son sintéticos; el temporizador y el proceso sí son reales.
- Evidencia local: `build/installer-audit-fixes-final.json`,
  `build/driver-state-audit-fixes.json`; la CI conserva los JSON equivalentes
  mediante upload `always()`.

### Sensibilidad contra la base auditada

```sh
git show fca9f9e:callaudio/installer/manage-driver.sh > build/manager-fca9f9e.sh
python3 callaudio/tests/test_installer.py --manager-source build/manager-fca9f9e.sh \
  --evidence build/installer-audit-counterprobes.json \
  InstallerTests.test_update_and_restore_refused_before_any_system_mutation \
  InstallerTests.test_failed_recovery_copy_rescues_original_without_publishing_partial \
  InstallerTests.test_second_ctrl_c_during_recovery_probe_does_not_abort_cleanup

git show fca9f9e:callaudio/installer/nv_driver_state.c > build/nv_driver_state-fca9f9e.c
python3 callaudio/tests/test_driver_state.py --probe-source build/nv_driver_state-fca9f9e.c \
  --evidence build/driver-state-audit-counterprobe.json \
  DriverStateTests.test_blocked_hal_calls_have_real_process_deadline
```

Resultados esperados y observados: la base anuncia `Completed install` bajo el
modelo de UID persistentes; deja la ruta instalada ausente si falla la copia y
conserva candado huérfano tras doble Ctrl-C. El subcaso de restore con origen no
gestionado falla por una guarda distinta, no demuestra por sí solo un falso
restore: la candidata lo prueba además con respaldo gestionado válido.
La sonda histórica supera el timeout externo de 8 s en los tres puntos HAL;
las tres `TimeoutExpired` son la contraprueba esperada, no una aceptación verde.

## Aceptación nativa pendiente

Exige SDK Apple/Bash 3.2/ZIP/firma del SHA exacto, retirada y restauración real,
pruebas de errores y audio/hardware/defaults después. Una firma ad hoc correcta
no demuestra carga. No instalar en la Mac del usuario con estos resultados ni
ofrecer actualización física: su identidad sigue sin poder acreditarse. Los
fallos UI/E2E históricos quedan abiertos y no se relajan con este cambio.
