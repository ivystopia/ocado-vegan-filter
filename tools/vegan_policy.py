"""Pure, evidence-only whole-product policy shared by every decision path.

Models interpret evidence; this module enforces evidence references, precedence,
known risks and the positive decision contract. It performs no I/O or research.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
from typing import Any
from functools import lru_cache
import native_product_identity

POLICY_VERSION = "whole-product-v2"
KNOWLEDGE_VERSION = "vegan-risk-registry-v51"
CATALOGUE_PROVENANCE = {"retailer": "Ocado", "market": "UK"}
CATALOGUE_CONTEXT_POLICY = (
    "The application supplies catalogue_provenance: these target products belong to Ocado's UK catalogue, so the target sale market is UK even when individual product fields omit it. This is catalogue provenance, not a manufacturer's country of origin, production site or proof that formulations in different markets are identical. Assess each website claim against that target and its complete source/market_scope. A supplied UK scope can establish market alignment; a different-market restriction still needs applicable resolution. Cite actual source market metadata or source statements in source_applicability.market, never fabricate a product market field or a pointer to the shared catalogue_provenance object."
)

BATCH_CONTEXT_POLICY = (
    "Assess each product independently. A batch is transport only: use that product's own context and linked full source captures, never another product's fields or assessment. Identical names, URLs or shared source keys across contexts do not merge their evidence, prove additional variants or create an applicability conflict. Any contrary variant or scope restriction must be evidenced within this product's supplied context or linked capture."
)
SOURCE_VARIANT_POLICY = (
    "An applicable unqualified whole-product assurance about the matched named product need not separately enumerate ordinary pack formats or sizes. Their presence in the product record alone is not a source-variant conflict. Establish applicability from the named-product assertion and its full capture, without claiming the source explicitly mentions unlisted formats or materials. This does not make a matching generic title/URL sufficient, cover a different flavour, colour, formulation, market or vintage, or erase a source's displayed selection, format restriction, food-only scope or contrary identity evidence. Those material differences still require applicable resolution."
)
MATERIALITY_VERSION = "vegan-materiality-v1"
MATERIALITY_POLICY = (
    "Only block discrepancies that materially affect vegan suitability or whether the relied-on vegan claim applies. "
    "A copy discrepancy is not automatically a material identity conflict. Explain its concrete connection to animal use, "
    "unresolved sourcing/processing/component suitability, or a demonstrated failure to bind the vegan assurance to the sold product. "
    "An independent whole-product official tag or native manufacturer assurance applies to its SKU unless actual supplied evidence "
    "shows that assurance is restricted, contradicted by animal use, or belongs to another product. Do not infer that every field, "
    "including the vegan assurance, is misjoined just because another field contains inconsistent copy. "
    "Plant-only recipe differences, ingredient counts, beer style, nutrition wording, botanical descriptions, silicone-free versus "
    "dimethicone, perfume-free versus fragrance, and ordinary bottle/can wording do not alone defeat that independently applicable assurance. "
    "Do not invent which recipe, material or formulation is physically present. If uncertainty changes whether the positive basis applies "
    "(for example a claim limited to a different variant, market, formulation or vintage), or leaves an actual relevant animal-use concern, "
    "return unknown. Correctly matched definite animal ingredients or processing still mean nonvegan even with a vegan assurance. "
    "Plain unmodified botanical products such as hay/forage or natural dried petals may qualify from unambiguous natural identity "
    "when no coating, treatment, binder or other material concern is evidenced; do not invent manufacture or supply-chain risks merely "
    "because the item is not human food. Manufactured articles, supplied pods/filters and evidenced component concerns still need assessment. "
    "For an unresolved identity/evidence conflict, state the vegan-suitability or claim-applicability nexus in its target/rationale and cite "
    "the actual conflicting/restrictive facts. A generic assertion that vegan claims cannot resolve any formulation discrepancy is insufficient. "
)

# These mechanical observations never established animal use or claim binding.
COPY_OBSERVATION_RULES = frozenset({
    'full-product-identity-v1', 'component-material-consistency-v1',
    'explicit-recipe-count-consistency-v1', 'declared-preservative-consistency-v1',
    'declared-soup-blend-consistency-v1', 'declared-recipe-components-consistency-v1',
})

SCHEMA_VERSION = 7
REASONS = ("tagged", "manufacturer", "name", "ingredients")
PRODUCER_ASSERTION_POLICY = (
    "For the manufacturer/name boundary, an applicable affirmative producer self-description in product information is manufacturer evidence even when its subject repeats the vegan SKU designation: a declarative sentence identifying this product as a vegan article remains an assertion, whether its predicate describes flavour, texture or nutrition. Apply this consistently across products. "
    "A name mentioned without an assertion, a quoted title, a serving suggestion, another product, a recipe or a component-only description is not that confirmation. Read the whole product information rather than choosing a lower-priority name because a later serving suggestion is easier to quote. "
    "Do not require the separate formula 'suitable for vegans', and do not invent producer provenance or extend a restrictive claim's scope."
    " An independent whole-product Vegan attribute in native product features (including its field_title=features/content copy) is product-information confirmation under the manufacturer reason, even in a mixed list of recyclability, natural origin, biodegradability and skin-testing attributes. This native route needs no separate website capture or publisher sentence. Assess the complete field and subject: headings, topic/component/formula-only wording, exceptions and other-product mentions do not qualify. Website publisher and exact-product binding checks remain required for website claims."
)
DIMENSIONS = ("identity", "composition", "sourcing", "processing", "components")
DIMENSION_STATES = ("established", "resolved", "covered_by_claim", "not_applicable", "insufficient", "unresolved", "conflicted")
ISSUE_CODES = (
    "identity_conflict", "identity_insufficient", "evidence_conflict", "claim_scope_mismatch",
    "composition_incomplete", "source_unresolved", "processing_unresolved", "evidence_stale",
    "animal_ingredient", "animal_processing", "explicit_nonvegan", "tag_animal_conflict",
    "unsupported_assessment", "other_material_uncertainty",
)
REFERENCE_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["path", "quote"],
    "properties": {"path": {"type": "string", "pattern": r"^/(product|flags|categories|manufacturer_vegan_evidence|manufacturer_source_candidates|manufacturer_assertion_history)(/|$)"}, "quote": {"type": "string"}},
}
REFERENCES_SCHEMA = {"type": "array", "items": REFERENCE_SCHEMA}
DIMENSION_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["status", "evidence_refs"],
    "properties": {"status": {"type": "string", "enum": list(DIMENSION_STATES)}, "evidence_refs": REFERENCES_SCHEMA},
}
SUPPORT_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["reason", "evidence_refs"],
    "properties": {"reason": {"type": "string", "enum": list(REASONS)}, "evidence_refs": REFERENCES_SCHEMA},
}
ISSUE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["code", "target", "rule_id", "evidence_refs", "resolution_refs", "blocks_claim"],
    "properties": {
        "code": {"type": "string", "enum": list(ISSUE_CODES)}, "target": {"type": "string"},
        "rule_id": {"type": "string"}, "evidence_refs": REFERENCES_SCHEMA, "resolution_refs": REFERENCES_SCHEMA,
        "blocks_claim": {"type": "boolean"},
    },
}
SOURCE_DIMENSIONS = ("product", "variant", "market", "period")
SOURCE_DIMENSION_SCHEMA = {
    **DIMENSION_SCHEMA,
    "properties": {
        **DIMENSION_SCHEMA["properties"],
        # A claim may cover a generic product risk, but cannot establish its
        # own identity/applicability binding by claiming vegan suitability.
        "status": {"type": "string", "enum": [state for state in DIMENSION_STATES if state != "covered_by_claim"]},
    },
}
SOURCE_APPLICABILITY_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["source_path", "scope", *SOURCE_DIMENSIONS],
    "properties": {
        "source_path": {"type": "string", "pattern": r"^/manufacturer_(?:vegan_evidence|source_candidates)/\d+$"},
        "scope": {"type": "string", "enum": ["product", "range", "component", "other_product", "unresolved"]},
        **{dimension: SOURCE_DIMENSION_SCHEMA for dimension in SOURCE_DIMENSIONS},
    },
}
ASSESSMENT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": [*DIMENSIONS, "positive_support", "material_issues", "source_applicability"],
    "properties": {
        **{dimension: DIMENSION_SCHEMA for dimension in DIMENSIONS},
        "positive_support": {"type": "array", "items": SUPPORT_SCHEMA},
        "material_issues": {"type": "array", "items": ISSUE_SCHEMA},
        "source_applicability": {"type": "array", "items": SOURCE_APPLICABILITY_SCHEMA},
    },
}

# A mechanism identifies uncertainty, not a verdict about every product in a
# category. Whole-product claims can resolve generic risks, not animal content.
RISK_REGISTRY = (
    {"id": "wine-fining-v1", "code": "processing_unresolved", "pattern": r"\b(?:wine|prosecco|champagne|sherry|port)\b",
     "source": "https://www.oiv.int/node/3247", "resolution": "Applicable whole-product claim or adequately established animal-free processing."},
    {"id": "wine-other-processing-v1", "code": "processing_unresolved", "pattern": r"\b(?:wine|prosecco|champagne|sherry|port)\b",
     "source": "https://www.oiv.int/standards/international-code-of-oenological-practices/part-ii-oenological-treatments-and-practices/musts/lysozyme-treatment",
     "origin_source": "https://www.oiv.int/standards/international-oenological-codex/part-i-monographs/monographs/lysozyme",
     "resolution": "Applicable whole-product claim or established animal-free production inputs covering the complete grape/must/wine production chain, including non-fining processing aids. OIV documents egg-derived lysozyme for bacterial control during must fermentation, separately from fining. Unfined identity or animal-free fining agents resolve only fining, not other production inputs. This mechanism does not establish actual animal use by a particular SKU."},
    {"id": "liquid-clarification-v1", "code": "processing_unresolved",
     "pattern": r"\b(?:ciders?|beers?|lagers?|ales?|stouts?|vinegars?|juices?)\b",
     "source": "https://vegan.org/vegan-faq/clarifying-agents",
     "resolution": "Applicable whole-product claim or established animal-free production covering clarification and relevant precursors. Identity, plant ingredients, distillation or an unfiltered final product alone do not establish that animal aids were never used."},
    {"id": "maple-defoaming-v1", "code": "processing_unresolved", "pattern": r"\bmaple\s+syrup\b",
     "source": "https://vegan.org/vegan-faq/clarifying-agents",
     "resolution": "Applicable whole-product claim or explicit animal-free manufacturing/defoaming evidence. Maple origin alone does not resolve processing-aid use."},
    {"id": "planting-seed-treatment-v1", "code": "processing_unresolved", "pattern": r"\bseeds?\b",
     "source": "https://patents.google.com/patent/US20180303023A1/en",
     "resolution": "For seeds sold for planting, establish untreated and uncoated seed identity, animal-free seed treatments/coatings/binders, or an applicable whole-product claim. The primary patent documents animal and non-animal binder options, not actual treatment of any SKU. Native-grown/organic identity does not resolve this question. Do not apply this mechanism to edible seeds or ordinary harvested produce."},
    {"id": "sugar-refining-v1", "code": "processing_unresolved", "pattern": r"\b(?:sugar|sucrose)\b",
     "source": "https://www.wearetateandlylesugars.com/faq/", "resolution": "Applicable whole-product claim, unrefined identity, or supplier/process assurance with exceptions applied."},
    {"id": "source-dependent-additives-v1", "code": "source_unresolved",
     "pattern": r"\b(?:synthetic bees\s*wax|e\s*322|e\s*422|e\s*47[1234567]|e\s*631|e\s*635|e\s*920|lecithin|glycer(?:ol|in|ine)|mono(?:[- ]and[- ]|/)?diglycerides?|mono[- ]and[- ]di[- ]glycerides?)\b",
     "source": "https://www.vegansociety.com/news/blog/how-avoid-buying-non-vegan-products",
     "origin_source": "https://store.kosterkeunen.com/product/synthetic-beeswax/",
     "resolution": "Applicable whole-product claim or explicit source for the relevant ingredient. Synthetic Beeswax is not proof of bee-derived wax; its INCI identity alone does not establish a particular supplier mixture as vegan."},
    {"id": "citrus-coating-v1", "code": "processing_unresolved", "pattern": r"\b(?:lemons?|limes?|oranges?|satsumas?|tangerines?)\b",
     "source": "https://www.vegansociety.com/news/blog/how-avoid-buying-non-vegan-products", "resolution": "Applicable whole-product claim, unwaxed identity or established suitable coating."},
    {"id": "unspecified-composition-v1", "code": "source_unresolved",
     "pattern": r"\b(?:flavou?rings?|enzymes?|vitamins?|vitamin d3|cholecalciferol|rennet|glaze|raising agents?|firming agents?|acidity regulators?)\b",
     "source": "https://www.gov.uk/government/publications/approved-additives-and-e-numbers/approved-additives-and-e-numbers",
     "resolution": "Applicable whole-product claim or explicit relevant composition/source; a functional class alone is not identity."},
    {"id": "unspecified-added-minerals-v1", "code": "source_unresolved",
     "pattern": r"\bminerals?(?:\s+(?:premix|blend|mixture))?\b",
     "source": "https://www.solgar.com/products/calcium-600-tablets-from-oyster-shell-with-vitamin-d3",
     "origin_source": "https://www.nowfoods.com/products/supplements/bone-meal-powder",
     "resolution": "For an unspecified added mineral ingredient/group, establish relevant complete composition and animal-free source, or an applicable whole-product vegan assurance. Primary manufacturers document oyster-shell calcium and bovine bone mineral preparations; this establishes source variation, not animal use by any particular SKU. Generic plant-based marketing does not identify or source the mineral group. Do not apply to intrinsic minerals, nutrition descriptions, mineral water or standard flour fortification; individually identified minerals remain subject to ordinary evidence assessment, not a blanket mineral blacklist."},
    {"id": "compound-ingredient-composition-v1", "code": "composition_incomplete",
     "pattern": r"\b(?:vegetable suet|margarine)\b",
     "source": "https://www.gov.uk/food-labelling-and-packaging/ingredients-list",
     "resolution": "Applicable whole-product claim or declared complete constituents and relevant source/process facts. A plant descriptor does not specify a compound ingredient."},
    {"id": "manufactured-nonfood-composition-v1", "code": "composition_incomplete",
     "pattern": r"\b(?:body cream|skin cream|body lotion|shampoo|conditioner|moisturiser|paper plates?|cotton pads?|candles?|toothpaste|napkins?|plastic bowls?|melamine bowls?)\b",
     "source": "https://www.vegansociety.com/vegan-trademark/vegan-trademark-standards",
     "resolution": "Applicable whole-product claim or explicit complete composition of the sold article with relevant source/process issues resolved. One material entry is not automatically the complete article."},
    {"id": "supplied-components-composition-v1", "code": "composition_incomplete",
     "pattern": r"\b(?:pods?|cup filters?|coffee filters?|tea bags?|brewing filters?|packaging|wrappers?|bags?|box(?:es)?|cartons?|pouches?|bottles?|jars?|trays?|tins?|cans|foil|blisters?|sleeves?|lids?|caps?)\b",
     "source": "project-policy:ethical-whole-product-components-2026-10-06",
     "resolution": "Assess supplied packaging and non-edible components as part of the sold product. Applicable whole-product assurance or adequately established complete relevant component composition, sourcing and manufacture can resolve uncertainty. A food-only claim or one headline fibre/material cannot."},
)


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


@lru_cache(maxsize=512)
def _normalized_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def normalized(value: Any) -> str:
    # Key by exact text, never product ID or an evidence/version flag. Changed
    # source values cannot reuse an older canonicalization; memory is bounded.
    return _normalized_text(str(value or ""))


def factory_warning(text: str) -> bool:
    if re.search(r'\b(?:cross[- ]contamination|cross[- ]contact)\b', text, re.I):
        return True
    # Preserve the original same-line, ordered-token semantics without
    # rescanning the rest of a long page for every occurrence of "site".
    for line in text.split('\n'):
        place = re.search(r'\b(?:factory|facility|site|equipment|production line)\b', line, re.I)
        if place and re.search(r'\b(?:handles?|handling|processes?|processing|uses?|using)\b',
                               line[place.end():], re.I):
            return True
    return False


@lru_cache(maxsize=128)
def visible_source_text(value: str) -> str:
    """Canonical visible words, preserving the separately hashed raw capture."""
    value=re.sub(r'(?m)^L\d+(?:@P\d+)?:[ \t]?', '',value)
    value=re.sub('\ue200cite\ue202([^\ue201]*)\ue201',lambda match:match.group(1).split('†')[1] if '†' in match.group(1) else '',value)
    value=re.sub(r'\[([^\]\n]+)\]\(https?://[^\s)]+\)',r'\1',value)
    value=html.unescape(value.replace('**','').replace('__',''))
    return re.sub(r'\s+([,.;:!?])',r'\1',normalized(value))


def validate_schema(value: Any, schema: dict[str, Any], path: str = "$") -> None:
    """Validate the deliberately small JSON Schema vocabulary used here locally."""
    if "anyOf" in schema:
        for branch in schema["anyOf"]:
            try:
                validate_schema(value, branch, path)
                return
            except ValueError:
                pass
        raise ValueError(f"{path}: no permitted schema alternative")
    kind = schema.get("type")
    if isinstance(kind, list):
        validate_schema(value, {**schema, "type": None, "anyOf": [{"type": item} for item in kind]}, path)
        if "enum" in schema and value not in schema["enum"]:
            raise ValueError(f"{path}: invalid enum {value!r}")
        return
    matches = {"object": type(value) is dict, "array": type(value) is list,
               "string": type(value) is str, "boolean": type(value) is bool,
               "null": value is None, "integer": type(value) is int,
               "number": type(value) in (int, float)}
    if kind and not matches.get(kind, False):
        raise ValueError(f"{path}: expected {kind}")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path}: invalid enum {value!r}")
    if kind=='string' and 'pattern' in schema and not re.search(schema['pattern'],value):
        raise ValueError(f'{path}: string does not match required pattern')
    if kind == "object":
        properties = schema.get("properties", {})
        missing = set(schema.get("required", [])) - set(value)
        extra = set(value) - set(properties)
        if missing or (extra and schema.get("additionalProperties") is False):
            raise ValueError(f"{path}: missing {sorted(missing)}, unexpected {sorted(extra)}")
        for key, item in value.items():
            if key in properties:
                validate_schema(item, properties[key], f"{path}/{key}")
    elif kind == "array":
        if len(value) < schema.get("minItems", 0):
            raise ValueError(f"{path}: too few items")
        if len(value) > schema.get("maxItems", float("inf")):
            raise ValueError(f"{path}: too many items")
        for index, item in enumerate(value):
            validate_schema(item, schema["items"], f"{path}/{index}")


def resolve_reference(context: dict[str, Any], reference: dict[str, str]) -> Any:
    path = reference["path"]
    if not path.startswith("/"):
        raise ValueError(f"Invalid evidence pointer {path!r}")
    value: Any = context
    try:
        for part in path[1:].split("/"):
            part = part.replace("~1", "/").replace("~0", "~")
            value = value[int(part)] if isinstance(value, list) else value[part]
    except (KeyError, IndexError, ValueError, TypeError) as exc:
        raise ValueError(f"Evidence pointer does not exist: {path}") from exc
    quote = normalized(reference["quote"])
    text = normalized(value) if isinstance(value, str) else json.dumps(value, ensure_ascii=True)
    if quote and quote in text:
        return value
    if quote and isinstance(value,str) and path.endswith('/source_text'):
        visible_quote=visible_source_text(reference['quote'])
        if visible_quote and visible_quote in visible_source_text(value):
            return value
    raise ValueError(f"Evidence quote is absent at {path}")


def reference(path: str, value: Any) -> dict[str, str]:
    return {"path": path, "quote": normalized(value) if isinstance(value, str) else json.dumps(value)}


def compact_assessment(value: Any, field: str | None = None) -> Any:
    """Remove exact duplicates from logical evidence sets, without editing text.

    A repeated reference is not another independent reading. Raw responses,
    independent-pass records and older audits remain intact. Preserve distinct
    issues, resolutions, scopes and quotation spellings in their original order.
    """
    if isinstance(value, dict):
        return {key: compact_assessment(child, key) for key, child in value.items()}
    if isinstance(value, list):
        children = [compact_assessment(child) for child in value]
        if field not in {'evidence_refs', 'resolution_refs', 'material_issues',
                         'positive_support', 'source_applicability'}:
            return children
        unique = []; seen = set()
        for child in children:
            key = json.dumps(child, sort_keys=True, ensure_ascii=True, separators=(',', ':'))
            if key not in seen:
                seen.add(key); unique.append(child)
        return unique
    return value


def validate_assessment(assessment: dict[str, Any], context: dict[str, Any]) -> None:
    validate_schema(assessment, ASSESSMENT_SCHEMA)
    for dimension in DIMENSIONS:
        if assessment[dimension]["status"] in {"established", "resolved", "covered_by_claim"} and not assessment[dimension]["evidence_refs"]:
            raise ValueError(f"{dimension}: a conclusive assessment requires evidence")
        for ref in assessment[dimension]["evidence_refs"]:
            resolve_reference(context, ref)
    for support in assessment["positive_support"]:
        if not support["evidence_refs"]:
            raise ValueError("Positive support requires evidence")
        for ref in support["evidence_refs"]:
            resolve_reference(context, ref)
    for issue in assessment["material_issues"]:
        if not issue["evidence_refs"]:
            raise ValueError("A material issue requires its applicable evidence")
        for ref in [*issue["evidence_refs"], *issue["resolution_refs"]]:
            resolve_reference(context, ref)
    paths = set()
    for source in assessment["source_applicability"]:
        path = source["source_path"]
        if path in paths:
            raise ValueError("Duplicate source applicability assessment")
        paths.add(path)
        row = source_row(context, path)
        if not row or not row.get("source_text"):
            raise ValueError("Source applicability requires a retained complete capture")
        for dimension in SOURCE_DIMENSIONS:
            for ref in source[dimension]["evidence_refs"]:
                resolve_reference(context, ref)
                if not (ref["path"].startswith(path + "/") or ref["path"].startswith("/product/")):
                    raise ValueError("Applicability cites a different source or unrelated field")


def official_tag(context: dict[str, Any]) -> bool:
    return context["product"].get("official_vegan") == 1 or "vegan" in context.get("flags", [])


def negated_name_claim(name: str) -> bool:
    # Punctuation cannot turn a literal negative into an affirmative claim.
    # Certification absence is different: "not certified vegan" is not a
    # contradiction of an independently applicable vegan tag/assurance.
    return bool(re.search(r'\b(?:(?:non\W*|(?:not|no|never|almost)\W+)vegan|not\W+suitable\W+for\W+vegans?)\b',name,re.I))


def uncertain_vegan_claim(text: str) -> bool:
    """Reject uncertainty about suitability, not unrelated modal wording."""
    property_text = (r'(?:certified\s+)?vegan\b|suitable\s+for\s+'
                     r'(?:(?:a\s+)?vegans?\b|vegetarians?\s+(?:and|&)\s+vegans?\b)')
    hedge = (r'probably|possibly|potentially|perhaps|maybe|usually|sometimes|likely|'
             r'normally|generally|often|mostly|largely|nearly|almost|typically|'
             r'apparently|presumably')
    return bool(re.search(
        r'\b(?:may|might|could|can|would|should)\s+(?:(?:well|still|also)\s+)?be\s+'
        r'(?:(?:' + hedge + r')\s+)?(?:' + property_text + r')|'
        r'\b(?:' + hedge + r')(?:\s+(?:is|are|to|be))*\s+(?:' + property_text + r')|'
        r'\b(?:(?:we|i)\s+(?:think|believe|guess|assume|suspect|hope)(?:\s+that)?|maybe|perhaps)\s+'
        r'(?:this|these|the|our|it|they)\s+'
        r'(?:(?!(?:is|are|but|and|or|though|whereas|however|because|if|while|when)\b)[a-z][\w-]*\s+){0,6}'
        r'(?:is|are)\s+(?:also\s+)?(?:' + property_text + r')|'
        r'\b(?:appears?|seems?|looks?|believed|thought|expected|assumed)\s+'
        r'(?:to\s+)?(?:be\s+)?(?:' + property_text + r')',
        text, re.I))


def affirmative_name(name: str, brand: str = '') -> bool:
    # A producer's name/reputation is not a claim about the sold article.
    if re.search(r'\bvegan\b',brand,re.I):
        name=re.sub(re.escape(brand),'',name,count=1,flags=re.I).strip()
    if '?' in name or negated_name_claim(name) or uncertain_vegan_claim(name) or not re.search(r"\bvegan\b", name, re.I):
        return False
    if re.search(r'\b(?:probably|possibly|potentially|perhaps|maybe|usually|sometimes|likely)(?:\W+(?:is|to|be))*\W+vegan\b',name,re.I):
        return False  # A hedged assertion cannot establish suitability.
    if re.search(r"\b(?:cookbook|recipe book|guide|diary|book)\b|\bvegan[’']s\b",name,re.I):
        return False  # A vegan topic/author is not manufacturing assurance.
    # Difficult scope is deliberately left to structured model interpretation.
    return not re.search(r"\b(?:non[- ]?vegan|(?:no|not)\W+(?:\w+\W+){0,2}vegan|veganism|for\s+vegans?|vegan\s+(?:option|variant|alternative)\s+(?:available|also)|vegan\s+(?:certification|claims?|ingredients?|components?|formula|formulation|leather|collagen|keratin|coating|lining|bristles|handle|upper))\b|\b(?:formula|formulation)(?:\W+(?:is|are))?\W+vegan\b|\bwith\b.*\bvegan\b|\b(?:serve|serving|brand|recipe)\b", name, re.I)


def scoped_claim(text: str, *, negative: bool = False) -> bool:
    # Do not match a reassurance before reading its qualification afterwards.
    if re.search(r"\b(?:another|other|sister|sibling|alternative|variant|except|excluding|some|certain|selected|not all|serving suggestion)\b", text, re.I):
        return False
    if '?' in text:
        return False
    if negative:
        return bool(re.search(r"\b(?:(?:not|no longer) vegan|(?:not|no longer) suitable for (?:a )?vegans?)\b", text, re.I))
    if uncertain_vegan_claim(text):
        return False
    if component_only_claim(text):
        return False
    text = re.sub(r"\b(?:not|may not|no longer) suitable for (?:a )?vegans?\b|\bnot vegan\b", "", text, flags=re.I)
    if re.search(r"\b(?:not|never|no longer|may not|cannot|can't)\b.*\bvegans?\b", text, re.I):
        return False
    if re.search(r"\b(?:vegan (?:ingredients?|components?|dressing|sauce)|serve|serving|recipe|add (?:a )?vegan)\b", text, re.I):
        return False
    return text.strip().lower() in {"vegan", "suitable for vegans"} or bool(re.search(
        r"\b(?:suitable for (?:a )?vegans?|suitable for (?:a )?vegan diet|suitable for vegetarians? (?:and|&) vegans?|certified vegan|vegan certified|vegan society approved|registered with (?:the )?vegan society|vegan[- ]friendly)\b", text, re.I))


def dietary_attribute_claim(text: str) -> bool:
    """Recognise a complete dietary-attribute list, never narrative scope."""
    positive=r'(?:suitable for vegetarians? (?:and|&) vegans?|suitable for vegans? (?:and|&) vegetarians?|suitable for a vegan diet|suitable for vegans?|certified vegan|vegan[- ]friendly|vegan friendly|vegan)'
    other=r'(?:(?:suitable for )?vegetarians?|plant[- ]based|not tested on animals|cruelty[- ]free|(?:gluten|dairy|soya?|nut|egg|wheat)[ -]+free|source of (?:protein|fibre|fiber)|(?:high|low)(?: in)? (?:protein|fibre|fiber|fat|sugar|salt|carbs?|carbohydrates?)|keto(?:genic)?|lchf|no add(?:ed|es) sugars?|no artificial (?:sweeteners?|sweetners?))'
    attribute=f'(?:{positive}|{other})'
    separator=r'(?:\s*[,;\n&/]\s*|\s+and\s+|\.\s+)'
    return bool(re.search(r'\bvegans?\b',text,re.I) and re.fullmatch(
        attribute+f'(?:{separator}{attribute})*',text.strip().rstrip('.!'),re.I))


def mixed_product_feature_attribute(text: str) -> bool:
    """Recognise independent attributes in a typed product feature list.

    This supports a cited semantic assessment, never a deterministic shortcut.
    Keep the complete list: component headings and narrative qualifications
    cannot be discarded to extract an isolated Vegan attribute.
    """
    parts = [item.strip().rstrip('.!') for item in re.split(r'[,;\n]', str(text))]
    other = (r'(?:recyclable(?:\s+paper)?\s+(?:wrapper|packaging)|'
             r'(?:\d+(?:\.\d+)?\s*%\s*)?(?:natural origin|biodegradable(?:\s+soap)?\s+formula)|'
             r'dermatologically tested)')
    return len(parts) > 1 and any(item.casefold() == 'vegan' for item in parts) and all(
        dietary_attribute_claim('Vegan, ' + item) or re.fullmatch(other, item, re.I)
        for item in parts)


def direct_claim(text: str, path: str) -> bool:
    """Only simple product-level claims bypass semantic scope interpretation."""
    if '?' in text or component_only_claim(text) or scoped_claim(text,negative=True) or re.search(r'\b(?:another|other|except|excluding|some|certain|selected|not all|variant)\b',text,re.I):return False
    plain=text.strip().strip('.!').lower()
    standalone={'vegan','vegan friendly','vegan-friendly','suitable for vegans','suitable for a vegan diet',
                'suitable for vegetarians and vegans','suitable for vegetarians & vegans','certified vegan'}
    if plain in standalone:return True
    if path in {'/product/dietary_information','/product/features'} and dietary_attribute_claim(text) and any(
            part.strip().strip('.!').lower() in standalone for part in re.split(r'[,;\n/]|\.\s+',text)):
        return True
    if not scoped_claim(text):return False
    return bool(re.fullmatch(r'(?:this|the) product is (?:vegan|suitable for vegans)[.!]?',text.strip(),re.I))


def component_only_claim(text: str) -> bool:
    """Explicit dietary/component-only scope cannot assure the entire article."""
    return bool(re.search(r'\b(?:food|edible (?:contents?|components?)|contents?|drink|coffee)[\s‐‑-]+only\b|'
        r'\bonly (?:the )?(?:food|edible (?:contents?|components?)|contents?|drink|coffee)\b|'
        r'\b(?:exclud(?:e[sd]?|ing)|except|not includ(?:e[sd]?|ing))\s+(?:the )?(?:packaging|pods?|filters?|wrappers?)\b|'
        r'\b(?:packaging|pods?|filters?|wrappers?)\s+(?:are |is )?(?:excluded|not covered|not assessed)\b',text,re.I))


def claim_sources(context: dict[str, Any]) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    product = context["product"]
    positive, negative = [], []
    fields = ("dietary_information", "features", "detailed_description", "further_description", "manufacturer_marketing", "brand_marketing", "other_information", "specification", "vinification_details")
    candidates = [(f"/product/{key}", value, False, None, True) for key in fields if (value := product.get(key))]
    candidates += [(f"/manufacturer_vegan_evidence/{i}/content", row.get("content", ""),
                    row.get("source_kind") == "manufacturer_website", row.get("verified_status"),
                    row.get("review_policy_version") == POLICY_VERSION)
                   for i, row in enumerate(context.get("manufacturer_vegan_evidence", []))]
    for path, value, verified, status, reviewed in candidates:
        text = normalized(value)
        if not text:
            continue
        if (not verified and scoped_claim(text,negative=True)) or (verified and reviewed and status == "nonvegan"):
            negative.append(reference(path, text))
        # A prior website association/review is not a fresh applicability proof.
        # Its positive claim is interpreted with the full source assessment.
        if not verified and (direct_claim(text,path) or (scoped_claim(text,negative=True) and scoped_claim(text))):
            positive.append(reference(path, text))
    return positive, negative


def material_risks(context: dict[str, Any], cleaned_ingredients: str = "") -> list[dict[str, Any]]:
    product = context["product"]
    name = normalized(product.get("name"))
    ingredients = normalized(product.get("ingredients"))
    risks = []
    for rule in RISK_REGISTRY:
        if rule['id'] == 'wine-other-processing-v1':
            # Share validated wine/precursor applicability, including taxonomy,
            # rather than creating a second category or name shortcut.
            continue
        target_text = cleaned_ingredients if rule["id"] == "unspecified-composition-v1" else ingredients if rule["id"] in {"sugar-refining-v1", "source-dependent-additives-v1", "unspecified-added-minerals-v1"} else f"{name} {ingredients}"
        if rule['id'] == 'unspecified-added-minerals-v1':
            # Only an actual ingredient declaration, not intrinsic mineral
            # content, water identity or a nutrition/marketing statement.
            declared = re.search(r'(?:^|[,;:]\s*)minerals?(?:\s+(?:premix|blend|mixture))?\s*(?:[,;.]|$)', ingredients, re.I)
            if not declared:
                continue
        if rule['id']=='planting-seed-treatment-v1':
            planting=normalized(product.get('preparation_and_usage'))
            seed_taxonomy=any(re.search(r'/(?:flower|vegetable|grass|lawn|plant)[- ]seeds?$',category,re.I)
                              for category in context.get('categories',[]))
            if not re.search(rule['pattern'],name,re.I) or not (
                    seed_taxonomy or re.search(r'\b(?:sow|sowing|germinat\w*)\b',planting,re.I)):
                continue
            # A seed brand name on edible seeds is not planting identity.
            if re.search(r'\b(?:edible|snack|roasted|toasted|food|tools?|planters?|pots?|trays?|dibbers?|labels?)\b',name,re.I):continue
            path='/product/name';text=name
            refs=[reference(path,text)]
            if planting:refs.append(reference('/product/preparation_and_usage',planting))
            risks.append({'code':rule['code'],'target':'planting seed treatments','rule_id':rule['id'],
                          'evidence_refs':refs,'resolution_refs':[],'blocks_claim':False})
            continue
        if rule['id']=='supplied-components-composition-v1':
            fields=('name','package_type','features','specification','other_information','detailed_description','further_description')
            # A numbered PO Box is a postal address, not a supplied box. Keep
            # the full original field for citations; remove only that locator
            # from the component applicability check.
            component_text=lambda key: re.sub(r'\bP\.?\s*O\.?\s*Box(?=\s+\d)',
                'postal address',normalized(product.get(key)),flags=re.I)
            matches=[(key,normalized(product.get(key))) for key in fields if re.search(rule['pattern'],component_text(key),re.I)
                or (key=='package_type' and normalized(product.get(key))
                    and not re.fullmatch(r'(?:loose|none|not applicable|unpackaged)',normalized(product.get(key)),re.I))]
            if matches:
                risks.append({'code':rule['code'],'target':'supplied components','rule_id':rule['id'],
                    'evidence_refs':[reference('/product/'+key,text) for key,text in matches],
                    'resolution_refs':[],'blocks_claim':False})
            continue
        if rule["id"] in {"sugar-refining-v1", "source-dependent-additives-v1"} and not ingredients:
            target_text = name
            if rule["id"] == "sugar-refining-v1" and re.search(r"\b(?:sugar[- ]free|no added sugar)\b", name, re.I):
                target_text = ""
        if rule["id"] == "unspecified-composition-v1":
            # A functional label accompanied by named chemical composition is
            # interpretable evidence, not the same as a bare class. Leave it to
            # the model rather than inventing a universal class blacklist.
            target_text = re.sub(r"\b(?:raising agents?|firming agents?|acidity regulators?)\s*\([^)]*\)", "", target_text, flags=re.I)
        if not re.search(rule["pattern"], target_text, re.I):
            continue
        if rule["id"] == "sugar-refining-v1" and re.search(r"\b(?:unrefined|raw)\s+(?:cane\s+)?sugar\b", target_text, re.I) and not re.search(r"(?:,|;)\s*(?:sugar|sucrose)\b", ingredients, re.I):
            continue
        if rule["id"] == "citrus-coating-v1" and re.search(r"\bunwaxed\b", name, re.I):
            continue
        if rule["id"] == "citrus-coating-v1" and re.search(r"\b(?:juice|flavou?red|vodka|extract|oil|sauce|cake|biscuit|lemonade|drink)\b", name, re.I):
            continue
        if rule["id"] == "source-dependent-additives-v1":
            unresolved_text = re.sub(r"\b(?:soya?|sunflower)\s+lecithin\b(?:\s*\(?e\s*322\)?)?|\be\s*322\s*\((?:soya?|sunflower)\)|\b(?:vegetable|plant[- ]derived)\s+glycer(?:ol|in|ine)\b", "", ingredients, flags=re.I)
            if not re.search(rule["pattern"], unresolved_text, re.I):
                continue
        path, text = ("/product/ingredients", ingredients) if re.search(rule["pattern"], ingredients, re.I) else ("/product/name", name)
        targets=sorted({match.group().lower() for match in re.finditer(rule['pattern'],unresolved_text if rule['id']=='source-dependent-additives-v1' else target_text,re.I)}) if rule['id']=='source-dependent-additives-v1' else ['whole_product']
        for target in targets:
            risks.append({"code": rule["code"], "target": target, "rule_id": rule["id"],
                          "evidence_refs": [reference(path, text)], "resolution_refs": [], "blocks_claim": False})
    # Specific wine taxonomy supplies a mechanism when a chateau name omits
    # 'wine'. Mixed beer/wine/spirits parents do not establish wine identity.
    if not any(issue['rule_id']=='wine-fining-v1' for issue in risks):
        for index,category in enumerate(context.get('categories',[])):
            if any(re.search(r'\b(?:wine|wines|prosecco|champagne|sherry|port)\b',segment)
                   and not re.search(r'\b(?:beer|spirits|vinegar|glass|glasses|glassware|accessories|gifts)\b',segment)
                   for segment in category.lower().split('/')):
                risks.append({'code':'processing_unresolved','target':'whole_product','rule_id':'wine-fining-v1',
                    'evidence_refs':[reference(f'/categories/{index}',category)],'resolution_refs':[],'blocks_claim':False})
                break
    if not any(issue['rule_id']=='liquid-clarification-v1' for issue in risks):
        for index,category in enumerate(context.get('categories',[])):
            if any(re.search(r'\b(?:ciders?|beers?|ales?|lagers?|stouts?|vinegars?|juices?)\b',segment)
                   and not re.search(r'\b(?:wines?|spirits|oils?|fats?|sauces?|condiments?|glass(?:es|ware)?|accessories|gifts)\b',segment)
                   for segment in category.lower().split('/')):
                risks.append({'code':'processing_unresolved','target':'whole_product','rule_id':'liquid-clarification-v1',
                    'evidence_refs':[reference(f'/categories/{index}',category)],'resolution_refs':[],'blocks_claim':False})
                break
    wine = next((risk for risk in risks if risk['rule_id'] == 'wine-fining-v1'), None)
    if wine:
        risks.append({**wine, 'rule_id': 'wine-other-processing-v1',
                      'target': 'non-fining inputs across grape, must and wine production'})
    return risks


def base_assessment(context: dict[str, Any], *, complete: bool = False) -> dict[str, Any]:
    product = context["product"]
    name_refs = [reference("/product/name", product["name"])] if product.get("name") else []
    ingredient_refs = [reference("/product/ingredients", product["ingredients"])] if product.get("ingredients") else name_refs
    return {
        "identity": {"status": "established" if name_refs else "insufficient", "evidence_refs": name_refs},
        "composition": {"status": "established" if complete else "insufficient", "evidence_refs": ingredient_refs},
        "sourcing": {"status": "not_applicable", "evidence_refs": []},
        "processing": {"status": "not_applicable", "evidence_refs": []},
        "components": {"status": "established" if complete else "insufficient", "evidence_refs": name_refs},
        "positive_support": [], "material_issues": [], "source_applicability": [],
    }


def resolved_risk(risk: dict[str, Any], assessment: dict[str, Any], context=None) -> bool:
    """A risk's own identity is not evidence resolving that risk.

    Interpretation still belongs to the model, but an unchanged ambiguous term
    (e.g. 'E471' or 'vegetable suet') cannot serve as its own source/composition
    assurance. Whole-product claims are evaluated separately.
    """
    resolution_patterns = {
        "source-dependent-additives-v1": r"\b(?:plant|vegetable|soya?|sunflower|rapeseed|vegan)\b",
        "unspecified-composition-v1": r"\b(?:plant|vegetable|soya?|sunflower|lichen|vegan)\b|\([^()]+\)",
        "wine-fining-v1": r"\bunfined\b|\b(?:fining|fined|processing|processed|manufactur\w*)\b.*\b(?:no animal|without animal|animal[- ]free|vegan)\b|\b(?:no animal|without animal|animal[- ]free|vegan)\b.*\b(?:fining|fined|processing|processed|manufactur\w*)\b",
        "wine-other-processing-v1": r"\b(?:all production stages including (?:all )?ingredient and precursor production|(?:entire|complete) wine production (?:chain|process)|all wine production stages|all production inputs(?: and processing aids)? from (?:the )?grapes through (?:the )?must (?:to|and) (?:the )?finished wine)\b.*\b(?:no animal|without animal|animal[- ]free|vegan)\b|\b(?:no animal|without animal|animal[- ]free|vegan)\b.*\b(?:all production stages including (?:all )?ingredient and precursor production|(?:entire|complete) wine production (?:chain|process)|all wine production stages|all production inputs(?: and processing aids)? from (?:the )?grapes through (?:the )?must (?:to|and) (?:the )?finished wine)\b",
        "liquid-clarification-v1": r"\b(?:all production stages including (?:all )?ingredient and precursor production|from (?:the )?whole (?:fruit|apples|grapes|grains) to (?:the )?finished product)\b.*\b(?:no animal|without animal|animal[- ]free|vegan)\b|\b(?:no animal|without animal|animal[- ]free|vegan)\b.*\b(?:all production stages including (?:all )?ingredient and precursor production|from (?:the )?whole (?:fruit|apples|grapes|grains) to (?:the )?finished product)\b",
        "maple-defoaming-v1": r"\b(?:defoam\w*|processing aids?|manufactur\w*|production)\b.*\b(?:no animal|without animal|animal[- ]free|vegan)\b|\b(?:no animal|without animal|animal[- ]free|vegan)\b.*\b(?:defoam\w*|processing aids?|manufactur\w*|production)\b",
        "planting-seed-treatment-v1": r"\buntreated\b.*\buncoated\b|\buncoated\b.*\buntreated\b|\b(?:all seed treatments|seed coatings and binders)\b.*\b(?:animal[- ]free|vegan|no animal|without animal)\b",
        "sugar-refining-v1": r"\bunrefined\b|\b(?:refining|refined|processing|processed|manufactur\w*)\b.*\b(?:no animal|without animal|animal[- ]free|no bone char|without bone char|vegan)\b|\b(?:no animal|without animal|animal[- ]free|no bone char|without bone char|vegan)\b.*\b(?:refining|refined|processing|processed|manufactur\w*)\b",
        "citrus-coating-v1": r"\b(?:unwaxed|no animal|without animal|animal[- ]free|vegan)\b",
        "compound-ingredient-composition-v1": r"\b(?:vegetable suet|margarine)\s*\([^()]+\)|\b(?:constituents|composed of|consists of)\b",
        "manufactured-nonfood-composition-v1": r"\b(?:100% pure|only ingredient|single[- ]ingredient|complete (?:composition|ingredient list)|all ingredients|entire (?:article|product))\b",
        "supplied-components-composition-v1": r"\b(?:complete (?:component|packaging|pod|filter|wrapper) (?:composition|materials?)|all (?:components|packaging materials)|entire (?:article|product)|(?:pod|filter|wrapper|packaging)\s+(?:is |are )?vegan)\b",
    }
    for issue in assessment["material_issues"]:
        if issue["rule_id"] != risk["rule_id"]:
            continue
        for ref in issue["resolution_refs"]:
            text = ref["quote"].strip()
            if risk['rule_id'] in {'wine-fining-v1', 'wine-other-processing-v1', 'sugar-refining-v1'}:
                # Process facts need affirmative evidence just like whole-
                # product claims. Bind cropped excerpts to their retained
                # sentences so omitting "may" cannot clear a process risk.
                retained = [text]
                if context is not None:
                    raw, website = published_manufacturer_reference(context, ref)
                    if raw is None:
                        continue
                    clean = visible_source_text if website else normalized
                    clauses = [clean(part) for part in re.split(r'(?<=[.!;?])\s+|\n+', str(raw)) if clean(part)]
                    joined = ' '.join(clauses)
                    retained = []
                    for match in re.finditer(re.escape(clean(text)), joined):
                        offset = 0
                        for clause in clauses:
                            end = offset + len(clause)
                            if offset < match.end() and end > match.start():
                                retained.append(clause)
                            offset = end + 1
                    if not retained:
                        continue
                qualification = (
                    r'\b(?:may|might|could|can|would|should)\s+(?:(?:well|still|also)\s+)?'
                    r'(?:be\s+(?:unfined|unrefined|fined|refined|processed|manufactured|animal[- ]free|vegan)\b|'
                    r'(?:use|undergo|involve|include|avoid|omit|rely|employ|remain)\b)|'
                    r'\b(?:probably|possibly|potentially|perhaps|maybe|usually|sometimes|likely|'
                    r'normally|generally|often|mostly|largely|nearly|almost|typically|apparently|presumably|'
                    r'not|never|except|excluding|another|other variant)\b')
                if any(re.search(resolution_patterns[risk['rule_id']], clause, re.I)
                       and re.search(qualification, clause, re.I) for clause in retained):
                    continue
            if risk['rule_id'] in {'liquid-clarification-v1','maple-defoaming-v1','planting-seed-treatment-v1'} and re.search(
                    r'\b(?:not|never|may|might|could|except|excluding|another|other variant)\b',text,re.I):
                continue
            if risk['rule_id']=='source-dependent-additives-v1':
                target=re.escape(risk['target']).replace(r'\ ',r'\s*')
                marker=r'(?:plant[- ]derived|vegetable|soya?|sunflower|rapeseed|vegan)'
                source_link=rf'\b{target}\b\s*(?:\([^)]*\b{marker}\b[^)]*\)|(?:(?:derived|sourced|made|obtained)\s+)?from\s+(?:\w+\s+){{0,3}}{marker}\b)|\b{marker}\b\s+(?:derived\s+)?\b{target}\b'
                global_origin=r'\b(?:all|entire|100%)\b.*\b(?:ingredients|formula|composition)\b.*\b(?:plant|vegetable|non[- ]animal|vegan)\b'
                if re.search(source_link,text,re.I) or re.search(global_origin,text,re.I):return True
                continue
            if risk['rule_id']=='unspecified-added-minerals-v1':
                # A plant-based food description is not a mineral-source fact.
                # Bind assurance to the added group, with retained qualifiers.
                raw, website = published_manufacturer_reference(context, ref) if context is not None else (text, False)
                if raw is None:
                    continue
                clean = visible_source_text if website else normalized
                clauses = [clean(part) for part in re.split(r'(?<=[.!;?])\s+|\n+', str(raw)) if clean(part)]
                for clause in clauses:
                    if clean(text) not in clause:
                        continue
                    if re.search(r'\b(?:may|might|could|probably|possibly|not|except|excluding|other variant)\b', clause, re.I):
                        continue
                    if re.search(r'\b(?:all\s+)?minerals?(?:\s+(?:premix|blend|mixture))?\s+(?:(?:is|are)\s+)?(?:sourced from|derived from|comes? from)?\s*(?:entirely\s+)?(?:vegan|animal[- ]free|non[- ]animal|quarried|mined)\b', clause, re.I):
                        return True
                continue
            if risk['rule_id'] in resolution_patterns and re.search(resolution_patterns[risk["rule_id"]], text, re.I):
                return True
    return False


def source_row(context, path):
    if not isinstance(path, str):
        return None
    match = re.fullmatch(r"/manufacturer_(vegan_evidence|source_candidates)/(\d+)", path)
    if not match:
        return None
    rows = context.get("manufacturer_" + match.group(1), [])
    index = int(match.group(2))
    return rows[index] if index < len(rows) else None


def rebind_source_assessment(old_context, new_context, assessment):
    """Rebind pointers after imports using URL + full capture hash, never title."""
    mapping = {}
    for field in ('manufacturer_source_candidates', 'manufacturer_vegan_evidence'):
        for index, row in enumerate(old_context.get(field, [])):
            if not row.get('source_text'):
                continue
            old_path = f'/{field}/{index}'
            for new_field in ('manufacturer_source_candidates', 'manufacturer_vegan_evidence'):
                for new_index, candidate in enumerate(new_context.get(new_field, [])):
                    if (row.get('source_url'), row.get('source_sha256')) == (candidate.get('source_url'), candidate.get('source_sha256')):
                        mapping.setdefault(old_path, f'/{new_field}/{new_index}')
    def replace(value):
        if isinstance(value, dict):
            return {key: pointer(item) if key in {'path','source_path'} else replace(item) for key,item in value.items()}
        if isinstance(value, list):
            return [replace(item) for item in value]
        return value
    def pointer(value):
        match = re.match(r'^(/manufacturer_(?:vegan_evidence|source_candidates)/\d+)(/.*)?$', value)
        if not match:
            return value
        if match.group(1) not in mapping:
            raise ValueError('Reviewed source capture is absent after import')
        return mapping[match.group(1)] + (match.group(2) or '')
    result = replace(assessment)
    validate_assessment(result, new_context)
    return result


def source_path(ref):
    match = re.match(r"^(/manufacturer_(?:vegan_evidence|source_candidates)/\d+)/(?:source_text|content)$", ref["path"])
    return match.group(1) if match else None


def source_variant_conflict(context, path, *, ignore_colour=False, ignore_period=False):
    """Check explicit variant selectors, independently of model assurances.

    This is deliberately narrow: colour selectors and labelled vintages are
    facts, while fragrance prose and other semantic equivalences remain model
    work. A source association hash says nothing about these selectors.
    """
    row = source_row(context, path)
    if not row:
        return []
    raw = re.sub(r"(?m)^L\d+(?:@P\d+)?:[ \t]?", "", row.get("source_text", ""))
    product = context["product"]
    name = normalized(product.get("name"))
    target_colour = normalized(product.get("colour") or product.get("color"))
    if not target_colour and "," in name:
        suffix = name.rsplit(",", 1)[1].strip()
        if re.fullmatch(r"[A-Za-z]+(?:[- ][A-Za-z]+){0,2}", suffix):
            target_colour = suffix
    conflicts = []
    for match in re.finditer(r"(?im)^\s*colou?r\s*[:=]?\s+([^\n]+)", raw):
        colour = re.sub(r"(?i)^colou?r\s+|\s+(?:out of stock|in stock|available|selected|add to).*$", "", match.group(1)).strip()
        if not ignore_colour and target_colour and colour and normalized(target_colour).lower() != normalized(colour).lower():
            conflicts.append([reference("/product/name", name),
                              {"path": path + "/source_text", "quote": visible_source_text(match.group())}])
    target_period = normalized(product.get("current_vintage"))
    for match in re.finditer(r"\bvintage\s*[:=]?\s*(20\d{2}|19\d{2})\b", raw, re.I):
        if not ignore_period and re.fullmatch(r"(?:19|20)\d{2}", target_period) and target_period != match.group(1):
            conflicts.append([reference("/product/current_vintage", target_period),
                              {"path": path + "/source_text", "quote": match.group()}])
    return conflicts


def source_applicable(context, path, assessment):
    if not assessment:
        return False
    check = next((item for item in assessment["source_applicability"] if item["source_path"] == path), None)
    if not check or check["scope"] not in {"product", "range"}:
        return False
    for dimension in SOURCE_DIMENSIONS:
        value = check[dimension]
        if value["status"] not in {"established", "resolved", "not_applicable"}:
            return False
        refs = value["evidence_refs"]
        # Even a non-material dimension needs a source-grounded explanation.
        if not any(ref["path"].startswith(path + "/") for ref in refs):
            return False
        if dimension == "product" and (value["status"] == "not_applicable" or
                not any(ref["path"] in {"/product/name", "/product/id"} for ref in refs)):
            return False
    range_refs = check["variant"]["evidence_refs"]
    inclusive_range = check["scope"] == "range" and any(
        ref["path"].startswith(path + "/") and re.search(
            r"\b(?:all|every|entire|whole)\b.*\b(?:products?|range|variants?|colou?rs?|wines?|candles?)\b", ref["quote"], re.I)
        and not re.search(r'\b(?:except|excluding|not all|selected|certain|some)\b',ref['quote'],re.I)
        for ref in range_refs)
    if check["scope"] == "range" and not inclusive_range:
        return False
    inclusive_period = any(ref['path'].startswith(path + '/') and re.search(
        r'\b(?:all|every)\s+(?:vintages?|production years?)\b',ref['quote'],re.I)
        and not re.search(r'\b(?:except|excluding|not all|selected|certain|some)\b',ref['quote'],re.I)
        for ref in check['period']['evidence_refs'])
    # An inclusive colour/range assurance does not erase a stated vintage;
    # explicit assurance covering all vintages can resolve that limitation.
    return not source_variant_conflict(context, path, ignore_colour=inclusive_range, ignore_period=inclusive_period)


def source_reference(context, ref, assessment=None):
    """A raw associated page needs current catalogue binding, not old approval."""
    if ref['path'].startswith('/manufacturer_vegan_evidence/'):
        row = source_row(context, source_path(ref))
        if row and row.get('source_kind') != 'manufacturer_website':
            # This table also holds typed Ocado product-information fields.
            # They are not external pages requiring website applicability.
            return True
        return bool(source_applicable(context, source_path(ref), assessment))
    match=re.match(r'^/manufacturer_source_candidates/(\d+)/source_text$',ref['path'])
    if not match:return False
    row=context['manufacturer_source_candidates'][int(match.group(1))]
    return row.get('association_status')=='catalogue_match' and source_applicable(context, source_path(ref), assessment)


def claim_qualifier_text(text):
    """Preserve restrictions while separating quantified facts from scope."""
    text = re.sub(
        r'\b\d+(?:\.\d+)?\s*(?:(?:g|mg|mcg|µg)\s+(?:of\s+)?'
        r'(?:protein|fibre|fiber|fat|sugars?|salt|carbohydrates?)|'
        r'kcal|calories?|kj|kilojoules?)\s+per(?:\s+recommended)?\s+serving\b',
        '', text, flags=re.I)
    return re.sub(r'\b((?:combination|blend|mixture) of )some of\b',
                  r'\1', text, flags=re.I)


def manufacturer_claim_quote(text, *, brand=''):
    # An independent testing attribute does not negate vegan suitability.
    # Full-list parsing is deliberately bounded; cruelty-free alone supplies
    # no vegan assertion, and arbitrary negatives/qualifications still fail.
    attribute_text = text
    if re.search(r'\bvegan\b', brand, re.I):
        attribute_text = re.sub(r'(?<!\w)' + re.escape(brand) + r'(?!\w)',
                                'the producer', text, flags=re.I)
    if dietary_attribute_claim(attribute_text) and not component_only_claim(text):
        return True
    # Quantified nutrition per serving does not scope an independent vegan
    # assertion to a serving suggestion. Ignore only those nutrient figures
    # in the qualifier veto; keep the actual assertion and citation intact.
    qualifier_text = claim_qualifier_text(text)
    # Selecting constituents for a combination/blend is not a restriction on
    # a separate whole-product assertion. Leave range qualifiers and the
    # original source/claim text intact; this cannot supply an assertion.
    if component_only_claim(text) or negated_name_claim(text) or re.search(
            r'\b(?:not|never|no longer|may not|except|excluding)\b|'
            r'\b(?:selected|some|certain)\s+(?:variants?|products?|ranges?|flavou?rs?|markets?)\s+only\b',
            qualifier_text, re.I):
        return False
    # A vegan word inside the known producer's name is not a product
    # attribute. Keep independent assertions ("Vegan Foods Shampoo is vegan")
    # while preventing brand-led descriptions from supplying confirmation.
    if re.search(r'\bvegan\b', brand, re.I):
        text = re.sub(r'(?<!\w)' + re.escape(brand) + r'(?!\w)',
                      'the producer', text, flags=re.I)
    if uncertain_vegan_claim(text):
        return False
    # Product-level attribute lists and affirmative self-description are
    # valid claims too. Semantic applicability still needs the model's cited
    # complete assessment; this helper never establishes it independently.
    # Composition/recipe words in a separate sentence must not erase a
    # whole-product assertion. Keep restrictive qualifiers across the full
    # quotation, and reject component/topic scope within the claim's clause.
    for clause in re.split(r'(?<=[.!;?])[\s,]+|\n+|,\s+(?=(?:this|the)\s+(?:complete\s+)?(?:product|flavou?r|article)\b)',text,flags=re.I):
        # A question is not an assertion. Rhetorical/usage questions in a
        # different clause cannot erase an independently affirmative claim.
        if '?' in clause:
            continue
        # Range/serving qualifiers must belong to this assertion. Advice
        # about other products or some extra care in another clause is not
        # a restriction on an independent whole-product assurance.
        if re.search(r'\b(?:another|other|sibling|variant|some|certain|selected|serving)\b', claim_qualifier_text(clause), re.I):
            continue
        if (re.search(r'\b(?:another|other|sibling|variant|some|certain|selected|serving)\b', qualifier_text, re.I)
                and not re.search(r'\b(?:this|the|our|these)\s+(?:[a-z][\w\x27-]*\s+){1,7}(?:is|are)\b', clause, re.I)):
            continue  # A subjectless badge cannot borrow another item's scope.
        if re.search(r'\b(?:recipe|cookbook|guide|book|components?|ingredients?)\b|\bvegan\s+(?:formula|formulation|materials?|leather|lining|coating|bristles|handle|upper|filling|topping)\b|\b(?:formula|formulation)\s+(?:is|are)\s+(?:also\s+)?vegan\b',clause,re.I):
            continue
        # Coordinated dietary attributes assert vegan suitability as clearly
        # as a standalone "Vegan" label. Require the entire clause to be an
        # attribute list; arbitrary subject/topic/scope words cannot qualify.
        # This recognises a cited interpretation, never bypasses assessment.
        if dietary_attribute_claim(clause):
            return True
        if direct_claim(clause,'/product/features') or scoped_claim(clause) or re.search(
                r'\b(?:is|are) (?:suitable for (?:a )?vegans?|(?:also )?vegan)\b|\bthis\s+(?:is\s+(?:[a-z][\w-]*\s+){0,4})?vegan\b|'
                r'\b(?:created|developed|made)\s+(?:a|the|our)\s+vegan\s+version\s+of\s+our\b|'
                r'\bvegan\s+(?:[a-z][\w-]*\s+){1,4}(?:is|are)\b',clause,re.I):
            return True
    return False


def published_manufacturer_reference(context, ref):
    raw=resolve_reference(context,ref)
    row=source_row(context,source_path(ref)) if source_path(ref) else None
    website=bool(row and (row.get('source_kind')=='manufacturer_website' or ref['path'].startswith('/manufacturer_source_candidates/')))
    if website:
        if ref['path'].rsplit('/',1)[-1] not in {'content','source_text'} or not row.get('source_text'):
            return None,True
        return row['source_text'],True
    return raw,False


def manufacturer_reference_claim(context, ref):
    if native_product_identity.packaging_attribute(context, ref):
        return False
    raw,website=published_manufacturer_reference(context,ref)
    if raw is None:
        return False
    text=normalized(raw)
    if component_only_claim(text):
        return False
    quote=normalized(ref['quote'])
    brand=normalized(context['product'].get('brand'))
    if not re.search(r'\bvegans?\b',quote,re.I):
        return False
    row = source_row(context, source_path(ref)) if source_path(ref) else None
    feature_field = ref['path'] == '/product/features' or bool(
        row and normalized(row.get('field_title')).casefold() == 'features')
    if not website and feature_field and mixed_product_feature_attribute(raw):
        return True
    # Bind even apparently conclusive excerpts to their complete retained
    # sentence/line. An isolated "vegan" can otherwise discard a packaging
    # subject, negation or qualification. Keep original line boundaries for
    # website/product-information lists before normalising their whitespace.
    clean=visible_source_text if website else normalized
    quote=clean(ref['quote'])
    clauses=[clean(part) for part in re.split(r'(?<=[.!;?],)\s+|(?<=[.!;?])\s+|\n+',str(raw)) if clean(part)]
    joined=' '.join(clauses)
    spans=[];offset=0
    for clause in clauses:
        spans.append((offset,offset+len(clause),clause));offset+=len(clause)+1
    component_subject=r'^(?:(?:the|this|these|those|our|its|supplied)\s+)?(?:vegan\s+)?(packaging|wrappers?|lining|coating|bristles|handles?|upper|filling|topping|components?|ingredients?|bottles?|jars?|bags?|boxes|box|cartons?|pouches?|pods?|filters?|lids?|caps?)\s+(?:is|are)\b'
    intrinsic_parts=r'packaging|wrappers?|lining|coating|bristles|handles?|upper|filling|topping|components?|ingredients?'
    package_type=normalized(context['product'].get('package_type'))
    for match in re.finditer(re.escape(quote),joined):
        retained=[(start,end,clause) for start,end,clause in spans if start<match.end() and end>match.start()]
        # Questions establish a reply's target, not an affirmative predicate.
        # Validate retained assertions together, then bind dependent replies
        # to the question below; a combined exact quote may contain both.
        assertions=' '.join(clause for _,_,clause in retained if not clause.rstrip(', ').endswith('?'))
        if not manufacturer_claim_quote(assertions,brand=brand):
            continue
        for start,end,clause in retained:
            dependent_reply=bool(re.match(r'^(?:yes\b|it\s+is\b|they\s+are\b|this\s+is\b|these\s+are\b)',clause,re.I)) or dietary_attribute_claim(clause)
            independent_subject=bool(re.search(
                r'\b(?:this|the|our|these)\s+(?:(?:complete|whole|entire)\s+)?(?:products?|articles?|flavou?r)\s+(?:is|are)\b',clause,re.I))
            product_name=normalized(context['product'].get('name'))
            if product_name and re.search(re.escape(product_name)+r'\s+(?:is|are)\b',clause,re.I):
                independent_subject=True
            questions=list(re.finditer(r'[^?]*\?',joined[:start]))
            if dependent_reply and not independent_subject and questions and not manufacturer_question_scope(questions[-1].group(),context):
                continue
            subject=re.match(component_subject,clause,re.I)
            stem={'boxes':'box','pouches':'pouch'}.get(subject.group(1).lower(),subject.group(1).lower().rstrip('s')) if subject else ''
            if subject and (re.fullmatch(intrinsic_parts,subject.group(1),re.I) or re.search(
                    r'\b'+re.escape(stem)+r'(?:s|es)?\b',package_type,re.I)):
                continue
            if manufacturer_claim_quote(clause,brand=brand):
                return True
    return False


def whole_product_vegan_question(text, context=None):
    # An answer to an ingredients/materials question is composition evidence,
    # not a published assurance about the complete product and its processes.
    package_question=re.fullmatch(r'(?:is|are)\s+(?:(?:the|this|your|our|its|these|supplied)\s+)?(bottles?|jars?|bags?|boxes|box|cartons?|pouches?|pods?|filters?|lids?|caps?)\s+(?:vegan|suitable for (?:a )?vegans?)\??',text.strip(),re.I)
    if package_question and context:
        noun=package_question.group(1)
        stem={'boxes':'box','pouches':'pouch'}.get(noun.lower(),noun.lower().rstrip('s'))
        if re.search(r'\b'+re.escape(stem)+r'(?:s|es)?\b',normalized(context['product'].get('package_type')),re.I):
            return False
    whole_including_parts=bool(re.fullmatch(
        r'(?:is|are)\s+(?:(?:your|our|the|this|these|all|whole|entire|complete)\s+)*(?:products?|articles?)\s*,?\s+(?:including|with)\s+(?:(?:all|their|its|the|supplied)\s+)*(?:packaging|wrappers?|components?)\s*,?\s+(?:vegan|suitable for (?:a )?vegans?)\??',text.strip(),re.I))
    return bool(re.search(r'\bvegans?\b',text,re.I)) and not component_only_claim(text) and (whole_including_parts or not re.search(
        r'\b(?:ingredients?|formula|components?|materials?|fibres?|fibers?|packaging|wrappers?|lining|coating|bristles|handles?|upper|filling|topping|recipe|cookbook|guide|another|other|sibling|except|excluding|some|certain|selected|serving)\b',text,re.I))


def manufacturer_question_scope(text, context):
    full_question=normalized(text)
    last=re.split(r'(?<=[.!;])\s+',full_question)[-1]
    if re.match(r'^(?:is|are)\s+(?:(?:this|the|our|your|these|all|complete|entire|whole)\s+)*(?:products?|articles?|bottles?|jars?|bags?|boxes|box|cartons?|pouches?|pods?|filters?|lids?|caps?)\b',last,re.I):
        full_question=last
    return whole_product_vegan_question(full_question,context)


def manufacturer_faq_pair(context, assessment, question, answer):
    """Require a scoped retained question and its own cited answer section."""
    if not source_reference(context,question,assessment) or not source_reference(context,answer,assessment):
        return False
    if source_path(question)!=source_path(answer):
        return False
    raw,website=published_manufacturer_reference(context,question)
    answer_raw,answer_website=published_manufacturer_reference(context,answer)
    if raw is None or raw!=answer_raw or website!=answer_website:
        return False
    clean=visible_source_text if website else normalized
    text='\n'.join(clean(line) for line in str(raw).splitlines())
    question_quote=clean(question['quote']);answer_quote=clean(answer['quote'])
    # A negative reply ("No. Animal-derived aids are used") is not an
    # assertion that animal inputs are absent. Bind the negation to animal
    # use/content, without crossing sentence punctuation.
    negative_animal=(r'\b(?:no|without)\s+(?:any\s+)?animal\b|'
        r'\bnone\s+of\s+(?:(?:the|our)\s+)?animal\b|'
        r'\b(?:does|do)\s+not\s+(?:use|contain|include)\s+(?:any\s+)?animal\b')
    if not re.search(negative_animal,answer_quote,re.I):
        return False
    questions=list(re.finditer(r'[^?\n]*\?',text))
    for index,match in enumerate(questions):
        full_question=normalized(match.group())
        if question_quote not in full_question and full_question not in question_quote:
            continue
        # A preceding sentence must not remove an antecedent's part scope.
        # Explicit product/pack-format questions can identify their subject.
        if not manufacturer_question_scope(full_question,context):
            continue
        end=questions[index+1].start() if index+1<len(questions) else len(text)
        reply=text[match.end():end]
        # The citation may be concise, but restrictions in the complete
        # retained answer still apply. A later FAQ has its own scope.
        if component_only_claim(reply) or re.search(r'\b(?:except|excluding|not all)\b',reply,re.I):
            continue
        # Match exact visible words with the already-supported whitespace
        # normalisation, then require the negative assertion in this section.
        pattern=re.escape(answer_quote).replace(r'\ ',r'\s+')
        for found in re.finditer(pattern,text):
            if found.end()>end:
                continue
            section=text[max(match.end(),found.start()):min(end,found.end())]
            if re.search(negative_animal,normalized(section),re.I):
                return True
    return False


def interpreted_manufacturer_support(context, assessment):
    if not assessment or any(assessment[d]['status'] in {'insufficient','unresolved','conflicted'} for d in DIMENSIONS):return []
    # Canonical precedence follows the cited basis, not the model's route
    # spelling. Certification text is manufacturer evidence, not an Ocado tag.
    refs=[ref for support in assessment['positive_support'] for ref in support['evidence_refs']]
    fields={'/product/dietary_information','/product/features','/product/detailed_description','/product/further_description',
            '/product/manufacturer_marketing','/product/brand_marketing','/product/other_information','/product/specification','/product/vinification_details'}
    if any((source_reference(context,ref,assessment) or ref['path'] in fields) and manufacturer_reference_claim(context,ref)
           for ref in refs):return refs
    if any(manufacturer_faq_pair(context,assessment,question,answer) for question in refs for answer in refs):return refs
    return []


def evaluate(context: dict[str, Any], *, animal_matches: list[str], cleaned_ingredients: str,
             composition_safe: bool, ordinary_identity: bool, assessment: dict[str, Any] | None = None,
             requested_status: str | None = None, requested_reason: str | None = None,
             animal_quote_checker=None, animal_declarations=None, qualified_animal_declarations=None,
             uncertain_ingredient_refs=None, identity_conflict_refs=None) -> dict[str, Any]:
    """Apply common precedence, with supplied model assessments as interpretations."""
    if assessment is not None:
        assessment = compact_assessment(assessment)
    product = context["product"]
    identity_conflict_refs = list(identity_conflict_refs or [])
    native_identity_refs = native_product_identity.conflict_refs(context)
    copy_refs = list(native_identity_refs)
    copy_refs += native_product_identity.component_material_conflict_refs(context)
    for observer in (native_product_identity.explicit_recipe_count_conflict_refs,
                     native_product_identity.declared_preservative_claim_conflict_refs,
                     native_product_identity.declared_soup_blend_conflict_refs,
                     native_product_identity.declared_recipe_components_conflict_refs):
        copy_refs += observer(context)
    positive, negative = claim_sources(context)
    native_assurance = bool(official_tag(context) or any(not source_path(ref) for ref in positive)
                            or affirmative_name(normalized(product.get('name')), normalized(product.get('brand'))))
    hard_identity_refs = (identity_conflict_refs + native_product_identity.producer_heading_refs(context)
                          + native_product_identity.formal_article_kind_conflict_refs(context))
    identity_conflict_refs = hard_identity_refs + ([] if native_assurance else native_identity_refs)
    # Supplied assessments may identify a real restriction using the same rule
    # ID as an automatic observer. Never erase their semantic concerns here.
    # Replay can recover original agreeing positive readings separately while
    # retaining the superseded gate assessment in the audit history.
    name = normalized(product.get("name"))
    ingredients = normalized(product.get("ingredients"))
    risks = material_risks(context, cleaned_ingredients)
    if uncertain_ingredient_refs:
        risks.append({'code':'source_unresolved','target':'malformed botanical ingredient','rule_id':'inci-identity-ambiguity-v1',
            'evidence_refs':uncertain_ingredient_refs,'resolution_refs':[],'blocks_claim':False})
    result_assessment = assessment or base_assessment(context, complete=composition_safe or ordinary_identity)
    if assessment is not None:
        validate_assessment(assessment, context)
        negative = [ref for ref in negative if not source_path(ref) or source_reference(context, ref, assessment)]
        if requested_status=='vegan':positive=positive or interpreted_manufacturer_support(context,assessment)
        # Carry validated, issue-specific resolutions into generated registry
        # entries. Otherwise persistence/export sees a fresh unresolved copy
        # of the same risk and demotes a previously supported positive.
        # Original model issues remain intact, including any unresolved issue
        # retained from either independent reading.
        risks=[{**risk,'resolution_refs':list({digest(ref):ref for issue in assessment['material_issues']
            if resolved_risk(risk,{'material_issues':[issue]},context) for ref in issue['resolution_refs']}.values())}
            if resolved_risk(risk,assessment,context) else risk for risk in risks]
    else:
        negative = [ref for ref in negative if not source_path(ref)]
    used_refs = [] if not assessment else [ref for item in assessment['positive_support'] for ref in item['evidence_refs']] + [
        ref for dimension in DIMENSIONS for ref in assessment[dimension]['evidence_refs']
    ] + [ref for issue in assessment['material_issues'] for ref in [*issue['evidence_refs'], *issue['resolution_refs']]] + [
        ref for item in assessment['source_applicability'] for dimension in SOURCE_DIMENSIONS for ref in item[dimension]['evidence_refs']]
    unproven_sources = {source_path(ref) for ref in used_refs if source_path(ref)
                       and not source_reference(context, ref, assessment)}
    issues = list({digest(issue): issue for issue in [*risks, *result_assessment["material_issues"]]}.values())
    hard_codes={'identity_conflict','identity_insufficient','evidence_conflict','evidence_stale','animal_ingredient','animal_processing','explicit_nonvegan'}
    issues=[{**issue,'blocks_claim':True} if issue['code'] in hard_codes else issue for issue in issues]
    if negated_name_claim(name):
        issues.append({'code':'evidence_conflict','target':'whole_product','rule_id':'negated-name-claim-v1',
            'evidence_refs':[reference('/product/name',name)],'resolution_refs':[],'blocks_claim':True})
    for ref in qualified_animal_declarations or []:
        resolutions=[]
        if assessment:
            # A short explicit self-product processing statement plus the
            # interpreter's established identity is already scope evidence.
            # Do not demand a redundant issue solely because the source puts
            # "This wine is" before "processed with". Other prose/qualifiers
            # retain the existing explicit context-resolution requirement.
            self_processing = re.match(r'^(?:this|our) (?:wine|product) (?:is|was) (?:fined|clarified|processed|refined) (?:with|using)\b', normalized(ref['quote']), re.I)
            identity_refs = [r for r in assessment['identity']['evidence_refs'] if r['path'] == '/product/name']
            if (requested_status == 'nonvegan' and self_processing and len(ref['quote'].split()) <= 12
                    and assessment['identity']['status'] == 'established' and identity_refs
                    and animal_quote_checker and animal_quote_checker(ref['quote'])
                    and not factory_warning(ref['quote'])
                    and not re.search(r'\b(?:no|not|never|without|another|other|some|certain|may|can|except|excluding|recipe|serve|serving)\b', ref['quote'], re.I)
                    and any(i['code'] == 'animal_processing' and any(r['path'] == ref['path'] for r in i['evidence_refs'])
                            for i in assessment['material_issues'])):
                resolutions = [ref, *identity_refs]
            resolutions=[resolution for issue in assessment['material_issues'] if issue['rule_id']=='animal-declaration-context-v1'
                         for resolution in issue['resolution_refs'] if resolution['path']==ref['path'] and (
                             re.search(r'\b(?:another|other|certain|selected)\s+(?:\w+\s+){0,3}(?:variant|product|range)\b',resolution['quote'],re.I)
                             or (re.search(r'\b(?:recipe|serving suggestion|serve with|prepare with)\b',resolution['quote'],re.I)
                                 and re.search(r'\b(?:milk|egg|gelati(?:n|ne)|honey|butter|cheese|meat|fish)\b',resolution['quote'],re.I))
                             or re.search(r'\b(?:not|never)\s+(?:fined|clarified|processed)\s+(?:with|using)\b',resolution['quote'],re.I)
                             or (re.search(r'\b(?:no|without|free from|may contain)\b',resolution['quote'],re.I)
                                 and re.search(r'\b(?:milk|egg|gelati(?:n|ne)|honey|isinglass|bone char|casein|beeswax|shellac|carmine|animal)\b',resolution['quote'],re.I)
                                 and animal_quote_checker and not animal_quote_checker(resolution['quote'])))] or resolutions
            # Difficult marketing/recipe scope belongs to the evidence
            # interpreter. Require the actual full declaration plus identity,
            # not a detached reassuring phrase or a bare vegan claim.
            for issue in assessment['material_issues']:
                if requested_status=='nonvegan' and not factory_warning(ref['quote']) and issue['rule_id']=='animal-declaration-context-v1' and any(
                    resolution['path']==ref['path'] and normalized(resolution['quote'])==normalized(ref['quote'])
                    for resolution in issue['resolution_refs']) and any(
                    resolution['path']=='/product/name' for resolution in issue['resolution_refs']):
                    resolutions=issue['resolution_refs']
        issues.append({'code':'evidence_conflict','target':'whole_product','rule_id':'animal-declaration-context-v1',
                       'evidence_refs':[ref],'resolution_refs':resolutions,'blocks_claim':True})
    for index, row in enumerate(context.get('manufacturer_vegan_evidence', [])):
        if row.get('source_kind')!='manufacturer_website':
            continue
        source=row.get('source_text')
        if source is None or hashlib.sha256(source.encode()).hexdigest()!=row.get('source_sha256'):
            issues.append({'code':'evidence_stale','target':'manufacturer_capture','rule_id':'source-capture-integrity-v1',
                'evidence_refs':[reference(f'/manufacturer_vegan_evidence/{index}/content',row['content'])],
                'resolution_refs':[],'blocks_claim':True})
    for index, row in enumerate(context.get('manufacturer_source_candidates', [])):
        source=row.get('source_text')
        if source is None or hashlib.sha256(source.encode()).hexdigest()!=row.get('source_sha256'):
            issues.append({'code':'evidence_stale','target':'manufacturer_capture','rule_id':'source-capture-integrity-v1',
                'evidence_refs':[reference(f'/manufacturer_source_candidates/{index}/source_id',row['source_id'])],
                'resolution_refs':[],'blocks_claim':True})
    for index, row in enumerate(context.get("manufacturer_assertion_history", [])):
        issues.append({"code":"evidence_stale","target":"whole_product","rule_id":"active-contrary-assertion-v1",
            "evidence_refs":[reference(f"/manufacturer_assertion_history/{index}/content",row["content"])],
            "resolution_refs":[],"blocks_claim":True})
    # Obvious data misjoins precede animal-content decisions. A recipe naming
    # butter/meat is not a plain produce identity and does not use this guard.
    plant_identity = re.search(r"\b(?:ground cumin|cumin|carrots?|apples?|bananas?|spinach|lettuce|green beans)\b", name, re.I)
    recipe_name = re.search(r"\b(?:with|butter|cream|cheese|milk|honey|meat|pork|chicken|beef|fish|sauce|soup|cake|salad|capsules?|tablets?)\b", name, re.I)
    identity_term = re.sub(r'^ground ', '', plant_identity.group().lower()).rstrip('s') if plant_identity else ''
    identity_composition=ingredients or ' '.join(ref['quote'] for ref in animal_declarations or [])
    different_plain_food = bool(re.search(r'\b(?:carrots?|apples?|bananas?|spinach|lettuce|green beans|cumin)\b',identity_composition,re.I))
    mismatched_identity = bool(plant_identity and not recipe_name and (animal_matches or animal_declarations or (different_plain_food and not native_assurance)) and identity_composition
                               and not re.search(re.escape(identity_term), identity_composition, re.I))
    # A volume-labelled liquid cannot be assumed to have an ingredient list
    # explicitly describing a capsule shell. Do not accuse a bottled product
    # from a potentially misjoined formulation/market declaration.
    pack=normalized(product.get('pack_size'))
    capsule_form_mismatch=bool(re.search(r'\b(?:gelati(?:n|ne)\s+capsules?|capsule\s+shell)\b',ingredients,re.I)
        and re.search(r'\d\s*(?:ml|litres?|liters?|l)\b',pack,re.I)
        and not re.search(r'\b(?:capsules?|softgels?|tablets?)\b',name,re.I))
    mismatched_identity = mismatched_identity or capsule_form_mismatch
    identity_conflict = mismatched_identity or bool(identity_conflict_refs) or result_assessment["identity"]["status"] in {"conflicted", "insufficient", "unresolved"}
    if mismatched_identity:
        issues.append({"code": "identity_conflict", "target": "whole_product", "rule_id": "identity-consistency-v1", "evidence_refs": [reference("/product/name", name), *([reference("/product/ingredients", ingredients)] if ingredients else animal_declarations), *([reference('/product/pack_size',pack)] if capsule_form_mismatch else [])], "resolution_refs": [], "blocks_claim": True})
    elif identity_conflict_refs:
        issues.append({'code':'identity_conflict','target':'whole_product','rule_id':'full-product-identity-v1',
                       'evidence_refs':identity_conflict_refs,'resolution_refs':[],'blocks_claim':True})
    elif identity_conflict:
        issues.append({"code": "identity_conflict" if result_assessment["identity"]["status"] == "conflicted" else "identity_insufficient",
            "target": "whole_product", "rule_id": "identity-consistency-v1",
            "evidence_refs": result_assessment["identity"]["evidence_refs"] or [reference("/product/id",product["id"])],
            "resolution_refs": [], "blocks_claim": True})
    component_conflicts = native_product_identity.component_material_conflict_refs(context)
    if component_conflicts and not native_assurance:
        # Conflicting bottle-body declarations block suitability but do not
        # erase correctly matched animal content of the food/formula itself.
        issues.append({'code':'evidence_conflict','target':'supplied_component',
                       'rule_id':'component-material-consistency-v1','evidence_refs':component_conflicts,
                       'resolution_refs':[],'blocks_claim':True})
    count_conflicts = native_product_identity.explicit_recipe_count_conflict_refs(context)
    if count_conflicts and not native_assurance:
        # A recipe-count discrepancy blocks positive suitability but does not
        # by itself make an explicitly matched animal ingredient a misjoin.
        issues.append({'code':'evidence_conflict','target':'whole_product_composition',
                       'rule_id':'explicit-recipe-count-consistency-v1','evidence_refs':count_conflicts,
                       'resolution_refs':[],'blocks_claim':True})
    preservative_conflicts = native_product_identity.declared_preservative_claim_conflict_refs(context)
    if preservative_conflicts and not native_assurance:
        issues.append({'code':'evidence_conflict','target':'whole_product_composition',
                       'rule_id':'declared-preservative-consistency-v1','evidence_refs':preservative_conflicts,
                       'resolution_refs':[],'blocks_claim':True})
    soup_conflicts = native_product_identity.declared_soup_blend_conflict_refs(context)
    if soup_conflicts and not native_assurance:
        issues.append({'code':'evidence_conflict','target':'whole_product_composition',
                       'rule_id':'declared-soup-blend-consistency-v1','evidence_refs':soup_conflicts,
                       'resolution_refs':[],'blocks_claim':True})
    recipe_conflicts = native_product_identity.declared_recipe_components_conflict_refs(context)
    if recipe_conflicts and not native_assurance:
        issues.append({'code':'evidence_conflict','target':'whole_product_composition',
                       'rule_id':'declared-recipe-components-consistency-v1','evidence_refs':recipe_conflicts,
                       'resolution_refs':[],'blocks_claim':True})
    status, reason, block = "unknown", None, False
    animal_refs = []
    substantive_negative = []
    if identity_conflict:
        block = True
    elif animal_matches or animal_declarations:
        status = "nonvegan"
        animal_refs=[reference("/product/ingredients",ingredients)] if animal_matches else animal_declarations
        processing = not animal_matches and any(re.search(r'\b(?:fined|fining|clarified|clarifying|processed|refined|refining|manufactur\w*)\b',ref['quote'],re.I) for ref in animal_refs)
        issues.append({"code": "animal_processing" if processing else "animal_ingredient", "target": "whole_product", "rule_id": "animal-ingredients-v1", "evidence_refs": animal_refs, "resolution_refs": [], "blocks_claim": True})
        if official_tag(context):
            issues.append({"code": "tag_animal_conflict", "target": "whole_product", "rule_id": "animal-overrides-tag-v1", "evidence_refs": animal_refs, "resolution_refs": [], "blocks_claim": True})
    else:
        unresolved_blocks = [issue for issue in issues if issue["blocks_claim"] and not issue["resolution_refs"]]
        for issue in result_assessment["material_issues"]:
            quotes = " ".join(ref["quote"] for ref in issue["evidence_refs"])
            definite = (issue["code"] == "explicit_nonvegan" and scoped_claim(quotes, negative=True)) or (
                issue["code"] == "animal_processing"
                and re.search(r"\b(?:fined|fining|clarified|processed|processing|refined|refining|manufactur\w*)\b", quotes, re.I)
                and re.search(r"\b(?:isinglass|gelati(?:n|ne)|casein|egg (?:whites?|album\w*)|bone char|animal fats?)\b", quotes, re.I)) or (
                issue['code']=='animal_ingredient' and animal_quote_checker and animal_quote_checker(quotes)
                and re.search(r'\b(?:contains|includes|made (?:with|from)|(?:derived|obtained|sourced|extracted) from|(?:blending|mixing|combining)\s+(?:the\s+)?(?:batches|product|mixture)\s+(?:with|using)|ingredients?\s*:|(?:packaging|wrapper|pod|filter|coating)(?:\s+(?:materials?|composition))?\s*:)',quotes,re.I))
            # Non-negative issue types cannot become substantive animal proof.
            # Their references were already validated above; avoid rescanning
            # complete source pages for a conclusion that is already false.
            if not definite:
                continue
            # A substring that omits 'not', 'other variant' or a qualification
            # cannot turn the complete supplied assertion into negative proof.
            full_text = " ".join(normalized(resolve_reference(context, ref)) for ref in issue["evidence_refs"])
            full_text = re.sub(r"\bnot (?:suitable for (?:a )?vegans?|vegan)\b", "", full_text, flags=re.I)
            if (issue['code'] == 'animal_processing' and re.search(r'\begg whites?\b', quotes, re.I)
                    and animal_quote_checker and not animal_quote_checker(full_text)):
                definite = False
            if factory_warning(full_text) or re.search(r"\b(?:no|not|never|without|free from|another|other|some|certain|may|can)\b", full_text, re.I):
                definite = False
            if any(source_path(ref) and not source_reference(context,ref,assessment) for ref in issue['evidence_refs']):
                definite = False
            if definite and not issue["resolution_refs"]:
                substantive_negative.append(issue)
        name_supported=assessment is None or any(item['reason']=='name' and any(
            ref['path']=='/product/name' for ref in item['evidence_refs']) for item in assessment['positive_support'])
        claim_reason = "tagged" if official_tag(context) else "manufacturer" if positive else "name" if name_supported and affirmative_name(name,normalized(product.get('brand'))) else None
        animal_negative=any(issue['code'] in {'animal_processing','animal_ingredient'} for issue in substantive_negative)
        # Once applicable definite animal use is established, missing facts
        # about other constituents or processes cannot make it suitable. Keep
        # identity, applicability and concrete-evidence conflicts as blockers;
        # generic sufficiency doubts only block a positive suitability claim.
        negative_compatible = {"animal_processing", "animal_ingredient", "explicit_nonvegan", "tag_animal_conflict",
                               "processing_unresolved", "source_unresolved", "composition_incomplete"}
        if animal_negative and all(issue["code"] in negative_compatible for issue in unresolved_blocks):
            status = "nonvegan"
        elif substantive_negative and not claim_reason and all(issue['code']=='explicit_nonvegan' for issue in unresolved_blocks):
            status = "nonvegan"
        elif unresolved_blocks or (negative and claim_reason):
            block = True
            if negative and claim_reason:
                issues.append({"code": "evidence_conflict", "target": "whole_product", "rule_id": "conflicting-claims-v1",
                    "evidence_refs": [*negative, *positive, *([reference("/product/name", name)] if claim_reason == "name" else [])],
                    "resolution_refs": [], "blocks_claim": True})
        elif negative:
            simple=all(ref['path'].startswith('/manufacturer_vegan_evidence/') or ref['quote'].strip().strip('.!').lower() in {
                'not vegan','not suitable for vegans','no longer vegan','no longer suitable for vegans'} for ref in negative)
            if simple or substantive_negative:status='nonvegan'
            else:
                block=True
                issues.append({'code':'evidence_conflict','target':'whole_product','rule_id':'negative-claim-scope-v1',
                               'evidence_refs':negative,'resolution_refs':[],'blocks_claim':True})
        elif claim_reason:
            status, reason = "vegan", claim_reason
        elif composition_safe or ordinary_identity:
            unresolved_dimensions = assessment and any(assessment[d]["status"] in {"insufficient", "unresolved", "conflicted"} for d in DIMENSIONS)
            if not risks and not any(not issue["resolution_refs"] for issue in issues) and not unresolved_dimensions:
                status, reason = "vegan", "ingredients"
        # A structured interpretation may establish ordinary composition or
        # difficult claim scope; every claimed resolution must quote input.
        if status == "unknown" and not block and assessment and requested_status == "vegan":
            supported = {support["reason"]: support for support in assessment["positive_support"]}
            complete = all(assessment[d]["status"] in {"established", "resolved", "covered_by_claim", "not_applicable"} for d in DIMENSIONS)
            unresolved = [i for i in assessment["material_issues"] if not i["resolution_refs"]]
            # Applicable whole-product assurances resolve generic source/process
            # risks; composition-only positives need explicit risk resolutions.
            for candidate_reason in REASONS:
                covered_risks = candidate_reason != 'ingredients' or all(resolved_risk(risk, assessment, context) for risk in risks)
                if candidate_reason not in supported or not complete or unresolved or not covered_risks or unproven_sources:
                    continue
                # Validate each supplied basis in canonical order. An invalid
                # preferred reason cannot erase independently sufficient proof
                # already supplied for another route; it cannot invent it.
                refs = supported[candidate_reason]["evidence_refs"]
                if candidate_reason == "ingredients" and any(ref["path"] in {
                        "/product/name", "/product/ingredients", "/product/other_information", "/product/detailed_description",
                        "/product/features", "/product/specification"} or source_reference(context,ref,assessment) for ref in refs):
                    status, reason = "vegan", "ingredients"
                elif candidate_reason == "manufacturer" and any(
                        (source_reference(context,ref,assessment) or ref["path"] in {
                            "/product/dietary_information", "/product/features", "/product/detailed_description", "/product/further_description",
                            "/product/manufacturer_marketing", "/product/brand_marketing", "/product/other_information", "/product/specification", "/product/vinification_details"})
                        and manufacturer_reference_claim(context,ref) for ref in refs):
                    status, reason = "vegan", "manufacturer"
                elif candidate_reason=='manufacturer' and any(manufacturer_faq_pair(context,assessment,ref,answer) for ref in refs for answer in refs):
                    # A scoped answer to a vegan-suitability question can be
                    # explicit confirmation without repeating 'vegan' in it.
                    status,reason='vegan','manufacturer'
                elif candidate_reason == "name" and affirmative_name(name,normalized(product.get('brand'))) and any(ref["path"] == "/product/name" for ref in refs):
                    status, reason = "vegan", "name"
                if status == 'vegan':
                    break
        if assessment and requested_status == "nonvegan" and status == "unknown" and not block:
            # Evidence-backed model negatives must identify the actual source;
            # an unsupported negative label is never sufficient.
            if substantive_negative:
                status = "nonvegan"
    if status == 'vegan' and assessment and any(
            assessment[dimension]['status'] in {'insufficient','unresolved','conflicted'} for dimension in DIMENSIONS):
        # A supplied positive cannot erase doubt retained from either reading.
        # The interpreter must establish that the claim actually covers it.
        status,reason='unknown',None
    if status == 'unknown' and unproven_sources:
        block = True
        for path in sorted(unproven_sources):
            row = source_row(context, path)
            refs = [reference('/product/name', name), *[ref for ref in used_refs if source_path(ref) == path]]
            issues.append({'code': 'claim_scope_mismatch', 'target': path, 'rule_id': 'source-applicability-v1',
                           'evidence_refs': refs, 'resolution_refs': [], 'blocks_claim': True})
            for conflict_refs in source_variant_conflict(context, path):
                issues.append({'code': 'identity_conflict', 'target': path, 'rule_id': 'source-variant-consistency-v1',
                               'evidence_refs': conflict_refs, 'resolution_refs': [], 'blocks_claim': True})
    # Preserve an unresolved dimension even if its redundant issue entry was
    # omitted. This canonicalises the assessment, never the benchmark label.
    for dimension, code in {'composition':'composition_incomplete', 'components':'composition_incomplete', 'sourcing':'source_unresolved',
                            'processing':'processing_unresolved'}.items():
        value = result_assessment[dimension]
        if assessment is not None and status == 'unknown' and value['status'] in {'insufficient', 'unresolved', 'conflicted'} and value['evidence_refs']:
            issues.append({'code': code, 'target': 'whole_product', 'rule_id': 'assessment-dimension-v1',
                           'evidence_refs': value['evidence_refs'], 'resolution_refs': [], 'blocks_claim': False})
    # Report an Ocado contradiction only from animal evidence accepted for
    # this product. Raw model accusations remain in the independent readings;
    # an unaccepted accusation is a concern to resolve, not a confirmed report.
    accepted_animal_refs = animal_refs if status == 'nonvegan' else []
    if status == 'nonvegan' and not accepted_animal_refs:
        accepted_animal_refs = [ref for issue in substantive_negative
            if issue['code'] in {'animal_processing','animal_ingredient'} for ref in issue['evidence_refs']]
    reportable_animal_conflict = official_tag(context) and bool(accepted_animal_refs)
    unaccepted_tag_issues = [issue for issue in issues if issue['code'] == 'tag_animal_conflict']
    issues = [issue for issue in issues if issue['code'] != 'tag_animal_conflict']
    if reportable_animal_conflict:
        issues.append({'code':'tag_animal_conflict','target':'whole_product','rule_id':'animal-overrides-tag-v1',
            'evidence_refs':accepted_animal_refs,'resolution_refs':[],'blocks_claim':True})
    else:
        issues.extend({**issue,'code':'evidence_conflict','rule_id':'unverified-tag-animal-conflict-v1',
            'blocks_claim':True} for issue in unaccepted_tag_issues)
        if any(not issue['resolution_refs'] for issue in unaccepted_tag_issues):
            status,reason,block = 'unknown',None,True
    if reason:
        refs = ([reference("/product/official_vegan", product["official_vegan"])] if product.get("official_vegan") == 1 else [reference("/flags", context.get("flags", []))]) if reason == "tagged" else (positive or next((support["evidence_refs"] for support in result_assessment["positive_support"] if support["reason"] == reason), [])) if reason == "manufacturer" else [reference("/product/name", name)] if reason == "name" else result_assessment["composition"]["evidence_refs"]
        if reason!='ingredients':
            issues=[{**issue,'resolution_refs':refs} if not issue['blocks_claim'] and not issue['resolution_refs'] else issue for issue in issues]
        result_assessment = {**result_assessment, "positive_support": [{"reason": reason, "evidence_refs": refs}], "material_issues": issues}
        if reason != "ingredients":
            result_assessment = {**result_assessment, **{d: {"status": "covered_by_claim", "evidence_refs": refs} for d in ("composition", "sourcing", "processing", "components")}}
    else:
        result_assessment = {**result_assessment, "material_issues": issues}
    result_assessment=compact_assessment(result_assessment)
    return {"vegan_status": status, "vegan_reason": reason, "positive_block": block,
            "policy_version": POLICY_VERSION, "knowledge_version": KNOWLEDGE_VERSION,
            "materiality_version": MATERIALITY_VERSION, "copy_discrepancy_refs": copy_refs,
            "assessment": result_assessment, "tag_animal_conflict": any(i["code"] == "tag_animal_conflict" for i in issues)}


def policy_prompt() -> str:
    return (
        "Assess complete-product vegan suitability from supplied evidence only. General knowledge can identify a material risk; it cannot invent product-specific assurances. "
        + MATERIALITY_POLICY + " "
        + BATCH_CONTEXT_POLICY + " "
        + CATALOGUE_CONTEXT_POLICY + " "
        + SOURCE_VARIANT_POLICY + " "
        "Scope includes the complete sold article: packaging, coffee pods, brewing filters and every supplied non-edible component, not only food. Assess components separately from edible composition. A food-only claim cannot resolve another component. A single plant-fibre/material description does not automatically establish a complete manufactured component. Ordinary unambiguous foods remain eligible; do not invent speculative risks, but unresolved relevant component composition/source/process means unknown. Sugar-cane fibres are not refined sugar: do not apply sugar-refining risk to a fibre merely because its name contains sugar. "
        "Material identity conflicts that undermine vegan suitability or claim applicability come first and mean unknown. Correctly matched explicit animal ingredients or animal-derived processing mean nonvegan EVEN WITH an official vegan tag or manufacturer/name claim. "
        "Other unresolved material contradictions mean unknown. Applicable official tags, explicit manufacturer claims, affirmative whole-product vegan names, and unambiguous ordinary vegan identity/composition are the four positive routes. "
        "Evaluate and cite all applicable positive routes before selecting the canonical reason: tagged, manufacturer, name, ingredients. Preserve manufacturer ahead of name. Before choosing name, inspect the supplied product-information fields for an applicable producer assertion about this product, including affirmative vegan self-descriptions. Do not omit a higher-priority supported route merely because the name already qualifies. "
        + PRODUCER_ASSERTION_POLICY + " "
        + "Tagged means the supplied official Ocado vegan flag/tag, not a producer's Vegan Society approval or certification. An applicable published producer approval/certification is manufacturer evidence. Cite the actual official flag/tag for tagged, and the producer's assertion for manufacturer. "
        "A name-only positive needs an affirmative assertion about the complete sold article. Negated, hedged ('probably/possibly vegan'), topical, brand-only, component/formula and serving-suggestion wording are not that assertion. Other applicable whole-product evidence may qualify, but actual contrary evidence must be resolved. "
        "A normal affirmative vegan SKU name asserts suitability for the named product. A food noun or ordinary pack-format suffix alone does not restrict that assertion to edible contents; require actual restrictive wording or evidence for food-only/component scope. Apply the same whole-product assertion standard to names, official tags and manufacturer claims. "
        "A separate recipe/formula-only statement does not itself restrict an independently affirmative whole-product vegan name or other applicable whole-product claim. Reject that narrower statement as a manufacturer basis, without treating its mere presence as contrary evidence. If recording its scope as an issue, resolve its non-applicability to the selected route with the actual narrower statement and the independent whole-product claim in resolution_refs. A statement that actually restricts that whole-product claim, concrete contrary evidence or identity conflict still blocks it. "
        "When animal words in marketing clearly describe another recipe, serving suggestion or product rather than this product's contents, record evidence_conflict with rule_id animal-declaration-context-v1 and resolve it by citing BOTH the complete marketing field and /product/name in resolution_refs. Do not clear a real animal-content declaration merely from a vegan assurance. "
        "Missing ingredients is not proof of simplicity. Carrots and simple leaf salads can qualify. Standard flour fortification (calcium, iron, niacin, thiamin, folic acid) is permitted. "
        "A compound ingredient such as vegetable suet or margarine requires its complete constituents or an applicable whole-product claim; a plant descriptor alone is insufficient. "
        "For manufactured non-food goods, headline materials such as cotton, plastic, metal or glass do not establish complete composition, dyes, adhesives, coatings or relevant processing. "
        "The same assessment applies to explicitly described packaging (bags, cartons, jars and other formats), not just coffee pods. Ordinary-food permission does not itself prove a stated manufactured package's complete composition. Conversely, do not invent a supplied component solely because its absence is not proved; an ordinary food with no evidenced material component concern remains eligible. Tie every component doubt to the supplied evidence identifying that component. "
        "For plain produce with no packaging details and no evidenced material component concern, common-sense suitability is permitted. Missing packaging information alone is not composition_incomplete or an unresolved components assessment. Cite the clear ordinary product identity and assess components as established under this policy; do not claim the packaging is absent or invent its materials. A modal verb such as 'can be cooked' is not evidence of a metal can. "
        "The same no-invented-concern principle applies to other unambiguous ordinary foods. 'Packaged in a protective atmosphere' alone identifies neither a bag/pod/filter/material nor a material component concern; it does not by itself require unknown. Do not infer that packaging is absent or suitable from that wording. Explicitly described manufactured components and evidenced component concerns still require their complete assessment. "
        "Check relevant processing (wine/cider/beer/vinegar/juice clarification, maple-syrup defoaming, sugar refining, coatings) and source-dependent ingredients/E numbers, including within compound ingredients. Vinegar identity and plant origin do not prove animal-free clarification of the final product or its precursor. An applicable whole-product claim resolves generic risks, but cannot defeat definite animal content. "
        "No probability, brand reputation, category assumptions or 'usually vegan'. May-contain warnings are not intentional ingredients. Distinguish cocoa butter, coconut milk, oyster mushrooms, plant alternatives, serving suggestions, component claims and other variants. "
        "Manufacturer website records include full source_text and publisher/market metadata. Inspect all complete captures and their exceptions; verified_status and prior scope_match are previous assertions, not independent proof. manufacturer_source_candidates includes other associated retained pages, without inherited decisions. Catalogue-changed associations need fresh exact applicability proof and cannot independently establish a positive. Legacy reviews must be reassessed for this product before promotion. Product-name/format references alone cannot establish website variant applicability: cite the source's named-product assertion as well. "
        "Rows in manufacturer_vegan_evidence WITHOUT source_kind=manufacturer_website are stored Ocado product-information fields (field_title/content), not external websites; interpret their product-level claim scope directly, with no website source_applicability entry. "
        "For each website source relied on, supply source_applicability with its source_path container, scope (product/range/component/other_product/unresolved), and separate product, variant, market and period assessments. Product applicability must cite both /product/name and the source identity. Source applicability is an identity/scope assessment, never covered_by_claim: use established/resolved/not_applicable only when the binding is supported, otherwise insufficient/unresolved/conflicted. Claim coverage belongs to composition, sourcing, processing and components after applicability is established. Every dimension needs source evidence, even when not_applicable; explain exceptions and relevant colour, flavour, fragrance, format, market, formulation and vintage differences. A matching generic title, URL or catalogue association hash does not establish the exact variant. A different displayed variant cannot support this product without explicit inclusive range evidence. For range scope, variant references must quote the inclusive all/every/entire-range statement and establish this product belongs to it. Cite source_text for page facts, market_scope for market metadata and retrieved_at for capture dates; a recent capture date alone does not resolve a formulation/vintage limitation. Empty source_applicability is appropriate when no external source is relied on. "
        "Inside each source_applicability entry, every dimension's evidence_refs MUST refer only to /product/... or that entry's own source_path followed by /. Never cite a companion page inside another page's applicability dimensions. Assess each companion separately; combine their supported facts only in positive_support, material_issues and the whole-product dimensions. If one page alone cannot establish its applicability, mark the relevant dimension insufficient or unresolved. Cross-page dimension citations are invalid and reject the entire batch. "
        "Manufacturer confirmation means explicit whole-product vegan suitability, including an applicable affirmative answer to a vegan-suitability FAQ or the producer's affirmative self-description of this product as vegan. It need not use the exact phrase 'suitable for vegans'. Establish that the assertion concerns this complete product, not another variant, a component, formula, topic or serving suggestion. A question alone, '100% plant-based formula', or absence of animal content is not by itself a whole-product manufacturing assurance. Such composition/source facts can support ingredients only when complete and every relevant process/source issue is resolved. "
        "Use canonical reason priority tagged, manufacturer, name, ingredients when multiple valid routes exist. For unknown/nonvegan reason is null. "
        "Return a structured assessment for all five dimensions (identity, composition, sourcing, processing, components), with positive_support and material_issues. components covers supplied packaging and non-edible parts; do not mark it not_applicable just because a part is not eaten. Every reference is an exact JSON pointer relative to the individual supplied context and a verbatim quote from its value. "
        "Valid examples are /product/name, /product/ingredients and /manufacturer_vegan_evidence/0/content. Never prefix references with /products/N or /classifier_input. "
        "Each applicable unresolved issue must state its code, target, rule_id, supporting references, empty resolution_refs and whether it blocks even a positive claim. "
        "Also retain each applicable registry issue that supplied facts resolve, with the same rule_id and exact resolution_refs quoting those facts. Establishing a dimension alone does not clear a registry issue without its resolution references. "
        "For qualified animal declarations in product information, use rule_id animal-declaration-context-v1 and quote the actual qualifier in resolution_refs only if it establishes non-applicability to this product. A vegan tag/name is not such a resolution. "
        "Generic missing composition, component, sourcing or processing information has blocks_claim=false and may be resolved by an applicable whole-product claim. Missing compound constituents alone do not contradict a whole-product vegan name. Concrete contrary evidence, identity mismatch or an actual restriction/inapplicability of the selected positive basis has blocks_claim=true. Do not retain an unresolved claim_scope_mismatch solely because a different, unused positive statement is narrower. Keep a resolved generic issue with the claim in resolution_refs and assess covered dimensions covered_by_claim, rather than retaining insufficient/unresolved for that same resolved question. Resolutions must quote actual supplied evidence, not model knowledge. "
        "Confidence describes evidence sufficiency under this policy, not a probability estimate about undisclosed physical facts. A supported vegan decision has confidence=certain; if material uncertainty remains, return unknown with confidence=uncertain. Generic missing facts already covered by an applicable whole-product claim are not remaining material uncertainty. "
        "Resolve each ambiguous additive separately, linking the quoted source to that additive, not to a different ingredient. No-animal-content statements alone do not resolve animal-derived fining/refining processes. "
        "For missing information, cite the product name or ID establishing what is being assessed; do not use empty issue references. "
        "Copy short exact reference excerpts; do not retype entire ingredient lists, insert ellipses, change case/punctuation or invent pointer fields. Preserve the complete qualifier where it is needed to establish claim/declaration scope. "
        "An ingredients-only positive requires adequately established composition, applicable sourcing and processing; no empty risk list as a substitute for those assessments. Prefer unknown over unsupported assertions.\n"
        + "Known mechanism registry: " + json.dumps(RISK_REGISTRY, ensure_ascii=True) + "\n"
    )
