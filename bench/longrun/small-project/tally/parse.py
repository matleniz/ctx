"""Parse "name,amount" lines into (name, amount) records."""


def parse_line(line):
    name, amount = line.split(",")
    return name.strip(), float(amount)


def parse_lines(lines):
    return [parse_line(line) for line in lines]
