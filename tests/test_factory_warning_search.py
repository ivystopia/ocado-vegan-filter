"""Differential contracts for the equivalent linear factory-warning search."""
from pathlib import Path
import random
import re
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import vegan_policy as policy

ORIGINAL = re.compile(r'\b(?:factory|facility|site|equipment|production line)\b.*\b(?:handles?|handling|processes?|processing|uses?|using)\b|\b(?:cross[- ]contamination|cross[- ]contact)\b', re.I)


class FactoryWarningSearchTests(unittest.TestCase):
    def test_order_word_boundaries_case_and_newlines_match_original(self):
        examples = ['', 'Factory handles milk', 'handling milk at a factory',
            'factory\nhandles milk', 'factory\rhandles milk', 'SITE uses milk',
            'website handles products', 'production lines handle milk',
            'equipment on site never processes food', 'cross-contact',
            'cross contamination', 'factory\nsite uses milk']
        for text in examples:
            with self.subTest(text=text):
                self.assertEqual(policy.factory_warning(text), bool(ORIGINAL.search(text)))

    def test_differential_generated_token_sequences(self):
        rng = random.Random(1729)
        words = ['site', 'facility', 'Factory', 'equipment', 'production line',
            'handles', 'handling', 'process', 'processes', 'processing', 'used',
            'use', 'uses', 'using', 'website', 'milk', 'cross-contact', 'normal',
            '\n', '\r', '.', ',', ';']
        for _ in range(2000):
            text = ' '.join(rng.choice(words) for _ in range(rng.randrange(25)))
            self.assertEqual(policy.factory_warning(text), bool(ORIGINAL.search(text)), text)

    def test_long_repeated_site_page_remains_negative(self):
        self.assertFalse(policy.factory_warning('Visit our site for product information. '*5000))


if __name__ == '__main__':
    unittest.main()
