"""Search Google Maps and save the available listings to CSV."""

import argparse
import asyncio
import csv
from pathlib import Path
import sys
from urllib.parse import quote

FIELDS = ["id", "name", "rating", "phone", "website", "address", "google_maps"]


def csv_value(value):
    text = str(value or "")
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) else text


class MapsScraper:
    """Reuse one browser across calls inside `async with MapsScraper()`."""

    def __init__(self, headless=True, concurrency=3):
        if not 1 <= concurrency <= 8:
            raise ValueError("concurrency must be between 1 and 8")
        self.headless = headless
        self.concurrency = concurrency
        self.playwright = self.browser = None

    async def __aenter__(self):
        from playwright.async_api import async_playwright

        self.playwright = await async_playwright().start()
        try:
            self.browser = await self.playwright.chromium.launch(headless=self.headless)
        except BaseException:
            await self.playwright.stop()
            raise
        return self

    async def __aexit__(self, *exc):
        try:
            if self.browser:
                await self.browser.close()
        finally:
            await self.playwright.stop()

    @staticmethod
    async def block_resources(route):
        if route.request.resource_type in {"image", "font", "media"}:
            await route.abort()
        else:
            await route.continue_()

    async def collect_links(self, page, query, limit):
        from playwright.async_api import TimeoutError as BrowserTimeout

        await page.goto(
            "https://www.google.com/maps/search/" + quote(query, safe="") + "?hl=en",
            wait_until="domcontentloaded", timeout=60000,
        )
        if not self.headless:
            print("Complete any consent or verification in the browser.")
        feed = page.locator('[role="feed"]')
        try:
            await feed.wait_for(state="visible", timeout=15000 if self.headless else 60000)
        except BrowserTimeout:
            raise RuntimeError(
                "No results list appeared. Try --headed to check consent or verification; "
                "the query may have no matches or Google's layout may have changed."
            ) from None

        links, seen, unchanged = [], set(), 0
        for _ in range(100):
            for href in await feed.locator('a[href*="/maps/place/"]').evaluate_all(
                "elements => elements.map(el => el.href)"
            ):
                if href not in seen:
                    seen.add(href)
                    links.append(href)
            if len(links) >= limit:
                break
            await feed.evaluate("el => el.scrollTop = el.scrollHeight")
            try:
                await page.wait_for_function(
                    """seen => Array.from(document.querySelectorAll(
                        '[role="feed"] a[href*="/maps/place/"]'
                    )).some(el => !seen.includes(el.href))""",
                    arg=list(seen), timeout=2500,
                )
                unchanged = 0
            except BrowserTimeout:
                unchanged += 1
                if unchanged >= 3:
                    break
        if not links:
            raise RuntimeError("No business links found. Google's layout may have changed.")
        return links[:limit]

    async def extract(self, page, href, index):
        await page.goto(href, wait_until="domcontentloaded", timeout=30000)
        await page.locator("h1").first.wait_for(state="visible")
        # Wait for listing fields, rather than sleeping a fixed amount on every page.
        await page.wait_for_function("""() => {
            const main = document.querySelector('[role="main"]');
            return main && main.querySelector('h1') &&
                main.querySelector('[data-item-id]') &&
                !main.querySelector('[aria-busy="true"]');
        }""", timeout=10000)
        data = await page.evaluate("""() => {
            const main = document.querySelector('[role="main"]');
            const attr = (selector, name) => main.querySelector(selector)?.getAttribute(name) || '';
            return {
                name: main.querySelector('h1')?.innerText.trim() || '',
                rating: attr('[role="img"][aria-label*=" stars"]', 'aria-label').split(' stars')[0],
                phone: attr('[data-item-id^="phone:tel:"]', 'data-item-id').replace(/^phone:tel:/, ''),
                website: attr('a[data-item-id="authority"]', 'href'),
                address: attr('[data-item-id="address"]', 'aria-label').replace(/^Address: /, '')
            };
        }""")
        if not data["name"]:
            raise ValueError("Listing has no business name")
        return {"id": index, **data, "google_maps": href}

    async def scrape(self, query, limit, output, on_result=None):
        from playwright.async_api import Error as BrowserError

        if not self.browser:
            raise RuntimeError("Use MapsScraper inside an async with block")
        # Separate contexts isolate simultaneous searches; the browser is reused.
        context = await self.browser.new_context(locale="en-US", service_workers="block")
        context.set_default_timeout(10000)
        tasks = []
        completed = 0
        try:
            await context.route("**/*", self.block_resources)
            search_page = await context.new_page()
            links = await self.collect_links(search_page, query, limit)
            await search_page.close()
            queue = asyncio.Queue()
            for index, href in enumerate(links, 1):
                queue.put_nowait((index, href))

            # Exclusive creation protects an existing export, including concurrent runs.
            with Path(output).open("x", newline="", encoding="utf-8-sig") as file:
                writer = csv.DictWriter(file, fieldnames=FIELDS)
                writer.writeheader()
                file.flush()

                async def worker():
                    nonlocal completed
                    page = await context.new_page()
                    try:
                        while True:
                            try:
                                index, href = queue.get_nowait()
                            except asyncio.QueueEmpty:
                                return
                            try:
                                row = await self.extract(page, href, index)
                            except (BrowserError, ValueError) as error:
                                print(f"Skipped listing {index}: {error}", file=sys.stderr)
                            else:
                                # No await between writing and flushing: workers cannot interleave.
                                writer.writerow({key: csv_value(row.get(key, "")) for key in FIELDS})
                                file.flush()
                                completed += 1
                                if on_result:
                                    on_result(row)
                                print(f"{completed}. {row['name']}")
                            finally:
                                queue.task_done()
                    finally:
                        await page.close()

                tasks = [asyncio.create_task(worker())
                         for _ in range(min(self.concurrency, len(links))) ]
                try:
                    await asyncio.gather(*tasks)
                finally:
                    for task in tasks:
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            await context.close()
        if not completed:
            raise RuntimeError("No listings extracted. The CSV contains only headers.")
        return completed


async def scrape(query, limit, output, headless=True, concurrency=3):
    async with MapsScraper(headless, concurrency) as scraper:
        return await scraper.scrape(query, limit, output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", help='Example: "Bakeries in Los Angeles, CA"')
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--output", type=Path, default=Path("businesses.csv"))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--headed", dest="headless", action="store_false", help="Show the browser")
    mode.add_argument("--headless", dest="headless", action="store_true", help="Hide the browser (default)")
    parser.set_defaults(headless=True)
    parser.add_argument("--concurrency", type=int, default=3, help="Parallel listing pages, 1–8 (default: 3)")
    args = parser.parse_args()
    if not args.query.strip():
        parser.error("query cannot be empty")
    if args.limit < 1:
        parser.error("--limit must be positive")
    if not 1 <= args.concurrency <= 8:
        parser.error("--concurrency must be between 1 and 8")
    if args.output.exists():
        parser.error("output already exists; use --output with a new filename")
    if not args.output.parent.is_dir():
        parser.error("output directory does not exist")
    try:
        count = asyncio.run(scrape(args.query, args.limit, args.output, args.headless, args.concurrency))
    except KeyboardInterrupt:
        print("\nStopped. Completed listings remain in the CSV.", file=sys.stderr)
        return 130
    except ImportError:
        print("Install dependencies: python -m pip install -r requirements.txt", file=sys.stderr)
        return 1
    except Exception as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    print(f"Saved {count} businesses to {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
