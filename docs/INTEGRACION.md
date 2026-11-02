# Integración revisada, sin modificar entradas

Se creó una carpeta nueva de entrega. Los árboles originales de branding y SpeexDSP, sus builds, paquetes y evidencias se mantienen sin sobrescribir. Los inventarios completos SHA-256 antes/después y diffs detallados quedan **solo en `build/evidence/`**, no se exportan al repositorio público.

## Selección de entradas

- Motor, cola C, ABI, pruebas originales de motor/integración y smoke Swift: variante SpeexDSP. Se mantienen byte a byte motor, cola, cabeceras y señales/aserciones; no se sustituye la ruta por silencio ni un mock.
- Identidad: textos Nivox, estructura principal `NivoxApp`, tipografía/layout SwiftUI y ejecutable Nivox. Comparación del Swift de branding con SpeexDSP revisada: cambios funcionales de motor/latencia/avisos, no rediseño visual. Se retiró de la entrega la rama muerta `CI_NO_MODEL`; no se exporta el motor ausente de la otra receta.
- PNG original, ICNS, manifiesto y validador sin dependencias: copia exacta del branding vigente. No se regeneró ni retocó el icono. PNG SHA-256 `c493e3898244e0b27fe0e5ce4d4215c1908fc83421314c892cdd3ed8b8955a2e`; ICNS SHA-256 `9511557b16733f118e58aee0c6a6f4c4796de26549a57a08c5ac70be2881c2c5`.
- Se mantiene deliberadamente ID de variante `local.christian.nivox.speex.experimental` para no confundir TCC con el prototipo anterior; propósito de micrófono actualizado a SpeexDSP. Audio-input entitlement conservado. No se heredan permisos demostrados, porque no los hay.
- `build.py` sigue enlazando solo SpeexDSP estático; añade validación obligatoria de icono/identidad/entitlements y copia del original/manifiesto. El bundle futuro se llama `Nivox.app`; el ZIP `Nivox-arm64-experimental.zip` se coteja tras extraer, incluido icono obligatorio.
- Workflow manual con permisos mínimos, acciones fijadas y guarda real arm64. No hay credenciales de firma, publicación ni Release. Tabla oficial de runners consultada por HTTPS: <https://docs.github.com/en/actions/reference/runners/github-hosted-runners>; `macos-26` figura arm64, sin sustituir la verificación `uname` durante el build.

## Exportación pública mínima

`ci/export_sources.py` exporta solo una allowlist explícita y rechaza salidas existentes/fuera de `build/`, entradas enlazadas simbólicamente, ausentes o fuera de la raíz. Tar determinista y verificación de conjunto exacto de entradas y de cada byte/hash. Incluye fuentes propias, arte aportado solicitado (derechos separados de MIT), licencias, SpeexDSP fuente fijado, pruebas sintéticas y documentación nueva sin datos de canal.

Se excluyen árbol Git, builds, cachés, históricos de integración, snapshots con rutas internas, comparaciones históricas y sus audios, RNNoise/pesos, fuentes Neep, credenciales y binarios de desarrollo. El tar upstream oficial mantiene sus avisos propios. El original Nivox se conserva por petición expresa; la titularidad independiente debe confirmarse antes de publicar. Solo este tar es candidato a revisión pública, no el directorio `build/` completo ni el workspace padre.

## Verificación y límites

Las pruebas ejecutan el DSP real en Linux, integración hasta 15 s y sanitizadores sobre el arnés C. Los controles de parser Mach-O usan texto sintético y se etiquetan como tales. Rechazar Mac en Linux acredita la guarda, no un build Apple. La extracción independiente y reconstrucción acreditan autocontención de fuentes; no autorizan publicación ni acreditan calidad en llamadas.
