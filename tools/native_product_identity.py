"""Source-backed contradictions between native product-information fields.

These checks establish review blockers, never animal content or vegan suitability.
They use declared subjects and formats, not brand reputation or product ID lists.
"""
import re
import unicodedata
from decimal import Decimal
from functools import lru_cache

from catalogue_brand_identity import BRAND_LABELS


def fold(value):
    value = re.sub(r'[™®©]', '', str(value or ''))
    words = re.findall(r'[a-z0-9]+', ''.join(c for c in unicodedata.normalize(
        'NFKD', value).casefold() if not unicodedata.combining(c)))
    spelling = {'foccacia':'focaccia','yogurt':'yoghurt','chili':'chilli','flavored':'flavoured',
                'cyder':'cider','basilico':'basil','electrolite':'electrolyte'}
    return ' '.join(spelling.get(word,word) for word in words)


@lru_cache(maxsize=1)
def brand_heading_patterns():
    indexed = {}
    for label in BRAND_LABELS:
        words = fold(label).split()
        pattern = re.compile(r'\b' + r'[\W_]+'.join(map(re.escape, words)) + r'\b', re.I)
        indexed.setdefault(words[0], []).append((label, pattern))
    return indexed


def producer_heading_refs(context):
    """Identify a different named product asserting its own composition."""
    p = context['product']
    own = fold(p.get('brand'))
    if not own:
        return []
    owner_fields = ('name', 'brand', 'manufacturer', 'manufacturer_name',
                    'manufacturer_address', 'importer_name', 'importer_address',
                    'distributor_name', 'distributor_address')
    owned = ' '.join(fold(p.get(key)) for key in owner_fields)
    refs = []
    for key in ('detailed_description', 'further_description', 'specification'):
        for sentence in re.split(r'[.!?;\n]', str(p.get(key) or '')):
            sentence = sentence.strip(' ,:-')
            verb = re.search(r'\b(?:contains?|provides?)\b', sentence, re.I)
            if not verb:
                continue
            heading = sentence[:verb.start()].strip()
            tokens = fold(heading).split()
            if not tokens:
                continue
            for label, pattern in brand_heading_patterns().get(tokens[0], ()):
                normalized = fold(label)
                # Explicit possessive brand families can share a declared
                # manufacturer while using different range names. A common
                # word or location alone is not ownership evidence.
                owner = re.match(r"^([A-Za-z][A-Za-z-]{3,})['’]s\b", str(p.get('brand') or ''), re.I)
                other_owner = re.match(r"^([A-Za-z][A-Za-z-]{3,})['’]s\b", label, re.I)
                manufacturer = re.sub(r'^(?:UK|GB|IE|EU|US)\s*:\s*', '',
                                      str(p.get('manufacturer') or ''), flags=re.I).split(',')[0]
                declared_owner = fold(manufacturer).split()
                if (owner and other_owner and fold(owner.group(1)) == fold(other_owner.group(1))
                        and declared_owner and declared_owner[0] == fold(owner.group(1))):
                    continue
                if (re.search(r'\b' + re.escape(normalized) + r'\b', owned)
                        or normalized.startswith(own + ' ') or own.startswith(normalized + ' ')):
                    continue  # Declared owner, parent or range alias.
                match = pattern.match(heading)
                if not match:
                    continue
                words = re.findall(r'[A-Za-z]+', match.group())
                tail = heading[match.end():].strip(' ,:&-')
                if (sum(word[0].isupper() for word in words) < 2 or not tail
                        or len(tail.split()) > 18):
                    continue
                if re.search(r'\b(?:other|another|range|company|business|products|portfolio|serve|serving|recipe|example|compare|comparison)\b', tail, re.I):
                    continue
                refs.extend([{'path': '/product/name', 'quote': str(p.get('name') or '')},
                             {'path': '/product/brand', 'quote': str(p['brand'])},
                             {'path': '/product/' + key, 'quote': sentence}])
                break
    return refs


COUNTRIES = {
    'australia': ('australia', 'australian'), 'spain': ('spain', 'spanish'),
    'france': ('france', 'french'), 'italy': ('italy', 'italian'),
    'portugal': ('portugal', 'portuguese'), 'germany': ('germany', 'german'),
    'austria': ('austria', 'austrian'), 'greece': ('greece', 'greek'),
    'argentina': ('argentina', 'argentinian', 'argentine'), 'chile': ('chile', 'chilean'),
    'new zealand': ('new zealand',), 'south africa': ('south africa', 'south african'),
    'united states': ('united states', 'usa', 'u s a', 'american'),
    'united kingdom': ('united kingdom', 'uk', 'u k', 'britain', 'british', 'england', 'english',
                       'scotland', 'scottish', 'wales', 'welsh'),
    'ireland': ('ireland', 'irish'), 'canada': ('canada', 'canadian'),
    'hungary': ('hungary', 'hungarian'), 'romania': ('romania', 'romanian'),
    'bulgaria': ('bulgaria', 'bulgarian'), 'lebanon': ('lebanon', 'lebanese'),
}

# Common named cultivar identities, including equivalent wine-name spellings.
# These identify declarations; they do not establish ingredients or suitability.
GRAPE_IDENTITIES = (
    ('sauvignon blanc',), ('chardonnay',), ('pinot grigio', 'pinot gris'),
    ('pinot noir',), ('pinot blanc',), ('riesling',), ('chenin blanc',),
    ('viognier',), ('gewurztraminer',), ('gruner veltliner',), ('verdejo',),
    ('albarino',), ('vermentino',), ('semillon',), ('muscat', 'moscato'),
    ('merlot',), ('cabernet sauvignon',), ('cabernet franc',), ('shiraz', 'syrah'),
    ('malbec',), ('tempranillo',), ('grenache', 'garnacha'),
    ('zinfandel', 'primitivo'), ('nebbiolo',), ('sangiovese',),
    ('mourvedre', 'monastrell'), ('carmenere',), ('gamay',), ('barbera',), ('glera',),
    ('xinomavro',), ('moschofilero',), ('assyrtiko',),
)


def component_material_conflict_refs(context):
    p = context['product']; refs = labelled_container_conflict_refs(context)
    # Explicit bottle-body materials disagree; missing materials, a plastic
    # cap on a glass bottle, and generic recycling advice are not conflicts.
    package = str(p.get('package_type') or '')
    material = re.fullmatch(r'\s*(glass|plastic)\s+bottle\s*', package, re.I)
    if material:
        for key in ('detailed_description', 'further_description', 'specification',
                    'features', 'other_information', 'brand_marketing', 'manufacturer_marketing'):
            text = str(p.get(key) or '')
            declared = re.search(r'\b(?:our|this|the|these)\s+bottles?\s+(?:(?:is|are)\s+)?made\s+(?:of|from)\s+(?:(?:100\s*%|post[- ]consumer|PCR|recycled)\s+)*(glass|plastic)\b', text, re.I)
            if declared and declared.group(1).casefold() != material.group(1).casefold():
                refs.append({'path': '/product/' + key, 'quote': declared.group()})
                refs.append({'path': '/product/package_type', 'quote': package})
            # A direct sentence/bullet can omit the determiner: "Bottle is
            # made from recycled plastic" still declares this bottle's body.
            for sentence in re.split(r'[.!?;\n•]', text):
                if re.search(r'\b(?:other|another|example|range|compare|comparison)\b', sentence, re.I):
                    continue
                bare = re.search(r'(?:^|:|,)\s*(bottles?\s+(?:(?:is|are)\s+)?made\s+'
                                 r'(?:of|from)\s+(?:(?:100\s*%|post[- ]consumer|PCR|recycled)\s+)*'
                                 r'(glass|plastic)\b)', sentence, re.I)
                if bare and bare.group(2).casefold() != material.group(1).casefold():
                    refs.extend([{'path': '/product/' + key, 'quote': bare.group(1)},
                                 {'path': '/product/package_type', 'quote': package}])
            # A specification's "Bottle / PCR plastic" names the body.
            # A cap, lid, pump or label entry is not that declaration.
            body = re.search(r'\bbottle\s*/\s*(?:PCR\s+)?(glass|plastic)\b(?!\s*(?:caps?|lids?|pumps?|labels?)\b)', text, re.I)
            if body and body.group(1).casefold() != material.group(1).casefold():
                refs.extend([{'path': '/product/' + key, 'quote': body.group()},
                             {'path': '/product/package_type', 'quote': package}])
            if key == 'features':
                # A nominal packaging feature is an own-product assertion,
                # unlike a comparison with another bottle in general prose.
                feature = re.search(r'(?:^|[,;•])\s*(?:[^,;•]{0,80}[-–—]\s*)?'
                                    r'((?:(?:post[- ]consumer|PCR|recycled|100\s*%)\s+)*'
                                    r'(glass|plastic)\s+bottles?)\b'
                                    r'(?!\s*(?:caps?|lids?|pumps?|labels?)\b)', text, re.I)
                if feature and feature.group(2).casefold() != material.group(1).casefold():
                    refs.extend([{'path': '/product/features', 'quote': feature.group(1)},
                                 {'path': '/product/package_type', 'quote': package}])
    return list({(r['path'],r['quote']):r for r in refs}.values())


def assortment_conflict_refs(context):
    """Block a corroborated pack list paired with a different complete outline.

    Mere omission from a partial description is not a conflict. Require counted
    contents, independent ingredient headings, and an equally extensive outline
    beginning with one of those contents, with at least two constituents absent.
    """
    p = context['product']
    if not re.search(r'\b(?:mixed|variety|assortment|selection)\b', str(p.get('name') or ''), re.I):
        return []
    other = str(p.get('other_information') or '')
    contents = re.search(r'\b(?:Box|Pack) Contents:\s*(.*?)(?=\b(?:Full Product Name|Storage|Safety Warning|Usage|Additional Information):|$)', other, re.I)
    if not contents:
        return []
    items = [part.strip() for part in contents.group(1).split(',')]
    labels = []
    for item in items:
        counted = re.fullmatch(r'\d+\s*[xX]\s+([\w &\'’+-]+)', item)
        if not counted:
            return []
        labels.append(counted.group(1).strip())
    if len(labels) < 3 or len({fold(label) for label in labels}) != len(labels):
        return []
    ingredients = str(p.get('ingredients') or '')
    if not all(re.search(r'\b' + re.escape(label) + r'\s*:', ingredients, re.I) for label in labels):
        return []
    description = str(p.get('detailed_description') or '')
    if (not any(re.match(re.escape(label) + r'\s*[-–—]', description, re.I) for label in labels)
            or re.search(r'\b(?:our other|serve with|serving suggestion|recipe idea)\b', description, re.I)):
        return []
    headings = re.findall(r'\b[A-Z][A-Za-z0-9\'’]*(?:\s+[A-Z][A-Za-z0-9\'’]*){0,9}\s+[-–—]\s+(?=[A-Z])', description)
    present = [label for label in labels if re.search(r'\b' + re.escape(label) + r'\b', description, re.I)]
    if len(headings) < len(labels) or len(present) < 2 or len(labels) - len(present) < 2:
        return []
    return [{'path': '/product/name', 'quote': str(p['name'])},
            {'path': '/product/other_information', 'quote': contents.group()},
            {'path': '/product/ingredients', 'quote': ingredients},
            {'path': '/product/detailed_description', 'quote': description}]


def cereal_blend_conflict_refs(context):
    """Compare an explicit own-flake blend with a declared cereal blend.

    Require two named grains in both accounts. This is an identity blocker,
    not an inference that a missing ingredient or either grain is animal-derived.
    """
    p = context['product']
    name = str(p.get('name') or '')
    ingredients = str(p.get('ingredients') or '')
    if not re.search(r'\b(?:flakes?|cereals?)\b', name, re.I):
        return []
    if not re.search(r'\bcereal\s+blend\W*\(', ingredients, re.I):
        return []
    grains = ('rice', 'buckwheat', 'wheat', 'rye', 'oats?', 'barley', 'millet', 'sorghum',
              'quinoa', '(?:maize|corn)')
    def grain_set(text):
        return {grain for grain in grains if re.search(r'\b' + grain + r'\b', text, re.I)}
    declared = grain_set(ingredients)
    if len(declared) < 2:
        return []
    for sentence in re.split(r'[.!?;\n]', str(p.get('detailed_description') or '')):
        scope = re.sub(r'\bsingle[- ]serve\b', 'single portion', sentence, flags=re.I)
        if re.search(r'\b(?:other|another|range|recipe|serve|serving|example|instead|without|free\s+from)\b', scope, re.I):
            continue
        match = re.search(r'\bflakes?\s+made\s+from\s+(?:a\s+)?([^.!?;]+?)\s+blend\b', sentence, re.I)
        if not match:
            continue
        described = grain_set(match.group(1))
        if len(described) >= 2 and described - declared:
            return [{'path': '/product/name', 'quote': name},
                    {'path': '/product/ingredients', 'quote': ingredients},
                    {'path': '/product/detailed_description', 'quote': sentence.strip()}]
    return []


def sweetening_composition_conflict_refs(context):
    """Compare an explicit own-product syrup addition with its recipe.

    This is a formulation-review blocker, never proof of animal content.
    Flavour/tasting notes and optional recipes cannot assert an added syrup.
    """
    product = context['product']
    ingredients = str(product.get('ingredients') or '')
    if not ingredients.strip():
        return []  # Missing composition alone cannot prove a contradiction.
    for key in ('detailed_description', 'further_description', 'specification'):
        for sentence in re.split(r'[.!?;\n]', str(product.get(key) or '')):
            text = fold(sentence)
            if re.search(r'\b(?:not|never|without|unsweetened|other|another|range|recipe|recipes|serve|serving|try|add|adding|can|could|may|might|example|instead|optional|optionally)\b', text):
                continue
            syrup = re.search(r'\bsweetened\s+(?:with|using)\s+(?:(?:pure|organic|natural)\s+)?(agave(?:\s+syrup)?|maple\s+syrup|date\s+syrup)\b', text)
            if not syrup:
                continue
            identity = syrup.group(1).split()[0]
            if not re.search(r'\b' + re.escape(identity) + r'\b', fold(ingredients)):
                return [{'path': '/product/ingredients', 'quote': ingredients},
                        {'path': '/product/' + key, 'quote': sentence.strip()}]
    return []


def declared_flavour_conflict_refs(context):
    """A single SKU flavour and an explicit different flavour attribute."""
    p = context['product']
    name = fold(p.get('name')); brand = fold(p.get('brand'))
    if brand and name.startswith(brand + ' '):
        name = name[len(brand):]
    flavour = r'apple|banana|blackcurrant|blueberry|cherry|coconut|cranberry|grapefruit|lemon|lime|mango|orange|peach|pear|peppermint|pineapple|raspberry|spearmint|strawberry|vanilla'
    own = set(re.findall(r'\b(?:' + flavour + r')\b', name))
    if len(own) != 1 or re.search(r'\b(?:mixed|variety|selection|assorted|recipe|book)\b', name):
        return []
    text = str(p.get('features') or '')
    for clause in re.split(r'[,;.!?\n]', text):
        declared = re.fullmatch(r'\s*(?:natural\s+)?(' + flavour + r')\s+flavou?r\s*', clause, re.I)
        if declared and declared.group(1).lower() not in own:
            return [{'path': '/product/name', 'quote': str(p['name'])},
                    {'path': '/product/features', 'quote': clause.strip()}]
    return []


def vinegar_composition_conflict_refs(context):
    """Compare named preserved-in brine vinegar with a different declared type."""
    p = context['product']; ingredients = str(p.get('ingredients') or '')
    kinds = r'red\s+wine|white\s+wine|spirit|malt|apple\s+cider|balsamic'
    declared = {fold(m.group(1)) for m in re.finditer(r'\b(' + kinds + r')\s+vinegar\b', ingredients, re.I)}
    if not declared:
        return []
    for key in ('features', 'detailed_description'):
        for sentence in re.split(r'[.!?;\n]', str(p.get(key) or '')):
            if re.search(r'\b(?:other|another|recipe|serve|serving|try|can|could|may|might|example|not|without)\b', sentence, re.I):
                continue
            match = re.search(r'\bpreserved\s+in\s+(?:(?:a|the|delicate)\s+){0,2}(' + kinds + r')\s+vinegar\s+brine\b', sentence, re.I)
            if match and fold(match.group(1)) not in declared:
                return [{'path': '/product/ingredients', 'quote': ingredients},
                        {'path': '/product/' + key, 'quote': sentence.strip()}]
    return []


def essential_oil_composition_conflict_refs(context):
    """Require two explicitly added oils absent from an ungrouped INCI list.

    Primary producer mappings: tisserand.com/products/grapefruit-essential-oil
    and tisserand.com/products/lavender-ethically-harvested-essential-oil.
    A parfum/aroma group can contain oils without listing their names.
    """
    p = context['product']; ingredients = str(p.get('ingredients') or '')
    grouping_text = re.split(r'\.\s*\*+\s*essential\s+oils\b', ingredients, maxsplit=1, flags=re.I)[0]
    if (not ingredients or not re.search(r'\b(?:wash|shampoo|conditioner|soap|cream|lotion|deodorant|bath)\b', str(p.get('name') or ''), re.I)
            or re.search(r'\b(?:parfum|fragrance|aroma|essential oils?|botanical extracts?)\b', grouping_text, re.I)):
        return []
    aliases = {'grapefruit': r'\b(?:grapefruit|citrus\s+(?:x\s+)?paradisi)\b',
               'lavender': r'\b(?:lavender|lavandula)\b'}
    for key in ('detailed_description', 'features', 'specification'):
        for sentence in re.split(r'[.!?;\n•]', str(p.get(key) or '')):
            if re.search(r'\b(?:other|another|recipe|serve|serving|try|can|could|may|might|example|not|without|notes?|scented like)\b', sentence, re.I):
                continue
            match = re.search(r'\b(?:essential\s+oils\s+of\s+(.+)|blend\s+of\s+(.+?)\s+essential\s+oils)\b', sentence, re.I)
            if not match:
                continue
            blend = match.group(1) or match.group(2)
            missing = [oil for oil, pattern in aliases.items()
                       if re.search(r'\b'+oil+r'\b', blend, re.I) and not re.search(pattern, ingredients, re.I)]
            if len(missing) >= 2:
                return [{'path': '/product/ingredients', 'quote': ingredients},
                        {'path': '/product/' + key, 'quote': sentence.strip()}]
    return []


def fermentation_account_conflict_refs(context):
    """A main oak-fermentation account versus a defining steel account.

    Barrel maturation following steel fermentation and declared mixed-vessel
    vinification are compatible. Compare fermentation, not vessel words alone.
    """
    p = context['product']; details = str(p.get('vinification_details') or '')
    other = str(p.get('other_information') or '')
    if (re.search(r'\boak\b', details, re.I)
            or re.search(r'\b(?:other|another|historical|previous|formerly|example)\b', other, re.I)):
        return []
    main_oak = re.search(r'\bfree\s+run\b[^.!?]*\b(?:filled|transferred)\s+to\s+(?:French\s+)?oak\s+for\s+(?:spontaneous[/ ]wild\s+|spontaneous\s+|wild\s+)?fermentation\b', other, re.I)
    minor_steel = re.search(r'\b(?:a\s+)?small\s+(?:juice\s+)?portion\b[^.!?]*\bfermentation\b[^.!?]*\bstainless\s+steel\b', other, re.I)
    defining_steel = re.search(r'\bfermentation\s+in\s+stainless\s+steel\s+defines?\s+(?:the\s+)?vinification\s+process\b', details, re.I)
    if main_oak and minor_steel and defining_steel:
        return [{'path': '/product/other_information', 'quote': other},
                {'path': '/product/vinification_details', 'quote': details}]
    return []


def formal_article_kind_conflict_refs(context):
    """A complete food identity cannot label a separately named garment/article."""
    p = context['product']; name = str(p.get('name') or '')
    article_name = fold(name); brand = fold(p.get('brand'))
    if brand and article_name.startswith(brand + ' '):
        article_name = article_name[len(brand):].strip()
    # Clothing on an explicitly chocolate figurine is a depicted feature,
    # not the sold article. Its formal chocolate identity remains consistent.
    if re.search(r'\b(?:milk|dark|white)\s+chocolate\s+(?:tedd(?:y|ies)|bunn(?:y|ies)|santa|figures?|bears?)\b', article_name):
        return []
    nonfood = r'\b(?:cardigans?|jumpers?|sweaters?|trousers?|leggings?|socks?|shirts?|t[- ]shirts?|dresses?|skirts?|shoes?|boots?|gloves?|scarves?|shampoo|conditioner|detergent|hand wash|body wash|toothpaste|skin cream|body lotion)\b'
    if not re.search(nonfood, article_name, re.I) or re.search(r'\b(?:gift|hamper|bundle)\b', article_name, re.I):
        return []
    text = str(p.get('other_information') or '')
    formal = re.search(r'\bfull product name:\s*(.*?)(?=\s+(?:storage|usage|additional information):|$)', text, re.I)
    if (formal and not re.search(nonfood, formal.group(1), re.I)
            and re.match(r'\s*(?:(?:organic|frozen|fresh|prepared|ready[- ]to[- ]eat)\s+)*(?:pizza|bread|biscuits?|cookies?|cake|soup|yogh?urt|milk|cheese|food supplement|ready meal|pasta|chocolate|cereal|tea|coffee)\b', formal.group(1), re.I)):
        return [{'path': '/product/name', 'quote': name},
                {'path': '/product/other_information', 'quote': formal.group()}]
    return []


def named_grape_composition_conflict_refs(context):
    """Bind an explicit made-from grape account to its named varietal SKU."""
    p = context['product']; name = fold(p.get('name'))
    if re.search(r'\b(?:style|inspired|vinegar|sauce|recipe|glass|gift|cocktail)\b', name):
        return []
    def varieties(text):
        value = fold(text)
        return {group for group in GRAPE_IDENTITIES
                if any(re.search(r'\b'+re.escape(alias)+r'\b', value) for alias in group)}
    own = varieties(name)
    if len(own) != 1:
        return []
    for key in ('detailed_description', 'vinification_details'):
        for sentence in re.split(r'[.!?;\n]', str(p.get(key) or '')):
            if re.search(r'\b(?:other|another|previous|history|historical|example|serve|serving|pair|pairing|recipe|dessert|cocktail|sauce|vinegar)\b', sentence, re.I):
                continue
            match = re.search(r'\b(?:made|produced)\s+(?:from|with)\s+(?:(?:a|the)\s+blend\s+of\s+)?(.+?)\s+grapes\b', sentence, re.I)
            stated = varieties(match.group(1)) if match else set()
            if stated and not stated & own:
                return [{'path': '/product/name', 'quote': str(p['name'])},
                        {'path': '/product/' + key, 'quote': sentence.strip()}]
    return []



def rice_bowl_pasta_account_conflict_refs(context):
    """Own cooked pasta contradicts a rice-bowl recipe that declares no pasta.

    This is an identity/composition block, not animal proof. Rice and pasta
    can coexist; the contradiction requires an explicit own-product account
    plus the supplied recipe, excluding serving ideas and other products.
    """
    p = context['product']; name = str(p.get('name') or '')
    ingredients = str(p.get('ingredients') or '')
    if not re.search(r'\brice\s+bowl\b', name, re.I):
        return []
    if not re.search(r'\brice\b', ingredients, re.I):
        return []
    pasta = r'\b(?:pasta|rigatini|rigatoni|penne|fusilli|macaroni|spaghetti|tagliatelle|vermicelli|noodles?)\b'
    if re.search(pasta, ingredients, re.I):
        return []
    for key in ('detailed_description', 'further_description'):
        for sentence in re.split(r'[.!?;\n]', str(p.get(key) or '')):
            if re.search(r'\b(?:other|another|previous|history|historical|recipe|recipes|serve|serving|try|optional|example|could|can|may|might|instead|without|free\s+from)\b', sentence, re.I):
                continue
            own = re.search(r"\bwe(?:['’]ve|\s+have)\s+(?:cooked|prepared|combined)\b", sentence, re.I)
            if own and re.search(pasta, sentence[own.end():], re.I):
                return [{'path': '/product/name', 'quote': name},
                        {'path': '/product/ingredients', 'quote': ingredients},
                        {'path': '/product/' + key, 'quote': sentence.strip()}]
    return []



def body_wash_set_conflict_refs(context):
    """Compare a complete own set outline with independent recipe headings.

    Both lists must have the same extent and at least three unique variants.
    Commas within a fragrance name stay part of that name. Ingredient headers
    corroborate the declared contents; incidental scent prose cannot qualify.
    """
    p = context['product']; name = str(p.get('name') or '')
    if not re.search(r'\b(?:gift|discovery)\s+set\b', name, re.I):
        return []
    other = str(p.get('other_information') or '')
    contents = re.search(r'\bBox Contents:\s*(.*?)(?=\b(?:Full Product Name|Storage|Safety Warning|Usage|Additional Information):|$)', other, re.I)
    if not contents:
        return []
    listing = contents.group(1).strip(); ingredients = str(p.get('ingredients') or '')
    starts = [0] + [m.end() for m in re.finditer(',', listing)]
    ends = [m.start() for m in re.finditer(',', listing)] + [len(listing)]
    labels = []
    for start in starts:
        for end in ends:
            label = listing[start:end].strip()
            if end <= start or not 3 <= len(label) <= 65 or '&' not in label:
                continue
            if re.search(r'(?<![A-Za-z])' + re.escape(label) + r'\s*:\s*(?:Aqua|Water)\b', ingredients, re.I):
                labels.append(label)
    labels = [label for label in labels if not any(label != longer and fold(label) in fold(longer) for longer in labels)]
    declared = {fold(label) for label in labels}
    if len(declared) < 3:
        return []
    description = str(p.get('detailed_description') or '')
    for sentence in re.split(r'[.!?;\n]', description):
        own = re.search(r'\b(?:this|our)\s+(?:(?:curated|discovery|gift)\s+)*set\s+contains\b', sentence, re.I)
        if not own or re.search(r'\b(?:other|another|previous|historical|example|may|might|could)\b', sentence, re.I):
            continue
        labels = re.findall(r"\b\d+(?:[.,]\d+)?\s*ml\s+([A-Za-z][A-Za-z &'’,+-]{0,60}?)\s+body\s*wash\b", sentence[own.end():], re.I)
        described = {fold(label.strip(' ,')) for label in labels}
        if len(labels) == len(described) == len(declared) and len(described & declared) >= 2 and described != declared:
            return [{'path': '/product/name', 'quote': name},
                    {'path': '/product/other_information', 'quote': contents.group()},
                    {'path': '/product/ingredients', 'quote': ingredients},
                    {'path': '/product/detailed_description', 'quote': sentence.strip()}]
    return []



def named_grain_recipe_conflict_refs(context):
    """A named grain snack cannot be corroborated only as cross-contact.

    Require an actual declared recipe, an explicit precautionary declaration,
    and the named grain absent from that recipe. No animal-use inference.
    """
    p = context['product']; name = str(p.get('name') or '')
    match = re.search(r'\b(oat|wheat|rice|rye|barley)\s+crunch\b', name, re.I)
    ingredients = str(p.get('ingredients') or '')
    if not match or not ingredients.strip():
        return []
    grain = match.group(1).casefold(); pattern = r'\b' + grain + (r's?\b' if grain == 'oat' else r'\b')
    if re.search(pattern, ingredients, re.I):
        return []
    allergens = str(p.get('allergens') or ''); normalized = fold(allergens)
    if re.search(r'(?<!may )\bcontains?\b[^.;]*' + pattern, normalized):
        return []  # Intentional grain declaration corroborates the identity.
    if not re.search(r'\bmay contain\b[^.;]*' + pattern, normalized):
        return []
    return [{'path': '/product/name', 'quote': name},
            {'path': '/product/ingredients', 'quote': ingredients},
            {'path': '/product/allergens', 'quote': allergens}]


def gel_drink_identity_conflict_refs(context):
    """A carbohydrate gel conflicts with an own zero-calorie drink feature."""
    p = context['product']; name = str(p.get('name') or '')
    if not re.search(r'\benergy gel\b', name, re.I) or re.search(r'\b(?:gift|bundle|mixed|variety|set)\b', name, re.I):
        return []
    formal = re.search(r'\bFull Product Name:\s*([^:]+?)(?=\s+(?:Storage|Origin|Distributor|Additional Information):|$)', str(p.get('other_information') or ''), re.I)
    spec = str(p.get('specification') or '')
    if not formal or not re.search(r'\bgel\b', formal.group(1), re.I):
        return []
    if not re.search(r'\b[1-9]\d*(?:[.,]\d+)?\s*g\s+(?:of\s+)?carbohydrate energy per gel\b', spec, re.I):
        return []
    features = str(p.get('features') or '')
    if (re.match(r'\s*(?:(?:our|this|the|a|light|refreshing|and|natural|fruit)\b[\s&,-]*){0,7}electrolyte drink\b', features, re.I)
            and re.search(r'\b(?:zero|0) calories\b', features, re.I)):
        return [{'path': '/product/name', 'quote': name},
                {'path': '/product/other_information', 'quote': formal.group()},
                {'path': '/product/specification', 'quote': spec},
                {'path': '/product/features', 'quote': features}]
    return []


def formal_vintage_conflict_refs(context):
    """A formal wine identity's leading vintage must match the current field.

    This is a stated vintage conflict, not a bottling/harvest convention check.
    A distributor's foundation year or prior-vintage history is not this label.
    """
    p = context['product']; vintage = str(p.get('current_vintage') or '')
    name = str(p.get('name') or '')
    if not re.fullmatch(r'(?:19|20)\d{2}', vintage.strip()):
        return []
    if re.search(r'\b(?:glass|gift|shirt|print|recipe|sauce|vinegar|candle|fragrance|colou?r)\b', name, re.I):
        return []
    text = str(p.get('other_information') or '')
    formal = re.search(r'\bFull Product Name:\s*(.*?)(?=\s+(?:Storage|Origin|Distributor|Manufacturer|Additional Information):|$)', text, re.I)
    if not formal:
        return []
    declared = formal.group(1); year = re.match(r'((?:19|20)\d{2})\b', declared)
    wine = bool(re.search(r'\b(?:wine|champagne|prosecco|cava)\b', declared, re.I)
        or any(re.search(r'\b' + re.escape(alias) + r'\b', declared, re.I) for group in GRAPE_IDENTITIES for alias in group))
    if (year and wine and year.group(1) != vintage.strip()
            and len(set(re.findall(r'\b(?:19|20)\d{2}\b', declared))) == 1):
        return [{'path': '/product/name', 'quote': name},
                {'path': '/product/current_vintage', 'quote': vintage},
                {'path': '/product/other_information', 'quote': formal.group()}]
    return []


def later_awarded_vintage_conflict_refs(context):
    """An own wine's explicit later vintage designation needs review.

    A competition year is not a vintage. Earlier-vintage awards and explicitly
    historical or other-product copy do not identify the sold vintage.
    """
    p = context['product']; vintage = str(p.get('current_vintage') or '').strip()
    if not re.fullmatch(r'(?:19|20)\d{2}', vintage):
        return []
    formal = str(p.get('other_information') or '')
    if not re.search(r'\b(?:wine|champagne|prosecco|cava)\b', formal, re.I):
        return []
    owner = fold(p.get('producer') or p.get('brand'))
    owner = re.sub(r'^(?:chateau|domaine)\s+', '', owner)
    if len(owner.split()) < 2:
        return []
    for sentence in re.split(r'[.!?;\n]', formal):
        if re.search(r'\b(?:other|another|previous|earlier|historical|history|example|range|comparison)\b', sentence, re.I):
            continue
        designation = re.search(r'\b((?:19|20)\d{2})\s+(?:is\s+proud\s+to\s+have\s+won|has\s+won|was\s+awarded)\b', sentence, re.I)
        if (designation and int(designation.group(1)) > int(vintage)
                and re.search(r'\b' + re.escape(owner) + r'\b', fold(sentence[:designation.start()]))
                and re.search(r'\b(?:rose|red|white|wine|champagne|prosecco|cava)\b', fold(sentence[:designation.start()]))):
            return [{'path': '/product/current_vintage', 'quote': vintage},
                    {'path': '/product/other_information', 'quote': sentence.strip()}]
    return []


def sunscreen_strength_conflict_refs(context):
    """Own SPF feature labels must match the single named formulation.

    A range assurance establishes suitability only after identity is bound;
    it cannot choose between incompatible own-product strength declarations.
    Other-product comparisons, usage advice and mixed sets are not labels.
    """
    p = context['product']; name = str(p.get('name') or '')
    strengths = set(re.findall(r'\bSPF\s*(\d{1,3})\s*\+?', name, re.I))
    if len(strengths) != 1 or re.search(r'\b(?:set|bundle|mixed|variety|selection|kit)\b', name, re.I):
        return []
    text = str(p.get('features') or '')
    for clause in re.split(r'[,;.!?\n•]', text):
        match = re.match(r'\s*SPF\s*(\d{1,3})\s*\+?\s*(?:[-–—:]\s*|(?=sun))', clause, re.I)
        if (match and match.group(1) not in strengths
                and re.search(r'\bsun\s*(?:screen|block)|\b(?:UVA|UVB)\b', clause, re.I)
                and not re.search(r'\b(?:other|another|range|compare|comparison|instead|use|apply|recommend|choose|not|without)\b', clause, re.I)):
            return [{'path': '/product/name', 'quote': name},
                    {'path': '/product/features', 'quote': clause.strip()}]
    return []


def toothpaste_flavour_attribute_conflict_refs(context):
    """An explicit own-flavour bullet differs from a named toothpaste SKU.

    Compare a flavour attribute, not individual aroma ingredients or an
    incidental description of other flavours. Blends and mixed sets survive.
    """
    p = context['product']; name = str(p.get('name') or '')
    if not re.search(r'\btoothpaste\b', name, re.I):
        return []
    flavours = r'bubblegum|banana|blackcurrant|blueberry|cherry|coconut|grape|lemon|lime|mango|mint|orange|peach|pear|peppermint|pineapple|raspberry|spearmint|strawberry|vanilla'
    own = set(re.findall(r'\b(?:' + flavours + r')\b', fold(name)))
    if len(own) != 1 or re.search(r'\b(?:set|mixed|variety|selection|assorted|blend)\b', name, re.I):
        return []
    text = str(p.get('detailed_description') or '')
    # Literal bullet boundaries distinguish a product attribute from prose
    # about the range. The attribute must be complete and unqualified.
    for clause in re.split(r'[•\n]', text)[1:]:
        match = re.fullmatch(r'\s*(?:(?:organic|natural)\s+)?(' + flavours + r')\s*[- ]\s*flavou?red\s+toothpaste\s*[.;]?\s*', clause, re.I)
        if match and match.group(1).casefold() not in own:
            return [{'path': '/product/name', 'quote': name},
                    {'path': '/product/detailed_description', 'quote': clause.strip()}]
    return []


def protein_blend_composition_conflict_refs(context):
    """An explicitly made-from protein blend names a missing plant source.

    Compare ingredient sources rather than assuming flour cannot provide
    protein. Generic protein groups, other recipes and incomplete data do not
    establish that the named source is absent.
    """
    p = context['product']; ingredients = str(p.get('ingredients') or '')
    if not ingredients or re.search(r'\b(?:plant|vegetable|legume)\s+proteins?\b', ingredients, re.I):
        return []
    sources = r'pea|rice|soy|soya|wheat|hemp|pumpkin|lentil|chickpea'
    for key in ('detailed_description', 'further_description'):
        for sentence in re.split(r'[.!?;\n]', str(p.get(key) or '')):
            if re.search(r'\b(?:other|another|recipe|serve|serving|try|could|may|example|previous|formerly)\b', sentence, re.I):
                continue
            blend = re.search(r'\b(?:crafted|made|created|formed)\s+from\s+(?:a\s+)?blend\s+of\s+(.+)', sentence, re.I)
            if not blend:
                continue
            named = set(re.findall(r'\b(' + sources + r')\s+proteins?\b', blend.group(1), re.I))
            for source in named:
                alias = r'soy(?:a)?' if source.lower() in {'soy', 'soya'} else re.escape(source)
                if not re.search(r'\b' + alias + r's?\b', ingredients, re.I):
                    return [{'path': '/product/ingredients', 'quote': ingredients},
                            {'path': '/product/' + key, 'quote': sentence.strip()}]
    return []


def wine_ageing_account_conflict_refs(context):
    """Two complete own-wine pre-release ageing accounts disagree.

    Require explicit barrel and bottle durations in each account. Historical
    examples, minimum/ranged periods and partial/additional stages do not
    establish incompatible complete production accounts.
    """
    p = context['product']
    if not p.get('vinification_details'):
        return []
    accounts = []
    for key in ('other_information', 'vinification_details'):
        text = str(p.get(key) or '')
        if (not re.search(r'\b(?:prior to release|before (?:being )?released)\b', text, re.I)
                or re.search(r'\b(?:other|another|previous|formerly|historical|example|minimum|at least|up to|approximately|about)\b', text, re.I)
                or re.search(r'\d+\s*[-–]\s*\d+\s+months?|\bbetween\s+\d+\s+and\s+\d+\s+months?', text, re.I)):
            return []
        bottles = set(re.findall(r'\b(\d{1,3})\s+months?\s+in\s+(?:the\s+)?bottles?\b', text, re.I))
        barrels = set(re.findall(r'\b(\d{1,3})\s+months?\s+in\s+(?:[a-z-]+\s+){0,5}barrels?\b', text, re.I))
        barrels.update(re.findall(r'\bbarrels?\b[^.!?;\n]{0,150}?\bfor\s+(\d{1,3})\s+months?\b', text, re.I))
        if len(bottles) != 1 or len(barrels) != 1:
            return []
        accounts.append((key, text, next(iter(barrels)), next(iter(bottles))))
    if accounts[0][2:] != accounts[1][2:]:
        return [{'path': '/product/' + key, 'quote': text}
                for key, text, _, _ in accounts]
    return []


def labelled_container_conflict_refs(context):
    """An own lot/date locator identifies a conflicting primary container.

    Explicit gift/outer-container accounts can contain both a bottle and tin;
    a bare material mention or an incidental can/modal word is not a locator.
    """
    p = context['product']; package = fold(p.get('package_type'))
    if re.search(r'\b(?:gift|presentation|outer|sleeve|bundle|selection|set|kit)\b', str(p.get('name') or ''), re.I):
        return []
    features = str(p.get('features') or '')
    if package in {'bottle', 'glass bottle', 'plastic bottle'}:
        for clause in re.split(r'[,;.!?\n•]', features):
            # A standalone nominal package feature, not the modal verb can.
            match = re.fullmatch(r'\s*[-–—]?\s*(can\s+of\s+[A-Za-z][A-Za-z0-9 &-]{1,45})\s*', clause, re.I)
            if match and not re.search(r'\b(?:other|another|example|range|refill|gift)\b', match.group(), re.I):
                return [{'path': '/product/package_type', 'quote': str(p['package_type'])},
                        {'path': '/product/features', 'quote': match.group(1)}]
    other = str(p.get('other_information') or '')
    carton = re.search(r'\b(?:for\s+)?best\s+before\s*(?::\s*)?(?:see|date\s*:?\s*see)\s+(?:the\s+)?(?:top|base|bottom)\s+of\s+(?:the\s+)?carton\b', other, re.I)
    if (package == 'pot' and carton and re.search(r'\bTetra\s+Brik\b', other, re.I)
            and not re.search(r'\b(?:outer|inner|gift|presentation|sleeve)\b', other, re.I)):
        return [{'path': '/product/package_type', 'quote': str(p['package_type'])},
                {'path': '/product/other_information', 'quote': carton.group()},
                {'path': '/product/other_information', 'quote': re.search(r'\bTetra\s+Brik\b', other, re.I).group()}]
    if package == 'aerosol':
        usage = str(p.get('preparation_and_usage') or '')
        # Actual dispensing instructions for this liquid article, not a
        # reference to another bottle, a refill or squeezing a spray trigger.
        if (not re.search(r'\b(?:refill|other|another|example|trigger|spray)\b', usage, re.I)
                and re.search(r'\btip\s+up\s+the\s+bottle\b', usage, re.I)
                and re.search(r'\bsqueeze\s+the\s+bottle\b', usage, re.I)):
            return [{'path': '/product/package_type', 'quote': str(p['package_type'])},
                    {'path': '/product/preparation_and_usage', 'quote': usage}]
    if package not in {'glass bottle', 'glass jar', 'plastic bottle', 'plastic jar'}:
        return []
    if re.search(r'\b(?:gift|presentation|outer|sleeve|bundle|selection|set)\b', str(p.get('name') or ''), re.I):
        return []
    text = str(p.get('other_information') or '')
    match = re.search(r'\b(?:lot\s*&\s*best before|best before|batch|lot)\s*:\s*see\s+(?:the\s+)?(?:bottom|base)\s+of\s+(?:the\s+)?(?:tin|can)\b', text, re.I)
    if match and not re.search(r'\b(?:outer|gift|presentation|sleeve|inner)\b', text, re.I):
        return [{'path': '/product/package_type', 'quote': str(p['package_type'])},
                {'path': '/product/other_information', 'quote': match.group()}]
    return []


def declared_preservative_claim_conflict_refs(context):
    """An unqualified own feature conflicts with a declared preservative role.

    This is composition review, not animal proof or a legal labelling verdict.
    Intrinsic sulphites, allergen warnings and another recipe are not a role.
    """
    p = context['product']; ingredients = str(p.get('ingredients') or '')
    role = re.search(r'\bpreservatives?\s*\([^()]+\)', ingredients, re.I)
    if not role or re.search(r'\b(?:may\s+contain|free\s+from|without|allergy\s+advice)\b', ingredients[:role.start()], re.I):
        return []
    features = str(p.get('features') or '')
    for clause in re.split(r'[,;.!?\n•]', features):
        claim = re.match(r'\s*[-–—]?\s*(no\s+(?:added\s+)?preservatives?\b)', clause, re.I)
        if claim and not re.search(r'\b(?:other|another|recipe|range|example|optional)\b', clause, re.I):
            return [{'path': '/product/features', 'quote': claim.group(1)},
                    {'path': '/product/ingredients', 'quote': role.group()}]
    return []


def declared_soup_blend_conflict_refs(context):
    """A soup's explicit fruit/vegetable blend contradicts its supplied recipe.

    Require at least three plain named foods and corroboration of two in the
    recipe. Only a missing named fruit qualifies here; generic fruit/flavour
    groups leave absence unestablished. Serving ideas and other recipes do not.
    """
    p = context['product']; name = str(p.get('name') or '')
    ingredients = str(p.get('ingredients') or '')
    if not re.search(r'\bsoup\b', name, re.I) or not ingredients.strip():
        return []
    if re.search(r'\b(?:fruit|fruits|fruity|flavou?ring|flavou?rings)\b', ingredients, re.I):
        return []
    foods = {'butternut': ('butternut', 'butternut squash'), 'carrot': ('carrot', 'carrots'),
             'turmeric': ('turmeric',), 'tomato': ('tomato', 'tomatoes'),
             'mango': ('mango', 'mangoes', 'mangos'), 'apple': ('apple', 'apples'),
             'pear': ('pear', 'pears'), 'banana': ('banana', 'bananas'),
             'pineapple': ('pineapple', 'pineapples'), 'peach': ('peach', 'peaches'),
             'apricot': ('apricot', 'apricots'), 'orange': ('orange', 'oranges'),
             'lemon': ('lemon', 'lemons'), 'lime': ('lime', 'limes')}
    fruits = set(foods) - {'butternut', 'carrot', 'turmeric', 'tomato'}
    aliases = {alias: food for food, values in foods.items() for alias in values}
    for sentence in re.split(r'[.!?;\n]', str(p.get('detailed_description') or '')):
        if re.search(r'\b(?:other|another|previous|historical|recipe|serve|serving|try|example|optional|tasting|notes)\b', sentence, re.I):
            continue
        blend = re.search(r'\bblend\s+of\s+([A-Za-z ,&-]+?)\s+(?:and|&)\s+([A-Za-z -]+?)(?=,|$)', sentence, re.I)
        if not blend:
            continue
        terms = [fold(term) for term in re.split(r',|\band\b|&', blend.group(1) + ',' + blend.group(2))]
        if len(terms) < 3 or any(term not in aliases for term in terms):
            continue
        named = {aliases[term] for term in terms}
        present = {food for food in named if any(re.search(r'\b' + re.escape(alias) + r'\b', ingredients, re.I) for alias in foods[food])}
        if len(present) >= 2 and (named - present) & fruits:
            return [{'path': '/product/ingredients', 'quote': ingredients},
                    {'path': '/product/detailed_description', 'quote': blend.group()}]
    return []


def declared_recipe_components_conflict_refs(context):
    """Compare explicit own-recipe components, never suggested accompaniments.

    A closed berry ingredient list and independently corroborated plant-food
    profile can identify a substituted berry. An initial composition clause
    can identify a missing named vegetable. Generic unexpanded food groups,
    flavours, partial titles and cereal varieties do not prove exclusion.
    """
    p = context['product']; ingredients = str(p.get('ingredients') or '')
    if not ingredients.strip():
        return []
    berries = {'blackcurrant': r'blackcurrants?', 'redcurrant': r'redcurrants?',
               'cranberry': r'cranberr(?:y|ies)', 'cherry': r'cherr(?:y|ies)',
               'strawberry': r'strawberr(?:y|ies)', 'raspberry': r'raspberr(?:y|ies)',
               'blueberry': r'blueberr(?:y|ies)', 'blackberry': r'blackberr(?:y|ies)'}
    def named(text, aliases):
        return {key for key, pattern in aliases.items() if re.search(r'\b(?:' + pattern + r')\b', text, re.I)}
    description = str(p.get('detailed_description') or '')
    features = str(p.get('features') or '')
    declared = re.search(r'\b(?:berries|fruit mix)\s*(?:\([\d.,%\s]+\)\s*)?\(([^()]*)\)', ingredients, re.I)
    if declared and re.search(r'\b\d+\s+plant[- ]based\s+foods\b', description, re.I):
        recipe = named(declared.group(1), berries)
        # Two own-product fields must corroborate the substituted component.
        def own_named(text):
            return set().union(*(named(sentence, berries) for sentence in re.split(r'[.!?;\n]', text)
                if not re.search(r'\b(?:serve|serving|try|pair|suggestion|other|another|example|tasting|notes|flavouring)\b', sentence, re.I)))
        described = own_named(description) & own_named(features)
        if len(recipe) >= 3 and len(described) >= 3 and len(recipe & described) >= 2 and described - recipe:
            return [{'path':'/product/ingredients','quote':ingredients},
                    {'path':'/product/detailed_description','quote':description},
                    {'path':'/product/features','quote':features}]
    vegetables = {'tomato': r'tomato(?:es)?', 'pumpkin': r'pumpkins?',
                  'carrot': r'carrots?', 'onion': r'onions?', 'spinach': r'spinach',
                  'beetroot': r'beetroots?', 'broccoli': r'broccoli',
                  'courgette': r'courgettes?', 'aubergine': r'aubergines?'}
    # Unexpanded collective declarations cannot establish a named omission.
    if re.search(r'\bvegetables?\b', ingredients, re.I) and not re.search(
            r'\bvegetables?\*?\s*(?:\([\d.,%\s]+\)\s*)?\(', ingredients, re.I):
        return []
    for sentence in re.split(r'[.!?;\n]', description):
        clause = sentence.strip().split(',', 1)[0]
        composition = re.fullmatch(r'(?:Combined|Blended|Mixed|Made)\s+with\s+([A-Za-z -]+?)\s+(?:and|&)\s+([A-Za-z -]+?)(?:\s+and\s+(?:lightly|gently)\s+(?:spiced|seasoned))?', clause, re.I)
        if not composition:
            continue
        terms = [fold(term) for term in composition.groups()]
        expected = set()
        for term in terms:
            matches = {key for key, pattern in vegetables.items() if re.fullmatch(pattern, term, re.I)}
            if len(matches) != 1:
                break
            expected.update(matches)
        else:
            present = named(ingredients, vegetables)
            if len(expected) == 2 and expected & present and expected - present:
                return [{'path':'/product/ingredients','quote':ingredients},
                        {'path':'/product/detailed_description','quote':clause}]
    return []


def declared_plant_food_count_conflict_refs(context):
    """Two own muesli recipe diversity declarations disagree.

    Compare explicit product accounts, not weekly diet targets, servings,
    a manufacturer's broader range or a different counting unit.
    """
    p = context['product']; name = str(p.get('name') or '')
    if not re.search(r'\bmuesli\b', name, re.I):
        return []
    description = str(p.get('detailed_description') or '')
    stated = re.search(r'\bmade\s+with\s+(\d{1,2})\s+of\s+your\s+\d{1,2}\s+weekly\s+diverse\s+plant[- ]based\s+foods\b', description, re.I)
    marketing = str(p.get('brand_marketing') or '')
    declared = re.search(r'\bpacked\s+with\s+(\d{1,2})\s+[^.!?;\n]{1,160}?\bour\s+(?:prebiotic\s+)?muesli\b', marketing, re.I)
    if (stated and declared and stated.group(1) != declared.group(1)
            and not re.search(r'\b(?:other|another|range|example|previous|different)\b', declared.group(), re.I)
            and re.search(r'\bfruits?\b', declared.group(), re.I)
            and re.search(r'\b(?:vegetables?|wholegrains?|legumes?|nuts?|seeds?)\b', declared.group(), re.I)):
        return [{'path': '/product/detailed_description', 'quote': stated.group()},
                {'path': '/product/brand_marketing', 'quote': declared.group()}]
    return []


def explicit_recipe_count_conflict_refs(context):
    """A complete simple percentage account contradicts 'just N ingredients'.

    This is a declared count conflict, not proof of an omitted animal input.
    Compound/grouped ingredients, bare unquantified lists, ranges, serving
    recipes and figurative additions cannot establish this comparison.
    """
    diversity_refs = declared_plant_food_count_conflict_refs(context)
    if diversity_refs:
        return diversity_refs
    p = context['product']; ingredients = str(p.get('ingredients') or '')
    parts = ingredients.split(',')
    entries = [re.fullmatch(r'\s*[^,;()\[\]]+\s*\((\d{1,3}(?:\.\d+)?)%\)\s*', part) for part in parts]
    if not entries or not all(entries):
        return []
    quantities = [Decimal(entry.group(1)) for entry in entries]
    if not all(value > 0 for value in quantities) or sum(quantities) != Decimal('100'):
        return []
    numbers = {'one':1,'two':2,'three':3,'four':4,'five':5,'six':6,'seven':7,'eight':8,'nine':9,'ten':10}
    for sentence in re.split(r'[.!?;\n]', str(p.get('detailed_description') or '')):
        if re.search(r'\b(?:other|another|recipe|serve|serving|try|example|you|love|water|removed|evaporat\w*)\b', sentence, re.I):
            continue
        match = re.search(r'\b(?:made|crafted|created)\s+(?:from|with)\s+just\s+(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten)\s+ingredients\b', sentence, re.I)
        if match:
            token = match.group(1).lower(); count = int(token) if token.isdigit() else numbers[token]
            if count != len(parts):
                return [{'path': '/product/ingredients', 'quote': ingredients},
                        {'path': '/product/detailed_description', 'quote': sentence.strip()}]
    return []


def declared_beer_style_conflict_refs(context):
    """Two formal APA identities conflict with an own named IPA assertion.

    IPA is a kind of pale ale; generic pale-ale wording is not a conflict.
    Require the specific American Pale Ale designation, a matching formal
    product name and the same named beer as the subject of an IPA statement.
    """
    p = context['product']; name = str(p.get('name') or '')
    if (not re.search(r'\b(?:American\s+Pale\s+Ale|APA)\s*$', name, re.I)
            or re.search(r'\b(?:mixed|variety|selection|assortment|recipe|book)\b', name, re.I)):
        return []
    other = str(p.get('other_information') or '')
    formal = re.search(r'\bFull Product Name:\s*(.*?)(?=\s+(?:Storage|Origin|Distributor|Manufacturer|Additional Information):|$)', other, re.I)
    if not formal:
        return []
    declared = re.fullmatch(r'(.+?)\s+American\s+Pale\s+Ale\.?', formal.group(1).strip(), re.I)
    if not declared:
        return []
    subject = fold(declared.group(1))
    if len(subject) < 4 or not re.search(r'\b'+re.escape(subject)+r'\b', fold(name)):
        return []
    description = str(p.get('detailed_description') or '')
    for sentence in re.split(r'[.!?;\n]', description):
        alias = re.search(r'\b(?:previously called|formerly known as|renamed to|renamed as)\b', sentence, re.I)
        if (alias and re.search(r'\b'+re.escape(subject)+r'\b', fold(sentence))
                and re.search(r'\bAmerican\s+Pale\s+Ale\b', sentence, re.I)
                and re.search(r'\bIPA\b', sentence, re.I)
                and not re.search(r'\b(?:not|never)\b', sentence[:alias.start()], re.I)):
            return []  # A supplied explicit alias is for the normal interpreters.
    for sentence in re.split(r'[.!?;\n]', description):
        normalized = fold(sentence)
        match = re.search(r'\b'+re.escape(subject)+r'\s+(?:ipa|india pale ale)(?:\s+beer)?\s+(?:is|has|offers|delivers)\b', normalized)
        if match and not re.search(r'\b(?:other|another|previous|formerly|example|compare|comparison|serve|serving|recipe|unlike)\b', normalized[:match.start()]):
            return [{'path':'/product/name','quote':name},
                    {'path':'/product/other_information','quote':formal.group()},
                    {'path':'/product/detailed_description','quote':sentence.strip()}]
    return []


def conflict_refs(context):
    p = context['product']; name = str(p.get('name') or ''); refs = producer_heading_refs(context) + assortment_conflict_refs(context) + cereal_blend_conflict_refs(context) + sweetening_composition_conflict_refs(context)
    refs += declared_flavour_conflict_refs(context) + vinegar_composition_conflict_refs(context) + essential_oil_composition_conflict_refs(context) + fermentation_account_conflict_refs(context)
    refs += formal_article_kind_conflict_refs(context) + named_grape_composition_conflict_refs(context) + rice_bowl_pasta_account_conflict_refs(context) + body_wash_set_conflict_refs(context) + named_grain_recipe_conflict_refs(context) + gel_drink_identity_conflict_refs(context) + formal_vintage_conflict_refs(context)
    refs += sunscreen_strength_conflict_refs(context) + toothpaste_flavour_attribute_conflict_refs(context) + declared_beer_style_conflict_refs(context)
    refs += later_awarded_vintage_conflict_refs(context)
    refs += protein_blend_composition_conflict_refs(context) + wine_ageing_account_conflict_refs(context)

    def add(key, quote):
        refs.extend([{'path': '/product/name', 'quote': name},
                     {'path': '/product/' + key, 'quote': quote}])

    # Compare the earliest calendar year allowed by the vintage convention.
    # Australia assigns September-December harvests the following year's
    # vintage; do not invent an actual harvest date from this convention.
    # Missing origin cannot establish that the exception is inapplicable.
    # https://www.wineaustralia.com/labelling/further-information/the-blending-rules
    vintage = str(p.get('current_vintage') or '')
    if re.fullmatch(r'(?:19|20)\d{2}', vintage.strip()):
        origin = fold(p.get('country_of_origin'))
        rollover_possible = not origin or origin == 'au' or bool(re.search(r'\baustrali(?:a|an)\b', origin))
        earliest_year = int(vintage) - int(rollover_possible)
        text = str(p.get('vinification_details') or '')
        # An explicit own-harvest window binds the vintage, unlike vineyard
        # history or a later bottling/ageing date. Keep the Australian late-
        # year exception; a known March/April harvest cannot use it.
        for harvest in re.finditer(r'\bharvest\s+(?:period|dates?|year)\s*[:=-]\s*[^.!?;\n]+', text, re.I):
            scope = text[max(0, harvest.start()-80):harvest.end()]
            if re.search(r'\b(?:previous|earlier|other|another|historical|history|formerly|example|typical|average|blend|component)\b', scope, re.I):
                continue
            years = {int(year) for year in re.findall(r'\b(?:19|20)\d{2}\b', harvest.group())}
            months = set(re.findall(r'\b(?:january|february|march|april|may|june|july|august|september|october|november|december)\b', harvest.group(), re.I))
            late_months = {'september', 'october', 'november', 'december'}
            harvest_earliest = earliest_year if not months or any(month.lower() in late_months for month in months) else int(vintage)
            if years and all(year < harvest_earliest or year > int(vintage) for year in years):
                refs.extend([{'path': '/product/current_vintage', 'quote': vintage},
                             {'path': '/product/vinification_details', 'quote': harvest.group()}])
        for sentence in re.split(r'[.!?]', text):
            bottled = re.search(r'\bbottl(?:ed|ing)\s+(?:in|at)\s+'
                                r'(?:(?:the\s+)?(?:end|start|beginning)\s+of\s+)?'
                                r'((?:19|20)\d{2})\b', sentence, re.I)
            if (bottled and int(bottled.group(1)) < earliest_year
                    and not re.search(r'\b(?:other|previous|earlier|formerly|history|historical|example)\b', sentence, re.I)):
                refs.extend([{'path': '/product/current_vintage', 'quote': vintage},
                             {'path': '/product/vinification_details', 'quote': sentence.strip()}])

    # Compare named wearable variants, not a textile's animal origin. Satin
    # may be synthetic, and a silicone adhesive/backing can accompany fabric.
    # An explicitly composite description therefore resolves this comparison.
    wearable = re.search(r'\b(satin|silicone)\s+(nipple covers?|breast covers?|breast petals?)\b', name, re.I)
    brand = str(p.get('brand') or '')
    if wearable and brand:
        text = str(p.get('detailed_description') or '')
        first = re.split(r'[.!?]', text, maxsplit=1)[0]
        named = re.search(re.escape(brand) + r'\s+(?:Nude\s+)?(Satin|Silicone)\s+'
                          + re.escape(wearable.group(2)) + r'\b', first, re.I)
        if (named and named.group(1).casefold() != wearable.group(1).casefold()
                and not re.search(r'\b' + re.escape(wearable.group(1)) + r'\b', text, re.I)
                and not re.search(r'\b(?:other|another|example|such as|previous|formerly)\b',
                                  first[:named.start()] + first[named.end():], re.I)):
            add('detailed_description', first)

    # An unqualified own-wine vinification account naming only another grape
    # leaves a named varietal's identity unresolved. Do not infer a conflict
    # from aliases, declared blends, tasting/serving notes or producer history.
    varieties = [aliases for aliases in GRAPE_IDENTITIES
                 if any(re.search(r'\b' + re.escape(alias) + r'\b', fold(name)) for alias in aliases)]
    identity_refs = []
    if not varieties and p.get('vinification_details'):
        # A broad wine name can have a clear own-product cultivar assertion
        # in its description. An incidental grape, recipe or range mention
        # is not that assertion.
        for key in ('detailed_description', 'further_description'):
            for sentence in re.split(r'[.!?]', str(p.get(key) or '')):
                text = fold(sentence)
                if re.search(r'\b(?:other|another|also|range|portfolio|history|historical|example|serve|serving|pair|pairing|recipe|blend|blended)\b', text):
                    continue
                for aliases in GRAPE_IDENTITIES:
                    if any(re.search(r'\b(?:this|our)\s+' + re.escape(alias) + r'\b', text) for alias in aliases):
                        if aliases not in varieties:
                            varieties.append(aliases)
                        identity_refs.append({'path': '/product/' + key, 'quote': sentence.strip()})
    if (len(varieties) == 1
            and not re.search(r'\b(?:vinegar|sauce|juice|marinade|cocktail|recipe|book)\b', name, re.I)):
        for key in ('vinification_details', 'detailed_description', 'further_description', 'specification'):
            original = str(p.get(key) or '')
            text = fold(original)
            if any(re.search(r'\b' + re.escape(alias) + r'\b', text) for alias in varieties[0]):
                continue  # A declared blend or comparison can name both.
            for sentence in re.split(r'[.!?]', original):
                text = fold(sentence)
                if re.search(r'\b(?:other|another|also|range|portfolio|previous|formerly|history|historical|example|blend|blended|mixture|serve|serving|pair|pairing|recipe)\b', text):
                    continue
                for aliases in GRAPE_IDENTITIES:
                    if aliases == varieties[0]:
                        continue
                    cultivar = r'(?:' + '|'.join(re.escape(alias) for alias in aliases) + r')'
                    # Explicit production subject or a complete single-grape
                    # composition, rather than a flavour or incidental noun.
                    declared = (re.search(r'\b' + cultivar + r'\s+grapes\s+(?:are|were|have|undergo)\b', text)
                                or key == 'vinification_details' and re.search(r'\b' + cultivar + r'\s+juice\b', text)
                                or re.search(r'\b(?:from|with|of)\s+100\s+' + cultivar + r'\b', text)
                                and re.search(r'100\s*%', sentence))
                    if declared:
                        refs.extend(identity_refs)
                        add(key, sentence.strip())
                        break

    # A single-legume product cannot inherit copy explicitly describing a
    # different legume as this product. Require corroborated composition,
    # and an own-product subject; recipes, range stories and mixtures differ.
    legumes = {'beans': r'\bbeans?\b', 'chickpeas': r'\b(?:chickpeas?|garbanzo)\b',
               'lentils': r'\blentils?\b', 'peas': r'\b(?:green\s+|garden\s+|marrowfat\s+|split\s+)?peas\b'}
    recipe = str(p.get('ingredients') or '')
    own = [kind for kind, pattern in legumes.items() if re.search(pattern, name, re.I)]
    if len(own) == 1 and re.search(legumes[own[0]], recipe, re.I):
        for kind, pattern in legumes.items():
            if kind == own[0] or re.search(pattern, recipe, re.I):
                continue
            text = str(p.get('detailed_description') or '')
            for sentence in re.split(r'[.!?]', text):
                if (re.search(r'\bour\s+' + pattern + r'\s+(?:have|has|are|is)\b', sentence, re.I)
                        and not re.search(r'\b(?:other|another|range|recipe|serve|serving|example|instead)\b', sentence, re.I)):
                    add('detailed_description', sentence.strip())

    # A named non-wheat flour cannot inherit a wheat-only composition.
    # Spelt/einkorn/emmer are wheat, not contradictory species. Mixtures,
    # recipes and absence of an ingredient field do not establish this issue.
    flour = re.search(r'\b(rye|rice|chickpea|buckwheat|corn|maize|almond|coconut|oat)\s*flour\b', name, re.I)
    ingredients = str(p.get('ingredients') or '')
    if (flour and not re.search(r'\b(?:blend|mixed|mix|recipe|book|guide)\b', name, re.I)
            and re.match(r'\s*wheat\s*flour\b', ingredients, re.I)
            and not re.search(r'\b'+flour.group(1)+r'\b', ingredients, re.I)):
        add('ingredients', ingredients)

    # A dry wine with an explicit final sweetening/ageing account has
    # contradictory formulation evidence. Sweet tasting notes, a historical
    # account of another wine and initial sweet must are not this assertion.
    if re.search(r'\bdry\b', name, re.I) and re.search(r'\b(?:wine|sherry)\b', name, re.I):
        text = str(p.get('vinification_details') or '')
        if (re.search(r'\b(?:is|then)\s+(?:aged|ageing|aging)\s+as\s+a\s+sweet\s+wine\b', text, re.I)
                and not re.search(r'\b(?:other|another|example|afterwards|subsequently|further fermented)\b', text, re.I)):
            add('vinification_details', text)

    # Compare explicit named subjects with the same article/range suffix,
    # allowing an introductory clause or an unbranded product heading.
    # Quality adjectives, partial names and generic range stories are not
    # enough to establish a different variant.
    brand = fold(p.get('brand')); title = fold(name).split()
    brand_words = brand.split()
    care = bool(re.search(r'\b(?:conditioner|shampoo|deodorant|toothpaste|body lotion|body wash|foaming wash|hand wash|shower|skincare)\b',
                          ' '.join(str(p.get(k) or '') for k in ('name','features','detailed_description')),re.I))
    if care and brand_words and title[:len(brand_words)] == brand_words:
        tail = title[len(brand_words):]
        quality = {'and','the','our','this','new','natural','organic','pure','fresh','gentle',
                   'advanced','luxurious','luxury','premium','delicious','heavenly','uplifting',
                   'refreshing','large','small','giant','king','collection','fairtrade','refill',
                   'squeezy','soft','nourishing','moisturising','rich','light','extra','fine'}
        quality.update({'vegan','vegetarian','liquid','hand','handwash','face','body','fruit','juice'})
        quality.update({'foaming','classic','effervescent','professional','quality','heavy','duty','kitchen','cleaner'})
        # A named range can precede or follow its fragrance. Compare an
        # explicit branded subject, not incidental oils or generic scent notes.
        scents = {'lychee','lotus','peony','raspberry','bergamot','patchouli','vetiver',
                  'sandalwood','cedar','rose','lemon','lime','ginger','coconut','vanilla',
                  'lavender','jasmine','orange','grapefruit','mint','peppermint','spearmint'}
        scent_quality = quality | {'flower','flowers','pink','nude','scent','fragrance'}
        own_scents = set(title[len(brand_words):]) & scents
        text = str(p.get('detailed_description') or '')
        first = re.split(r'[.!?]', text, maxsplit=1)[0]
        raw_brand = r'[\W_]*'.join(re.escape(w) for w in re.findall(r'[A-Za-z0-9]+', str(p.get('brand') or '')))
        named_scent = re.search(r'\b(?i:' + raw_brand + r')[\s™®©]*'
                                r'([A-Z][A-Za-z]+(?:\s+(?:[A-Z][A-Za-z]+|and|&)){1,11})', first)
        if named_scent and len(own_scents) >= 2:
            stated_words = fold(named_scent.group(1)).split()
            stated_scents = set(stated_words) & scents
            own_base = {w for w in title[len(brand_words):] if w not in scents | scent_quality}
            stated_base = {w for w in stated_words if w not in scents | scent_quality}
            scope = first[:named_scent.start()] + first[named_scent.end():]
            if (own_base and own_base == stated_base and len(stated_scents) >= 2
                    and own_scents - stated_scents and stated_scents - own_scents
                    and not re.search(r'\b(?:other|another|previous|formerly|example|serve|recipe|including|such as)\b', scope, re.I)):
                add('detailed_description', first)
        if len(tail) >= 3:
            article = tail[-1]
            own = [w for w in tail[:-1] if w not in quality and w.isalpha()]
            text = str(p.get('detailed_description') or '')
            first = re.split(r'[.!?]', text, maxsplit=1)[0]
            folded = fold(first)
            raw_brand = r'[\W_]*'.join(re.escape(w) for w in re.findall(r'[A-Za-z0-9]+',str(p.get('brand') or '')))
            subject = r'([A-Z][A-Za-z]+(?:\s+(?:[A-Z][A-Za-z]+|and|&)){1,7})\s+(?i:'+re.escape(article)+r')\b'
            named = re.search(r'\b(?i:'+raw_brand+r')[\s™®©]*'+subject, first)
            branded = named is not None
            if (not named and article in {'conditioner','shampoo','deodorant','toothpaste'}
                    and not folded.startswith(brand+' ')):
                named = re.match(r'^'+subject, first)
            scope = fold(first[:named.start()] + first[named.end():]) if branded else folded
            if named and not re.search(r'\b(?:another|other|previous|formerly|history|recipe|serving|example|including|such as)\b', scope):
                stated = [w for w in fold(named.group(1)).split() if w not in quality]
                if (len(own) >= 2 and len(stated) >= 2 and not set(own).intersection(stated)):
                    add('detailed_description', first)

    # An unflavoured identity cannot inherit an explicit flavouring
    # ingredient or own-product bullet attribute. Range discussions, tasting
    # notes, serving suggestions and "no artificial flavour" are different.
    flavour_free = re.search(r'\b(?:flavou?r[- ]free|unflavou?red)\b', name, re.I)
    artificial_only = re.search(r'\b(?:artificial|synthetic)\s+flavou?r[- ]free\b', name, re.I)
    if flavour_free and not artificial_only:
        for key, boundary in (('ingredients', r'(?:^|[,;])'),
                              ('detailed_description', r'(?:^|[•\n;])'),
                              ('features', r'(?:^|[•\n;,])')):
            text = str(p.get(key) or '')
            for match in re.finditer(boundary + r'\s*([A-Za-z]+(?:\s+[A-Za-z]+){0,4}\s+flavou?ring\b|flavou?ring\b)', text, re.I):
                quote = match.group(1)
                if not re.search(r'\b(?:no|not|without|free|other|another|some|may|except|recipe|serve|serving|for|used|use)\b', quote, re.I):
                    add(key, quote)

    # A field explicitly opening with another named flavour of this brand
    # leaves variant applicability unresolved. Serving suggestions and an
    # incidental flavour ingredient do not identify the field's subject.
    brand = fold(p.get('brand'))
    flavour = r'apple|banana|blackcurrant|blueberry|cherry|coconut|cranberry|grapefruit|lemon|lime|mango|orange|peach|pear|peppermint|pineapple|raspberry|spearmint|strawberry|vanilla'
    own = re.fullmatch(re.escape(brand) + r'\s+(' + flavour + r')\s+([a-z ]+)', fold(name)) if brand else None
    if own and not re.search(r'\b(?:mixed|variety|selection|assorted)\b', own.group(2)):
        for key in ('detailed_description', 'manufacturer_marketing', 'brand_marketing'):
            text = str(p.get(key) or '')
            sentence = re.split(r'[.!?]', text, maxsplit=1)[0]
            named = re.match(re.escape(brand) + r'\s+(' + flavour + r')\b', fold(sentence))
            if named and named.group(1) != own.group(1) and not re.search(r'\b(?:other|another|history|formerly|previously|used to|serve|serving|recipe|example)\b', fold(sentence)[len(brand):], re.I):
                add(key, sentence)

    # A dilution product cannot inherit a different ready-to-drink recipe.
    # Require three corroborating native declarations, rather than guessing
    # from flavour words, a brand, package type or a serving suggestion alone.
    full = re.search(r'\bfull product name:\s*(.*?)(?:\s+(?:storage|usage|additional information):|$)',
                     str(p.get('other_information') or ''), re.I)
    diluted = bool(re.search(r'\b(?:cordial|squash)\b', name, re.I))
    mixed = bool(re.search(r'\b(?:lemonade|sparkling|carbonated|cocktail|ready[- ]to[- ]drink)\b', name, re.I))
    recipe = str(p.get('ingredients') or '')
    description = str(p.get('detailed_description') or '')
    if diluted and not mixed and full:
        declared = full.group(1)
        ready = r'\b(?:lemonade|(?:sparkling|carbonated)\s+(?:soft\s+)?(?:drink|water))\b'
        described_identity = re.split(r'[.!?]', description, maxsplit=1)[0]
        if (re.search(ready, declared, re.I) and re.search(ready, described_identity, re.I)
                and not re.search(r'\b(?:serve|serving|mix|recipe|example)\b', described_identity, re.I)
                and not re.search(r'\b(?:cordial|squash|concentrate|dilut\w*)\b', declared, re.I)
                and re.match(r'\s*carbonated\s+(?:\w+\s+){0,2}water\b', recipe, re.I)):
            add('other_information', full.group())
            add('detailed_description', description)
            add('ingredients', recipe)

    # A solid oral dosage cannot share a nutrition declaration for a spray.
    # Water in an ingredient list or instructions to drink water are not proof.
    if re.search(r'\b(?:gumm(?:y|ies)|tablets?|capsules?|softgels?)\b', name, re.I):
        for key in ('dietary_information', 'nutrition_plain_text'):
            text = str(p.get(key) or '')
            marker = re.search(r'\b(?:each|per)\s+(?:\d+(?:\.\d+)?\s*)?sprays?(?:\(s\))?', text, re.I)
            if marker: add(key, marker.group())

    # Compare near-identical branded product headings, not arbitrary prose,
    # serving suggestions or a producer's general brand history.
    brand_words = fold(p.get('brand')).split(); words = fold(name).split()
    if brand_words and words[:len(brand_words)] == brand_words:
        title = words[len(brand_words):]
        text = str(p.get('detailed_description') or '')
        heading = re.split(r'[.!?]|\b(?:is|are|contains|includes|features)\b', text, maxsplit=1, flags=re.I)[0]
        stated = fold(heading).split()
        if stated[:len(brand_words)] == brand_words:
            subject = stated[len(brand_words):]
            flavours = {'apple','banana','blackberry','blackcurrant','blueberry','carrot','cherry',
                        'chocolate','coconut','cranberry','ginger','grape','lemon','lime','mango',
                        'orange','passionfruit','peach','pear','peppermint','pineapple','raspberry',
                        'spearmint','strawberry','vanilla'}
            plurals = {'berries':'berry','blackberries':'blackberry','blueberries':'blueberry',
                       'cherries':'cherry','cranberries':'cranberry','raspberries':'raspberry',
                       'strawberries':'strawberry'}
            def signature(tokens):
                tokens = [plurals.get(t, t[:-1] if t.endswith('s') else t) for t in tokens if t != 'and']
                return {t for t in tokens if t in flavours}, [t for t in tokens if t not in flavours]
            named, named_base = signature(title)
            described, described_base = signature(subject)
            # Compare explicit branded variant subjects with the same article
            # type. A partial flavour list or an additional ingredient alone
            # does not establish a different product.
            if (named_base == described_base and named_base and len(named) >= 2 and len(described) >= 2
                    and named - described and described - named):
                add('detailed_description', heading.strip())
        if 4 <= len(title) <= 12 and stated[:len(brand_words)] == brand_words:
            stated = stated[len(brand_words):len(brand_words)+len(title)]
            different = [(i,a,b) for i,(a,b) in enumerate(zip(title, stated)) if a != b]
            flavour_words = {'peppermint','spearmint','mango','lemon','orange','pineapple','strawberry','raspberry','blackcurrant','vanilla','chocolate','cherry','apple','pear'}
            descriptive_words = {'uplifting','refreshing','luxurious','heavenly','delicious','new','our','this','pure','natural','organic','gentle','advanced','fairtrade','collection','top','head','king','giant','large','small','squeezy','refill','bottle','bag','pouch','can'}
            if (len(stated) == len(title) and len(different) == 1
                    and all(a.isalpha() and b.isalpha() and min(len(a),len(b)) >= 3
                            and a.rstrip('s') != b.rstrip('s')
                            and a not in descriptive_words and b not in descriptive_words
                            and (i == 0 or {a,b} <= flavour_words)
                            and i < len(title)-1 for i,a,b in different)):
                add('detailed_description', heading.strip())

    # An unscented SKU and a different named variant in the product heading
    # are inconsistent; botanical ingredients and care instructions are not.
    if re.search(r'\b(?:fragrance[- ]free|unscented)\b', name, re.I):
        text = str(p.get('detailed_description') or '')
        heading = re.search(r'^[^.!?]{0,300}[–—]\s*([A-Z][A-Za-z]+(?:\s+[A-Za-z]+){0,3})\s+(?:is|are)\b', text)
        if heading and re.search(r'\b(?:wash\w*|dishwash\w*|soap|liquid|cream|lotion|deodorant)\b', heading.group(), re.I):
            if fold(heading.group(1)) not in {'fragrance free', 'unscented'}:
                add('detailed_description', heading.group())

    wine = bool(re.search(r'\b(?:wine|sherry|rose|rosé|merlot|chardonnay|sauvignon|rioja|pinot|fino)\b', name, re.I))
    wine = wine and not re.search(r'\b(?:mulled|cocktail|liqueur|spritzer|sangria|vinegar|sauce|stock|marinade|cake|chocolate|glass|gift|soap|candle|fragrance)\b', name, re.I)
    if wine:
        origin = fold(p.get('country_of_origin'))
        origins = {country for country, aliases in COUNTRIES.items()
                   if any(re.search(r'\b'+re.escape(a)+r'\b', origin) for a in aliases)}
        eu_origin = bool(re.fullmatch(r'(?:produce of (?:the )?)?(?:eu|european union)', origin))
        eu_members = {'spain','france','italy','portugal','germany','austria','greece','ireland','hungary','romania','bulgaria'}
        # Country words must qualify wine itself. "French oak", an overseas
        # winery address, ingredients and Australian-style wine do not pass.
        for key in ('brand_marketing', 'detailed_description', 'vinification_details', 'features'):
            text = str(p.get(key) or '')
            for country, aliases in COUNTRIES.items():
                if country in origins or not origins and not (eu_origin and country not in eu_members): continue
                for alias in aliases:
                    match = re.search(r'\b'+re.escape(alias)+r'\s+(?:(?:white|red|sparkling|dry|sweet)\s+)?(?:wine|rosé|rose|sherry)\b', text, re.I)
                    if not match:
                        for statement in re.split(r'[.;!?]', text):
                            if re.search(r'\b(?:other|another|previous|history|historical|example|serve|serving|pair|recipe|bottled|packaged|imported)\b', statement, re.I):continue
                            match = re.search(r'\bwine\s+of\s+(?:[A-Za-zÀ-ÿ]+[, ]+){0,3}'+re.escape(alias)+r'\b', statement, re.I)
                            if match:break
                    if match:
                        add(key, match.group())
                        refs.append({'path':'/product/country_of_origin','quote':str(p['country_of_origin'])})
        # Distinct named soleras in separate unqualified production accounts
        # leave identity unresolved. A place, oak source or generic solera
        # system alone does not supply a second named product.
        groups = []
        for key in ('vinification_details', 'other_information'):
            text = str(p.get(key) or '')
            for m in re.finditer(r'\bthe\s+([A-ZÀ-ÖØ-Þ][\wÀ-ÿ]*(?:\s+(?:[A-ZÀ-ÖØ-Þ][\wÀ-ÿ]*|\d+)){0,3})\s+solera\b', text):
                if re.search(r'\b(?:other|another|example)\b', text[max(0,m.start()-60):m.start()], re.I):continue
                groups.append((key, m.group(), fold(m.group(1))))
        for a in groups:
            for b in groups:
                if a[0] != b[0] and a[2] != b[2] and a[2] in fold(name) and b[2] not in fold(name):
                    add(a[0],a[1]);add(b[0],b[1])
    return list({(r['path'],r['quote']):r for r in refs}.values())


def packaging_attribute(context, ref):
    """A bare Vegan attribute under a recycling/packaging heading is ambiguous."""
    m = re.fullmatch(r'/manufacturer_vegan_evidence/(\d+)/content', ref['path'])
    if not m: return False
    row = context.get('manufacturer_vegan_evidence', [])[int(m.group(1))]
    if row.get('source_kind') == 'manufacturer_website': return False
    if fold(row.get('field_title')) not in {'recyclinginformation', 'recycling information', 'packaging'}: return False
    text = str(row.get('content') or '')
    return bool(re.match(r'\s*(?:recyclable\s+)?packaging\b',text,re.I)
                and re.search(r'(?:^|[.;])\s*vegan\s*(?:[.;]|$)',text,re.I)
                and not re.search(r'\b(?:this|the|our)\s+(?:complete\s+)?product\s+is\s+vegan\b',text,re.I))
