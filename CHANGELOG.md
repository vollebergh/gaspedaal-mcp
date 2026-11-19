# Changelog

Alle noemenswaardige wijzigingen worden in dit bestand gedocumenteerd,
volgens [Keep a Changelog](https://keepachangelog.com/) en SemVer (één bron:
`version` in `pyproject.toml`).

## [0.1.0] - 2026-11-19

### Toegevoegd
- Eerste release: ophalende MCP-server voor www.gaspedaal.nl.
- Zes tools: `list_brands`, `list_models`, `list_filters`, `search`,
  `get_listing`, `market_summary`.
- Host-allowlist (alleen `https://www.gaspedaal.nl`), throttle (≥ 1 s),
  groottelimiet (8 MB), Retry-After-afhandeling voor 429/503.
- Schijfcache (grootte- en leeftijdsgebonden, env-instelbaar).
- Flight-payload-parser met RSC-waardenormalisatie.
- Synthetische test-fixtures en unittests; live-tests achter `-m live`.

### Geverifieerd (live)
- `/toyota/rav4?srt=pr-a` geeft resultaten met oplopende prijzen.
- `list_models("toyota")` bevat `rav4`.
- De onbevestigde param `tmin` (trekgewicht) wordt door de site herkend
  (`total` daalt strikt). `gmin` en `cmin` zijn niet live bevestigd.