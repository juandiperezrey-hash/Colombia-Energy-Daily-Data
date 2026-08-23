"""
fetch_colombia_mix.py

Pulls Colombia's real electricity generation (MetricId "Gene") from XM's public
API (SINERGOX), broken down by generation resource (power plant), for both a
single recent day and the year-to-date accumulated period. It maps each
resource to its fuel type using XM's "ListadoRecursos" catalog, sums energy
by fuel type, and writes JSON files in the shape the Cordillera Energy
website expects for its pie charts.

No API key or account is required — XM's API is public.

Docs: https://github.com/EquipoAnaliticaXM/API_XM

IMPORTANT (learned from a live run): XM's responses nest each record as
{"Date": "...", "ListEntities": [{"Id": <code>, "Values": {...}}, ...]}
rather than a flat dict — this script parses that shape.
"""

import json
import time
import datetime as dt
from collections import defaultdict

import requests

BASE_URL = "https://servapibi.xm.com.co"
HEADERS = {"Content-Type": "application/json"}

# XM's public hourly/daily endpoints accept a maximum 30-day window per call.
CHUNK_DAYS = 28

# Maps XM's raw "Type" resource categories to the labels used on the website's
# pie chart. Extend this if XM introduces new categories.
FUEL_TYPE_MAP = {
    "HIDRAULICA": "Hydropower",
    "SOLAR": "Solar",
    "EOLICA": "Wind",
    "TERMICA": "Gas",          # bucket thermal plants under Gas unless EnerSource says otherwise
    "COGENERADOR": "Biomass",
    "BIOMASA": "Biomass",
    "AUTOGENERADOR": "Other",
}

# Some thermal plants run on coal/liquid fuels rather than gas — refine using
# the resource catalog's "EnerSource" field when Type == TERMICA.
ENERSOURCE_OVERRIDE = {
    "CARBON": "Coal, wind & other",
    "ACPM": "Coal, wind & other",
    "FUEL OIL": "Coal, wind & other",
    "JET A1": "Coal, wind & other",
    "JET-A1": "Coal, wind & other",
    "GAS": "Gas",
}


def daterange_chunks(start: dt.date, end: dt.date, chunk_days: int):
    cur = start
    while cur <= end:
        chunk_end = min(cur + dt.timedelta(days=chunk_days - 1), end)
        yield cur, chunk_end
        cur = chunk_end + dt.timedelta(days=1)


def fetch_metric(metric_id: str, entity: str, start: dt.date, end: dt.date) -> list:
    """Calls XM's /hourly endpoint (confirmed correct for MetricId 'Gene' —
    the /daily endpoint returns 'Id de Métrica no encontrada' for it)."""
    body = {
        "MetricId": metric_id,
        "StartDate": start.isoformat(),
        "EndDate": end.isoformat(),
        "Entity": entity,
    }
    resp = requests.post(f"{BASE_URL}/hourly", json=body, headers=HEADERS, timeout=60)
    if not resp.ok:
        print(f"  XM API error {resp.status_code} for {metric_id}/{entity} "
              f"{start}->{end}. Response body:\n{resp.text[:2000]}")
        resp.raise_for_status()
    payload = resp.json()
    items = payload.get("Items", payload.get("items", []))
    return items


def fetch_resource_catalog() -> dict:
    """
    Fetches XM's resource listing (plant -> fuel type / technology) so we can
    map each generator code (e.g. 'TBST') to a human fuel category.

    Response shape: {"Items": [{"Date": "...", "ListEntities": [
        {"Id": "Sistema", "Values": {"Code": "2QBW", "Type": "HIDRAULICA",
                                      "EnerSource": "AGUA", ...}}
    ]}, ...]}

    Returns {resource_code: {"tipo": ..., "enersource": ...}}
    """
    body = {"MetricId": "ListadoRecursos"}
    resp = requests.post(f"{BASE_URL}/lists", json=body, headers=HEADERS, timeout=60)
    if not resp.ok:
        print(f"  XM API error {resp.status_code} for ListadoRecursos. "
              f"Response body:\n{resp.text[:2000]}")
        resp.raise_for_status()
    payload = resp.json()
    items = payload.get("Items", payload.get("items", []))
    print(f"  ListadoRecursos returned {len(items)} raw items")

    mapping = {}
    for item in items:
        for entity in item.get("ListEntities", []):
            values = entity.get("Values", {})
            code = values.get("Code")
            tipo = (values.get("Type") or "").upper()
            enersource = (values.get("EnerSource") or "").upper()
            if not code:
                continue
            mapping[code] = {"tipo": tipo, "enersource": enersource}
    return mapping


def classify(tipo: str, enersource: str) -> str:
    if tipo == "TERMICA" and enersource in ENERSOURCE_OVERRIDE:
        return ENERSOURCE_OVERRIDE[enersource]
    return FUEL_TYPE_MAP.get(tipo, "Other")


def build_mix(totals_by_fuel: dict) -> list:
    colors = {
        "Hydropower": "#2E86DE",
        "Gas": "#7C8B99",
        "Solar": "#F2A93B",
        "Wind": "#14B8A6",
        "Biomass": "#A0522D",
        "Coal, wind & other": "#8E44AD",
        "Other": "#E8B923",
    }
    grand_total = sum(totals_by_fuel.values())
    if grand_total == 0:
        return []
    return [
        {
            "label": label,
            "value": round(total / grand_total * 100, 1),
            "color": colors.get(label, "#9AA5B1"),
        }
        for label, total in sorted(totals_by_fuel.items(), key=lambda kv: -kv[1])
    ]


def sum_generation(items: list, resource_catalog: dict) -> dict:
    """
    items: list of {"Date": "...", "ListEntities": [{"Id": <resource_code>,
                                                       "Values": {<hour keys>: <number>, ...}}]}
    Sums every numeric value found under each entity's "Values" dict (robust
    to whatever the hour-key naming convention turns out to be), then buckets
    the total by fuel type using the resource catalog.
    """
    totals_by_fuel = defaultdict(float)
    unmapped_resources = set()
    matched = 0

    for item in items:
        for entity in item.get("ListEntities", []):
            resource_code = entity.get("Id")
            values = entity.get("Values", {})
            if not resource_code:
                continue

            day_total = 0.0
            for val in values.values():
                try:
                    day_total += float(val)
                except (TypeError, ValueError):
                    continue

            info = resource_catalog.get(resource_code)
            if not info:
                unmapped_resources.add(resource_code)
                fuel = "Other"
            else:
                fuel = classify(info["tipo"], info["enersource"])
                matched += 1

            totals_by_fuel[fuel] += day_total

    print(f"  Matched {matched} resource-days to a known fuel type "
          f"({len(unmapped_resources)} unique unmapped codes bucketed as 'Other')")
    if unmapped_resources:
        print(f"    Sample unmapped codes: {sorted(unmapped_resources)[:10]}")

    return totals_by_fuel


def fetch_daily(resource_catalog: dict) -> dict:
    """Most recently completed day. XM usually needs 1-2 days to finalise
    'Gene' data, so we step back from 2 days ago and try a few days if needed."""
    for days_back in (2, 3, 4, 5, 6):
        candidate = dt.date.today() - dt.timedelta(days=days_back)
        print(f"Fetching Colombia daily generation: trying {candidate}")
        try:
            items = fetch_metric("Gene", "Recurso", candidate, candidate)
        except requests.exceptions.HTTPError:
            print(f"  {candidate} failed, trying an earlier day...")
            continue
        if items:
            totals_by_fuel = sum_generation(items, resource_catalog)
            mix = build_mix(totals_by_fuel)
            if mix:
                return {
                    "country": "Colombia",
                    "period_start": candidate.isoformat(),
                    "period_end": candidate.isoformat(),
                    "generated_at_utc": dt.datetime.utcnow().isoformat() + "Z",
                    "source": "XM (Administrador del Mercado de Energía Mayorista de Colombia) — SINERGOX public API",
                    "mix": mix,
                }
        print(f"  {candidate} returned no usable data, trying an earlier day...")
    raise SystemExit("No daily generation data returned from XM after trying several recent days.")


def fetch_ytd(resource_catalog: dict) -> dict:
    """Accumulated mix from Jan 1 of the current year through today."""
    today = dt.date.today()
    jan_1 = dt.date(today.year, 1, 1)

    print(f"Fetching Colombia YTD generation: {jan_1} -> {today}")

    totals_by_fuel = defaultdict(float)
    for start, end in daterange_chunks(jan_1, today, CHUNK_DAYS):
        print(f"  Fetching {start} -> {end} ...")
        items = fetch_metric("Gene", "Recurso", start, end)
        chunk_totals = sum_generation(items, resource_catalog)
        for fuel, total in chunk_totals.items():
            totals_by_fuel[fuel] += total
        time.sleep(1)  # be polite to the public API between chunks

    mix = build_mix(totals_by_fuel)
    if not mix:
        raise SystemExit("No YTD generation data returned from XM.")

    return {
        "country": "Colombia",
        "period_start": jan_1.isoformat(),
        "period_end": today.isoformat(),
        "generated_at_utc": dt.datetime.utcnow().isoformat() + "Z",
        "source": "XM (Administrador del Mercado de Energía Mayorista de Colombia) — SINERGOX public API",
        "mix": mix,
    }


def main():
    resource_catalog = fetch_resource_catalog()
    print(f"Loaded {len(resource_catalog)} resources from XM catalog")

    try:
        daily = fetch_daily(resource_catalog)
        with open("data/colombia_daily_mix.json", "w", encoding="utf-8") as f:
            json.dump(daily, f, indent=2, ensure_ascii=False)
        print("Wrote data/colombia_daily_mix.json")
        print(json.dumps(daily, indent=2, ensure_ascii=False))
    except SystemExit as e:
        print(f"WARNING: daily fetch failed ({e}), skipping daily output this run.")

    try:
        ytd = fetch_ytd(resource_catalog)
        with open("data/colombia_ytd_mix.json", "w", encoding="utf-8") as f:
            json.dump(ytd, f, indent=2, ensure_ascii=False)
        print("Wrote data/colombia_ytd_mix.json")
        print(json.dumps(ytd, indent=2, ensure_ascii=False))
    except SystemExit as e:
        print(f"WARNING: YTD fetch failed ({e}), skipping YTD output this run.")


if __name__ == "__main__":
    main()
