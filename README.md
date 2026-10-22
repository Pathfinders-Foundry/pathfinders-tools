# pathfinders-tools

Herramientas del pipeline de Pathfinders Foundry. Se instalan como paquete y
exponen estos comandos (siempre vía `uv run --with-requirements requirements.txt`).

| Comando | Qué hace |
|---|---|
| `pathfinders-fix-trial-subset` | Recorta los Trial al glyph set exacto (sin cierre GSUB) |
| `pathfinders-ttf-to-web` | WOFF y WOFF2 desde las TTF |
| `pathfinders-check-win-descent` | Comprueba usWinDescent >= \|yMin\| (no corrige) |
| `pathfinders-check-render` | Render cross-format: OTF de referencia frente a TTF/WOFF/WOFF2 |
| `pathfinders-hint-plan` | Regex de instancias hinted/unhinted de un designspace o source (para `fontmake -i`) |
| `pathfinders-check-hinting` | Detecta trazos que ttfautohint colapsa y verifica el plan de hinting |

## TTF sin hinting para estilos con hairlines (0.4.0)

**Problema.** ttfautohint deja a altura 0 los trazos horizontales de **≤ 8 u a 1000 UPM**
(9 u ya sobreviven), a cualquier ppem hasta su hinting-limit (200). En pesos finos o
stencil desaparecen guiones, barras y serifas finas en los renderizadores que ejecutan
las instrucciones TrueType (Windows). Ninguna opción de ttfautohint lo evita (probadas
`-a nnn/sss/qqq`, `-x 0`, `-p`, `-s`, `-H`, `-W`, `-l/-r`, control file, `-f none -S`;
los deltas del control file solo cubren ppem 6–53). Engordar el trazo a ≥ 9 u es una
decisión de diseño y, hinted, se pinta a ~0,9 px.

**Solución.** Esos estilos se compilan sin `-a`, declarados en `qa/project-spec.toml`:

```toml
[ttf]
unhinted = ["*-Thin*", "*Stencil*"]   # fnmatch sobre el nombre de archivo sin extensión
```

El Makefile pide la regex de cada grupo y compila cada estilo una sola vez:

```make
rx=$$($(UV_RUN) pathfinders-hint-plan --spec qa/project-spec.toml --designspace $$ds --unhinted)
[ -n "$$rx" ] && fontmake -m $$ds -i "$$rx" -o ttf --filter '...' ...      # sin -a
rx=$$($(UV_RUN) pathfinders-hint-plan --spec qa/project-spec.toml --designspace $$ds --hinted)
[ -n "$$rx" ] && fontmake -m $$ds -i "$$rx" -o ttf --filter '...' ... -a
```

(`--source X.glyphspackage` en lugar de `--designspace` para builds con `fontmake -g`.)

El `gasp` de los unhinted (smoothing sin gridfit) se fija en el source con el custom
parameter de Font Info `GASP Table` = `{65535 = 10;}`; ttfautohint lo sobrescribe a 15 en
los hinted, así que puede ir a nivel de fuente. No hay post-proceso de binarios.

**Cómo decidir qué estilos.** `pathfinders-check-hinting build/otf --suggest` lista, por
estilo, los glifos con trazos horizontales ≤ 8 u (`--max-u`, `--min-len`). Es
conservador: la prueba definitiva es `pathfinders-check-render` sobre la TTF hinted (un
estilo con uno o dos glifos marginales puede seguir hinted). Con
`--spec qa/project-spec.toml --ttf build/ttf` verifica que las TTF declaradas unhinted no
llevan `fpgm`/`prep`/`cvt ` y tienen `gasp = {65535: 10}`, y que las demás sí llevan
instrucciones (FAIL si no).

## pathfinders-check-render

Compara, por estilo, la OTF (referencia) con TTF, WOFF y WOFF2: shaping (HarfBuzz) y
raster (FreeType) con y sin hinting. Ver el docstring de `src/pathfinders_tools/render.py`.

- **Sin hinting @128 ppem** (u a 1000 UPM): FAIL si borde > 1,5 u, o si desplazamiento del
  centroide > 2 u **y** borde > 0,9 u. (0.3.0 usaba centroide > 2 u o borde > 1 u, que en
  hairlines da falsos positivos: el redondeo a enteros mueve el centroide hasta 12 u con
  borde < 0,7 u; bajar el error de cu2qu no cambia nada.) El bug de LSB de v1.000 sigue
  detectándose al 100 % (borde ≥ 1,3 u). Ajustable por proyecto en `[render_crossformat]`
  (`shift_u`, `edge_u`, `edge_with_shift_u`).
- **Con hinting @24 y 72 ppem**: sin cambios. Una TTF sin instrucciones se renderiza con
  `FT_LOAD_NO_AUTOHINT` para que FreeType no la pase por el autohinter.
- WOFF y WOFF2 contra la TTF: idénticas.

## Versiones

- **0.4.0** — `pathfinders-hint-plan`, `pathfinders-check-hinting`; tolerancia sin hinting
  robusta en trazos finos; TTF sin instrucciones sin autohinter en check-render.
- 0.3.0 — check de render cross-format; postproducción mínima.
