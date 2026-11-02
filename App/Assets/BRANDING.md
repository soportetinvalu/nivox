# Identidad Nivox

Nombre solicitado por Christian: **Nivox**. Icono aportado por el usuario; no se consultaron ni utilizaron fuentes, imágenes o código Neep.

- `Nivox-original.png`: copia byte a byte del PNG recibido, 1254 × 1254, sin recorte, eliminación del fondo, retoques ni estiramiento.
- `Nivox.icns`: once representaciones PNG, entre 16 y 1024 píxeles, incluidas variantes Retina. Reducción proporcional RGBA mediante LANCZOS; conserva todo el diseño original. Manifest de tamaños y SHA-256 en `icon-manifest.json`.
- Conversión histórica: reducción proporcional RGBA mediante Pillow/LANCZOS; esta entrega conserva los bytes ya validados, sin regenerarlos. `python3 ci/icon_assets.py` valida original, ICNS y manifiesto mediante biblioteca estándar; el workflow no necesita Pillow.
- Tipografía: estilos de sistema SwiftUI (`largeTitle`, `footnote`, `monospacedDigit`); no se incluyen ni descargan fuentes tipográficas.
- La imagen fue proporcionada para esta identidad. No se han verificado de manera independiente autoría, marca o derechos de redistribución del arte. La licencia MIT del código no se extiende a esta imagen. Confirmar titularidad antes de publicar.

`CFBundleIconFile=Nivox.icns`; la receta integrada exige el recurso, lo valida y lo copia antes de firmar. Esta preparación Linux no acredita renderizado de Finder/Dock ni aceptación por macOS.

Los nombres internos `NoiseProbe`, `CNoise`, `np_*`, `cn_*`, las cabeceras `christian_noise*.h` y el archivo histórico `NoiseProbeApp.swift` se conservan como identificadores de módulo/ABI y trazabilidad, no como nombre de producto. La estructura Swift principal y los nombres visibles, ejecutable, bundle y artefactos son Nivox. Los identificadores `local.christian.nivox.*` son locales experimentales, no un dominio registrado ni identidad Developer ID.
