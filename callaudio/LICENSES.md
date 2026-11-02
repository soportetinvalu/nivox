# Licencias · audio de llamadas

- Código propio en `callaudio/` (núcleo C, driver C++, motor y herramientas macOS, scripts): MIT, ver `LICENSE` raíz y cabeceras SPDX.
- **libASPL 3.1.2** (gavv/libASPL): MIT. Tarball oficial de la etiqueta `v3.1.2` en `vendor/libASPL-3.1.2.tar.gz`, SHA-256 `428f1ed5cecea725ac4e2db3ab65889a72d133cff8d23ac21b07bf6e2f0561e4` (verificado antes de extraer; fijar el hash no es una firma del autor). Fuente: <https://github.com/gavv/libASPL/archive/refs/tags/v3.1.2.tar.gz>. Incluye partes derivadas de código de ejemplo de Apple bajo `LICENSE.apple2012` y `LICENSE.apple2020`, de tipo MIT. El bundle del driver copia los tres archivos de licencia en `Contents/Resources/`.
- **SpeexDSP 1.2.1**: BSD, el mismo tarball fijado que ya usa Nivox (`LICENSES.md` raíz). Se usan el preprocesado (denoise) y el resampler.
- No se usa ni se ha consultado código de Neep, BlackHole (GPL-3.0), Background Music (GPL-2.0) ni eqMac. Tampoco se incluyen pesos ni modelos.
- Los identificadores `local.christian.nivox.audio-driver` y los UID `nivox.*.v1` son propios. «Nivox» como marca y su icono siguen la nota de `App/Assets/BRANDING.md`: la titularidad independiente está pendiente.

Este inventario no es asesoramiento jurídico.
