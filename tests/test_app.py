import json
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import urlopen

from app import create_server


class CatalogServiceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = create_server(host="127.0.0.1", port=0)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def test_health_returns_200(self) -> None:
        with urlopen(f"http://127.0.0.1:{self.port}/health") as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(
                json.load(response),
                {"status": "ok", "service": "catalog-service"},
            )

    def test_unknown_path_returns_404(self) -> None:
        with self.assertRaises(HTTPError) as context:
            urlopen(f"http://127.0.0.1:{self.port}/unknown")
        self.assertEqual(context.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
