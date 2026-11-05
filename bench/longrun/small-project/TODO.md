# TODO

1. Fix the failing test (`python3 -m unittest discover -s tests`).
2. Add `average(records)` to tally/stats.py (mean amount, ValueError on no records), with a test.
3. Add a `--json` option to the CLI: print `{"by_name": {...}, "total": x}` instead of the text lines.
4. parse.py: skip blank lines and lines starting with `#`, with a test.
5. Add a `--sort` option to the CLI: names by decreasing amount.
6. Add a `--pretty` option to the CLI: an aligned table with a header line and a separator.
