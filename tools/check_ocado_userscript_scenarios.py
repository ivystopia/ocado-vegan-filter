#!/usr/bin/env python3
"""Check live Ocado layouts in clean Firefox without changing a basket or profile."""

import argparse
import hashlib
import json
import os
import re
import time
from pathlib import Path

from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.support.ui import WebDriverWait

ROOT = Path(__file__).resolve().parents[1]
CARD_AUDIT = (ROOT / "tests/fixtures/ocado_card_audit.js").read_text()
PAGES = [
    ("milk", "/search?q=milk"),
    ("vegan-cheese", "/search?q=vegan%20cheese"),
    ("offers", "/promotions"),
]


def embedded_ids(source, name):
    match = re.search(r"const " + name + r" = new Set\(\s*`(.*?)`", source, re.DOTALL)
    if not match:
        raise ValueError(f"Missing embedded set: {name}")
    return set(re.findall(r"\b\d+\b", match[1]))


def check_rows(state, vegan_ids, nonvegan_ids):
    issues = []
    counts = {"vegan": 0, "nonvegan": 0, "unknown": 0}
    rows = [row for row in state["rows"] if row["id"]]
    for row in rows:
        positive = (
            row["id"] in vegan_ids
            or row["official"]
            or row["hydration"]
            or row["explicitName"]
        )
        expected = (
            "vegan"
            if positive
            else "nonvegan"
            if row["id"] in nonvegan_ids
            else "unknown"
        )
        counts[expected] += 1
        failures = []
        if row["muted"] != (expected != "vegan"):
            failures.append("classification")
        if row["add"]:
            allowed = (
                ["Add"]
                if positive
                else [
                    "Add anyway",
                    "Not vegan" if expected == "nonvegan" else "Unknown vegan",
                ]
            )
            if row["label"] not in allowed or row["marked"] != (not positive):
                failures.append("button appearance")
            if row["buttonOverflow"]:
                failures.append("button text overflow")
            if row["addHit"] is False:
                failures.append("button hit target")
        if (
            not positive
            and row["filter"] is not None
            and ("saturate(0)" not in row["filter"] or row["opacity"] != "0.42")
        ):
            failures.append("image appearance")
        if row["imageLinkHit"] is False:
            failures.append("image link hit target")
        if failures:
            issues.append({"id": row["id"], "failures": failures})
    return rows, counts, issues


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--userscript", type=Path, default=ROOT / "ocado-vegan-filter.user.js"
    )
    parser.add_argument("--widths", type=int, nargs="+", default=[1440, 768, 390])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if any(width < 320 for width in args.widths):
        parser.error("Viewport widths must be at least 320 pixels")
    source = args.userscript.read_text()
    vegan_ids = set().union(
        *(
            embedded_ids(source, name)
            for name in [
                "OFFICIAL_VEGAN_PRODUCT_IDS",
                "MANUFACTURER_OR_NAME_VEGAN_PRODUCT_IDS",
                "INGREDIENTS_VEGAN_PRODUCT_IDS",
            ]
        )
    )
    nonvegan_ids = embedded_ids(source, "KNOWN_NON_VEGAN_PRODUCT_IDS")
    options = Options()
    options.add_argument("-headless")
    options.enable_bidi = True
    if binary := os.environ.get("FIREFOX_BINARY"):
        options.binary_location = binary
    driver = webdriver.Firefox(options=options)
    driver.set_page_load_timeout(45)
    wait = WebDriverWait(driver, 30)
    report = {
        "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "browser": driver.capabilities["browserVersion"],
        "mode": "clean Firefox with injected script",
        "basket_actions": 0,
        "scenarios": [],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    consent_handled = False
    try:
        for width in args.widths:
            # Firefox's outer-window minimum would silently turn 390 into 500.
            driver.browsing_context.set_viewport(
                context=driver.current_window_handle,
                viewport={"width": width, "height": 900},
            )
            assert driver.execute_script("return innerWidth") == width
            for page, path in PAGES:
                driver.get("https://www.ocado.com" + path)
                wait.until(
                    lambda d: d.execute_script(
                        "return document.querySelector('.product-card-container a[href*=\"/products/\"]') !== null"
                    )
                )
                if not consent_handled:
                    try:
                        button = WebDriverWait(driver, 8).until(
                            lambda d: next(
                                (
                                    b
                                    for b in d.find_elements(
                                        By.XPATH,
                                        "//button[normalize-space()='Decline advertising cookies' or @id='onetrust-reject-all-handler']",
                                    )
                                    if b.is_displayed()
                                ),
                                False,
                            )
                        )
                        button.click()
                        wait.until(
                            lambda d: (
                                not any(
                                    e.is_displayed()
                                    for e in d.find_elements(
                                        By.CSS_SELECTOR, ".onetrust-pc-dark-filter"
                                    )
                                )
                            )
                        )
                    except TimeoutException:
                        pass
                    consent_handled = True
                assert not driver.execute_script(
                    "return !!document.getElementById('ocado-vegan-filter-style')"
                )
                driver.execute_script(source)
                for phase in ["initial", "scrolled"]:
                    if phase == "scrolled":
                        for _ in range(6):
                            driver.execute_script("window.scrollBy(0,500);")
                            time.sleep(0.3)
                    state = None

                    def settled(d):
                        nonlocal state
                        state = d.execute_script("return " + CARD_AUDIT)
                        rows, _, issues = check_rows(state, vegan_ids, nonvegan_ids)
                        return rows and state["styles"] == 1 and not issues

                    try:
                        wait.until(settled)
                    except Exception:
                        driver.save_screenshot(
                            str(args.output.parent / "responsive-failure.png")
                        )
                        if state:
                            report["failure"] = {
                                "page": page,
                                "phase": phase,
                                "width": width,
                                "issues": check_rows(state, vegan_ids, nonvegan_ids)[2],
                            }
                            args.output.write_text(json.dumps(report, indent=2) + "\n")
                        raise
                    rows, counts, issues = check_rows(state, vegan_ids, nonvegan_ids)
                    row = {
                        "page": page,
                        "phase": phase,
                        "requested_width": width,
                        "actual_width": state["width"],
                        "cards": len(state["rows"]),
                        "rendered_cards": len(rows),
                        "statuses": counts,
                        "image_link_hits": sum(r["imageLinkHit"] is True for r in rows),
                        "add_button_hits": sum(r["addHit"] is True for r in rows),
                        "issues": issues,
                    }
                    report["scenarios"].append(row)
                    args.output.write_text(json.dumps(report, indent=2) + "\n")
                    print(json.dumps(row), flush=True)
                    if page == "milk" and phase == "initial":
                        driver.save_screenshot(
                            str(args.output.parent / f"milk-{width}.png")
                        )
    finally:
        driver.quit()


if __name__ == "__main__":
    main()
