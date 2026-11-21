# Changelog

Alle noemenswaardige wijzigingen worden in dit bestand gedocumenteerd,
volgens [Keep a Changelog](https://keepachangelog.com/) en SemVer (één bron:
`version` in `pyproject.toml`).

## [0.2.0] - 2026-11-20

### Toegevoegd
- Optionele HTTP-modus (`gaspedaal-mcp --http`) voor clients die alleen externe
  servers kennen, zoals ChatGPT: alleen op 127.0.0.1, geheim pad `/<token>/mcp`
  (`GASPEDAAL_MCP_HTTP_TOKEN`), Host-allowlist via `--public-host`, Origin alleen
  localhost, hoogstens 32 sessies en 64 kB per verzoek, geen toegangslog.
- `--help`; de server meldt zijn eigen versie in plaats van die van de SDK.
- Licentie MIT-0 (`LICENSE`) en pakketgegevens (auteur, trefwoorden, URL's).

### Gewijzigd (sinds 0.1.0, uit de code- en security review)
- Cachesleutel bevat de versie; een gedeelde of andermans cachemap wordt geweigerd.
- Tekstvelden ≤ 200 tekens; offer-URL's alleen naar de doorverwijzer van Gaspedaal.
- Grenzen: deadline 60 s per verzoek, 5 redirects, lijsten ≤ 20 waarden,
  getallen ≤ 10.000.000. Geen zoek-URL's (met postcode) meer in de logs.
- README: installatie voor elke client, en het hoofdstuk *Voor taalmodellen*
  (vervangt `llms.txt`).

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