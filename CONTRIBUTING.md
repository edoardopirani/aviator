# Contributing to Aviator

Small project, low ceremony. Fork it, branch, open a PR.

## Setup

```bash
git clone https://github.com/edoardopirani/aviator.git
cd aviator
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
aviator --from LHR --to FCO --dates 2026-09-15    # smoke test
```

Everything lives in [`src/aviator/cli.py`](src/aviator/cli.py) — one file, ~350 lines, laid out in
labelled sections: config loading → Google Flights client → parsing → rendering → `main()`.

## Most useful contributions

1. **More `ground-access.yaml` locations.** The whole point of the tool is honest
   alternative-airport comparison, and that only works if your city is in there. Add a block with
   realistic door-to-gate times and return ground costs.
2. **Better `airlines.yaml` baggage data**, especially non-European carriers. The current figures
   are European short-haul norms verified July 2026. Cite your source in a comment and date it —
   these drift constantly.
3. **Scraper resilience.** Google changes their markup periodically. Fixes that degrade gracefully
   (skip a malformed result, keep the rest) beat fixes that throw.
4. **New comparison dimensions.** Anything that makes the *adjusted* price more honest — checked
   bag fees, seat selection, airport transfer time at the destination end.

## Ground rules

- **Be gentle with Google.** Don't lower the default `--delay`, don't add concurrency, don't add
  anything that hammers the endpoint. Occasional personal use is what keeps this viable.
- **No API keys or accounts.** Zero-setup is a feature. If a change needs a signup, it belongs
  behind an optional flag with a working default path.
- **Keep it one file.** If `cli.py` genuinely outgrows itself, split it — but don't pre-emptively
  build a package structure for a tool this size.
- **Prices are indicative.** Nothing in the output should imply a bookable, guaranteed fare. The
  "confirm on the airline's site" line stays.

## Reporting a break

If it stops returning results, include: the exact command, whether *every* route is empty or just
one, your `fast-flights` version (`pip show fast-flights`), and your country. Empty-on-one-route is
usually a real gap in flights; empty-on-everything is a scraper break.
