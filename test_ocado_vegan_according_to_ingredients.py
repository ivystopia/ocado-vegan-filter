#!/usr/bin/env python3
"""Tests for the Ocado vegan-according-to-ingredients scraper helpers."""

from __future__ import annotations

import importlib
import inspect
import json
import unittest
from collections.abc import Mapping, Sequence


MODULE_NAME = "scan_ocado_vegan_according_to_ingredients"


GREEN_BEANS = {
    "retailerProductId": "517986011",
    "name": "Ocado Green Beans",
    "url": "https://www.ocado.com/products/ocado-green-beans-517986011",
    "iconAttributes": [{"label": "Vegetarian", "file": "vegetarian"}],
    "fields": [
        {"title": "Description", "content": "Ocado Green Beans."},
        {"title": "Storage", "content": "Keep refrigerated."},
    ],
    "detailedDescription": "Fresh green beans.",
}

RUMMO_SPAGHETTI = {
    "retailerProductId": "624307011",
    "name": "Rummo Spaghetti No.3",
    "url": "https://www.ocado.com/products/rummo-spaghetti-no-3-624307011",
    "iconAttributes": [{"label": "Vegetarian", "file": "vegetarian"}],
    "fields": [
        {
            "title": "Ingredients",
            "content": "Durum <b>wheat</b> semolina.",
        },
        {
            "title": "Dietary Information",
            "content": "May contain soya and mustard.",
        },
    ],
    "detailedDescription": "Traditional Italian dried pasta.",
}

MS_UDON_NOODLES = {
    "retailerProductId": "528416011",
    "name": "M&S Udon Noodles",
    "url": "https://www.ocado.com/products/m-s-udon-noodles-528416011",
    "iconAttributes": [{"label": "Vegetarian", "file": "vegetarian"}],
    "fields": [
        {
            "title": "Ingredients",
            "content": (
                "Wheat Flour contains Calcium Carbonate, Iron, Niacin, Thiamin, "
                "Water, Salt."
            ),
        },
        {
            "title": "Dietary Information",
            "content": "Suitable for vegetarians.",
        },
    ],
    "detailedDescription": "Ready to wok udon noodles.",
}


OFFICIAL_VEGAN_PRODUCT = {
    "retailerProductId": "999999011",
    "name": "Already Tagged Vegan Product",
    "iconAttributes": [{"label": "Vegan", "file": "vegan"}],
}


class VeganAccordingToIngredientsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.scraper = importlib.import_module(MODULE_NAME)

    def call_helper(self, name: str, *args, **kwargs):
        helper = getattr(self.scraper, name)
        signature = inspect.signature(helper)
        if kwargs:
            try:
                return helper(*args, **kwargs)
            except TypeError:
                pass

        positional = [
            parameter
            for parameter in signature.parameters.values()
            if parameter.default is inspect.Signature.empty
            and parameter.kind
            in (parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD)
        ]
        return helper(*args[: len(positional)])

    def explicitly_says_vegan(self, text: str, title: str = "Description") -> bool:
        helper = getattr(self.scraper, "explicitly_says_vegan")
        signature = inspect.signature(helper)
        if len(signature.parameters) == 1:
            return bool(helper(text))
        return bool(helper(title, text))

    def evidence_for(self, product: Mapping[str, object]) -> list[object]:
        evidence = self.call_helper("extract_product_evidence", product)
        self.assertIsInstance(evidence, Sequence)
        self.assertNotIsInstance(evidence, (str, bytes))
        return list(evidence)

    def assert_evidence_mentions(self, evidence: Sequence[object], *needles: str) -> None:
        haystack = json.dumps(evidence, sort_keys=True).lower()
        for needle in needles:
            self.assertIn(needle.lower(), haystack, evidence)

    def test_normalize_text_unescapes_html_strips_tags_and_collapses_whitespace(self) -> None:
        normalized = self.call_helper(
            "normalize_text",
            " Suitable&nbsp;for <b>vegans</b>\n\nand vegetarians. ",
        )

        self.assertEqual(normalized, "Suitable for vegans and vegetarians.")

    def test_explicitly_says_vegan_accepts_positive_claims_and_rejects_negatives(self) -> None:
        self.assertTrue(self.explicitly_says_vegan("Suitable for vegans."))
        self.assertTrue(self.explicitly_says_vegan("Suitable for vegetarians and vegans."))
        self.assertTrue(self.explicitly_says_vegan("Vegan", title="Dietary Information"))
        self.assertFalse(self.explicitly_says_vegan("Not suitable for vegans."))
        self.assertFalse(self.explicitly_says_vegan("Suitable for vegetarians."))
        self.assertFalse(self.explicitly_says_vegan("Vegan-style flavour, contains milk."))

    def test_product_has_vegan_tag_uses_icon_attributes_only(self) -> None:
        self.assertTrue(self.call_helper("product_has_vegan_tag", OFFICIAL_VEGAN_PRODUCT))
        self.assertFalse(self.call_helper("product_has_vegan_tag", GREEN_BEANS))
        self.assertFalse(
            self.call_helper(
                "product_has_vegan_tag",
                {
                    "name": "Vegan in name is not an Ocado vegan tag",
                    "iconAttributes": [{"label": "Vegetarian", "file": "vegetarian"}],
                },
            )
        )

    def test_green_beans_single_ingredient_without_ingredients_field_includes_evidence(self) -> None:
        evidence = self.evidence_for(GREEN_BEANS)

        self.assert_evidence_mentions(evidence, "517986011", "green beans")
        self.assert_evidence_mentions(evidence, "single")
        self.assert_evidence_mentions(evidence, "no ingredients")

    def test_rummo_spaghetti_ingredients_include_evidence(self) -> None:
        evidence = self.evidence_for(RUMMO_SPAGHETTI)

        self.assert_evidence_mentions(evidence, "624307011", "ingredients")
        self.assert_evidence_mentions(evidence, "durum wheat semolina")

    def test_ms_udon_ambiguous_fortified_wheat_is_excluded_from_evidence(self) -> None:
        evidence = self.evidence_for(MS_UDON_NOODLES)
        evidence_text = json.dumps(evidence, sort_keys=True).lower()

        self.assertNotIn("528416011", evidence_text, evidence)
        self.assertNotIn("fortified wheat", evidence_text, evidence)
        self.assertEqual(evidence, [])

    def test_classifier_prompt_contains_product_context_evidence_and_ambiguity_rules(self) -> None:
        evidence = self.evidence_for(RUMMO_SPAGHETTI)
        prompt = self.call_helper("build_classifier_prompt", RUMMO_SPAGHETTI, evidence)

        self.assertIsInstance(prompt, str)
        self.assertIn("624307011", prompt)
        self.assertIn("Rummo Spaghetti", prompt)
        self.assertIn("Durum wheat semolina", prompt)
        self.assertIn("vegan", prompt.lower())
        self.assertIn("ambiguous", prompt.lower())

    def test_merge_classifier_decisions_keeps_yes_decisions_and_drops_rejections(self) -> None:
        candidates = [
            {
                "retailerProductId": "517986011",
                "name": GREEN_BEANS["name"],
                "evidence": [{"source": "single_ingredient", "text": "Single ingredient green beans"}],
            },
            {
                "retailerProductId": "624307011",
                "name": RUMMO_SPAGHETTI["name"],
                "evidence": [{"source": "ingredients", "text": "Durum wheat semolina"}],
            },
            {
                "retailerProductId": "528416011",
                "name": MS_UDON_NOODLES["name"],
                "evidence": [{"source": "ingredients", "text": "Fortified wheat flour"}],
            },
        ]
        decisions = {
            "517986011": {"isVegan": True, "reason": "Single ingredient vegetable"},
            "624307011": {"isVegan": True, "reason": "Plain dried pasta ingredients"},
            "528416011": {"isVegan": False, "reason": "Ambiguous fortified wheat"},
        }

        merged = self.call_helper("merge_classifier_decisions", candidates, decisions)
        merged_text = json.dumps(merged, sort_keys=True)

        self.assertIn("517986011", merged_text)
        self.assertIn("624307011", merged_text)
        self.assertNotIn("528416011", merged_text)
        self.assertIn("Single ingredient vegetable", merged_text)

    def test_manufacturer_list_skip_helper_if_exposed(self) -> None:
        helper = getattr(self.scraper, "should_skip_vegan_according_to_manufacturer", None)
        if helper is None:
            self.skipTest("vegan-according-to-manufacturer manufacturer-list skip helper is optional")

        manufacturer_vegan_ids = {"517986011"}
        self.assertTrue(helper(GREEN_BEANS, manufacturer_vegan_ids))
        self.assertFalse(helper(RUMMO_SPAGHETTI, manufacturer_vegan_ids))


if __name__ == "__main__":
    unittest.main()
