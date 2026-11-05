import unittest

from tally.parse import parse_line, parse_lines


class TestParse(unittest.TestCase):
    def test_line(self):
        self.assertEqual(parse_line("ann, 2.5"), ("ann", 2.5))

    def test_lines(self):
        self.assertEqual(parse_lines(["a,1", "b,2"]), [("a", 1.0), ("b", 2.0)])


if __name__ == "__main__":
    unittest.main()
