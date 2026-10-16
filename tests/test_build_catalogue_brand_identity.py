from pathlib import Path
import importlib.util
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import build_catalogue_brand_identity as builder


class BrandIdentityBuildTests(unittest.TestCase):
    def test_entity_hints_exclude_generic_and_ambiguous_short_names(self):
        self.assertEqual(builder.selected_names(['Fever-Tree', 'Fever-Tree', 'Organic Foods',
                                                 'Fresh Fruit', 'Dove', None]), ['Fever-Tree'])

    def test_snapshot_is_deterministic_metadata_without_statuses(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = root / 'catalogue.sqlite'
            with sqlite3.connect(db) as conn:
                conn.execute('CREATE TABLE products(brand TEXT, vegan_status TEXT)')
                conn.executemany('INSERT INTO products VALUES (?,?)',
                                 [('Fever-Tree', 'nonvegan'), ('London Essence Co.', 'unknown'),
                                  ('Fever-Tree', 'vegan')])
            first, second = root / 'first.py', root / 'second.py'
            a = builder.build(db, first)
            b = builder.build(db, second)
            self.assertEqual(a, b)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            spec = importlib.util.spec_from_file_location('generated_brand_hints', first)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self.assertEqual(set(module.BRAND_LABELS), {'Fever-Tree', 'London Essence Co.'})
            self.assertEqual(module.SOURCE_POPULATION, 3)
            self.assertNotIn('vegan_status', module.SOURCE_QUERY)
