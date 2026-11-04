# Corrección transaccional del instalador HAL

## Alcance y estado

Corrección proporcionada de los defectos auditados en `fca9f9e` y de los
contraexperimentos X1/X2b/X4b reproducidos sobre `faa7b6d`: señales durante
la reversa, errores de salida, sonda bloqueada, copia de recuperación fallida y
aceptación falsa de una actualización basada únicamente en los mismos UID.
No cambia UI, DSP,
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
- La sonda arma una alarma **antes de cualquier llamada HAL**: por defecto 5 s,
  o un argumento explícito entero `1..5` que nunca permite desactivarla/ampliarla.
  El gestor pasa `min(5, deadline-SECONDS)`. El manejador usa solo
  `write`/`_exit(124)`, incluso con una consulta IPC bloqueada. RC124 es un fallo
  reintentable solo dentro del plazo de la fase; nunca acredita presencia/ausencia.
  No depende de una utilidad `timeout` ausente en macOS.
- El polling tiene presupuesto transcurrido de 40 s (`SECONDS`) y máximo
  de 40 consultas, sin reiniciar el plazo al reintentar. La última sonda recibe
  solo el tiempo restante, sin los anteriores 5 s adicionales. RC0 llegado al
  vencer el plazo tampoco se acepta. Se conserva cada intento en el log de fase.
  RC1/2 mantienen el polling previo por discrepancia/error de consulta; otros
  errores y señales de la sonda abortan la fase y conservan su RC, no se ignoran.
  `SECONDS` tiene resolución de un segundo: este límite del polling no es una
  garantía de tiempo real ni incluye el coste de lanzar procesos/planificación.
  La reversa tiene su propio presupuesto. Esto no es un plazo global de la
  transacción: copia, firma, reinicio, arranque previo a `main`, fallos del kernel
  o planificación no tienen aquí un watchdog independiente. No se promete una
  duración máxima de interrupción del audio.

## Reversa e interrupciones

- EXIT y HUP/INT/TERM/PIPE llaman directamente al mismo `finish`, con el RC
  como argumento. **Una señal no ejecuta `exit` saltándose un EXIT en curso**,
  incluso si llega antes del primer comando de protección. `finish` ignora
  HUP/INT/TERM/PIPE primero y después desactiva EXIT. Los hijos de recuperación
  heredan esa disposición hasta liberar el candado. No se añade supervisor ni
  otra máquina de estados. Se conserva el RC no-cero de entrada (130 para
  HUP/INT/TERM y 141 para PIPE); ninguna reversa emite `Completed`. Marcar
  intención antes de mover el original protege también la ventana tras `mv`.
- Solo dentro de la limpieza se desactiva `errexit`: las operaciones de
  recuperación conservan sus comprobaciones explícitas. Un aviso que falla con
  ENOSPC/EIO/EPIPE no debe omitir reinicio, verificación ni liberación del candado.
  Ignorar PIPE durante la limpieza evita morir por escribir a un lector cerrado.
  Esto **no garantiza entregar avisos si stderr no admite escrituras**; los
  bundles y logs de sonda que se hayan podido escribir siguen retenidos.
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

- **29 pruebas de gestor** (25 anteriores + 4 regresiones): primer install; rechazo sin mutación de install y
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

### Regresiones adicionales X1/X2b/X4b sobre `faa7b6d`

- Resultado histórico: **29/29 en GNU Bash 3.2.57 y 5.2.21 sobre Linux**; la
  auditoría posterior encontró inestable la fixture de doble Ctrl-C en 3.2
  (véase la corrección FIFO abajo). No acredita estabilidad repetida. `--bash`
  selecciona explícitamente el shell del gestor; los sustitutos OS/HAL siguen
  usando `/bin/bash`. No acredita el binario Bash de Apple ni semántica HAL real.
- X1: ventana ampliada **antes del primer comando de `finish`**, con HUP/INT/TERM,
  más 150 ráfagas TERM sin modificar el gestor por cada shell. El ensayo
  independiente original añade otras 300 ráfagas por shell: cero pérdidas de
  recuperación/candado en ambos lotes. Son muestras, no prueba exhaustiva del
  planificador ni garantía frente a SIGKILL.
- X2b y X4b: `/dev/full` y pipe stderr sin lector, respectivamente. Para cada
  salida se prueban copia normal, rescate por renombrado, reinicio fallido,
  consulta fallida y rescate fallido. Se exigen bytes/copia retenida, RC no-cero,
  ningún `Completed`, candado liberado y reinicio/consulta cuando corresponden.
  `/dev/full` es una fixture Linux; ese método se omite donde no exista.
- Las seis sondas independientes originales X1/X2a/X2b/X2c/X4a/X4b pasan en
  ambos shells, incluido hangup real de PTY. No se cambió el archivo de auditoría.
- Contraprueba aislada contra `faa7b6d`: 3 métodos, 13 fallos de aserción y cero
  errores del arnés en cada shell. X1 deja original desplazado/candado; X2b
  rescata bytes pero omite segundo reinicio/sonda; X4b muere por SIGPIPE.
  La primera ejecución diagnóstica contaminaba los subcasos posteriores con el
  candado fallido anterior. Se conservó y se aisló cada fixture antes de repetir;
  solo los archivos `counterprobe-isolated-*` acreditan cada subcaso por separado.
- Sonda C: **6/6**. Regresiones adicionales sin cambios: compatibilidad de build
  **5/5**, parser de recuperación **8/8**, clasificación E2E **7/7** y orquestación
  diagnóstica UI **13/13**. No se despachó CI ni se instaló o reinició servicio real.
- Evidencia cruda local conservada en `build/installer-interruption-fix/`:
  `bash{32,52}-final.{json,log}`, `independent-bash{32,52}.{json,log}`,
  `counterprobe-isolated-bash{32,52}.{json,log}` y `driver-state.json`.
  Resumen versionado y hashes: [`INSTALLER_INTERRUPTION_EVIDENCE.json`](INSTALLER_INTERRUPTION_EVIDENCE.json).

```sh
python3 callaudio/tests/test_installer.py --bash /ruta/a/bash-3.2 \
  --evidence build/installer-bash32.json
# Sensibilidad: repetir estos tres métodos con --manager-source de faa7b6d.
python3 callaudio/tests/test_installer.py \
  InstallerTests.test_X1_signal_before_first_cleanup_command_still_recovers \
  InstallerTests.test_X2b_stderr_full_does_not_abort_rescue_restart_or_unlock \
  InstallerTests.test_X4b_stderr_epipe_does_not_abort_recovery_or_unlock
```

## Candado y fixture sincronizada después de `6950832`

- El manifiesto de `6950832` conservaba siete hashes antiguos: workflow callaudio,
  README callaudio, gestor, sonda C, ambas pruebas del instalador/sonda y este
  documento. Se regenera desde `collect_payload()` sin ampliar la allowlist ni
  modificar las comprobaciones de igualdad de bytes.
- Se mueve `mkdir "$lock"` después de definir `finish`. HUP/INT/TERM/PIPE se
  ignoran únicamente desde la adquisición hasta activar los traps existentes;
  `mkdir` hereda esa protección. En ese tramo no se desplaza/publica ningún
  bundle. Las señales en ese tramo se descartan, no se difieren. EXIT se instala
  solo tras adquirir el candado: un competidor no puede liberar el ajeno.
  No se añade estado, supervisor ni recuperación automática de candados viejos.
  La reversa y las guardas de sustitución permanecen intactas. SIGKILL y pérdida
  de energía siguen fuera de esta garantía.
- La fixture anterior publicaba `probe-1` antes de lanzar `sleep 10`: SIGINT
  podía llegar entre ambos y dejar un hijo vivo más allá del plazo del arnés.
  Ahora un proceso Python configura SIGINT antes de publicar su marcador y
  bloquea en FIFO; la recuperación conserva SIG_IGN y el test la libera solo
  después del segundo Ctrl-C. No hay espera por duración supuesta ni se amplía
  el plazo. Los sustitutos OS/HAL siguen siendo sintéticos.
- **30/30** pruebas en GNU Bash **3.2.57 y 5.2.21 sobre Linux**, cero errores.
  Incluyen cuatro señales justo después del `mkdir` real, candado competidor,
  reversa, rescate y stderr roto. Doble Ctrl-C: **100/100 repeticiones por shell**.
  Sintaxis `-n` pasa en ambos. Sonda C: **6/6**, sin cambio de fuente.
- Contraprueba de candado contra `6950832`: tres subcasos (HUP/TERM/PIPE) dejan
  candado huérfano en cada shell; SIGINT solo al padre con hijo RC0 no reproduce
  ese fallo. Cero errores del arnés. Doble Ctrl-C contra `fca9f9e`: un fallo por
  candado retenido en cada shell. `faa7b6d` pasa ese método aislado: no detecta
  su ventana distinta de entrada a EXIT (cubierta por X1). Se conservan también
  esos controles positivos, sin presentarlos como contrapruebas rojas.
- El manifiesto viejo falla únicamente `test_source_manifest_matches_payload`
  (24 métodos, un fallo, cero errores). La prueba no se relaja.
- Evidencia cruda local: `build/lock-manifest-fix/`, con JSON y logs por shell,
  controles históricos, repetidor FIFO y prueba de entrega/manifiesto.
  Gestor SHA-256: `ea3f8b21e66de6a4b6693a43c9f4d15e4b6a8c12193c256bfd3b97f151037bbe`.
  Prueba SHA-256: `9c04ded1240403e540c6c686e6d4d3a6bc1681d3322d2b012c6fb669c132ca04`.

```sh
python3 callaudio/tests/test_installer.py --bash /ruta/a/bash-3.2 \
  --evidence build/installer-lock-bash32.json
python3 callaudio/tests/test_installer.py --bash /bin/bash \
  --evidence build/installer-lock-bash5.json
git show 6950832:callaudio/installer/manage-driver.sh > build/manager-6950832.sh
python3 callaudio/tests/test_installer.py --manager-source build/manager-6950832.sh \
  InstallerTests.test_signals_immediately_after_lock_creation_do_not_orphan_it
# Se espera RC1 y fallo concreto por candado retenido, no un error de ejecución.
python3 tests/test_delivery.py --out build/delivery-lock-fix
python3 ci/export_sources.py --out build/public-sources-lock-fix
```

## Aceptación nativa pendiente

### Corrección de la causa temporal evidenciada en run `41770252364`

La recuperación de `coreaudiod` tras reinicios consecutivos puede superar la
alarma individual de 5 s (throttle de launchd observado de unos 10 s). Se cambia
solo la política de RC124, no la definición de presencia ni las guardas de firma,
reemplazo, reinicio o reversa. No se añaden esperas ciegas a la CI.

Pruebas actuales: **36 métodos del gestor en GNU Bash 5.2/Linux**, incluidos
timeout transitorio en install/uninstall/restore, HAL que sigue ausente, timeout
permanente, reversa verificada/no verificada, éxito tardío rechazado, RC126 fatal,
doble Ctrl-C después de timeouts en ambas fases, y los casos anteriores de
HUP/INT/TERM/PIPE, candado y stderr roto. Una prueba usa los **40 s reales** de
producción, con sondeos bloqueantes y pausas reales; exige 39..42 s de reloj
externo incluyendo preparación/reversa, y una última sonda con presupuesto <5 s.
Otras usan plazos de 1..3 s exclusivamente en la copia sandbox para aislar cada
caso. La reversa conserva su plazo separado; no se promete transacción total de
40 s. **7 métodos de sonda C** verifican también argumentos inválidos y alarma
reducida a 1 s en las tres llamadas HAL bloqueadas. HAL/SDK siguen sintéticos.

Evidencia: `build/installer-timeout-final.json`, `build/driver-timeout-fix.json`.
Contraprueba aislada contra `e484669`: install/uninstall/restore rechazan el
timeout transitorio (tres fallos de aserción, cero errores del arnés). El primer
intento de contraprueba encadenaba precondiciones y produjo un error de fixture;
se conserva aparte y no cuenta como prueba de sensibilidad de restore.

El cambio de instalador requiere revisión independiente posterior. Ni estas
pruebas ni una CI aún pendiente autorizan instalar en una Mac real.

Exige SDK Apple/Bash 3.2/ZIP/firma del SHA exacto, retirada y restauración real,
pruebas de errores y audio/hardware/defaults después. Una firma ad hoc correcta
no demuestra carga. No instalar en la Mac del usuario con estos resultados ni
ofrecer actualización física: su identidad sigue sin poder acreditarse. Los
fallos UI/E2E históricos quedan abiertos y no se relajan con este cambio.
