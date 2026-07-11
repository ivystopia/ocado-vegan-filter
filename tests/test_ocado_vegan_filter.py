#!/usr/bin/env python3
"""Smoke-test the Ocado Vegan Filter userscript against real and fixture DOMs."""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.firefox.service import Service
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


ROOT = Path(__file__).resolve().parents[1]
USERSCRIPT = ROOT / "ocado-vegan-filter.user.js"
PROMOTIONS_URL = "https://www.ocado.com/promotions?source=header%20button"
CHEESE_SEARCH_URL = "https://www.ocado.com/search?q=cheese"
FIREFOX_HELPER_SCRIPTS = os.environ.get("BROWSE_WITH_FIREFOX_SCRIPTS", "")
OFFICIAL_VEGAN_IDS = {
    "369202011",
}
MANUFACTURER_OR_NAME_VEGAN_IDS = {
    "511102011",
    "577028011",
    "601607011",
    "652775011",
    "652776011",
    "672727011",
    "679700011",
    "666687011",
}
INGREDIENTS_VEGAN_IDS = {
    "517986011",
    "624307011",
}
KNOWN_NON_VEGAN_IDS = {
    "17959011",
}
MUTED_PROMOTION_RGB = "rgb(101, 67, 72)"


def userscript() -> str:
    return USERSCRIPT.read_text()


def extract_userscript_id_set(constant_name: str) -> set[str]:
    match = re.search(
        rf"const\s+{re.escape(constant_name)}\s*=\s*new\s+Set\(\s*`(?P<body>.*?)`\s*"
        r"\.trim\(\)\s*\.split\(\s*/\\s\+/\s*\)\s*,?\s*\)",
        userscript(),
        flags=re.S,
    )
    assert match, f"{constant_name} was not found in userscript"
    return set(re.findall(r"\b\d+\b", match.group("body")))


def userscript_source_test() -> None:
    assert "// @name        Ocado Vegan Filter" in userscript()
    assert "// @version     1.6.0" in userscript()
    assert "// @inject-into page" in userscript()

    official_ids = extract_userscript_id_set("OFFICIAL_VEGAN_PRODUCT_IDS")
    manufacturer_or_name_ids = extract_userscript_id_set("MANUFACTURER_OR_NAME_VEGAN_PRODUCT_IDS")
    ingredients_ids = extract_userscript_id_set("INGREDIENTS_VEGAN_PRODUCT_IDS")
    known_non_vegan_ids = extract_userscript_id_set("KNOWN_NON_VEGAN_PRODUCT_IDS")

    assert OFFICIAL_VEGAN_IDS <= official_ids
    assert MANUFACTURER_OR_NAME_VEGAN_IDS <= manufacturer_or_name_ids
    assert INGREDIENTS_VEGAN_IDS <= ingredients_ids
    assert KNOWN_NON_VEGAN_IDS <= known_non_vegan_ids
    assert not (official_ids | manufacturer_or_name_ids | ingredients_ids) & known_non_vegan_ids
    assert "const MANUFACTURER_VEGAN_PRODUCT_IDS" not in userscript()
    print("userscript source test passed")


def assert_card_state(rows: dict[str, dict[str, object]], card_id: str, *, blocked: bool, label: str = "", check_link_target: bool = True) -> None:
    row = rows[card_id]
    if blocked:
        assert row["buttonText"] == label, row
        assert row["buttonVisuallyMarked"] is True, row
        assert row["blocked"] is False, row
        assert row["nonVeganClass"] is True, row
        assert row["imageOpacity"] == "0.42", row
        assert row["imagePointerEvents"] == "none", row
        if check_link_target:
            assert row["imagePointTag"] == "A", row
        assert_zero_saturation_filter(row["imageFilter"], row)
        return

    assert row["buttonText"] == "Add", row
    assert row["buttonVisuallyMarked"] is False, row
    assert row["blocked"] is False, row
    assert row["nonVeganClass"] is False, row
    assert row["imageOpacity"] == "1", row
    assert row["imagePointerEvents"] != "none", row
    assert row["imageFilter"] == "none", row


def assert_zero_saturation_filter(value: object, row: object) -> None:
    css_filter = str(value)
    assert "saturate(0)" in css_filter, row
    assert "grayscale(1)" in css_filter or "grayscale(100%)" in css_filter, row


def fixture_smoke_test() -> None:
    html = """<!doctype html><html><body>
      <script>
        window.blockedAddClicks = 0;
        window.blockedImageClicks = 0;
        window.__INITIAL_STATE__ = {
          data: {
            products: {
              "430f643f-01fa-43e0-98bd-a0db2d9c7e0f": {
                retailerProductId: "999998011",
                name: "Synthetic Hydration Vegan Product",
                attributes: [
                  {icon: "freezable", label: "Suitable for freezing"},
                  {icon: "lactoseFree", label: "Lactose Free"},
                  {icon: "vegetarian", label: "Vegetarian"},
                  {icon: "glutenFree", label: "Gluten Free"},
                  {icon: "wheatFree", label: "Wheat Free"},
                  {icon: "vegan", label: "Vegan"}
                ]
              },
              "65ae304a-a28a-4019-bf00-c2bd4b963427": {
                retailerProductId: "999997011",
                name: "Synthetic Late Hydration Product",
                attributes: [
                  {icon: "vegetarian", label: "Vegetarian"}
                ]
              }
            }
          }
        };
      </script>
      <button data-test="fop-controls-show-alternatives-button" class="ocado-oos-button">Show alternatives</button>
      <article class="product-card-container" id="ready">
        <a href="https://www.ocado.com/products/squeaky-bean-ready-to-eat-marinated-chicken-style-pieces-kick-of-tikka/601607011"><img></a>
        <button data-test="counter-button" aria-label="Add Squeaky Bean Ready To Eat Marinated Chicken Style Pieces">Add</button>
      </article>
      <article class="product-card-container" id="ready-hyphen">
        <a href="https://www.ocado.com/products/squeaky-bean-ready-to-eat-marinated-chicken-style-pieces-kick-of-tikka-601607011"><img></a>
        <button data-test="counter-button" aria-label="Add Squeaky Bean Ready To Eat Marinated Chicken Style Pieces">Add</button>
      </article>
      <article class="product-card-container" id="cajun">
        <a href="https://www.ocado.com/products/squeaky-bean-chargrilled-cajun-mini-fillets/577028011"><img></a>
        <button data-test="counter-button" aria-label="Add Squeaky Bean Chargrilled Cajun Mini Fillets">Add</button>
      </article>
      <article class="product-card-container" id="cajun-hyphen">
        <a href="https://www.ocado.com/products/squeaky-bean-chargrilled-cajun-mini-fillets-577028011"><img></a>
        <button data-test="counter-button" aria-label="Add Squeaky Bean Chargrilled Cajun Mini Fillets">Add</button>
      </article>
      <article class="product-card-container" id="official">
        <a href="https://www.ocado.com/products/example-vegan-999999011"><img></a>
        <svg id="vegan"></svg>
        <button data-test="counter-button" aria-label="Add Official Vegan">Add</button>
      </article>
      <article class="product-card-container" id="official-overrides-known-nonvegan">
        <a href="https://www.ocado.com/products/example-live-vegan-17959011"><img></a>
        <svg id="vegan"></svg>
        <button data-test="counter-button" aria-label="Add Example Live Vegan">Add</button>
      </article>
      <article class="product-card-container" id="official-hidden-icon">
        <a href="https://www.ocado.com/products/itsu-vegetable-fusion-gyoza/369202011">itsu vegetable fusion gyoza<img></a>
        <svg data-test="product-card-lifestyle-freezable"></svg>
        <svg data-test="product-card-lifestyle-vegetarian"></svg>
        <svg data-test="product-card-lifestyle-microwavable"></svg>
        <svg data-test="product-card-lifestyle-frozen"></svg>
        <button data-test="counter-button" aria-label="Add itsu vegetable fusion gyoza">Add</button>
      </article>
      <article class="product-card-container" id="name-vegan">
        <a href="https://www.ocado.com/products/i-am-nut-ok-bluffalo-notzarella-vegan-mozzarella/634291011">I AM NUT OK Bluffalo Notzarella - Vegan Mozzarella<img></a>
        <button data-test="counter-button" aria-label="Add I AM NUT OK Bluffalo Notzarella - Vegan Mozzarella">Add</button>
      </article>
      <article class="product-card-container" id="hydration-vegan">
        <a href="https://www.ocado.com/products/synthetic-hydration-vegan-product/999998011">Synthetic Hydration Vegan Product<img></a>
        <svg data-test="product-card-lifestyle-freezable"></svg>
        <svg data-test="product-card-lifestyle-lactoseFree"></svg>
        <svg data-test="product-card-lifestyle-vegetarian"></svg>
        <svg data-test="product-card-lifestyle-glutenFree"></svg>
        <button data-test="counter-button" aria-label="Add Synthetic Hydration Vegan Product">Add</button>
      </article>
      <article class="product-card-container" id="late-hydration-vegan">
        <a href="https://www.ocado.com/products/synthetic-late-hydration-product/999997011">Synthetic Late Hydration Product<img></a>
        <button data-test="counter-button" aria-label="Add Synthetic Late Hydration Product">Add</button>
      </article>
      <article class="product-card-container ocado-vegan-filter-non-vegan" id="stale-vegan">
        <a href="https://www.ocado.com/products/violife-non-dairy-cheese-alternative-slices/315701011">Violife Non-Dairy Cheese Alternative Slices<img
          src="data:image/png;base64,stale"
          data-ocado-vegan-filter-grayscale-source="https://www.ocado.com/images-v3/example/original.webp"
          data-ocado-vegan-filter-original-srcset="https://www.ocado.com/images-v3/example/100x100.webp 100w, https://www.ocado.com/images-v3/example/200x200.webp 200w"
          data-ocado-vegan-filter-original-sizes="(max-width: 36em) 100px, 175px"
          style="filter: grayscale(100%) saturate(0) !important; opacity: 0.42 !important; animation: none !important; pointer-events: none !important; transition: none !important;"
        ></a>
        <button data-test="counter-button" aria-label="Add Violife Non-Dairy Cheese Alternative Slices">Add</button>
      </article>
      <article class="product-card-container" id="ingredients-beans">
        <a href="https://www.ocado.com/products/m-s-extra-fine-beans/517986011">M&amp;S Extra Fine Beans<img></a>
        <button data-test="counter-button" aria-label="Add M&S Extra Fine Beans">Add</button>
      </article>
      <article class="product-card-container" id="ingredients-pasta">
        <a href="https://www.ocado.com/products/rummo-spaghetti-pasta-no-3/624307011">Rummo Spaghetti Pasta No.3<img></a>
        <button data-test="counter-button" aria-label="Add Rummo Spaghetti Pasta No.3">Add</button>
      </article>
      <article class="product-card-container" id="features-gherkins">
        <a href="https://www.ocado.com/products/kuhne-gherkins/511102011">Kuhne Gherkins<img></a>
        <button data-test="counter-button" aria-label="Add Kuhne Gherkins">Add</button>
      </article>
      <article class="product-card-container" id="blocked">
        <a href="https://www.ocado.com/products/mcvities-penguin-orange-biscuit-bars-multipack-123456789" onclick="window.blockedImageClicks += 1; event.preventDefault();"><img style="display: block; width: 100px; height: 100px;"></a>
        <span data-test="fop-offer-text" style="color: rgb(169, 0, 22)">Half price</span>
        <span class="_text--promotion_fixture" style="color: rgb(169, 0, 22)">£1.00 per pack</span>
        <span data-test="fop-price" class="_display--promotion_fixture" style="color: rgb(169, 0, 22)">£1.00</span>
        <svg data-test="fop-offer-icon" style="fill: rgb(169, 0, 22)"></svg>
        <button data-test="counter-button" aria-label="Add McVitie's Penguin Orange Biscuit Bars Multipack" onclick="window.blockedAddClicks += 1">Add</button>
      </article>
      <article class="product-card-container" id="known-nonvegan">
        <a href="https://www.ocado.com/products/example-known-nonvegan-17959011"><img style="display: block; width: 100px; height: 100px;"></a>
        <button data-test="counter-button" aria-label="Add Example Known Nonvegan">Add</button>
      </article>
      <article class="product-card-container" id="load-mutation-source">
        <a href="https://www.ocado.com/products/load-mutation-source-123456780"><img src="blob:null/ocado-vegan-filter-test"></a>
        <button data-test="counter-button" aria-label="Add Load Mutation Source">Add</button>
      </article>
    </body></html>"""

    options = Options()
    options.add_argument("-headless")
    driver = webdriver.Firefox(options=options)
    try:
        driver.get("data:text/html;base64," + base64.b64encode(html.encode()).decode())
        driver.execute_script(
            """
            window.ocadoTestAnimationFrameCount = 0;
            window.ocadoTestNativeRequestAnimationFrame = window.requestAnimationFrame.bind(window);
            window.requestAnimationFrame = callback => window.ocadoTestNativeRequestAnimationFrame(timestamp => {
              window.ocadoTestAnimationFrameCount += 1;
              return callback(timestamp);
            });
            """
        )
        driver.execute_script(
            """
            document.querySelector('#load-mutation-source img').addEventListener('load', () => {
              document.body.insertAdjacentHTML('beforeend', `
                <article class="product-card-container" id="card-added-during-image-load">
                  <a href="https://www.ocado.com/products/card-added-during-image-load-123456781"><img></a>
                  <button data-test="counter-button" aria-label="Add Card Added During Image Load">Add</button>
                </article>
              `);
            }, { once: true });
            """
        )
        driver.execute_script(userscript())
        wait = WebDriverWait(driver, 5)
        wait.until(lambda d: d.execute_script("return getComputedStyle(document.querySelector('#blocked img')).opacity") == "0.42")
        driver.find_element(By.CSS_SELECTOR, "#blocked button").click()
        driver.find_element(By.CSS_SELECTOR, "#blocked a").click()
        rows = driver.execute_script(
            """
            const blockedButton = document.querySelector('#blocked button');
            blockedButton.dispatchEvent(new MouseEvent('mouseenter', { bubbles: true }));
            const blockedHoverText = blockedButton.textContent.trim();
            blockedButton.dispatchEvent(new MouseEvent('mouseleave', { bubbles: true }));
            const blockedLeaveText = blockedButton.textContent.trim();
            const knownNonveganButton = document.querySelector('#known-nonvegan button');
            knownNonveganButton.dispatchEvent(new MouseEvent('mouseenter', { bubbles: true }));
            const knownNonveganHoverText = knownNonveganButton.textContent.trim();
            knownNonveganButton.dispatchEvent(new MouseEvent('mouseleave', { bubbles: true }));
            const knownNonveganLeaveText = knownNonveganButton.textContent.trim();
            return Object.fromEntries(['ready', 'ready-hyphen', 'cajun', 'cajun-hyphen', 'official', 'official-overrides-known-nonvegan', 'official-hidden-icon', 'name-vegan', 'hydration-vegan', 'late-hydration-vegan', 'stale-vegan', 'ingredients-beans', 'ingredients-pasta', 'features-gherkins', 'blocked', 'known-nonvegan'].map(id => {
              const card = document.getElementById(id);
              const button = card.querySelector('button');
              const img = card.querySelector('img');
              const offerText = card.querySelector('[data-test="fop-offer-text"]');
              const offerUnitPrice = card.querySelector('[class*="_text--promotion_"]');
              const offerPrice = card.querySelector('[data-test="fop-price"]');
              const offerIcon = card.querySelector('[data-test="fop-offer-icon"]');
              return [id, {
                buttonText: button.textContent.trim(),
                buttonClassName: button.className,
                buttonVisuallyMarked: button.classList.contains('ocado-vegan-filter-not-vegan-add'),
                blocked: false,
                nonVeganClass: card.classList.contains('ocado-vegan-filter-non-vegan'),
                imageOpacity: getComputedStyle(img).opacity,
                imageFilter: getComputedStyle(img).filter,
                imagePointerEvents: getComputedStyle(img).pointerEvents,
                imageSrc: img.getAttribute('src') || '',
                imageSrcset: img.getAttribute('srcset') || '',
                imageDataset: {...img.dataset},
                imagePointTag: document.elementFromPoint(
                  img.getBoundingClientRect().left + img.getBoundingClientRect().width / 2,
                  img.getBoundingClientRect().top + img.getBoundingClientRect().height / 2
                )?.tagName || null,
                offerColor: offerText && getComputedStyle(offerText).color,
                offerUnitPriceColor: offerUnitPrice && getComputedStyle(offerUnitPrice).color,
                offerPriceColor: offerPrice && getComputedStyle(offerPrice).color,
                offerIconFill: offerIcon && getComputedStyle(offerIcon).fill,
                blockedHoverText,
                blockedLeaveText,
                knownNonveganHoverText,
                knownNonveganLeaveText,
              }];
            }));
            """
        )
        click_counts = driver.execute_script("return {add: window.blockedAddClicks, image: window.blockedImageClicks};")
        time.sleep(0.15)
        idle_frame_count = driver.execute_script("return window.ocadoTestAnimationFrameCount;")
        time.sleep(0.25)
        settled_frame_count = driver.execute_script("return window.ocadoTestAnimationFrameCount;")
        driver.execute_script(
            """
            window.ocadoTestRepeatedMutationCount = 0;
            window.ocadoTestRepeatedMutations = [];
            window.ocadoTestRepeatedMutationObserver = new MutationObserver(mutations => {
              window.ocadoTestRepeatedMutationCount += mutations.length;
              window.ocadoTestRepeatedMutations.push(...mutations.map(mutation => ({
                attributeName: mutation.attributeName,
                target: mutation.target.id || mutation.target.tagName,
                type: mutation.type,
              })));
            });
            window.ocadoTestRepeatedMutationObserver.observe(document.querySelector('#blocked'), {
              attributes: true,
              childList: true,
              subtree: true,
            });
            """
        )
        driver.execute_script("document.querySelector('#blocked').classList.add('external-page-update');")
        wait.until(lambda d: d.execute_script("return window.ocadoTestAnimationFrameCount;") > settled_frame_count)
        external_update_frame_count = driver.execute_script("return window.ocadoTestAnimationFrameCount;")
        time.sleep(0.25)
        final_frame_count = driver.execute_script("return window.ocadoTestAnimationFrameCount;")
        repeated_mutation_count = driver.execute_script("return window.ocadoTestRepeatedMutationCount;")
        repeated_mutations = driver.execute_script("return window.ocadoTestRepeatedMutations;")
        driver.execute_script(
            """
            window.__INITIAL_STATE__.data.products['65ae304a-a28a-4019-bf00-c2bd4b963427'].attributes = [
              {icon: 'vegan', label: 'Vegan'}
            ];
            document.querySelector('#late-hydration-vegan').classList.add('hydration-updated');
            """
        )
        wait.until(
            lambda d: d.execute_script(
                "return document.querySelector('#late-hydration-vegan button').textContent.trim() === 'Add' && !document.querySelector('#late-hydration-vegan').classList.contains('ocado-vegan-filter-non-vegan');"
            )
        )
        driver.execute_script(
            """
            window.ocadoTestRepeatedMutationObserver.disconnect();
            document.querySelector('#blocked button').textContent = 'Add';
            document.querySelector('#blocked img').style.setProperty('opacity', '1', 'important');
            """
        )
        wait.until(
            lambda d: d.execute_script(
                "return document.querySelector('#blocked button').textContent.trim() === 'Unknown vegan' && getComputedStyle(document.querySelector('#blocked img')).opacity === '0.42';"
            )
        )
        driver.execute_script("document.querySelector('#load-mutation-source img').dispatchEvent(new Event('load')); ")
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "#card-added-during-image-load")))
        wait.until(
            lambda d: d.execute_script(
                "return document.querySelector('#card-added-during-image-load').classList.contains('ocado-vegan-filter-non-vegan') && document.querySelector('#card-added-during-image-load button').textContent.trim() === 'Unknown vegan';"
            )
        )
    finally:
        driver.quit()

    for card_id in [
        "ready",
        "ready-hyphen",
        "cajun",
        "cajun-hyphen",
        "official",
        "official-overrides-known-nonvegan",
        "official-hidden-icon",
        "name-vegan",
        "hydration-vegan",
        "stale-vegan",
        "ingredients-beans",
        "ingredients-pasta",
        "features-gherkins",
    ]:
        assert_card_state(rows, card_id, blocked=False)
    assert rows["stale-vegan"]["imageSrc"] == "https://www.ocado.com/images-v3/example/original.webp", rows["stale-vegan"]
    assert rows["stale-vegan"]["imageSrcset"].startswith("https://www.ocado.com/images-v3/example/100x100.webp"), rows["stale-vegan"]
    assert "ocadoVeganFilterGrayscaleSource" not in rows["stale-vegan"]["imageDataset"], rows["stale-vegan"]
    assert_card_state(rows, "blocked", blocked=True, label="Unknown vegan")
    assert_card_state(rows, "late-hydration-vegan", blocked=True, label="Unknown vegan", check_link_target=False)
    assert_card_state(rows, "known-nonvegan", blocked=True, label="Not vegan", check_link_target=False)
    assert click_counts == {"add": 1, "image": 1}, click_counts
    assert rows["blocked"]["blockedHoverText"] == "Add anyway", rows["blocked"]
    assert rows["blocked"]["blockedLeaveText"] == "Unknown vegan", rows["blocked"]
    assert rows["known-nonvegan"]["knownNonveganHoverText"] == "Add anyway", rows["known-nonvegan"]
    assert rows["known-nonvegan"]["knownNonveganLeaveText"] == "Not vegan", rows["known-nonvegan"]
    assert "ocado-oos-button" in rows["blocked"]["buttonClassName"], rows["blocked"]
    assert rows["blocked"]["offerColor"] == MUTED_PROMOTION_RGB, rows["blocked"]
    assert rows["blocked"]["offerUnitPriceColor"] == MUTED_PROMOTION_RGB, rows["blocked"]
    assert rows["blocked"]["offerPriceColor"] == MUTED_PROMOTION_RGB, rows["blocked"]
    assert rows["blocked"]["offerIconFill"] == MUTED_PROMOTION_RGB, rows["blocked"]
    assert settled_frame_count == idle_frame_count, (idle_frame_count, settled_frame_count)
    assert external_update_frame_count == settled_frame_count + 1, (settled_frame_count, external_update_frame_count)
    assert final_frame_count == external_update_frame_count, (external_update_frame_count, final_frame_count)
    assert repeated_mutation_count == 1, repeated_mutations
    print("fixture smoke test passed")


def grayscale_cache_smoke_test() -> None:
    image_count = 260
    cards = "".join(
        f"""<article class="product-card-container">
          <a href="https://www.ocado.com/products/cache-{800100000 + index}"><img></a>
          <button data-test="counter-button" aria-label="Add cache item {index}">Add</button>
        </article>"""
        for index in range(image_count)
    )
    html = f"<!doctype html><html><body>{cards}</body></html>"
    options = Options()
    options.add_argument("-headless")
    driver = webdriver.Firefox(options=options)
    try:
        driver.get("data:text/html;base64," + base64.b64encode(html.encode()).decode())
        driver.execute_script(
            """
            const NativeMap = window.Map;
            window.ocadoTestMaps = [];
            window.Map = class extends NativeMap {
              constructor(...args) {
                super(...args);
                window.ocadoTestMaps.push(this);
              }
            };
            """
        )
        driver.execute_async_script(
            """
            const done = arguments[0];
            const images = Array.from(document.querySelectorAll('img'));
            Promise.all(images.map((image, index) => new Promise(resolve => {
              image.onload = resolve;
              image.onerror = resolve;
              const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="2" height="2"><rect width="2" height="2" fill="rgb(${index % 255},0,0)"/></svg>`;
              image.src = URL.createObjectURL(new Blob([svg], { type: 'image/svg+xml' }));
            }))).then(() => {
              window.ocadoTestImageSources = images.map(image => image.src);
              done();
            });
            """
        )
        driver.execute_script(
            "const script = document.createElement('script'); script.textContent = arguments[0]; document.documentElement.append(script);",
            userscript(),
        )
        WebDriverWait(driver, 20).until(
            lambda d: d.execute_script("return document.querySelectorAll('.ocado-vegan-filter-non-vegan').length") == image_count
        )
        cache_state = driver.execute_script(
            """
            const cache = window.ocadoTestMaps[0];
            return {
              size: cache.size,
              hasFirst: cache.has(window.ocadoTestImageSources[0]),
              hasLast: cache.has(window.ocadoTestImageSources.at(-1)),
              failures: document.querySelectorAll('[data-ocado-vegan-filter-grayscale-failed-source]').length,
            };
            """
        )
    finally:
        driver.quit()

    assert cache_state == {"size": 256, "hasFirst": False, "hasLast": True, "failures": 0}, cache_state
    print("grayscale cache smoke test passed")


def import_firefox_helpers():
    if FIREFOX_HELPER_SCRIPTS and FIREFOX_HELPER_SCRIPTS not in sys.path:
        sys.path.insert(0, FIREFOX_HELPER_SCRIPTS)

    from firefox_session import firefox_options, resolve_current_profile, snapshot_profile

    return firefox_options, resolve_current_profile, snapshot_profile


def remove_configured_extensions_from_temp_profile(profile: Path) -> None:
    extension_ids = {value.strip() for value in os.environ.get("OCADO_TEST_REMOVE_EXTENSION_IDS", "").split(",") if value.strip()}

    if not extension_ids:
        return

    for extension_id in extension_ids:
        xpi = profile / f"extensions/{extension_id}.xpi"
        if xpi.exists():
            xpi.unlink()

    prefs = profile / "prefs.js"
    if prefs.exists():
        lines = prefs.read_text(errors="ignore").splitlines()
        lines = [line for line in lines if not any(extension_id in line for extension_id in extension_ids)]
        prefs.write_text("\n".join(lines) + "\n")

    for name in ["extensions.json", "extension-settings.json", "extension-preferences.json"]:
        path = profile / name
        if not path.exists():
            continue

        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue

        changed = False
        if name == "extensions.json" and isinstance(data.get("addons"), list):
            before = len(data["addons"])
            data["addons"] = [addon for addon in data["addons"] if addon.get("id") not in extension_ids]
            changed = before != len(data["addons"])
        elif isinstance(data, dict):
            for extension_id in extension_ids:
                if extension_id in data:
                    data.pop(extension_id, None)
                    changed = True

        if changed:
            path.write_text(json.dumps(data))


def collect_rows(driver: webdriver.Firefox) -> list[dict[str, object]]:
    return driver.execute_script(
        r"""
        const productIdFromUrl = url => String(url || '').match(/\/products\/(?:[^/?#]*[-/])?(\d+)(?:\/details)?(?:[/?#]|$)/)?.[1] || null;
        const originalPromotionRed = 'rgb(169, 0, 22)';
        const cards = Array.from(document.querySelectorAll('.product-card-container, [data-test^="fop-wrapper:"], [data-testid^="fop-wrapper:"]'))
          .map(el => el.matches('.product-card-container') ? el : el.querySelector('.product-card-container') || el);
        return [...new Set(cards)].map((card, index) => {
          const productLink = card.querySelector('a[href*="/products/"]');
          const img = productLink && productLink.querySelector('img') || card.querySelector('img');
          const button = card.querySelector('button[data-test="counter-button"], button[data-testid="counter-button"]');
          const offerText = card.querySelector('[data-test="fop-offer-text"], [data-testid="fop-offer-text"], [class*="_text--promotion_"], [class*="_display--promotion_"]');
          const offerPrice = card.querySelector('[data-test="fop-price"][class*="promotion"], [data-testid="fop-price"][class*="promotion"], [class*="_display--promotion_"]');
          const offerIcon = card.querySelector('[data-test="fop-offer-icon"], [data-testid="fop-offer-icon"], [data-icon="icon__promotion"]');
          const originalRedElements = Array.from(card.querySelectorAll('[data-test*="offer"], [data-testid*="offer"], [data-test="fop-price"], [data-testid="fop-price"], [data-icon="icon__promotion"], [class*="promotion"]'))
            .flatMap(el => {
              const style = getComputedStyle(el);
              if (style.color !== originalPromotionRed && style.fill !== originalPromotionRed) {
                return [];
              }

              return [{
                tag: el.tagName.toLowerCase(),
                dataTest: el.getAttribute('data-test') || el.getAttribute('data-testid') || '',
                className: String(el.className || '').slice(0, 160),
                color: style.color,
                fill: style.fill,
                text: el.textContent.replace(/\s+/g, ' ').trim().slice(0, 120),
              }];
            });
          const official = Boolean(card.querySelector('[data-test="product-card-lifestyle-vegan"], [data-testid="product-card-lifestyle-vegan"], svg#vegan, svg[data-icon="icon__vegan"]')) ||
            Array.from(card.querySelectorAll('use')).some(use => String(use.getAttribute('href') || use.getAttribute('xlink:href') || '').endsWith('#vegan'));
          return {
            index,
            text: card.textContent.replace(/\s+/g, ' ').trim().slice(0, 320),
            href: productLink && productLink.href,
            id: productIdFromUrl(productLink && productLink.href),
            official,
            nonVeganClass: card.classList.contains('ocado-vegan-filter-non-vegan'),
            blocked: false,
            buttonVisuallyMarked: Boolean(button && button.classList.contains('ocado-vegan-filter-not-vegan-add')),
            buttonText: button && button.textContent.replace(/\s+/g, ' ').trim(),
            buttonClientWidth: button && button.clientWidth,
            buttonScrollWidth: button && button.scrollWidth,
            imageOpacity: img && getComputedStyle(img).opacity,
            imageFilter: img && getComputedStyle(img).filter,
            imageCurrentSrc: img && (img.currentSrc || img.src),
            grayscaleSource: img && img.dataset.ocadoVeganFilterGrayscaleSource,
            offerColor: offerText && getComputedStyle(offerText).color,
            offerPriceColor: offerPrice && getComputedStyle(offerPrice).color,
            offerIconFill: offerIcon && getComputedStyle(offerIcon).fill,
            originalRedElements,
          };
        });
        """
    )


def promotions_page_smoke_test() -> None:
    firefox_options, resolve_current_profile, snapshot_profile = import_firefox_helpers()
    runtime_profile, temporary_root = snapshot_profile(resolve_current_profile().path)
    remove_configured_extensions_from_temp_profile(runtime_profile)

    options = firefox_options(runtime_profile, headless=True)
    service = Service(log_output=str(temporary_root / "geckodriver.log"), env={**os.environ, "MOZ_HEADLESS": "1"})
    driver = webdriver.Firefox(options=options, service=service)
    try:
        driver.set_page_load_timeout(90)
        wait = WebDriverWait(driver, 90)
        driver.get(PROMOTIONS_URL)
        wait.until(lambda d: d.execute_script("return document.readyState") in ("interactive", "complete"))
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".product-card-container, [data-test^='fop-wrapper:'], [data-testid^='fop-wrapper:']")))
        driver.execute_script(userscript())

        rows: list[dict[str, object]] = []
        for _ in range(12):
            time.sleep(0.8)
            rows = collect_rows(driver)
            if any(row["id"] in MANUFACTURER_OR_NAME_VEGAN_IDS for row in rows) and any(row["nonVeganClass"] for row in rows):
                break
            driver.execute_script("window.scrollBy(0, Math.max(800, window.innerHeight * 1.4))")
    finally:
        driver.quit()
        shutil.rmtree(temporary_root, ignore_errors=True)

    manufacturer_vegan_rows = [row for row in rows if row["id"] in MANUFACTURER_OR_NAME_VEGAN_IDS]
    muted_rows = [row for row in rows if row["nonVeganClass"]]
    official_rows = [row for row in rows if row["official"]]

    assert rows, "No product cards found on promotions page"
    assert manufacturer_vegan_rows, "No vegan-according-to-manufacturer products found in loaded promotions slice"
    assert muted_rows, "No visually muted non-vegan or unknown products found in loaded promotions slice"

    for row in manufacturer_vegan_rows:
        assert row["buttonText"] == "Add", row
        assert row["buttonVisuallyMarked"] is False, row
        assert row["blocked"] is False, row
        assert row["nonVeganClass"] is False, row
        assert row["imageFilter"] == "none", row
        assert row["imageOpacity"] == "1", row

    known_non_vegan_ids = extract_userscript_id_set("KNOWN_NON_VEGAN_PRODUCT_IDS")
    for row in muted_rows[:5]:
        expected_label = "Not vegan" if row["id"] in known_non_vegan_ids else "Unknown vegan"
        assert row["buttonText"] == expected_label, row
        assert row["buttonVisuallyMarked"] is True, row
        assert row["buttonScrollWidth"] <= row["buttonClientWidth"], row
        assert row["blocked"] is False, row
        assert_zero_saturation_filter(row["imageFilter"], row)
        assert row["imageOpacity"] == "0.42", row
        assert row["grayscaleSource"] or str(row["imageCurrentSrc"]).startswith("data:image/png"), row

    muted_promotion_rows = [row for row in muted_rows if row["offerColor"]]
    assert muted_promotion_rows, "No muted promotional rows found in loaded promotions slice"
    for row in muted_promotion_rows[:5]:
        assert row["offerColor"] == MUTED_PROMOTION_RGB, row
        if row["offerPriceColor"]:
            assert row["offerPriceColor"] == MUTED_PROMOTION_RGB, row
        if row["offerIconFill"]:
            assert row["offerIconFill"] == MUTED_PROMOTION_RGB, row
        assert row["originalRedElements"] == [], row

    muted_promo_price_rows = [row for row in muted_rows if row["offerPriceColor"]]
    assert muted_promo_price_rows, "No muted promotional price rows found in loaded promotions slice"

    evidence = {
        "loadedCards": len(rows),
        "manufacturerVeganExamples": [
            {"id": row["id"], "href": row["href"], "buttonText": row["buttonText"], "text": row["text"][:120]}
            for row in manufacturer_vegan_rows[:3]
        ],
        "mutedExamples": [
            {
                "id": row["id"],
                "buttonText": row["buttonText"],
                "imageFilter": row["imageFilter"],
                "offerColor": row["offerColor"],
                "offerPriceColor": row["offerPriceColor"],
                "text": row["text"][:120],
            }
            for row in muted_rows[:3]
        ],
        "officialVeganExamples": [
            {"id": row["id"], "buttonText": row["buttonText"], "text": row["text"][:120]}
            for row in official_rows[:3]
        ],
    }
    print(json.dumps(evidence, indent=2))
    print("promotions page smoke test passed")


def cheese_search_hydration_smoke_test() -> None:
    firefox_options, resolve_current_profile, snapshot_profile = import_firefox_helpers()
    runtime_profile, temporary_root = snapshot_profile(resolve_current_profile().path)
    remove_configured_extensions_from_temp_profile(runtime_profile)

    options = firefox_options(runtime_profile, headless=True)
    service = Service(log_output=str(temporary_root / "geckodriver.log"), env={**os.environ, "MOZ_HEADLESS": "1"})
    driver = webdriver.Firefox(options=options, service=service)
    try:
        driver.set_page_load_timeout(90)
        wait = WebDriverWait(driver, 90)
        driver.get(CHEESE_SEARCH_URL)
        wait.until(lambda d: d.execute_script("return document.readyState") in ("interactive", "complete"))
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'a[href*="/products/violife-non-dairy-cheese-alternative-slices/315701011"]')))
        driver.execute_script(userscript())

        target = None
        rows: list[dict[str, object]] = []
        for _ in range(10):
            time.sleep(0.5)
            rows = collect_rows(driver)
            target = next((row for row in rows if row["id"] == "315701011"), None)
            if target and target["buttonText"]:
                break

        assert target, "Violife cheese product was not found in the loaded cheese search results"
    finally:
        driver.quit()
        shutil.rmtree(temporary_root, ignore_errors=True)

    assert target["buttonText"] == "Add", target
    assert target["blocked"] is False, target
    assert target["nonVeganClass"] is False, target
    assert target["imageFilter"] == "none", target
    assert target["imageOpacity"] == "1", target
    print(
        json.dumps(
            {
                "hydrationVeganExample": {
                    "id": target["id"],
                    "href": target["href"],
                    "buttonText": target["buttonText"],
                    "visibleOfficialIcon": target["official"],
                    "text": target["text"][:160],
                }
            },
            indent=2,
        )
    )
    print("cheese search hydration smoke test passed")


def main() -> None:
    userscript_source_test()
    fixture_smoke_test()
    grayscale_cache_smoke_test()
    cheese_search_hydration_smoke_test()
    promotions_page_smoke_test()


if __name__ == "__main__":
    main()
