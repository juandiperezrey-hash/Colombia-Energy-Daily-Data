"""
fetch_colombia_mix.py

Pulls Colombia's real electricity generation (MetricId "Gene") from XM's public
API (SINERGOX), from Jan 1 of the current year through today, broken down by
generation resource (power plant). It then maps each resource to its fuel
type using XM's "ListadoRecursos" catalog, sums energy by fuel type, and
writes a JSON file with percentage shares in the same shape the Cordillera
Energy website expects for its pie charts.

No API key or account is required — XM's API is public.

Docs: https://github.com/EquipoAnaliticaXM/API_XM
"""

import json
import time
import datetime as dt
from collections import defaultdict

import requests

BASE_URL = "https://servapibi.xm.com.co"
HEADERS = {"Content-Type": "application/json"}

# XM's public daily endpoint accepts a maximum 30-day window per call, so we
# fetch in monthly-sized chunks and stitch the results together.
CHUNK_DAYS = 28

# Maps XM's raw "Tipo" (or "Combustible") resource categories to the labels
# used on the website's pie chart. Extend this if XM introduces new categories.
FUEL_TYPE_MAP = {
    "HIDRAULICA": "Hydropower",
    "SOLAR": "Solar",
    "EOLICA": "Wind",
    "TERMICA": "Gas",          # bucket thermal plants under Gas unless fuel says otherwise
    "COGENERADOR": "Biomass",
    "BIOMASA": "Biomass",
    "AUTOGENERADOR": "Other",
}

# Some thermal plants run on coal/liquid fuels rather than gas — if the
# resource listing exposes a "Combustible" column, refine the bucket here.
THERMAL_FUEL_OVERRIDE = {
    "CARBON": "Coal, wind & other",
    "ACPM": "Coal, wind & other",
    "FUEL OIL": "Coal, wind & other",
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
    """Calls XM's /daily endpoint for a given metric/entity and date range."""
    body = {
        "MetricId": metric_id,
        "StartDate": start.isoformat(),
        "EndDate": end.isoformat(),
        "Entity": entity,
    }
    resp = requests.post(f"{BASE_URL}/daily", json=body, headers=HEADERS, timeout=60)
    resp.raise_for_status()
    payload = resp.json()
    return payload.get("Items", payload.get("items", []))


def fetch_resource_catalog() -> dict:
    """
    Fetches XM's resource listing (plant -> fuel type / technology) so we can
    map each generator code (e.g. 'TBST') to a human fuel category.
    Returns {resource_code: fuel_type_str}
    """
    body = {"MetricId": "ListadoRecursos"}
    resp = requests.post(f"{BASE_URL}/lists", json=body, headers=HEADERS, timeout=60)
    resp.raise_for_status()
    payload = resp.json()
    items = payload.get("Items", payload.get("items", []))

    mapping = {}
    for item in items:
        # Field names vary by XM release; try the common variants defensively.
        code = item.get("Values_Code") or item.get("Codigo") or item.get("Values_Recurso")
        tipo = (item.get("Values_Type") or item.get("Tipo") or "").upper()
        combustible = (item.get("Values_Fuel") or item.get("Combustible") or "").upper()
        if not code:
            continue
        mapping[code] = {"tipo": tipo, "combustible": combustible}
    return mapping


def classify(tipo: str, combustible: str) -> str:
    if tipo == "TERMICA" and combustible in THERMAL_FUEL_OVERRIDE:
        return THERMAL_FUEL_OVERRIDE[combustible]
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
    totals_by_fuel = defaultdict(float)
    unmapped_resources = set()

    for item in items:
        resource_code = item.get("Values_code") or item.get("Codigo")
        if not resource_code:
            continue

        day_total = 0.0
        for key, val in item.items():
            if key.lower().startswith("values_hour") or key.lower().startswith("value"):
                try:
                    day_total += float(val)
                except (TypeError, ValueError):
                    continue

        info = resource_catalog.get(resource_code)
        if not info:
            unmapped_resources.add(resource_code)
            fuel = "Other"
        else:
            fuel = classify(info["tipo"], info["combustible"])

        totals_by_fuel[fuel] += day_total

    if unmapped_resources:
        print(f"  Warning: {len(unmapped_resources)} resource codes had no catalog match "
              f"(bucketed as 'Other'): {sorted(unmapped_resources)[:10]}...")

    return totals_by_fuel


def fetch_daily(resource_catalog: dict) -> dict:
    """Most recently completed day (yesterday — XM typically finalises 'Gene' a day behind)."""
    yesterday = dt.date.today() - dt.timedelta(days=1)
    print(f"Fetching Colombia daily generation: {yesterday}")

    items = fetch_metric("Gene", "Recurso", yesterday, yesterday)
    totals_by_fuel = sum_generation(items, resource_catalog)
    mix = build_mix(totals_by_fuel)

    if not mix:
        raise SystemExit("No daily generation data returned from XM.")

    return {
        "country": "Colombia",
        "period_start": yesterday.isoformat(),
        "period_end": yesterday.isoformat(),
        "generated_at_utc": dt.datetime.utcnow().isoformat() + "Z",
        "source": "XM (Administrador del Mercado de Energía Mayorista de Colombia) — SINERGOX public API",
        "mix": mix,
    }


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

    daily = fetch_daily(resource_catalog)
    with open("data/colombia_daily_mix.json", "w", encoding="utf-8") as f:
        json.dump(daily, f, indent=2, ensure_ascii=False)
    print("Wrote data/colombia_daily_mix.json")
    print(json.dumps(daily, indent=2, ensure_ascii=False))

    ytd = fetch_ytd(resource_catalog)
    with open("data/colombia_ytd_mix.json", "w", encoding="utf-8") as f:
        json.dump(ytd, f, indent=2, ensure_ascii=False)
    print("Wrote data/colombia_ytd_mix.json")
    print(json.dumps(ytd, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

