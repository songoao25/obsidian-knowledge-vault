from pathlib import Path
import tempfile
import unittest

from knowledge_vault.state import RunLock


class StateTests(unittest.TestCase):
    def test_lock_rejects_second_writer_and_cleans_up(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run.lock"
            with RunLock.acquire(path):
                self.assertTrue(path.exists())
                with self.assertRaises(RuntimeError):
                    with RunLock.acquire(path):
                        pass
            self.assertFalse(path.exists())


if __name__ == "__main__": unittest.main()

