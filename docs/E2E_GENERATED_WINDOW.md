# E2E: ventana del tono crudo delimitada por generación

## Diagnóstico contrastado offline

Base: `dfb398234179b1176e752a46e1d8fbd97a35ff99`. Se reprodujo el
`analyze()` original compilado en C sobre las 21 capturas de los runs
`41772936809` (`9a70858`), `41775575911` (`6e533f7`) y `41778903247`
(`dfb3982`). No se volvió a capturar ni se simuló una ejecución Apple.

Las seis capturas de los bucles crudos conservan el prefijo exacto de la
sinusoide original, incluida su muestra inicial de fase cero. Ese origen,
independiente del final de la señal, más `generated` determina el final
exclusivo. El último valor no nulo coincide con ese límite en las seis;
se usa solo para contrastar el diagnóstico, **no para decidir la ventana**.

| SHA | Captura | Final exclusivo | Cola de cierre | Glitches originales → acotados |
|---|---|---:|---:|---:|
| 9a70858 | mic (01) | 173772 | 3892 | 0 → 0 |
| 9a70858 | speaker (03) | 172324 | 2268 | 0 → 0 |
| 6e533f7 | mic (01) | 171432 | 8280 | 2 → 0 |
| 6e533f7 | speaker (03) | 176392 | 6904 | 1 → 0 |
| dfb3982 | mic (01) | 177600 | 7744 | 1 → 0 |
| dfb3982 | speaker (03) | 170708 | 5932 | 2 → 0 |

Todos los glitches originales están en `end` o `end + 1`, fuera de los
frames emitidos. Al insertar en cada captura seis cortes de ceros
(una muestra, 32 y 480 muestras centrales, 480 cerca del final, y pérdida
terminal de 480 y 4800 muestras), los **36** casos siguen siendo rechazados
por glitches con umbral `0.03f`. No se acepta una cola perdida como cierre.
Los otros 15 registros se reprodujeron con el analizador original; sus
métricas de impulso/ruido no se interpretan como pruebas de tono.

## Corrección acotada

Solo los dos tonos de los bucles **crudos** emiten un marcador de ocho
muestras a partir del frame cero, idéntico en ambos canales. Los valores
son exactamente representables y están fuera del rango del tono de
amplitud 0.25. Sustituye las primeras ocho muestras; no añade frames, no
cambia la fase ni el contador del generador. El motor, la reducción de
ruido, el driver y el instalador no cambian.

`nv_e2e_tone.h` encuentra el marcador capturado y exige exactamente uno.
La ventana analizada es:

```
start = marker_index + 24000   // mismos 0,5 s de asentamiento previos
end   = marker_index + generated  // exclusivo
```

Se incluyen **todos** los frames finales generados; desaparece el recorte
fijo de 0,1 s en estos dos bucles. No se busca la última muestra no nula,
no se concatenan intervalos, no se suaviza ni se reintenta la captura.
Una captura que no llega hasta `end`, marcador ausente/corrupto/duplicado,
o ventana insuficiente falla explícitamente. RMS/frecuencia/segunda
diferencia usan las mismas fórmulas y umbrales de antes; se mantiene la
exigencia de cero glitches. El JSON conserva origen, contador, validez y
límites para auditar el próximo run.

El analizador anterior se conserva para el tono que cruza el motor:
SRC/filtrado y sus retardos no permiten aplicar un marcador crudo exacto.
Este cambio no pretende resolver ventanas de denoise ni certificar
ausencia universal de defectos del producto.

## Regresiones y alcance

```
python3 callaudio/tests/test_e2e_tone.py --out build/e2e-tone-regression
```

Compila el analizador productivo y el callback generador extraído sin
cambios de `nv_e2e.c`, con transporte sintético (sin SDK). Prueba marcador
partido entre callbacks, estéreo idéntico, colas de 0 a 48000 muestras,
latencia inicial, frame final, cortes centrales y terminales, pico,
captura truncada, contador desbordado y marcadores inválidos/silencio.
Comprueba además que el generador no marcado del motor sigue intacto.

Resultado local: **11 positivos y 20 negativos** tanto en release como
con ASan+UBSan. Dos mutantes se rechazan por el oráculo esperado:
recorte fijo de cola (límites incorrectos) y recorte por último valor no
nulo (acepta indebidamente un corte terminal). CI ejecuta la regresión
con compilador Linux y macOS y conserva sus informes; la prueba E2E real
continúa exigiendo sus aserciones originales, sin `continue-on-error`.

La evidencia offline y los hashes de las capturas permanecen en el
cuaderno del canal, `evidence/e2e-generated-window-fix/`. Un verde offline
no acredita el marcador atravesando HAL: falta la ejecución macOS del
nuevo SHA y, por separado, cualquier aceptación física autorizada.
