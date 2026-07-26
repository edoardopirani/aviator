#!/usr/bin/env python3
"""
aviator — pull clean, comparable DIRECT flight prices across a date range and
across nearby departure airports, straight from Google Flights.

The Skyscanner / Google "flexible dates" grid lies: it silently mixes in indirect
flights and awful departure times, so the "cheap day" it highlights isn't the same
product as the flight you'd actually book. This tool pins the filters (direct only,
sane departure window) and produces an apples-to-apples price-per-day table. It also
compares alternative origin airports, adding an estimate of the extra ground time +
cost to reach them (ground-access.yaml), and annotates which airlines include a
carry-on bag in their cheapest fare (airlines.yaml).

Data source: Google Flights via the `fast-flights` scraper — no API key, no signup.
A consent cookie is injected to get past Google's EU "before you continue" wall.
Because it scrapes a live page it can break if Google changes their format; for
personal, occasional use that's an acceptable trade for zero setup + real prices.

Usage:
  aviator --from BHX,EMA,MAN --to FCO --dates 2026-07-20:2026-07-27 --home Birmingham
  aviator --from BHX --to FCO --dates 2026-07-20:2026-07-27 --need-carryon
  aviator --from BHX --to FCO --dates 2026-07-20 --return 2026-07-27   # round trip
"""

import argparse
import datetime as dt
import json
import os
import shutil
import sys
import time
from pathlib import Path

import yaml

from fast_flights import get_flights, create_query, FlightQuery, Passengers
from fast_flights.integrations.base import FetchIntegration
from primp import Client

try:
    from dotenv import load_dotenv
    load_dotenv(Path.cwd() / ".env")
except Exception:
    pass

__version__ = "1.0.1"

BUNDLED_CONFIG = Path(__file__).parent / "data"
USER_CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "aviator"

FLIGHTS_URL = "https://www.google.com/travel/flights"
# Google EU cookie-consent bypass. If Google ever walls requests again ("before you
# continue"), refresh this by accepting consent once in a browser and copying the
# SOCS cookie, or set FLIGHTS_SOCS in the environment / a .env file.
SOCS = os.environ.get(
    "FLIGHTS_SOCS",
    "SOCS=CAISNQgDEitib3FfaWRlbnRpdHlmcm9udGVuZHVpc2VydmVyXzIwMjQwODEzLjA2X3AwGgJlbiADGgYIgLC_tgY",
)


# ─────────────────────────── config loading ───────────────────────────
#
# Resolution order, first hit wins — so the tool works identically whether it was
# cloned (./config/ next to you) or pip-installed (~/.config/aviator/):
#   1. $AVIATOR_CONFIG_DIR   — explicit override
#   2. ./config/             — a checkout you're standing in
#   3. ~/.config/aviator/    — your personal copy (see `aviator --init-config`)
#   4. bundled defaults      — ship-with-the-package fallback

def config_dirs():
    dirs = []
    env = os.environ.get("AVIATOR_CONFIG_DIR")
    if env:
        dirs.append(Path(env).expanduser())
    dirs += [Path.cwd() / "config", USER_CONFIG, BUNDLED_CONFIG]
    return dirs


def load_yaml(name):
    for d in config_dirs():
        p = d / name
        if p.exists():
            with open(p) as f:
                return yaml.safe_load(f) or {}
    return {}


def init_config():
    """Copy the bundled configs to ~/.config/aviator/ so they can be edited."""
    USER_CONFIG.mkdir(parents=True, exist_ok=True)
    for src in sorted(BUNDLED_CONFIG.glob("*.yaml")):
        dst = USER_CONFIG / src.name
        if dst.exists():
            print(f"  kept   {dst}  (already exists — not overwritten)")
        else:
            shutil.copy(src, dst)
            print(f"  wrote  {dst}")
    print(f"\nEdit ground-access.yaml to add your own home location, then run with "
          f"--home <YourLocation>.")


AIRLINES = load_yaml("airlines.yaml")
GROUND = load_yaml("ground-access.yaml")
# name (lowercased) -> policy, for matching Google's airline-name strings
_BY_NAME = {(v.get("name") or k).lower(): v for k, v in AIRLINES.items()}


def airline_info(name):
    """Look up carry-on policy by the airline NAME Google returns (e.g. 'Ryanair')."""
    key = (name or "").lower()
    info = _BY_NAME.get(key)
    if info is None:  # fall back to substring match ("Wizz Air Malta" -> "wizz air")
        for n, v in _BY_NAME.items():
            if n and (n in key or key in n):
                info = v
                break
    info = info or {}
    raw = info.get("carryon_in_basic", "unknown")
    carryon = {True: "yes", False: "no"}.get(raw, str(raw).lower())  # YAML yes/no -> bool
    return {"name": info.get("name", name), "carryon": carryon, "fee": info.get("carryon_fee", 0)}


def ground_info(home, airport):
    """Extra ground time (min) + cost to reach `airport` from `home`."""
    node = (GROUND.get(home, {}) or {}).get(airport)
    if node is None:
        return {"minutes": None, "cost": 0, "note": "no ground estimate — add to ground-access.yaml"}
    return {"minutes": node.get("minutes"), "cost": node.get("cost", 0), "note": node.get("note", "")}


# ─────────────────────────── google flights client ───────────────────────────

class ConsentFetcher(FetchIntegration):
    """fast-flights fetcher that injects the consent cookie so EU IPs get results."""

    def __init__(self):
        self.client = Client(impersonate="chrome_145", impersonate_os="macos",
                             referer=True, cookie_store=True, headers={"Cookie": SOCS})

    def fetch_html(self, q):
        params = q.params() if hasattr(q, "params") else {"q": q}
        return self.client.get(FLIGHTS_URL, params=params).text


class GoogleFlights:
    def __init__(self, currency="GBP", seat="economy"):
        self.fetcher = ConsentFetcher()
        self.currency = currency
        self.seat = seat

    def search(self, origin, dest, date, ret_date, direct=True):
        legs = [FlightQuery(date=date, from_airport=origin, to_airport=dest,
                            max_stops=0 if direct else None)]
        if ret_date:
            legs.append(FlightQuery(date=ret_date, from_airport=dest, to_airport=origin,
                                    max_stops=0 if direct else None))
        q = create_query(flights=legs, seat=self.seat,
                         trip="round-trip" if ret_date else "one-way",
                         passengers=Passengers(adults=1), currency=self.currency)
        # A TypeError/FlightsNotFound from the parser almost always means "Google
        # returned no direct flights for this route/day" (its no-results page has a
        # different shape). We surface that as an empty result, not an error.
        last_err = None
        for attempt in range(2):
            try:
                return list(get_flights(q, integration=self.fetcher))
            except Exception as e:
                last_err = e
                if attempt == 0:
                    time.sleep(2)
        self.last_error = (origin, dest, date, last_err)
        return []


# ─────────────────────────── parsing helpers ───────────────────────────

def parse_range(spec):
    """'2026-07-20:2026-07-27' -> list of dates; also accepts single or comma list."""
    if not spec:
        return [None]
    out = []
    for chunk in spec.split(","):
        if ":" in chunk:
            a, b = chunk.split(":")
            d0, d1 = dt.date.fromisoformat(a), dt.date.fromisoformat(b)
            step = 1 if d1 >= d0 else -1
            d = d0
            while (d <= d1) if step == 1 else (d >= d1):
                out.append(d.isoformat())
                d += dt.timedelta(days=step)
        else:
            out.append(dt.date.fromisoformat(chunk).isoformat())
    return out


def _hm(t):
    """SimpleDatetime.time tuple -> (hour, minute). fast-flights returns None for
    the midnight hour (00) and omits :00 minutes, so coerce both None -> 0."""
    if not t:
        return 0, 0
    h = t[0] if t[0] is not None else 0
    m = t[1] if len(t) > 1 and t[1] is not None else 0
    return h, m


def flight_row(f, home, origin, dest, ret_date, need_carryon):
    """Flatten one Google Flights result (Flights) into a comparable row dict."""
    leg = f.flights[0]  # outbound leg
    dh, dm = _hm(leg.departure.time)
    ah, am = _hm(leg.arrival.time)
    dep_date = dt.date(*leg.departure.date)
    arr_date = dt.date(*leg.arrival.date)
    overnight = arr_date > dep_date

    name = "/".join(f.airlines) if f.airlines else "?"
    ai = airline_info(f.airlines[0] if f.airlines else "")

    fare = float(f.price)
    g = ground_info(home, origin)
    carryon_fee = ai["fee"] if (need_carryon and ai["carryon"] in ("no", "paid", "unknown")) else 0
    adjusted = fare + g["cost"] + carryon_fee

    return {
        "origin": origin,
        "dest": dest,
        "dep_date": dep_date.isoformat(),
        "airline": ai["name"] if ai["name"] else name,
        "airlines_raw": name,
        "dep_time": f"{dh:02d}:{dm:02d}",
        "arr_time": f"{ah:02d}:{am:02d}" + ("+1" if overnight else ""),
        "dur_min": leg.duration,
        "ret_date": ret_date,
        "round_trip": ret_date is not None,
        "fare": round(fare, 2),
        "carryon": ai["carryon"],
        "carryon_fee": ai["fee"],
        "ground_cost": g["cost"],
        "ground_min": g["minutes"],
        "ground_note": g["note"],
        "adjusted": round(adjusted, 2),
        "_dep_time_obj": dt.time(dh, dm),
    }


# ─────────────────────────── rendering ───────────────────────────

CARRYON_MARK = {"yes": "✓ inc", "no": "✗ pay", "paid": "+fee", "unknown": "?"}


def render_table(rows, currency, round_trip):
    if not rows:
        print("No matching direct flights found for the given filters.")
        return
    hdr = f"{'Date':<11}{'From':<5}{'Airline':<18}{'Dep':<6}{'Arr':<8}{'Dur':<6}{'Fare':>8} {'Bag':<6}{'Grnd':>7}{'Adj.':>8}"
    print(hdr)
    print("─" * len(hdr))
    for r in rows:
        dur = f"{r['dur_min']//60}h{r['dur_min']%60:02d}" if r["dur_min"] else "?"
        gmin = f"+{r['ground_min']}m" if r["ground_min"] is not None else "—"
        bag = CARRYON_MARK.get(r["carryon"], "?")
        print(
            f"{r['dep_date']:<11}{r['origin']:<5}{r['airline'][:17]:<18}"
            f"{r['dep_time']:<6}{r['arr_time']:<8}{dur:<6}"
            f"{r['fare']:>8.0f} {bag:<6}{gmin:>7}{r['adjusted']:>8.0f}"
        )
    print("─" * len(hdr))
    price_kind = "round-trip total" if round_trip else "one-way"
    print(f"Fare/Adj. in {currency} ({price_kind}).  Bag = carry-on in cheapest fare (✓ inc / ✗ pay / ?).  "
          f"Grnd = extra min to reach that airport.  Adj. = fare + ground cost (+ carry-on fee if --need-carryon).")
    print("Live Google Flights data — confirm the exact fare on the airline before booking.")


def recap(rows, currency):
    if not rows:
        return
    cheapest = min(rows, key=lambda r: r["adjusted"])
    print("\n=== RECAP ===")
    print(f"Cheapest door-to-door: {cheapest['airline']} on {cheapest['dep_date']} "
          f"from {cheapest['origin']} — {currency}{cheapest['adjusted']:.0f} "
          f"(fare {currency}{cheapest['fare']:.0f}"
          + (f" + ground {currency}{cheapest['ground_cost']:.0f}" if cheapest['ground_cost'] else "")
          + f", dep {cheapest['dep_time']})")
    by_origin = {}
    for r in rows:
        cur = by_origin.get(r["origin"])
        if cur is None or r["adjusted"] < cur["adjusted"]:
            by_origin[r["origin"]] = r
    if len(by_origin) > 1:
        print("Best per airport (adjusted):")
        for o, r in sorted(by_origin.items(), key=lambda kv: kv[1]["adjusted"]):
            extra = f", +{r['ground_min']}m ground" if r["ground_min"] else ""
            print(f"  {o}: {currency}{r['adjusted']:.0f}  ({r['airline']}, {r['dep_date']} {r['dep_time']}{extra})")


# ─────────────────────────── main ───────────────────────────

def build_parser():
    ap = argparse.ArgumentParser(
        prog="aviator",
        description="Compare direct flight prices across days and nearby airports.")
    ap.add_argument("--from", dest="origins", help="Origin airport code(s), comma-separated. e.g. BHX,EMA,MAN")
    ap.add_argument("--to", dest="dest", help="Destination airport code. e.g. FCO")
    ap.add_argument("--dates", help="Outbound date or range. e.g. 2026-07-20:2026-07-27")
    ap.add_argument("--return", dest="ret", help="Return date or range (omit for one-way).")
    ap.add_argument("--home", default="", help="Home location key for ground-access.yaml. e.g. Birmingham")
    ap.add_argument("--depart-after", default="09:00", help="Earliest outbound departure HH:MM (default 09:00).")
    ap.add_argument("--depart-before", default=None, help="Latest outbound departure HH:MM.")
    ap.add_argument("--max-dur", type=int, default=None, help="Max flight duration in minutes.")
    ap.add_argument("--need-carryon", action="store_true", help="Factor carry-on fee into adjusted price where the airline charges.")
    ap.add_argument("--currency", default="GBP")
    ap.add_argument("--seat", default="economy", choices=["economy", "premium-economy", "business", "first"])
    ap.add_argument("--include-indirect", action="store_true", help="Also include connecting flights (default: direct only).")
    ap.add_argument("--sort", default="adjusted", choices=["adjusted", "fare", "date", "dep"], help="Sort order.")
    ap.add_argument("--delay", type=float, default=1.5, help="Seconds between requests (be gentle with Google).")
    ap.add_argument("--json", dest="as_json", action="store_true", help="Emit rows as JSON instead of a table.")
    ap.add_argument("--init-config", action="store_true",
                    help="Copy the default config files to ~/.config/aviator/ for editing, then exit.")
    ap.add_argument("--version", action="version", version=f"aviator {__version__}")
    return ap


def main():
    ap = build_parser()
    args = ap.parse_args()

    if args.init_config:
        init_config()
        return

    missing = [f"--{n}" for n, v in
               (("from", args.origins), ("to", args.dest), ("dates", args.dates)) if not v]
    if missing:
        ap.error(f"the following arguments are required: {', '.join(missing)}")

    origins = [o.strip().upper() for o in args.origins.split(",")]
    dest = args.dest.upper()
    dates = parse_range(args.dates)
    ret_dates = parse_range(args.ret) if args.ret else [None]
    after = dt.time.fromisoformat(args.depart_after) if args.depart_after else None
    before = dt.time.fromisoformat(args.depart_before) if args.depart_before else None
    round_trip = args.ret is not None

    if args.home and args.home not in GROUND:
        print(f"Note — no '{args.home}' entry in ground-access.yaml, so ground costs count as 0. "
              f"Run `aviator --init-config` and add your own.", file=sys.stderr)

    api = GoogleFlights(currency=args.currency, seat=args.seat)
    rows = []
    total = len(origins) * len(dates) * len(ret_dates)
    print(f"Querying {total} date×airport combinations from Google Flights "
          f"({'incl. indirect' if args.include_indirect else 'direct only'})…", file=sys.stderr)

    empties = {o: 0 for o in origins}  # (origin) -> count of queries with no rows
    for origin in origins:
        for date in dates:
            for ret_date in ret_dates:
                results = api.search(origin, dest, date, ret_date, direct=not args.include_indirect)
                if not results:
                    empties[origin] += 1
                for f in results:
                    try:
                        r = flight_row(f, args.home, origin, dest, ret_date, args.need_carryon)
                    except Exception as e:
                        print(f"  ! skipped a result ({type(e).__name__}: {e})", file=sys.stderr)
                        continue
                    if r["dep_date"] != date:  # guard against stray nearby-date rows
                        continue
                    if after and r["_dep_time_obj"] < after:
                        continue
                    if before and r["_dep_time_obj"] > before:
                        continue
                    if args.max_dur and r["dur_min"] and r["dur_min"] > args.max_dur:
                        continue
                    rows.append(r)
                time.sleep(args.delay)

    # dedupe identical flights, keep cheapest
    seen = {}
    for r in rows:
        k = (r["origin"], r["dep_date"], r["airline"], r["dep_time"], r["ret_date"])
        if k not in seen or r["fare"] < seen[k]["fare"]:
            seen[k] = r
    rows = list(seen.values())
    for r in rows:
        r.pop("_dep_time_obj", None)

    sort_key = {
        "adjusted": lambda r: r["adjusted"],
        "fare": lambda r: r["fare"],
        "date": lambda r: (r["dep_date"], r["adjusted"]),
        "dep": lambda r: r["dep_time"],
    }[args.sort]
    rows.sort(key=sort_key)

    if args.as_json:
        print(json.dumps(rows, indent=2))
    else:
        render_table(rows, args.currency, round_trip)
        recap(rows, args.currency)
        # note airports that returned nothing on every queried date
        per_origin = len(dates) * len(ret_dates)
        gaps = [f"{o} (no direct {dest} flights on any queried day)"
                for o in origins if empties[o] == per_origin]
        if gaps:
            print("\nNote — " + "; ".join(gaps) + ".")


if __name__ == "__main__":
    main()
