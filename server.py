"""Local GMDB web app. Run: python server.py"""
import argparse
import asyncio
import copy
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
from urllib.parse import urlsplit
from uuid import uuid4

from scrape_maps import MapsScraper

ROOT = Path(__file__).resolve().parent


class SearchJobs:
    def __init__(self):
        self.jobs = {}
        self.lock = threading.Lock()
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        self.scraper = None
        self.future = None

    def start(self, query, limit):
        with self.lock:
            if any(j['state'] == 'running' for j in self.jobs.values()):
                raise RuntimeError('A search is already running. Try again when it finishes.')
            while len(self.jobs) >= 20:
                self.jobs.pop(next(iter(self.jobs)))
            job_id = uuid4().hex
            self.jobs[job_id] = dict(id=job_id, state='running', results=[], error=None)
        self.future = asyncio.run_coroutine_threadsafe(self.run(job_id, query, limit), self.loop)
        return job_id

    def snapshot(self, job_id):
        with self.lock:
            return copy.deepcopy(self.jobs.get(job_id))

    def update(self, job_id, **values):
        with self.lock:
            self.jobs[job_id].update(values)

    async def run(self, job_id, query, limit):
        def result(row):
            data = dict(row, id=str(row['id']), maps=row['google_maps'])
            with self.lock:
                self.jobs[job_id]['results'].append(data)
        try:
            if self.scraper is None:
                scraper = MapsScraper()
                await scraper.__aenter__()
                self.scraper = scraper
            with tempfile.TemporaryDirectory(prefix='gmdb-') as folder:
                await asyncio.wait_for(
                    self.scraper.scrape(query, limit, Path(folder) / 'results.csv', on_result=result),
                    timeout=300,
                )
            self.update(job_id, state='complete')
        except asyncio.TimeoutError:
            self.update(job_id, state='failed', error='Search timed out. Try a more specific location.')
        except Exception as error:
            print(f'Search failed: {error}', flush=True)
            message = str(error)
            if 'Executable doesn\'t exist' in message:
                message = 'Browser missing. Run: python -m playwright install chromium'
            self.update(job_id, state='failed', error=message)

    def close(self):
        async def cleanup():
            if self.future and not self.future.done():
                self.future.cancel()
                # Let cancellation unwind the workers before closing the browser.
                try:
                    await asyncio.wrap_future(self.future)
                except asyncio.CancelledError:
                    pass
            if self.scraper:
                await self.scraper.__aexit__(None, None, None)
        try:
            asyncio.run_coroutine_threadsafe(cleanup(), self.loop).result(timeout=15)
        finally:
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(timeout=5)


class Handler(BaseHTTPRequestHandler):
    def send(self, data, status=200, content_type='application/json; charset=utf-8'):
        body = json.dumps(data).encode() if content_type.startswith('application/json') else data
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def local_request(self):
        port = self.server.server_port
        hosts = {f'127.0.0.1:{port}', f'localhost:{port}'}
        if self.headers.get('Host') not in hosts:
            self.send({'error': 'Invalid host'}, 403)
            return False
        origin = self.headers.get('Origin')
        if origin and origin not in {'http://' + host for host in hosts}:
            self.send({'error': 'Invalid origin'}, 403)
            return False
        return True

    def do_GET(self):
        if not self.local_request():
            return
        path = urlsplit(self.path).path
        if path == '/':
            return self.send((ROOT / 'web/index.html').read_bytes(), content_type='text/html; charset=utf-8')
        if path == '/api/health':
            return self.send({'status': 'ok'})
        if path.startswith('/api/jobs/'):
            job = self.server.jobs.snapshot(path.removeprefix('/api/jobs/'))
            return self.send(job if job else {'error': 'Search not found'}, 200 if job else 404)
        self.send({'error': 'Not found'}, 404)

    def do_POST(self):
        if not self.local_request():
            return
        if self.path != '/api/search':
            return self.send({'error': 'Not found'}, 404)
        if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
            return self.send({'error': 'Expected JSON'}, 415)
        try:
            size = int(self.headers.get('Content-Length', 0))
            if not 0 < size <= 4096:
                return self.send({'error': 'Invalid request size'}, 400)
            payload = json.loads(self.rfile.read(size))
            if not isinstance(payload, dict):
                raise ValueError('Expected an object')
            query, limit = payload.get('query'), payload.get('limit', 40)
            if not isinstance(query, str) or not 1 <= len(query.strip()) <= 200:
                raise ValueError('Enter a search between 1 and 200 characters.')
            if type(limit) is not int or not 1 <= limit <= 100:
                raise ValueError('Limit must be between 1 and 100.')
        except (ValueError, UnicodeError) as error:
            return self.send({'error': str(error)}, 400)
        try:
            job_id = self.server.jobs.start(query.strip(), limit)
        except RuntimeError as error:
            return self.send({'error': str(error)}, 409)
        self.send({'id': job_id}, 202)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    server.jobs = SearchJobs()
    print(f'GMDB is running at http://localhost:{args.port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        server.jobs.close()


if __name__ == '__main__':
    main()
