"""Offline checks for worker limits and incremental CSV output."""
import asyncio
import csv
import io
from pathlib import Path
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import patch

from scrape_maps import MapsScraper


class FakePage:
    async def close(self):
        pass


class FakeContext:
    closed = False

    def set_default_timeout(self, value):
        pass

    async def route(self, *args):
        pass

    async def new_page(self):
        return FakePage()

    async def close(self):
        self.closed = True


class FakeBrowser:
    def __init__(self):
        self.context = FakeContext()

    async def new_context(self, **kwargs):
        return self.context


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_bounded_workers_partial_results_and_existing_file(self):
        scraper = MapsScraper(concurrency=3)
        scraper.browser = FakeBrowser()
        active = peak = 0

        async def collect(*args):
            return [str(i) for i in range(7)]

        async def extract(page, href, index):
            nonlocal active, peak
            active += 1
            peak = max(active, peak)
            try:
                await asyncio.sleep(0.005)
                if index == 2:
                    raise ValueError("test failure")
                return {"id": index, "name": "Bread, Cake", "phone": "+123"}
            finally:
                active -= 1

        scraper.collect_links = collect
        scraper.extract = extract
        api = types.ModuleType("playwright.async_api")
        api.Error = RuntimeError
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "results.csv"
            with patch.dict(sys.modules, {"playwright": types.ModuleType("playwright"),
                                          "playwright.async_api": api}):
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    count = await scraper.scrape("test", 7, output)
                self.assertEqual(count, 6)
                self.assertEqual(peak, 3)
                self.assertTrue(scraper.browser.context.closed)
                with output.open(encoding="utf-8-sig", newline="") as file:
                    rows = list(csv.DictReader(file))
                self.assertEqual(len(rows), 6)
                self.assertEqual(len({r["id"] for r in rows}), 6)
                self.assertEqual(rows[0]["name"], "Bread, Cake")
                self.assertEqual(rows[0]["phone"], "'+123")
                original = output.read_bytes()
                with self.assertRaises(FileExistsError):
                    await scraper.scrape("test", 7, output)
                self.assertEqual(output.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
