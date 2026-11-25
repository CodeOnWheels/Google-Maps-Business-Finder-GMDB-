# GMDB — Google Maps Business Search

Search Google Maps from a local web app or export business listings from the command line. Built with Python, Playwright, and a plain HTML/CSS/JavaScript interface. No API key or Node.js installation required.

## Features

- Search by business type and location, such as `Bakeries in Los Angeles, CA`.
- See listings appear as they finish loading.
- Sort by name or rating and filter for businesses without a website.
- Export the sorted and filtered results to CSV from the web app.
- Use the command-line scraper for addresses, Maps links, and configurable result limits.

## Quick start

Requires **Python 3.10+** and an internet connection. Install Playwright's Chromium browser once before running the app.

### Windows (PowerShell)

From the project folder:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe server.py
```

After setup, you can also double-click `start.cmd`.

### macOS / Linux

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
.venv/bin/python server.py
```

On Linux, if Chromium reports missing system libraries, run `.venv/bin/python -m playwright install --with-deps chromium`.

Open [localhost:8000](http://localhost:8000), enter a search, and press **Enter**. Keep the terminal running; press **Ctrl+C** to stop the server. To use another port, run `server.py --port 8001` with your virtual environment's Python.

The web app collects up to 40 listings per search and runs one search at a time. Chromium runs in the background and is reused between searches. The latest 20 searches stay in server memory until restart; the page displays the current search and does not provide a history view. Failed searches retain any partial results already collected.

## Command-line export

The examples below use `python` after activating the virtual environment (`.\.venv\Scripts\Activate.ps1` on Windows or `source .venv/bin/activate` on macOS/Linux). You can also use the full virtual environment Python path shown above.

```bash
python scrape_maps.py "Bakeries in Los Angeles, CA"
python scrape_maps.py "Florists in Los Angeles, CA" --limit 50 --output florists.csv
python scrape_maps.py "Mechanics in Los Angeles, CA" --headed --concurrency 1 --output mechanics.csv
```

| Option | Default | Description |
| --- | --- | --- |
| `query` | Required | Business type and location to search. |
| `--limit` | `20` | Maximum listings to collect; must be positive. |
| `--output` | `businesses.csv` | Destination CSV; its parent folder must exist. |
| `--concurrency` | `3` | Parallel listing pages, from 1 to 8. |
| `--headed` | Off | Show Chromium to inspect consent or verification prompts. |
| `--headless` | On | Run Chromium without a visible window. |

The command-line CSV contains `id`, `name`, `rating`, `phone`, `website`, `address`, and `google_maps`. Missing fields are blank. Rows are saved incrementally in completion order; IDs retain the original search order. Existing output files are never overwritten. Choose a new `--output` filename for each run.

The web app's **Export CSV** button exports the visible rows with ID, name, rating, phone number, and website. Both exports use UTF-8 with a BOM and escape potential spreadsheet formulas. Search exports are ignored by Git.

## Reuse the scraper in Python

Reuse a browser for multiple searches within one asynchronous session:

```python
import asyncio
from pathlib import Path

from scrape_maps import MapsScraper


async def main():
    async with MapsScraper(concurrency=3) as scraper:
        await scraper.scrape("Bakeries in Los Angeles, CA", 20, Path("bakeries.csv"))
        await scraper.scrape("Florists in Los Angeles, CA", 20, Path("florists.csv"))


asyncio.run(main())
```

## Project structure

```text
web/index.html        Web interface, sorting, filtering, and CSV export
scrape_maps.py        Playwright scraper and command-line entry point
server.py             Local HTTP server and background search jobs
start.cmd             Windows launcher for an existing virtual environment
requirements.txt      Python dependency
test_scrape_maps.py   Offline scraper tests
test_server.py        Local HTTP server tests
```

## Development

Run the existing tests from the project folder using your virtual environment's Python:

```bash
python -m unittest discover -v
```

Tests use simulated scraping and a temporary local HTTP server; they do not contact Google Maps. Edit `web/index.html` directly—there is no frontend build step.

## Limitations and troubleshooting

- **Missing browser:** run `python -m playwright install chromium` with the same Python environment used to launch the app.
- **No results or verification prompts:** try the command-line scraper with `--headed`. The scraper does not bypass verification.
- **Incomplete results:** Google controls which listings appear. Results are not a complete directory, and individual listings can fail. Social profiles are not extracted.
- **Layout changes or blocking:** changes to Google Maps can interrupt extraction and require updates to the scraper.
- **Local use:** the server binds to `127.0.0.1` and has no authentication. GitHub Pages cannot run this Python/Chromium backend. Remote deployment requires a suitable server and additional authentication and HTTPS configuration.

Use the tool in accordance with applicable Google Maps terms and respect the privacy of people whose information appears in listings.
