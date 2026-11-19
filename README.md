# gaspedaal-mcp

Lokale MCP-server (stdio) die het occasion-aanbod van **www.gaspedaal.nl** doorzoekbaar maakt voor een taalmodel (Claude, opencode, …). Gaspedaal is een aggregator van 40+ autosites (AutoTrack, AutoScout24, ANWB, dealersites). De server haalt zoekresultaatpagina's op, leest de ingebedde data en geeft genormaliseerde advertenties terug.

## Onofficieel & voorwaarden

Deze server is **niet** verbonden aan Gaspedaal of AutoScout24. Hun voorwaarden verbieden geautomatiseerd opvragen ("screen scraping") en tekst- en datamining; robots.txt sluit querystring-URL's uit en wordt door deze server **niet** gevolgd. Gebruik de server daarom uitsluitend voor **persoonlijk, niet-commercieel gebruik, met mate**. Het risico ligt bij de gebruiker. De server is wel zuinig (1 verzoek per ~1 s, cache, max 5 pagina's per aanroep) en ontwijkt geen botbescherming.

## Tools

| Tool | Wat het doet |
|---|---|
| `list_brands` | alle merken (slug, naam, aantal) |
| `list_models` | modellen van een merk |
| `list_filters` | beschikbare filterwaarden, stappen en optie-id's |
| `search` | zoek advertenties (1 pagina, max 100 resultaten) |
| `get_listing` | volledige advertentie uit de sessie (geen netwerk) |
| `market_summary` | prijs-/kilometerstatistiek over meerdere pagina's |

## Filters

`filters` is een object van toolsleutel → waarde. Bereiken zijn `{"min": x, "max": y}` (ieder deel optioneel). Lijsten mogen een string of een lijst van strings zijn (case-insensitive).

| Sleutel | Betekenis | Waarden |
|---|---|---|
| `year` | bouwjaar | bereik int |
| `price` | prijs € | bereik int |
| `mileage` | kilometerstand | bereik int |
| `power` | vermogen | bereik int (UI: pk) |
| `seats` | zitplaatsen | bereik int 1–9 |
| `towing_weight` | trekgewicht kg | bereik int |
| `empty_weight` | ledig gewicht kg | bereik int |
| `engine_cc` | motorinhoud cc | bereik int |
| `fuel` | brandstof | BENZINE, DIESEL, HYBRIDE, ELEKTRICITEIT, LPG, AARDGAS, WATERSTOF |
| `transmission` | transmissie | AUTOMATISCH, HANDGESCHAKELD |
| `body` | carrosserie | HATCHBACK, STATIONWAGEN, MPV, SEDAN, SUV, PICK_UP, COUPE, CABRIOLET, BEDRIJFSWAGEN, CAMPER |
| `color` | kleur | BEIGE, BLAUW, BRUIN, GEEL, GOUD, GRIJS, GROEN, ORANJE, PAARS, ROOD, WIT, ZILVER, ZWART |
| `doors` | deuren | int 2–6 |
| `options` | uitrusting | numerieke id's uit `list_filters` |
| `seller` | aanbieder | UNIVERSEEL_AUTOBEDRIJF, PARTICULIER |
| `warranty` | garantie | BOVAG, MERKGARANTIE |
| `nap` | Nationale Auto Pas | bool |
| `days_listed` | sinds (dagen) | 1, 3, 5, 7, 14, 21, 28 |
| `postcode` + `radius_km` | locatie | 4 cijfers (+ 2 letters) en 5–200 km (stappen) |

Sortering (`sort`): `relevance` (standaard), `distance` (vereist postcode), `price_asc`, `price_desc`, `mileage_asc`, `mileage_desc`, `year_asc`, `year_desc`, `newest`.

## Installatie

**Claude Code:**

```bash
claude mcp add --scope user gaspedaal -- uvx --exclude-newer "7 days" --from git+ssh://git@github.com/vollebergh/gaspedaal-mcp@v0.1.0 gaspedaal-mcp
```

**opencode** (`opencode.json`):

```json
{
  "mcp": {
    "gaspedaal": {
      "type": "local",
      "command": ["uvx", "--exclude-newer", "7 days", "--from", "git+ssh://git@github.com/vollebergh/gaspedaal-mcp@v0.1.0", "gaspedaal-mcp"]
    }
  }
}
```

`uvx` negeert `uv.lock` en installeert de actuele versies van de afhankelijkheden. `--exclude-newer "7 days"` slaat alleen versies over die in de afgelopen week zijn uitgebracht: zo blijven de pakketten actueel, maar komt een kwaadaardige release (die meestal binnen enkele dagen wordt ontdekt en ingetrokken) er niet direct in.

## Cache

| Variabele | Standaard | Betekenis |
|---|---|---|
| `GASPEDAAL_MCP_CACHE_DIR` | `~/.cache/gaspedaal-mcp` | cachemap |
| `GASPEDAAL_MCP_CACHE_MB` | 200 | maximale grootte; `0` = uit |
| `GASPEDAAL_MCP_CACHE_DAYS` | 7 | maximale leeftijd in dagen |

## Voorbeeldvragen

- "Wat kost een Toyota RAV4 hybride onder de € 20.000?"
- "Hoe liggen de prijzen van de Volkswagen Golf diesel stationwagon?"
- "Zijn er recentelijk geplaatste Audi A4-modellen met trekhaak in de buurt van Utrecht?"

## Ontwikkelen

```bash
uv sync                # installeer de omgeving
uv run pytest -q       # tests (live-tests uit)
uv run pytest -q -m live   # live-tests (max ~2 verzoeken per test)
uv run ruff check . && uv run ruff format --check .
uv build --out-dir builds/
```