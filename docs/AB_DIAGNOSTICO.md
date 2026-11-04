# Diagnóstico A/B del commit 3be147f (sin cambiar audio en vivo)

> Documento histórico de la corrección WAV. La ruta A/B candidata posterior sí cambia
> selección y copias de audio: ver `AB_RUTA_SEGURA.md`. Las afirmaciones siguientes
> sobre Swift/C/volumen describen exclusivamente el diagnóstico previo, no la candidata actual.

Esta candidata **no corrige todavía el zumbido real ni implementa aislamiento de hablante**.
No cambia Swift, C, SpeexDSP, volumen, dispositivos, tasas, permisos, driver ni instaladores.

## Corrección acotada y demostrada

`AudioProbe.export` usa el formato del buffer Float32/mono/48 kHz. La CLI histórica
solo leía PCM16 (`wave.Error: unknown format: 3` con un WAV IEEE Float32 válido).
Ahora admite PCM16 y Float32, incluidos subtipos WAVE_FORMAT_EXTENSIBLE. No
remuestrea ni mezcla canales; la CLI del motor sigue exigiendo mono/48 kHz y
muestras finitas normalizadas. Su salida sigue siendo PCM16, no Float32.

## Herramienta sin dependencias adicionales

Desde esta carpeta de fuentes, sobre una **misma captura** exportada con ambos botones:

```sh
python3 -B python/diagnose_ab.py prueba-original.wav prueba-limpia.wav > ab-completo.json
python3 -B python/diagnose_ab.py prueba-original.wav prueba-limpia.wav --start 0 --end 2 > ab-silencio.json
```

Solo lee archivos explícitos, imprime JSON y no captura ni reproduce. Mide pico,
RMS, componente continua, ceros, proximidad a escala completa y proyecciones
puntuales a 50/60 Hz y sus armónicos. `null` en niveles significa amplitud cero.
Las proyecciones NO prueban origen eléctrico, dispositivo, hablante ni causa;
frecuencias desviadas y ventanas no enteras tienen fuga espectral. El cambio
RMS entre A/B NO es SNR ni inteligibilidad y no identifica hablantes.

## Pruebas reproducibles

En Linux, con compilador existente, sin instalación ni red:

```sh
python3 -B build.py --target linux --out build/ab-nuevo
```

Salida nueva obligatoria. Ejecuta pruebas C reales, 21 tests históricos del motor,
14 combinaciones de integración y 10 tests focales de WAV/ruta A/B. Se puede
reproducir también el rechazo histórico del Float32, sin modificar esa base:

```sh
python3 -B tests/test_ab_diagnosis.py --library build/ab-nuevo/libNoiseProbe.so \
  --baseline-root /ruta/a/fuentes-exactas-3be147f --out build/ab-comparacion
```

Las fixtures contienen silencio, tonos y DC sintéticos, **no voz de Christian**.
Prueban copia exacta del original, ausencia de señal generada desde silencio en
esa costura C, conservación de Float32 y rechazo de canales/tasas inválidos. El
caso estéreo muestra que escoger canal 1 pierde una señal que solo exista en 2;
no demuestra que la entrada real de Christian sea estéreo ni autoriza cambiarla.

## Prueba física mínima, sin recompilar ni instalar

1. Sin llamadas, volumen bajo y auriculares. Anotar entrada/salida predeterminadas
   de macOS y sus conexiones (USB/Bluetooth/analógico/HDMI), número de canales y
   tasa visible, sin cambiar nada todavía. A/B NO usa la selección de Llamadas.
2. Escuchar brevemente «Tono suave 1 s». Si ya zumba, parar; ese tono no pasa por
   captura/Speex y queda por investigar generación/grafo/salida/reproducción.
3. Grabar 10 s: 0–2 silencio; 2–5 voz propia; 5–8 audio hablado no privado del
   celular; 8–10 silencio. Una sola toma. Exportar original y limpio sin editar,
   convertir, normalizar ni enviarlos por mensajería que comprima.
4. Escuchar el original exportado en un reproductor local, a volumen bajo y por
   la misma salida. Anotar si cambia el zumbido respecto al botón de Nivox.
5. Si el original exportado también zumba, comparar una toma de control de
   Grabación de audio en QuickTime con **la misma entrada**. No es la misma ruta
   de cliente, así que una diferencia acota el problema, pero no prueba hardware.

No elegir una entrada virtual para esta prueba de control. Si ya era la entrada
predeterminada, conservar ese dato primero y luego elegir explícitamente una
física en Ajustes, solo con decisión del usuario. No cambiar la tasa para evitar
la guarda: 44,1 kHz/formatos distintos deben ser rechazados, no reinterpretados.
Ante zumbido fuerte, no subir volumen o ganancia para compensar la voz baja.

Datos mínimos para avanzar: esos dos WAV de la misma toma, nombres/modelos de
entrada y salida/conexiones, captura de Audio MIDI con tasa/canales, resultado
del tono y del reproductor externo. Solo si lo anterior no acota el problema se
necesita la toma de control. No hacen falta cuenta Apple, sudo, sysdiagnose,
reinicios de servicios, credenciales ni quitar cuarentena.
