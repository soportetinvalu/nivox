# A/B: entrada explícita y niveles acotados — candidata sin validar en Mac

**No declarar resuelto el problema físico hasta probar esta candidata en el Mac real.**
No hay aislamiento de hablante ni corrección demostrada del Hollyland. No se tocó el Mac.

## Cambios de la ruta

- Selector A/B independiente y explícito por UID, con canal manual (1, 2, …), sin elegir por energía. No sigue el selector de Llamadas ni se cambia la entrada predeterminada del sistema. Se enumeran entradas vivas y físicas; virtuales, agregadas, ocultas y UID `nivox.*` quedan fuera.
- `AudioUnitSetProperty(CurrentDevice)` actúa sólo sobre el input/output AudioUnit del motor local, con lectura posterior del ID y UID. Esto necesita verificación con Apple SDK y hardware; una prueba C Linux no acredita esta API.
- Se conserva Float32 planar a 48 kHz: tasas distintas se rechazan, **no se cambia la tasa del accesorio ni se finge remuestreo**. Una entrada de 44,1/16/24 kHz sigue fuera de alcance. UID, nombre, canal, tasa HAL y formato del bus se muestran por toma.
- Captura sin monitorización. Reproducción detenida antes de capturar. Huecos/repeticiones de sample time invalidan la toma, además de las guardas históricas de cola/formato/rango. Desconexión/cambio detectado descarta, no conmuta silenciosamente a otro micrófono.
- La captura fuente permanece intacta. **Ganancia de copias A/B** explícita 0…24 dB, inicial 0 dB, no ganancia hardware; ambas copias usan el mismo factor constante. Se acota por el mayor pico de las dos al margen −1 dBFS. No se normaliza ruido ni silencio, no AGC ni compresor. Si se pide +24 y no cabe, se aplica menos y se avisa.
- Filtrado: mezcla `0.75 * raw + 0.25 * Speex` por defecto; deslizador 0…25 %, 0=bypass. Speex sigue −15 dB interno; no se modifica Llamadas. Correlación global negativa o fuente digitalmente nula omite el filtro en toda la toma, sin conmutación por bloques.
- Con correlación no negativa, la norma RMS global filtrada no cae por debajo de 0,75 de la fuente antes del factor común. **Esto no garantiza cada palabra, ventana ni inteligibilidad** y se mantiene ese límite en la interfaz. La mezcla conserva también ruido; no equivale a supresión −15 dB ni a aislamiento de voces.
- Volumen del nodo reproductor explícito **0…1**, inicial 0,35, nunca >1. Cambiar nivel detiene el sonido: debe pulsarse Escuchar otra vez. Confirmación manual de auriculares y volumen bajo, más rechazo de salida virtual/agregada. No puede certificar presión sonora segura ni detectar físicamente auriculares. No hay salida durante captura ni audio a llamadas.
- Exportar original/filtrado guarda las copias con ganancia común (no el volumen del reproductor); **Exportar captura fuente** conserva el tap sin ganancia. No se sobrescriben archivos ni se sube audio. Las medidas de pico/RMS son de señal, no de voz/SNR.

## Validación reproducible

```sh
python3 -B build.py --target linux --out build/ab-safe-linux
python3 -B build.py --target linux --sanitize --out build/ab-safe-sanitize
python3 -B tests/test_delivery.py --out build/ab-safe-delivery
# Opcional: corpus real YA disponible localmente, sin red. Nunca usa grabaciones privadas en CI.
python3 -B tests/test_ab_levels.py --library build/ab-safe-linux/libNoiseProbe.so \
  --out build/ab-safe-speech/report.json --speech-dir /ruta/local/al/corpus
```

`test_ab_levels.py`: identidad, ganancia compartida, límites, silencio, antífase,
entrada muy débil, bypass, rechazo de NaN/Inf/rango/parámetros, timestamps, motor C real,
mutantes compilados sin cota de pico y salida muda. La comprobación Swift es **sólo contrato
de fuente**, no compilación. En CI sin corpus, el caso real se omite explícitamente.
El workflow manual integrado compila en macos-26 arm64 y conserva reportes de niveles;
no instala driver ni captura micrófono. No lanzarlo si el ejecutor o el presupuesto lo bloquea.

## Aceptación pendiente en Mac M4 / macOS 26.5.2

1. Tras compilación Apple autorizada, abrir candidata sin instalar driver, con auriculares y salida baja.
2. En A/B seleccionar explícitamente **Wireless microphone** del Hollyland y el canal que realmente tenga voz. No cambiar Ajustes globales. Anotar ruta mostrada y mantener ganancia en 0 dB inicialmente.
3. Grabar 2 s de silencio + 3 s de voz. Ver RMS/pico. Si bajo, comparar canal correcto/colocación; sólo después aumentar gradualmente ganancia de copias, que aumenta también ruido.
4. Escuchar original y filtrado con el mismo volumen; comparar filtro 0 % y 25 %. Exportar localmente captura fuente y ambas copias si hace falta diagnosticar. No enviar a servicios externos. Comparar con QuickTime con la misma entrada física si sigue baja la fuente.
5. Contrapruebas: canal sin voz no debe confundirse con el otro; desconectar entrada debe descartar sin fallback; no escuchar mientras graba; cambiar volumen debe detener; ganancia máxima no debe superar pico −1 dBFS en los WAV copia. Confirmar que entrada/salida/tasa/volumen globales quedaron idénticos.

No atribuir el zumbido histórico de ~60 Hz al Hollyland: la identidad de entrada de esa toma no está demostrada. Las nuevas pruebas descritas por el usuario no estaban disponibles como nuevos archivos en esta carpeta; la evaluación local usa habla pública previamente conservada y la evidencia histórica separada.
