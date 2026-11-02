# Licencias y procedencia

- Adaptador C, cola SPSC, aplicación SwiftUI, scripts y arneses propios: MIT, `LICENSE` y cabeceras SPDX. Se conservan identificadores internos `NoiseProbe`, `CNoise`, `np_*`, `cn_*` y `christian_noise.h` por ABI/trazabilidad; no incorporan RNNoise.
- SpeexDSP **1.2.1**, fuentes oficiales Xiph: licencia BSD según `COPYING` y avisos individuales. Se redistribuye el archivo oficial íntegro, no una biblioteca binaria del sistema: <https://downloads.xiph.org/releases/speex/speexdsp-1.2.1.tar.gz>.
- `vendor/speexdsp-1.2.1.tar.gz`, SHA-256 `8c777343e4a6399569c72abc38a95b24db56882c83dbdb6c6424a5f4aeb54d3d`. La fijación por hash no es firma criptográfica del autor. El build verifica antes de extraer y rechaza enlaces/traversal en el tar.
- Build flotante, estático, sin SSE/NEON, FFTW o Homebrew, sin modelos ni red neuronal. La app futura conserva `SpeexDSP-COPYING.txt`, `Own-code-MIT.txt`, este documento y los avisos de fuentes/cabeceras en `upstream-notices/`.
- Icono Nivox original y derivado ICNS: material aportado por el usuario para esta identidad e incluido por petición expresa en esta entrega. **Excluido de la licencia MIT**; no se otorga una licencia pública genérica sobre el arte. Procedencia, dimensiones y hashes en `App/Assets/BRANDING.md` e `icon-manifest.json`. Confirmación independiente de autoría/titularidad/redistribución pública pendiente; no presentar el paquete como jurídicamente autorizado de forma independiente.
- No se incluyen fuentes Neep, RNNoise, pesos, drivers, binarios propietarios, tipografías externas, audios privados ni credenciales. Las señales de prueba se generan sintéticamente.

Este inventario no equivale a asesoramiento jurídico ni certificación de marcas.
