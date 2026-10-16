"""Provisional claims cannot bypass complete-product semantic assessment."""
from pathlib import Path
import sqlite3
import sys
import unittest
from dataclasses import replace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import build_ocado_database as database
import classify_ocado_vegan as classifier


def context(**fields):
    return {'product': {'id': '1', 'name': 'Example Ready Meal', **fields},
            'flags': [], 'categories': [], 'manufacturer_vegan_evidence': []}


class SemanticClaimRoutingTests(unittest.TestCase):
    def test_tag_and_manufacturer_are_supported_bases_but_need_semantic_readings(self):
        for fields, reason in [({'official_vegan': 1}, 'tagged'),
                               ({'dietary_information': 'Suitable for Vegans'}, 'manufacturer'),
                               ({'name': 'Example Vegan Meal', 'dietary_information': 'Suitable for Vegans'}, 'manufacturer')]:
            with self.subTest(reason=reason, fields=fields):
                value = context(**fields)
                candidate = classifier.classify_rule_candidate(value)
                self.assertEqual((candidate.vegan_status, candidate.vegan_reason), ('vegan', reason))
                self.assertIsNone(classifier.classify_by_rules(value))

    def test_unrecognised_recipe_prose_cannot_pass_from_an_official_tag_alone(self):
        value = context(official_vegan=1, ingredients='Carrots, Water',
                        detailed_description='Our ready meal consists of aubergine and black beans.')
        # The semantic interpreters must assess arbitrary composition text;
        # no existing named-ingredient comparison is assumed to understand it.
        self.assertEqual(classifier.classify_rule_candidate(value).vegan_reason, 'tagged')
        self.assertIsNone(classifier.classify_by_rules(value))

    def test_conclusive_animal_and_ordinary_food_routes_remain_deterministic(self):
        animal = classifier.classify_by_rules(context(name='Milk Drink', ingredients='Milk', official_vegan=1))
        self.assertEqual(animal.vegan_status, 'nonvegan')
        ordinary = classifier.classify_by_rules(context(name='Carrots', ingredients=None))
        self.assertEqual((ordinary.vegan_status, ordinary.vegan_reason), ('vegan', 'ingredients'))

    def create_database(self):
        conn = sqlite3.connect(':memory:'); conn.row_factory = sqlite3.Row
        self.addCleanup(conn.close)
        database.create_schema(conn); classifier.ensure_classification_schema(conn)
        conn.execute("INSERT INTO products(id,name,official_vegan) VALUES ('1','Example Ready Meal',1)")
        conn.commit()
        return conn

    def test_rule_writer_leaves_claims_unclassified_for_the_normal_model_queue(self):
        conn = self.create_database()
        self.assertEqual(classifier.classify_rules(conn, selection_scope='all-known'), 0)
        row = conn.execute("SELECT vegan_status,vegan_audit_id FROM products WHERE id='1'").fetchone()
        self.assertIsNone(row['vegan_status']); self.assertIsNone(row['vegan_audit_id'])
        self.assertEqual(classifier.select_unclassified_product_ids(conn, limit=0, selection_scope='all-known'), ['1'])

    def test_direct_candidate_write_cannot_bypass_the_routing_contract(self):
        conn = self.create_database()
        candidate = classifier.classify_rule_candidate(classifier.load_product_context(conn, '1'))
        run = classifier.start_run(conn, mode='rules', selection_scope='all-known')
        with self.assertRaisesRegex(ValueError, 'independent semantic classification'):
            classifier.write_classification(conn, run, candidate)
        self.assertIsNone(conn.execute("SELECT vegan_status FROM products WHERE id='1'").fetchone()[0])
        self.assertEqual(conn.execute('SELECT count(*) FROM product_vegan_classification_audit').fetchone()[0], 0)

    def test_canonical_reason_correction_cannot_bypass_the_writer_guard(self):
        conn = self.create_database()
        candidate = classifier.classify_rule_candidate(classifier.load_product_context(conn, '1'))
        run = classifier.start_run(conn, mode='rules', selection_scope='all-known')
        with self.assertRaisesRegex(ValueError, 'independent semantic classification'):
            classifier.write_classification(conn, run, replace(candidate, vegan_reason='ingredients'))
        self.assertIsNone(conn.execute("SELECT vegan_status FROM products WHERE id='1'").fetchone()[0])
        self.assertEqual(conn.execute('SELECT count(*) FROM product_vegan_classification_audit').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
