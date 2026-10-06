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

POLICY_VERSION = "whole-product-v2"
KNOWLEDGE_VERSION = "vegan-risk-registry-v6"
SCHEMA_VERSION = 4
REASONS = ("tagged", "manufacturer", "name", "ingredients")
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
SOURCE_APPLICABILITY_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["source_path", "scope", *SOURCE_DIMENSIONS],
    "properties": {
        "source_path": {"type": "string", "pattern": r"^/manufacturer_(?:vegan_evidence|source_candidates)/\d+$"},
        "scope": {"type": "string", "enum": ["product", "range", "component", "other_product", "unresolved"]},
        **{dimension: DIMENSION_SCHEMA for dimension in SOURCE_DIMENSIONS},
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
    {"id": "liquid-clarification-v1", "code": "processing_unresolved",
     "pattern": r"\b(?:ciders?|beers?|lagers?|ales?|stouts?|vinegars?|juices?)\b",
     "source": "https://vegan.org/vegan-faq/clarifying-agents",
     "resolution": "Applicable whole-product claim or established animal-free production covering clarification and relevant precursors. Identity, plant ingredients, distillation or an unfiltered final product alone do not establish that animal aids were never used."},
    {"id": "maple-defoaming-v1", "code": "processing_unresolved", "pattern": r"\bmaple\s+syrup\b",
     "source": "https://vegan.org/vegan-faq/clarifying-agents",
     "resolution": "Applicable whole-product claim or explicit animal-free manufacturing/defoaming evidence. Maple origin alone does not resolve processing-aid use."},
    {"id": "sugar-refining-v1", "code": "processing_unresolved", "pattern": r"\b(?:sugar|sucrose)\b",
     "source": "https://www.wearetateandlylesugars.com/faq/", "resolution": "Applicable whole-product claim, unrefined identity, or supplier/process assurance with exceptions applied."},
    {"id": "source-dependent-additives-v1", "code": "source_unresolved",
     "pattern": r"\b(?:e\s*322|e\s*422|e\s*47[1234567]|e\s*631|e\s*635|e\s*920|lecithin|glycer(?:ol|in|ine)|mono(?:[- ]and[- ]|/)?diglycerides?|mono[- ]and[- ]di[- ]glycerides?)\b",
     "source": "https://www.vegansociety.com/news/blog/how-avoid-buying-non-vegan-products", "resolution": "Applicable whole-product claim or explicit source for the relevant ingredient."},
    {"id": "citrus-coating-v1", "code": "processing_unresolved", "pattern": r"\b(?:lemons?|limes?|oranges?|satsumas?|tangerines?)\b",
     "source": "https://www.vegansociety.com/news/blog/how-avoid-buying-non-vegan-products", "resolution": "Applicable whole-product claim, unwaxed identity or established suitable coating."},
    {"id": "unspecified-composition-v1", "code": "source_unresolved",
     "pattern": r"\b(?:flavou?rings?|enzymes?|vitamins?|vitamin d3|cholecalciferol|rennet|glaze|raising agents?|firming agents?|acidity regulators?)\b",
     "source": "https://www.gov.uk/government/publications/approved-additives-and-e-numbers/approved-additives-and-e-numbers",
     "resolution": "Applicable whole-product claim or explicit relevant composition/source; a functional class alone is not identity."},
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


def normalized(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def factory_warning(text: str) -> bool:
    return bool(re.search(r'\b(?:factory|facility|site|equipment|production line)\b.*\b(?:handles?|handling|processes?|processing|uses?|using)\b|\b(?:cross[- ]contamination|cross[- ]contact)\b',text,re.I))


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
    visible_quote=visible_source_text(reference['quote'])
    visible_match=isinstance(value,str) and path.endswith('/source_text') and bool(visible_quote) and visible_quote in visible_source_text(value)
    if not quote or (quote not in text and not visible_match):
        raise ValueError(f"Evidence quote is absent at {path}")
    return value


def reference(path: str, value: Any) -> dict[str, str]:
    return {"path": path, "quote": normalized(value) if isinstance(value, str) else json.dumps(value)}


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


def affirmative_name(name: str, brand: str = '') -> bool:
    # A producer's name/reputation is not a claim about the sold article.
    if re.search(r'\bvegan\b',brand,re.I):
        name=re.sub(re.escape(brand),'',name,count=1,flags=re.I).strip()
    if '?' in name or negated_name_claim(name) or not re.search(r"\bvegan\b", name, re.I):
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
    if component_only_claim(text):
        return False
    text = re.sub(r"\b(?:not|may not|no longer) suitable for (?:a )?vegans?\b|\bnot vegan\b", "", text, flags=re.I)
    if re.search(r"\b(?:not|never|no longer|may not|cannot|can't)\b.*\bvegans?\b", text, re.I):
        return False
    if re.search(r"\b(?:vegan (?:ingredients?|components?|dressing|sauce)|serve|serving|recipe|add (?:a )?vegan)\b", text, re.I):
        return False
    return text.strip().lower() in {"vegan", "suitable for vegans"} or bool(re.search(
        r"\b(?:suitable for (?:a )?vegans?|suitable for (?:a )?vegan diet|suitable for vegetarians? (?:and|&) vegans?|certified vegan|registered with (?:the )?vegan society|vegan[- ]friendly)\b", text, re.I))


def direct_claim(text: str, path: str) -> bool:
    """Only simple product-level claims bypass semantic scope interpretation."""
    if '?' in text or component_only_claim(text) or scoped_claim(text,negative=True) or re.search(r'\b(?:another|other|except|excluding|some|certain|selected|not all|variant)\b',text,re.I):return False
    plain=text.strip().strip('.!').lower()
    standalone={'vegan','vegan friendly','vegan-friendly','suitable for vegans','suitable for a vegan diet',
                'suitable for vegetarians and vegans','suitable for vegetarians & vegans','certified vegan'}
    if plain in standalone:return True
    if path in {'/product/dietary_information','/product/features'} and any(part.strip().strip('.!').lower() in standalone for part in re.split(r'[,;\n]|\.\s+',text)):
        return True
    if not scoped_claim(text):return False
    return bool(re.fullmatch(r'(?:this|the) product is (?:vegan|suitable for vegans)[.!]?',text.strip(),re.I))


def component_only_claim(text: str) -> bool:
    """Explicit dietary/component-only scope cannot assure the entire article."""
    return bool(re.search(r'\b(?:food|edible (?:contents?|components?)|contents?|drink|coffee)\s+only\b|'
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
        target_text = cleaned_ingredients if rule["id"] == "unspecified-composition-v1" else ingredients if rule["id"] in {"sugar-refining-v1", "source-dependent-additives-v1"} else f"{name} {ingredients}"
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
            unresolved_text = re.sub(r"\b(?:soya?|sunflower)\s+lecithin\b(?:\s*\(?e\s*322\)?)?|\be\s*322\s*\((?:soya?|sunflower)\)|\b(?:vegetable|plant[- ]derived)\s+glycer(?:ol|in|ine)\b", "", cleaned_ingredients, flags=re.I)
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


def resolved_risk(risk: dict[str, Any], assessment: dict[str, Any]) -> bool:
    """A risk's own identity is not evidence resolving that risk.

    Interpretation still belongs to the model, but an unchanged ambiguous term
    (e.g. 'E471' or 'vegetable suet') cannot serve as its own source/composition
    assurance. Whole-product claims are evaluated separately.
    """
    resolution_patterns = {
        "source-dependent-additives-v1": r"\b(?:plant|vegetable|soya?|sunflower|rapeseed|vegan)\b",
        "unspecified-composition-v1": r"\b(?:plant|vegetable|soya?|sunflower|lichen|vegan)\b|\([^()]+\)",
        "wine-fining-v1": r"\bunfined\b|\b(?:fining|fined|processing|processed|manufactur\w*)\b.*\b(?:no animal|without animal|animal[- ]free|vegan)\b|\b(?:no animal|without animal|animal[- ]free|vegan)\b.*\b(?:fining|fined|processing|processed|manufactur\w*)\b",
        "liquid-clarification-v1": r"\b(?:all production stages including (?:all )?ingredient and precursor production|from (?:the )?whole (?:fruit|apples|grapes|grains) to (?:the )?finished product)\b.*\b(?:no animal|without animal|animal[- ]free|vegan)\b|\b(?:no animal|without animal|animal[- ]free|vegan)\b.*\b(?:all production stages including (?:all )?ingredient and precursor production|from (?:the )?whole (?:fruit|apples|grapes|grains) to (?:the )?finished product)\b",
        "maple-defoaming-v1": r"\b(?:defoam\w*|processing aids?|manufactur\w*|production)\b.*\b(?:no animal|without animal|animal[- ]free|vegan)\b|\b(?:no animal|without animal|animal[- ]free|vegan)\b.*\b(?:defoam\w*|processing aids?|manufactur\w*|production)\b",
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
            if risk['rule_id'] in {'liquid-clarification-v1','maple-defoaming-v1'} and re.search(
                    r'\b(?:not|never|may|might|could|except|excluding|another|other variant)\b',text,re.I):
                continue
            if risk['rule_id']=='source-dependent-additives-v1':
                target=re.escape(risk['target']).replace(r'\ ',r'\s*')
                marker=r'(?:plant[- ]derived|vegetable|soya?|sunflower|rapeseed|vegan)'
                source_link=rf'\b{target}\b\s*(?:\([^)]*\b{marker}\b[^)]*\)|(?:(?:derived|sourced|made|obtained)\s+)?from\s+(?:\w+\s+){{0,3}}{marker}\b)|\b{marker}\b\s+(?:derived\s+)?\b{target}\b'
                global_origin=r'\b(?:all|entire|100%)\b.*\b(?:ingredients|formula|composition)\b.*\b(?:plant|vegetable|non[- ]animal|vegan)\b'
                if re.search(source_link,text,re.I) or re.search(global_origin,text,re.I):return True
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


def manufacturer_claim_quote(text):
    if '?' in text or component_only_claim(text) or re.search(r'\b(?:not|never|no longer|may not|another|other|sibling|variant|except|excluding|some|certain|selected|serving|recipe|components?|ingredients?)\b', text, re.I):
        return False
    return scoped_claim(text) or bool(re.search(r'\b(?:is|are) (?:suitable for (?:a )?vegans?|vegan)\b',text,re.I))


def whole_product_vegan_question(text):
    # An answer to an ingredients/materials question is composition evidence,
    # not a published assurance about the complete product and its processes.
    return bool(re.search(r'\bvegans?\b',text,re.I)) and not component_only_claim(text) and not re.search(
        r'\b(?:ingredients?|formula|components?|materials?|fibres?|fibers?)\b',text,re.I)


def interpreted_manufacturer_support(context, assessment):
    if not assessment or any(assessment[d]['status'] in {'insufficient','unresolved','conflicted'} for d in DIMENSIONS):return []
    refs=next((support['evidence_refs'] for support in assessment['positive_support'] if support['reason']=='manufacturer'),[])
    fields={'/product/dietary_information','/product/features','/product/detailed_description','/product/further_description',
            '/product/manufacturer_marketing','/product/brand_marketing','/product/other_information','/product/specification','/product/vinification_details'}
    if any((source_reference(context,ref,assessment) or ref['path'] in fields) and manufacturer_claim_quote(ref['quote'])
           and not component_only_claim(normalized(resolve_reference(context,ref))) for ref in refs):return refs
    if any(source_reference(context,question,assessment) and whole_product_vegan_question(question['quote']) and any(
            answer['path']==question['path'] and re.search(r'\b(?:no|none|does not|do not|without)\b.*\b(?:animal|animal origin)\b',answer['quote'],re.I)
            for answer in refs) for question in refs):return refs
    return []


def evaluate(context: dict[str, Any], *, animal_matches: list[str], cleaned_ingredients: str,
             composition_safe: bool, ordinary_identity: bool, assessment: dict[str, Any] | None = None,
             requested_status: str | None = None, requested_reason: str | None = None,
             animal_quote_checker=None, animal_declarations=None, qualified_animal_declarations=None,
             uncertain_ingredient_refs=None, identity_conflict_refs=None) -> dict[str, Any]:
    """Apply common precedence, with supplied model assessments as interpretations."""
    product = context["product"]
    positive, negative = claim_sources(context)
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
            if resolved_risk(risk,{'material_issues':[issue]}) for ref in issue['resolution_refs']}.values())}
            if resolved_risk(risk,assessment) else risk for risk in risks]
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
            resolutions=[resolution for issue in assessment['material_issues'] if issue['rule_id']=='animal-declaration-context-v1'
                         for resolution in issue['resolution_refs'] if resolution['path']==ref['path'] and (
                             re.search(r'\b(?:another|other|certain|selected)\s+(?:\w+\s+){0,3}(?:variant|product|range)\b',resolution['quote'],re.I)
                             or (re.search(r'\b(?:recipe|serving suggestion|serve with|prepare with)\b',resolution['quote'],re.I)
                                 and re.search(r'\b(?:milk|egg|gelati(?:n|ne)|honey|butter|cheese|meat|fish)\b',resolution['quote'],re.I))
                             or re.search(r'\b(?:not|never)\s+(?:fined|clarified|processed)\s+(?:with|using)\b',resolution['quote'],re.I)
                             or (re.search(r'\b(?:no|without|free from|may contain)\b',resolution['quote'],re.I)
                                 and re.search(r'\b(?:milk|egg|gelati(?:n|ne)|honey|isinglass|bone char|casein|beeswax|shellac|carmine|animal)\b',resolution['quote'],re.I)
                                 and animal_quote_checker and not animal_quote_checker(resolution['quote'])))]
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
    mismatched_identity = bool(plant_identity and not recipe_name and (animal_matches or animal_declarations or different_plain_food) and identity_composition
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
    status, reason, block = "unknown", None, False
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
        substantive_negative = []
        for issue in result_assessment["material_issues"]:
            quotes = " ".join(ref["quote"] for ref in issue["evidence_refs"])
            definite = (issue["code"] == "explicit_nonvegan" and scoped_claim(quotes, negative=True)) or (
                issue["code"] == "animal_processing"
                and re.search(r"\b(?:fined|fining|clarified|processed|processing|refined|refining|manufactur\w*)\b", quotes, re.I)
                and re.search(r"\b(?:isinglass|gelati(?:n|ne)|casein|egg album\w*|bone char|animal fats?)\b", quotes, re.I)) or (
                issue['code']=='animal_ingredient' and animal_quote_checker and animal_quote_checker(quotes)
                and re.search(r'\b(?:contains|includes|made (?:with|from)|(?:derived|obtained|sourced|extracted) from|ingredients?\s*:|(?:packaging|wrapper|pod|filter|coating)(?:\s+(?:materials?|composition))?\s*:)',quotes,re.I))
            # A substring that omits 'not', 'other variant' or a qualification
            # cannot turn the complete supplied assertion into negative proof.
            full_text = " ".join(normalized(resolve_reference(context, ref)) for ref in issue["evidence_refs"])
            full_text = re.sub(r"\bnot (?:suitable for (?:a )?vegans?|vegan)\b", "", full_text, flags=re.I)
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
        if animal_negative and all(issue["code"] in {"animal_processing", "animal_ingredient", "explicit_nonvegan"} for issue in unresolved_blocks):
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
            covered_risks = requested_reason != 'ingredients' or all(resolved_risk(risk, assessment) for risk in risks)
            if requested_reason in supported and complete and not unresolved and covered_risks and not unproven_sources:
                refs = supported[requested_reason]["evidence_refs"]
                if requested_reason == "ingredients" and any(ref["path"] in {
                        "/product/name", "/product/ingredients", "/product/other_information", "/product/detailed_description",
                        "/product/features", "/product/specification"} or source_reference(context,ref,assessment) for ref in refs):
                    status, reason = "vegan", "ingredients"
                elif requested_reason == "manufacturer" and any(
                        (source_reference(context,ref,assessment) or ref["path"] in {
                            "/product/dietary_information", "/product/features", "/product/detailed_description", "/product/further_description",
                            "/product/manufacturer_marketing", "/product/brand_marketing", "/product/other_information", "/product/specification", "/product/vinification_details"})
                        and manufacturer_claim_quote(ref['quote']) and not component_only_claim(normalized(resolve_reference(context,ref))) for ref in refs):
                    status, reason = "vegan", "manufacturer"
                elif requested_reason=='manufacturer' and any(source_reference(context,ref,assessment) and whole_product_vegan_question(ref['quote']) and any(
                        source_reference(context,answer,assessment) and answer['path']==ref['path'] and re.search(r'\b(?:no|none|does not|do not|without)\b.*\b(?:animal|animal origin)\b',answer['quote'],re.I) for answer in refs) for ref in refs):
                    # A scoped answer to a vegan-suitability question can be
                    # explicit confirmation without repeating 'vegan' in it.
                    status,reason='vegan','manufacturer'
                elif requested_reason == "name" and affirmative_name(name,normalized(product.get('brand'))) and any(ref["path"] == "/product/name" for ref in refs):
                    status, reason = "vegan", "name"
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
    if reason:
        refs = ([reference("/product/official_vegan", product["official_vegan"])] if product.get("official_vegan") == 1 else [reference("/flags", context.get("flags", []))]) if reason == "tagged" else (positive or next((support["evidence_refs"] for support in result_assessment["positive_support"] if support["reason"] == reason), [])) if reason == "manufacturer" else [reference("/product/name", name)] if reason == "name" else result_assessment["composition"]["evidence_refs"]
        if reason!='ingredients':
            issues=[{**issue,'resolution_refs':refs} if not issue['blocks_claim'] and not issue['resolution_refs'] else issue for issue in issues]
        result_assessment = {**result_assessment, "positive_support": [{"reason": reason, "evidence_refs": refs}], "material_issues": issues}
        if reason != "ingredients":
            result_assessment = {**result_assessment, **{d: {"status": "covered_by_claim", "evidence_refs": refs} for d in ("composition", "sourcing", "processing", "components")}}
    else:
        result_assessment = {**result_assessment, "material_issues": issues}
    if status == "nonvegan" and official_tag(context) and not any(i["code"] == "tag_animal_conflict" for i in issues):
        animal_issues = [i for i in issues if i["code"] in {"animal_processing", "animal_ingredient"}]
        if animal_issues:
            issues.append({**animal_issues[0], "code": "tag_animal_conflict", "rule_id": "animal-overrides-tag-v1", "blocks_claim": True})
            result_assessment = {**result_assessment, "material_issues": issues}
    result_assessment={**result_assessment,'material_issues':list({digest(issue):issue for issue in result_assessment['material_issues']}.values())}
    return {"vegan_status": status, "vegan_reason": reason, "positive_block": block,
            "policy_version": POLICY_VERSION, "knowledge_version": KNOWLEDGE_VERSION,
            "assessment": result_assessment, "tag_animal_conflict": any(i["code"] == "tag_animal_conflict" for i in issues)}


def policy_prompt() -> str:
    return (
        "Assess complete-product vegan suitability from supplied evidence only. General knowledge can identify a material risk; it cannot invent product-specific assurances. "
        "Scope includes the complete sold article: packaging, coffee pods, brewing filters and every supplied non-edible component, not only food. Assess components separately from edible composition. A food-only claim cannot resolve another component. A single plant-fibre/material description does not automatically establish a complete manufactured component. Ordinary unambiguous foods remain eligible; do not invent speculative risks, but unresolved relevant component composition/source/process means unknown. Sugar-cane fibres are not refined sugar: do not apply sugar-refining risk to a fibre merely because its name contains sugar. "
        "Identity conflicts come first and mean unknown. Correctly matched explicit animal ingredients or animal-derived processing mean nonvegan EVEN WITH an official vegan tag or manufacturer/name claim. "
        "Other unresolved material contradictions mean unknown. Applicable official tags, explicit manufacturer claims, affirmative whole-product vegan names, and unambiguous ordinary vegan identity/composition are the four positive routes. "
        "When more than one positive route applies, canonical reason priority is tagged, manufacturer, name, ingredients. Preserve manufacturer ahead of name. "
        "A name-only positive needs an affirmative assertion about the complete sold article. Negated, hedged ('probably/possibly vegan'), topical, brand-only, component/formula and serving-suggestion wording are not that assertion. Other applicable whole-product evidence may qualify, but actual contrary evidence must be resolved. "
        "A normal affirmative vegan SKU name asserts suitability for the named product. A food noun or ordinary pack-format suffix alone does not restrict that assertion to edible contents; require actual restrictive wording or evidence for food-only/component scope. Apply the same whole-product assertion standard to names, official tags and manufacturer claims. "
        "When animal words in marketing clearly describe another recipe, serving suggestion or product rather than this product's contents, record evidence_conflict with rule_id animal-declaration-context-v1 and resolve it by citing BOTH the complete marketing field and /product/name in resolution_refs. Do not clear a real animal-content declaration merely from a vegan assurance. "
        "Missing ingredients is not proof of simplicity. Carrots and simple leaf salads can qualify. Standard flour fortification (calcium, iron, niacin, thiamin, folic acid) is permitted. "
        "A compound ingredient such as vegetable suet or margarine requires its complete constituents or an applicable whole-product claim; a plant descriptor alone is insufficient. "
        "For manufactured non-food goods, headline materials such as cotton, plastic, metal or glass do not establish complete composition, dyes, adhesives, coatings or relevant processing. "
        "The same assessment applies to explicitly described packaging (bags, cartons, jars and other formats), not just coffee pods. Ordinary-food permission does not itself prove a stated manufactured package's complete composition. Conversely, do not invent a supplied component solely because its absence is not proved; an ordinary food with no evidenced material component concern remains eligible. Tie every component doubt to the supplied evidence identifying that component. "
        "For plain produce with no packaging details and no evidenced material component concern, common-sense suitability is permitted. Missing packaging information alone is not composition_incomplete or an unresolved components assessment. Cite the clear ordinary product identity and assess components as established under this policy; do not claim the packaging is absent or invent its materials. A modal verb such as 'can be cooked' is not evidence of a metal can. "
        "Check relevant processing (wine/cider/beer/vinegar/juice clarification, maple-syrup defoaming, sugar refining, coatings) and source-dependent ingredients/E numbers, including within compound ingredients. Vinegar identity and plant origin do not prove animal-free clarification of the final product or its precursor. An applicable whole-product claim resolves generic risks, but cannot defeat definite animal content. "
        "No probability, brand reputation, category assumptions or 'usually vegan'. May-contain warnings are not intentional ingredients. Distinguish cocoa butter, coconut milk, oyster mushrooms, plant alternatives, serving suggestions, component claims and other variants. "
        "Manufacturer website records include full source_text and publisher/market metadata. Inspect all complete captures and their exceptions; verified_status and prior scope_match are previous assertions, not independent proof. manufacturer_source_candidates includes other associated retained pages, without inherited decisions. Catalogue-changed associations need fresh exact applicability proof and cannot independently establish a positive. Legacy reviews must be reassessed for this product before promotion. "
        "Rows in manufacturer_vegan_evidence WITHOUT source_kind=manufacturer_website are stored Ocado product-information fields (field_title/content), not external websites; interpret their product-level claim scope directly, with no website source_applicability entry. "
        "For each website source relied on, supply source_applicability with its source_path container, scope (product/range/component/other_product/unresolved), and separate product, variant, market and period assessments. Product applicability must cite both /product/name and the source identity. Every dimension needs source evidence, even when not_applicable; explain exceptions and relevant colour, flavour, fragrance, format, market, formulation and vintage differences. A matching generic title, URL or catalogue association hash does not establish the exact variant. A different displayed variant cannot support this product without explicit inclusive range evidence. For range scope, variant references must quote the inclusive all/every/entire-range statement and establish this product belongs to it. Cite source_text for page facts, market_scope for market metadata and retrieved_at for capture dates; a recent capture date alone does not resolve a formulation/vintage limitation. Empty source_applicability is appropriate when no external source is relied on. "
        "Manufacturer confirmation means explicit whole-product vegan suitability, including an applicable affirmative answer to a vegan-suitability FAQ. A question alone, '100% plant-based formula', or absence of animal content is not by itself a whole-product manufacturing assurance. Such composition/source facts can support ingredients only when complete and every relevant process/source issue is resolved. "
        "Use canonical reason priority tagged, manufacturer, name, ingredients when multiple valid routes exist. For unknown/nonvegan reason is null. "
        "Return a structured assessment for all five dimensions (identity, composition, sourcing, processing, components), with positive_support and material_issues. components covers supplied packaging and non-edible parts; do not mark it not_applicable just because a part is not eaten. Every reference is an exact JSON pointer relative to the individual supplied context and a verbatim quote from its value. "
        "Valid examples are /product/name, /product/ingredients and /manufacturer_vegan_evidence/0/content. Never prefix references with /products/N or /classifier_input. "
        "Each applicable unresolved issue must state its code, target, rule_id, supporting references, empty resolution_refs and whether it blocks even a positive claim. "
        "Also retain each applicable registry issue that supplied facts resolve, with the same rule_id and exact resolution_refs quoting those facts. Establishing a dimension alone does not clear a registry issue without its resolution references. "
        "For qualified animal declarations in product information, use rule_id animal-declaration-context-v1 and quote the actual qualifier in resolution_refs only if it establishes non-applicability to this product. A vegan tag/name is not such a resolution. "
        "Generic missing composition, component, sourcing or processing information has blocks_claim=false and may be resolved by an applicable whole-product claim. Missing compound constituents alone do not contradict a whole-product vegan name. Concrete contrary evidence, identity mismatch or a genuinely inapplicable/restricted claim has blocks_claim=true. Keep a resolved generic issue with the claim in resolution_refs and assess covered dimensions covered_by_claim, rather than retaining insufficient/unresolved for that same resolved question. Resolutions must quote actual supplied evidence, not model knowledge. "
        "Confidence describes evidence sufficiency under this policy, not a probability estimate about undisclosed physical facts. A supported vegan decision has confidence=certain; if material uncertainty remains, return unknown with confidence=uncertain. Generic missing facts already covered by an applicable whole-product claim are not remaining material uncertainty. "
        "Resolve each ambiguous additive separately, linking the quoted source to that additive, not to a different ingredient. No-animal-content statements alone do not resolve animal-derived fining/refining processes. "
        "For missing information, cite the product name or ID establishing what is being assessed; do not use empty issue references. "
        "Copy short exact reference excerpts; do not retype entire ingredient lists, insert ellipses, change case/punctuation or invent pointer fields. Preserve the complete qualifier where it is needed to establish claim/declaration scope. "
        "An ingredients-only positive requires adequately established composition, applicable sourcing and processing; no empty risk list as a substitute for those assessments. Prefer unknown over unsupported assertions.\n"
        + "Known mechanism registry: " + json.dumps(RISK_REGISTRY, ensure_ascii=True) + "\n"
    )
