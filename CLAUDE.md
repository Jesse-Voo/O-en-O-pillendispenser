# Thelma Medicijndispenser Simulator

## Doel
Python/tkinter simulatie van de Thelma medicijndispenser (TSC Connected Care).
Bedoeld als oefenings-/demo-tool voor het vertrouwd raken met de interface.

## Bronmateriaal
- Handleiding: https://www.technologievoorwarmezorg.nl/wp-content/uploads/42645_Thelma_Handleiding_Digitaal_V3.pdf
- 17-pagina PDF, voornamelijk afbeeldingen (geen extracteerbare tekst)
- UI-schermen zijn uit de PDF geëxtraheerd als PNG

## Bestandsstructuur
```
thelma.py       — hoofdbestand, start beide vensters
CLAUDE.md       — dit bestand
```

## Hoe starten
```bash
/opt/homebrew/bin/python3 thelma.py
```
(Shebang `#!/opt/homebrew/bin/python3` bovenaan — de systeem-Python op macOS heeft geen tkinter;
`brew install python-tk@3.14` is al uitgevoerd om dit op te lossen.)

## Architectuur

### `AppState` (gedeelde toestand)
Centrale datalaag tussen beide vensters:
- `roll_loaded` — of er een medicatierol geplaatst is
- `schedule` — lijst van medicatiemomenten (tijd, innamen, medicijnen)
- `dispense_state` — `idle | green | yellow | red | ready`
- `dispense_index` — welk schema-slot actief wordt uitgegeven
- Observer-patroon: `on_change(cb)` + `notify()`

### `ThelmaWindow` — patiëntvenster (420×840 px)
Simuleert het scherm op het fysieke apparaat. Schermen (screens):

| screen-naam     | beschrijving |
|----------------|--------------|
| `welcome`       | Opstartscherm: "WELKOM / Even geduld a.u.b." |
| `home`          | Klok, naam, voortgangsbalk, dagschema |
| `detail`        | Detailweergave per medicatiemoment |
| `menu`          | Menu: deksel, vroege uitgifte, apparaatinfo, geavanceerd |
| `unlock`        | Bevestiging deksel ontgrendeld |
| `dispense`      | Uitgifte-instructie (groen/geel/rood knop bovenaan) |
| `dispense`      | "Pak uw medicatie" met groene onderkant (ready-state) |
| `roll_success`  | "Medicatierol succesvol geplaatst" |
| `success`       | "Medicatie succesvol ingenomen" |
| `info`          | Apparaatinformatie |
| `advanced`      | Geavanceerde instellingen (rood/waarschuwing) |

### `AdminWindow` — beheervenster (480×680 px)
Simuleert de achterkant / technische bediening:

- **Status lampjes** (canvas-cirkels, groen/oranje/rood/grijs):
  - Medicatierol aanwezig
  - Uitgifte status
  - Verbinding
  - Batterij
- **Medicatierol laden** — knop die `roll_loaded` op `True` zet
- **Uitgifte simuleren** — 3 knoppen (groen / geel / rood urgentie)
- **Schema status** — realtime weergave welke momenten zijn ingenomen
- **Logboek** — timestamped events (groen-op-zwart terminal-look)

## Kleurenpalet (uit de handleiding)
| naam    | hex       | gebruik |
|---------|-----------|---------|
| Groen   | `#4CAF76` | op tijd, ingenomen, succes |
| Oranje  | `#F5A623` | waarschuwing, 10 min te laat, deksel |
| Rood    | `#E53935` | alarm, 30 min te laat, geavanceerd |
| Blauw   | `#4A5B8C` | MENU / SLUITEN knoppen |

## Interactieflow (fysiek apparaat)
1. Opstarten → welkomstscherm (2,5 s) → home
2. **Geen rol**: "Geen medicatierol aanwezig" + "Deksel ontgrendelen"
3. **Rol laden** (via admin): success-scherm → home met schema
4. **Medicatiemoment**: scherm toont groene knop bovenaan → drukken → "Pak uw medicatie" (groene onderkant) → bevestigen → ingenomen
5. **10 min geen actie**: knop wordt geel
6. **30 min geen actie**: knop wordt rood + alarm naar zorgverlener

## Escape Room Flow
1. Thelma toont **START-scherm** (geen naam, geen schema, Thelma logo + grote START-knop)
2. Zorgmedewerker drukt **START** → stuurt HTTP trigger naar `other_ip:other_port/trigger` met `{"event":"game_start"}`
3. **Countdown** (MM:SS, standaard 5:00) vervangt de klok bovenaan; naam toont "Laden..."
4. Ander apparaat stuurt patiëntinfo: `{"event":"patient_info","patient_name":"...","schedule":[...]}`
5. Naam en schema verschijnen op scherm
6. Bij **00:00** → auto-dispense (groen). Rode waarschuwing als medicatierol ontbreekt.
7. Zorgmedewerker laadt rol → drukt knop → pakt zakje → **"Escape room voltooid!"**
8. Bij voltooiing stuurt Thelma `{"event":"game_complete"}` naar het andere apparaat

## Netwerkconfiguratie (config.json)
```json
{
  "other_ip":          "192.168.1.100",
  "other_port":        5000,
  "listen_port":       5000,
  "countdown_seconds": 300
}
```
Thelma luistert op `0.0.0.0:listen_port` voor inkomende POSTs op `/trigger`.

Patiëntinfo trigger (JSON body):
```json
{
  "event": "patient_info",
  "patient_name": "Truus van Roeden",
  "schedule": [
    {"time": "10:00", "medicines": ["Paracetamol 1000mg"]},
    {"time": "16:00", "medicines": ["Simvastatine 20mg"]}
  ]
}
```

## Nog te doen / mogelijke uitbreidingen
- [ ] Automatische tijdgestuurde uitgifte op basis van echte klok
- [ ] Geluid/alarm simulatie
- [ ] Configureerbaar schema (patiëntnaam, tijden, medicijnen)
- [ ] Paginering voor meerdere dagen (‹ Vandaag ›)
- [ ] Verbindingsstatus animatie (knipperend signaalicoon)
- [ ] Exporteren van logboek naar bestand
