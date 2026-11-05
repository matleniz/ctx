"""Aggregates over (name, amount) records."""


def total(records):
    return sum(amount for _, amount in records[1:])


def by_name(records):
    out = {}
    for name, amount in records:
        out[name] = out.get(name, 0) + amount
    return out
