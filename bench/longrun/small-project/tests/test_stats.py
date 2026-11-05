import unittest

from tally.stats import by_name, total


class TestStats(unittest.TestCase):
    def test_total(self):
        self.assertEqual(total([("a", 1.0), ("b", 2.0), ("a", 3.0)]), 6.0)

    def test_by_name(self):
        self.assertEqual(by_name([("a", 1.0), ("b", 2.0), ("a", 3.0)]), {"a": 4.0, "b": 2.0})


if __name__ == "__main__":
    unittest.main()
