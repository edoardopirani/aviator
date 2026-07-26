# Aviator

**Honest direct-flight price comparison across dates and nearby airports.**

Skyscanner's "flexible dates" grid lies to you. The cheap day it highlights quietly re-adds
connecting flights and 5am departures, so it isn't the same product as the flight you'd actually
book. By the time you've pinned down the flight you want, a comparable one on another day — or
from an airport an hour away — is often cheaper, but you've lost the ability to compare cleanly.

Aviator pins the filters and shows you one apples-to-apples table:

- **Direct flights only** (unless you ask otherwise), within a **sane departure window**
- Across a **date range** and across **multiple origin airports** at once
- **Ground-adjusted**: the extra time and money to reach a further airport is added to the fare,
  so a "cheaper" flight 90 minutes away doesn't get counted as cheaper when it isn't
- **Carry-on aware**: flags whether the cheapest fare actually includes a cabin bag, and can fold
  the bag fee into the comparison

```
$ aviator --from BHX,EMA,MAN --to FCO --dates 2026-09-14:2026-09-18 --home Birmingham

Date       From Airline           Dep   Arr     Dur       Fare Bag      Grnd    Adj.
────────────────────────────────────────────────────────────────────────────────────
2026-09-15 BHX  Ryanair           10:35 14:15   2h40        58 ✗ pay    +30m      66
2026-09-16 EMA  Ryanair           11:10 14:50   2h40        49 ✗ pay    +60m      74
2026-09-14 MAN  Jet2              09:55 13:40   2h45        62 ✓ inc   +110m     107
────────────────────────────────────────────────────────────────────────────────────

=== RECAP ===
Cheapest door-to-door: Ryanair on 2026-09-15 from BHX — GBP66 (fare GBP58 + ground GBP8, dep 10:35)
```

The **Adj.** column is the number to compare. **Grnd** is there so you can decide whether saving
£8 is worth an extra hour each way — Aviator won't make that call for you.

---

## Install

Requires **Python 3.10+**. No account, no API key, no signup.

```bash
pip install git+https://github.com/edoardopirani/aviator.git
```

Or clone it if you want to hack on it:

```bash
git clone https://github.com/edoardopirani/aviator.git
cd aviator
pip install -e .
```

On macOS/Linux, if pip refuses with *"externally-managed-environment"*, either use a virtualenv
or add `--user --break-system-packages`.

Then set up your own config (see [Configuration](#configuration)):

```bash
aviator --init-config
```

## Usage

```bash
# One airport, a week of dates
aviator --from LHR --to FCO --dates 2026-09-14:2026-09-21

# The real use case: several departure airports, ground cost factored in
aviator --from BHX,EMA,MAN --to FCO --dates 2026-09-14:2026-09-21 --home Birmingham

# Round trip — a range for the outbound AND a range for the return
aviator --from BHX --to FCO --dates 2026-09-14:2026-09-16 --return 2026-09-21:2026-09-23

# Only count fares that actually let you bring a cabin bag
aviator --from BHX --to FCO --dates 2026-09-14:2026-09-21 --need-carryon
```

Airports are IATA codes (`LHR`, `BHX`, `FCO`…). Dates are `YYYY-MM-DD`; a range is two joined by
a colon; a comma-separated list also works.

### Flags

| Flag | Default | What it does |
|---|---|---|
| `--from` | *required* | Origin airport code(s), comma-separated |
| `--to` | *required* | Destination airport code |
| `--dates` | *required* | Outbound date or range, `YYYY-MM-DD[:YYYY-MM-DD]` |
| `--return` | — | Return date or range; omit for one-way |
| `--home` | — | Your location key in `ground-access.yaml` |
| `--depart-after` | `09:00` | Earliest outbound departure |
| `--depart-before` | — | Latest outbound departure |
| `--max-dur` | — | Max flight duration, minutes |
| `--need-carryon` | off | Add the cabin-bag fee where the airline charges one |
| `--currency` | `GBP` | Any currency Google Flights accepts |
| `--seat` | `economy` | `economy`, `premium-economy`, `business`, `first` |
| `--include-indirect` | off | Also return connecting flights |
| `--sort` | `adjusted` | `adjusted`, `fare`, `date`, `dep` |
| `--delay` | `1.5` | Seconds between requests — be gentle |
| `--json` | off | Emit rows as JSON instead of a table |
| `--init-config` | — | Copy default configs to `~/.config/aviator/` and exit |

### Output columns

| Column | Meaning |
|---|---|
| **Fare** | The headline price Google shows |
| **Bag** | Cabin bag in the cheapest fare — `✓ inc` / `✗ pay` / `?` |
| **Grnd** | Extra minutes to reach that airport vs. your usual one |
| **Adj.** | Fare + ground cost (+ bag fee with `--need-carryon`) — **compare on this** |

A week across three airports is ~21 requests at 1.5s apart, so expect well under a minute. Don't
run it in a loop; it's scraping a live website.

## Configuration

Two YAML files. `aviator --init-config` drops editable copies in `~/.config/aviator/`; Aviator
looks in `$AVIATOR_CONFIG_DIR`, then `./config/`, then `~/.config/aviator/`, then its bundled
defaults — first hit wins.

**`ground-access.yaml`** — extra travel time and cost from your home to each airport. Ships with
Birmingham, London and Manchester as worked examples; **replace them with your own**:

```yaml
Manchester:
  MAN: {minutes: 25,  cost: 10, note: "home airport — tram or short drive"}
  LPL: {minutes: 60,  cost: 20, note: "Liverpool John Lennon, ~35mi"}
  LBA: {minutes: 80,  cost: 25, note: "Leeds Bradford, ~70mi"}
```

Without an entry matching `--home`, ground costs count as zero — the tool still works, it's just
fare-only.

**`airlines.yaml`** — which airlines include a cabin bag in the basic fare and roughly what one
costs otherwise. Figures are European short-haul norms verified July 2026; budget-airline fees are
dynamic (Ryanair's priority swings ~£12–36 by route and timing), so treat them as ballpark.

## How it works, and how it can break

Data comes from **Google Flights** via the [`fast-flights`](https://pypi.org/project/fast-flights/)
scraper — no API key, no signup, real current prices. A consent cookie is injected to get past
Google's EU "before you continue" wall.

Because it scrapes a live page, **it can break when Google changes their layout**. That's the
trade for zero setup.

- **"No matching direct flights found"** on a route you know exists — usually genuine: the route
  may be seasonal, or the 09:00 departure filter cut everything. Retry with `--depart-after 06:00`
  or `--include-indirect` to sanity-check.
- **Every route returns empty** — that's the scraper, not your setup. Try
  `pip install --upgrade fast-flights` first.
- **Google is serving a consent wall** — grab a fresh `SOCS` cookie from your browser and set
  `FLIGHTS_SOCS` in your environment or a `.env` file. See `.env.example`.

> Data-source history: Amadeus's self-service API was the original plan, but it was decommissioned
> in July 2026. Travelpayouts (cached prices, free token) is a viable fallback if the scraper ever
> breaks for good.

## Contributing

Very welcome — see [CONTRIBUTING.md](CONTRIBUTING.md). The most useful contributions are new
`ground-access.yaml` locations, corrected `airlines.yaml` baggage figures (especially outside
Europe), and resilience fixes when Google shifts their markup.

## Licence

MIT — see [LICENSE](LICENSE).
