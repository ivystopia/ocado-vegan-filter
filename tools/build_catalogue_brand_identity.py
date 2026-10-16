#!/usr/bin/env python3
"""Build bounded entity hints from brand fields, never classification labels."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3

from native_product_identity import fold

GENERIC = {'organic', 'fresh', 'natural', 'quality', 'simply', 'our', 'the',
           'best', 'good', 'extra', 'pure', 'new', 'free', 'home', 'great'}


def selected_names(values):
    return sorted({str(value).strip() for value in values if value
                   and len(fold(value).split()) >= 2 and len(fold(value)) >= 7
                   and not GENERIC.intersection(fold(value).split())},
                  key=lambda value: (-len(value), value))


def build(db, output):
    with sqlite3.connect(Path(db).resolve().as_uri() + '?mode=ro', uri=True) as conn:
        names = selected_names(row[0] for row in conn.execute(
            'SELECT DISTINCT brand FROM products WHERE brand IS NOT NULL'))
        population = conn.execute('SELECT COUNT(*) FROM products').fetchone()[0]
    digest = hashlib.sha256(json.dumps(names, ensure_ascii=True).encode()).hexdigest()
    text = ('"""Generated catalogue entity hints; not vegan evidence or brand reputation.\n'
            'Rebuild explicitly, then validate the changed knowledge before assessment.\n'
            '"""\n'
            'SOURCE_QUERY = "SELECT DISTINCT brand FROM products WHERE brand IS NOT NULL"\n'
            'SOURCE_POPULATION = ' + str(population) + '\n'
            'BRANDS_SHA256 = ' + repr(digest) + '\n'
            'BRAND_LABELS = (\n' +
            ''.join('    ' + repr(name) + ',\n' for name in names) + ')\n')
    Path(output).write_text(text)
    return {'brands': len(names), 'source_population': population, 'brands_sha256': digest}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.db, args.output)))
