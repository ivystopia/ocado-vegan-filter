"""An own-product pasta account must bind to the actual rice-bowl recipe."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import classify_ocado_vegan as classifier
import native_product_identity as identity


def context(description, ingredients='Rice, Lentils, Tofu, Soy Sauce', name='Example Teriyaki Rice Bowl'):
    return {'product': {'id': '1', 'name': name, 'ingredients': ingredients,
                        'detailed_description': description, 'official_vegan': 1},
            'flags': [], 'categories': [], 'manufacturer_vegan_evidence': []}


class RiceBowlIdentityTests(unittest.TestCase):
    def test_own_cooked_pasta_conflicts_with_the_declared_rice_bowl(self):
        value = context("Rich and smoky, we've cooked up lentils and rigatini pasta in tomato sauce.")
        self.assertTrue(identity.rice_bowl_pasta_account_conflict_refs(value))
        self.assertIsNone(classifier.classify_by_rules(value))

    def test_serving_other_products_and_declared_combinations_do_not_conflict(self):
        for description, ingredients, name in [
            ("We've cooked up rice and lentils in teriyaki sauce.", 'Rice, Lentils', 'Example Rice Bowl'),
            ("Serve with rigatini pasta in tomato sauce.", 'Rice, Lentils', 'Example Rice Bowl'),
            ("For our other bowl we've cooked up rigatini pasta.", 'Rice, Lentils', 'Example Rice Bowl'),
            ("You can add pasta to this rice bowl.", 'Rice, Lentils', 'Example Rice Bowl'),
            ("We've cooked up rice and pasta together.", 'Rice, Rigatini Pasta, Lentils', 'Example Rice Bowl'),
            ("We've cooked up lentils and rigatini pasta.", 'Pasta, Lentils', 'Example Pasta Bowl'),
            ("We've cooked up noodles.", 'Rice, Rice Noodles', 'Example Rice Bowl'),
        ]:
            with self.subTest(description=description):
                self.assertEqual(identity.rice_bowl_pasta_account_conflict_refs(context(description, ingredients, name)), [])


if __name__ == '__main__':
    unittest.main()
