"""HTTP integration checks without hitting Google."""
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from server import Handler


class Jobs:
    def start(self, query, limit):
        self.request = (query, limit)
        return 'test-job'

    def snapshot(self, job_id):
        if job_id == 'test-job':
            return {'id': job_id, 'state': 'complete', 'results': [{'id': '1', 'name': 'Test Bakery', 'maps': 'https://example.com'}], 'error': None}


class APITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.server.jobs = Jobs()
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def request(self, path, data=None, origin=None):
        headers = {'Content-Type': 'application/json'}
        if origin:
            headers['Origin'] = origin
        req = Request(self.base + path, data=json.dumps(data).encode() if data is not None else None, headers=headers)
        try:
            response = urlopen(req, timeout=5)
        except HTTPError as error:
            response = error
        with response:
            return response.status, response.read()

    def test_search_and_poll(self):
        status, body = self.request('/api/search', {'query': '  Bakers in Los Angeles  ', 'limit': 3})
        self.assertEqual(status, 202)
        self.assertEqual(json.loads(body)['id'], 'test-job')
        self.assertEqual(self.server.jobs.request, ('Bakers in Los Angeles', 3))
        status, body = self.request('/api/jobs/test-job')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['results'][0]['name'], 'Test Bakery')

    def test_validation_and_origin(self):
        for data in [{'query': ''}, {'query': 'bakery', 'limit': True}, {'query': 'bakery', 'limit': 101}, []]:
            self.assertEqual(self.request('/api/search', data)[0], 400)
        self.assertEqual(self.request('/api/search', {'query': 'bakery'}, origin='https://other.example')[0], 403)
        self.assertEqual(self.request('/api/jobs/missing')[0], 404)
        self.assertEqual(self.request('/scrape_maps.py')[0], 404)

    def test_frontend_served(self):
        status, body = self.request('/')
        self.assertEqual(status, 200)
        self.assertIn(b'/api/jobs/', body)
        self.assertNotIn(b'SERPAPI', body)


if __name__ == '__main__':
    unittest.main()
