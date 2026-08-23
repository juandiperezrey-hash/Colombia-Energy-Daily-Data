# Cordillera Energy — Colombia Grid Data Pipeline

Pulls Colombia's year-to-date electricity generation mix (by fuel source)
from **XM** (the operator of Colombia's wholesale energy market) and publishes
it as a small JSON file this repo keeps up to date automatically.

No API key or account is needed — XM's API is public.

## What this does

1. `scripts/fetch_colombia_mix.py` calls XM's public SINERGOX API for daily
   generation ("Gene") by power plant ("Recurso"), from Jan 1 of the current
   year through today.
2. It matches each plant to its fuel type (hydro, thermal/gas, solar, wind,
   biomass) using XM's resource catalog.
3. It sums energy by fuel type and writes the percentage breakdown to
   `data/colombia_ytd_mix.json`.
4. A GitHub Actions workflow (`.github/workflows/update-colombia-mix.yml`)
   runs this script every day automatically and commits the updated file.

## Setting this up on GitHub

1. Create a new repository on GitHub (e.g. `cordillera-xm-data`), public or
   private — either works.
2. Upload everything in this folder, preserving the structure:
   ```
   .github/workflows/update-colombia-mix.yml
   scripts/fetch_colombia_mix.py
   data/               (can start empty)
   README.md
   ```
3. That's it — no secrets or API keys to configure. The workflow already has
   permission to commit back to the repo (`permissions: contents: write`).
4. To test it immediately instead of waiting for the daily schedule: go to
   the **Actions** tab → **Update Colombia Generation Mix (XM)** →
   **Run workflow**.
5. Once it runs successfully, `data/colombia_ytd_mix.json` will appear/update
   in the repo, and you can fetch it publicly (even from a private repo's
   public assets are not exposed — use a **public** repo, or GitHub Pages, if
   the website needs to read this file directly) at:
   ```
   https://raw.githubusercontent.com/<your-username>/<repo-name>/main/data/colombia_ytd_mix.json
   ```

## Wiring it into the website

Once the JSON file exists at that raw GitHub URL, the site's "Year-to-Date
Generation Mix" placeholder chart for Colombia can `fetch()` it on page load
and render it with the same `renderPie()` function already used for the other
two charts. Send me the repo name once it's live and I'll wire that up.

## Testing locally first (recommended)

Before relying on the GitHub Actions schedule, it's worth running the script
once on your own machine to confirm XM's response shape hasn't changed:

```bash
pip install requests
python scripts/fetch_colombia_mix.py
```

XM's API field names have shifted slightly across releases in the past, so
if the script errors out, share the error message and the printed response
and I'll adjust the field-mapping logic.

## Doing the same for the UK

The UK doesn't have a single free equivalent to XM, but **Carbon Intensity
API** (api.carbonintensity.org.uk) and **Elexon/NESO** publish free,
no-key-required generation-mix data. Once the Colombia pipeline is confirmed
working, I can build the equivalent `fetch_uk_mix.py` following the same
pattern.
