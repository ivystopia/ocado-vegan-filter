#!/usr/bin/env python3
"""Benchmark the userscript in clean Firefox using retained Ocado DOMs and images.

Variants are diagnostic ablations, not proposed release implementations.
Replay pages block external resources with CSP. --live measures public Ocado pages
without clicking controls or changing the installed userscript.
"""

import argparse
import hashlib
import json
import os
import platform
import tempfile
import threading
import time
import zipfile
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.support.ui import WebDriverWait

ROOT = Path(__file__).resolve().parents[1]
OUT = None
SOURCE = ""

INSTRUMENT = """
  const audit = window.__audit = {runs: [], processCards:0, hydration:[], canvas:[], classScans:0, pending:()=>scheduled};
  const originalRun = run;
  run = function() {const start=performance.now(); try{return originalRun();}finally{audit.runs.push(performance.now()-start);}};
  const originalProcessCard = processCard;
  processCard = function(card) {audit.processCards++; return originalProcessCard(card);};
  const originalIndex = indexHydrationProducts;
  indexHydrationProducts = function(roots) {const start=performance.now(); try{return originalIndex(roots);}finally{audit.hydration.push(performance.now()-start);}};
  if (typeof grayscaleImageDataUrl === 'function') {
    const originalGrayscale = grayscaleImageDataUrl;
    grayscaleImageDataUrl = function(...args) {const start=performance.now(); try{return originalGrayscale(...args);}finally{audit.canvas.push(performance.now()-start);}};
  }
  const originalClassScan = firstDocumentClassMatching;
  firstDocumentClassMatching = function(...args) {audit.classScans++; return originalClassScan(...args);};
"""

SCOPED_OBSERVER = """new MutationObserver((records) => {
    if(records.some(record => {
      const el=record.target.nodeType===1 ? record.target : record.target.parentElement;
      return el?.closest(CARD_SELECTOR) || [...record.addedNodes].some(n => n.nodeType===1 && (n.matches(CARD_SELECTOR) || n.querySelector(CARD_SELECTOR)));
    })) scheduleRun();
  })"""


def source_for(variant):
    source = SOURCE
    if "no_canvas" in variant:
        assert source.count("      replaceWithGrayscaleImage(image);") == 1
        source = source.replace("      replaceWithGrayscaleImage(image);", "")
    if "no_animation" in variant:
        animation_loop = "      for (const animation of image.getAnimations()) {\n        animation.cancel();\n      }"
        assert source.count(animation_loop) == 1
        source = source.replace(animation_loop, "")
    if "scoped" in variant:
        assert source.count("new MutationObserver(scheduleRun)") == 1
        source = source.replace("new MutationObserver(scheduleRun)", SCOPED_OBSERVER)
    assert source.count("  let scheduled = false;") == 1
    return source.replace("  let scheduled = false;", INSTRUMENT + "\n  let scheduled = false;")


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def start_server():
    handler = partial(QuietHandler, directory=str(OUT))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}"


def prepare():
    fixtures = {name: json.loads((OUT / f"{name}.json").read_text()) for name in ["promotions", "milk"]}
    mapping = json.loads((OUT / "image-map.json").read_text())
    for name, fixture in fixtures.items():
        html = "<!doctype html><html><head><meta charset=\"utf-8\"><meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; img-src 'self' data: blob:; style-src 'unsafe-inline'; script-src 'unsafe-inline'\">"
        html += (
            "<style>"
            + fixture["css"]
            + "</style><style>body{margin:0}#grid{display:grid;grid-template-columns:repeat(5,250px);gap:16px}.audit-wrapper{min-width:0}#unrelated{position:fixed;right:0;top:0}</style></head><body>"
        )
        html += (
            '<header id="unrelated">Header</header>'
            + fixture["template"]
            + '<main id="grid">'
            + "".join('<div class="audit-wrapper">' + c + "</div>" for c in fixture["cards"])
            + "</main></body></html>"
        )
        (OUT / f"{name}.html").write_text(html)
    return fixtures, mapping


def load_case(driver, base, name, fixture, mapping, multiplier=1):
    driver.get(base + "/" + name + ".html")
    driver.execute_async_script(
        """
      const mapping=arguments[0], products=arguments[1], multiplier=arguments[2], done=arguments[3];
      window.__INITIAL_STATE__={data:{products}};
      const grid=document.querySelector('#grid'), initial=Array.from(grid.children);
      for(let n=1;n<multiplier;n++) for(const child of initial) grid.append(child.cloneNode(true));
      document.querySelectorAll('a').forEach(a=>a.addEventListener('click', e=>e.preventDefault()));
      Promise.all([...document.images].map(image=>new Promise(resolve=>{
        const original=image.src;
        image.removeAttribute('srcset'); image.removeAttribute('sizes'); image.loading='eager';
        image.onload=resolve; image.onerror=resolve;
        if(!mapping[original]) throw new Error('Missing image mapping');
        image.src=mapping[original]; image.srcset=mapping[original]+' 200w'; image.sizes='175px';
        if(image.complete) resolve();
      }))).then(()=>requestAnimationFrame(()=>requestAnimationFrame(done)));
    """,
        mapping,
        fixture["products"],
        multiplier,
    )


def read_metrics(driver):
    return driver.execute_script("""
      const {pending, ...a}=window.__audit;
      return {...a, muted:document.querySelectorAll('.ocado-vegan-filter-muted').length,
        loadedImages:[...document.images].filter(i=>i.complete && i.naturalWidth).length,
        converted:document.querySelectorAll('[data-ocado-vegan-filter-grayscale-source]').length,
        failed:document.querySelectorAll('[data-ocado-vegan-filter-grayscale-failed-source]').length,
        dataUrlCharacters:[...document.images].reduce((n,i)=>n+(i.src.startsWith('data:')?i.src.length:0),0),
        states:[...document.querySelectorAll('.product-card-container')].map(c=>({
          muted:c.classList.contains('ocado-vegan-filter-muted'),
          button:c.querySelector('[data-test="counter-button"]')?.textContent,
          disabled:c.querySelector('[data-test="counter-button"]')?.disabled
        }))};
    """)


def mutation_phase(driver, target, iterations=20):
    driver.execute_script(
        "window.__audit.runs=[]; window.__audit.processCards=0; window.__audit.hydration=[]; window.__audit.canvas=[];window.__audit.classScans=0;"
    )
    return driver.execute_async_script(
        """
      const target=arguments[0], iterations=arguments[1], done=arguments[2]; let n=0;
      const ticks=[];let previous=performance.now();
      function step() {
        if(n===iterations) {done({...window.__audit,ticks});return;}
        const el=target==='unrelated' ? document.querySelector('#unrelated') : document.querySelector('.product-card-container');
        el.classList.toggle('audit-mutation');n++;
        requestAnimationFrame(()=>requestAnimationFrame(()=>{
          const now=performance.now();ticks.push(now-previous);previous=now;step();
        }));
      }
      step();
    """,
        target,
        iterations,
    )


def run():
    global OUT, SOURCE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--stress", action="store_true")
    parser.add_argument("--live", action="store_true")
    parser.add_argument(
        "--variants",
        nargs="+",
        choices=["current", "no_canvas", "no_animation", "no_canvas_no_animation_scoped"],
        default=["current"],
    )
    parser.add_argument("--fixture", type=Path, default=ROOT / "benchmarks/firefox-2026-09-04.zip")
    parser.add_argument("--userscript", type=Path, default=ROOT / "ocado-vegan-filter.user.js")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    SOURCE = args.userscript.read_text()
    with tempfile.TemporaryDirectory(prefix="ocado-firefox-benchmark-") as temporary:
        OUT = Path(temporary)
        with zipfile.ZipFile(args.fixture) as archive:
            archive.extractall(OUT)
        run_benchmark(args)


def run_benchmark(args):
    fixtures, mapping = prepare()
    server, base = start_server()
    options = Options()
    options.add_argument("-headless")
    if binary := os.environ.get("FIREFOX_BINARY"):
        options.binary_location = binary
    driver = webdriver.Firefox(options=options)
    driver.set_window_size(1440, 1000)
    driver.set_script_timeout(90)
    results = {
        "browser": driver.capabilities["browserVersion"],
        "platform": platform.platform(),
        "userscript_sha256": hashlib.sha256(SOURCE.encode()).hexdigest(),
        "results": [],
    }
    variants = args.variants
    if args.live:
        try:
            run_live(driver, args)
        finally:
            driver.quit()
            server.shutdown()
        return
    try:
        for repetition in range(args.repeats):
            for name, fixture in fixtures.items():
                for variant in variants[repetition % len(variants) :] + variants[: repetition % len(variants)]:
                    multiplier = 8 if args.stress else 1
                    load_case(driver, base, name, fixture, mapping, multiplier)
                    compile_ms = driver.execute_script(
                        "const t=performance.now();(0,eval)(arguments[0]);return performance.now()-t;",
                        source_for(variant),
                    )
                    driver.execute_async_script("""
                      const done=arguments[0];
                      function settled() {
                        if(window.__audit.pending()) requestAnimationFrame(settled);
                        else requestAnimationFrame(()=>setTimeout(done,100));
                      }
                      requestAnimationFrame(settled);
                    """)
                    initial = read_metrics(driver)
                    assert initial["loadedImages"] == len(fixture["images"]) * multiplier, initial["loadedImages"]
                    unrelated = mutation_phase(driver, "unrelated")
                    single = mutation_phase(driver, "single")
                    row = {
                        "page": name,
                        "cards": len(fixture["cards"]) * multiplier,
                        "variant": variant,
                        "repetition": repetition,
                        "compile_and_init_ms": compile_ms,
                        "initial": initial,
                        "unrelated": unrelated,
                        "single": single,
                    }
                    results["results"].append(row)
                    print(
                        json.dumps(
                            {
                                "page": name,
                                "variant": variant,
                                "rep": repetition,
                                "first": initial["runs"],
                                "initialCanvasMs": sum(initial["canvas"]),
                                "unrelatedMs": sum(unrelated["runs"]),
                                "oneCardMs": sum(single["runs"]),
                            }
                        ),
                        flush=True,
                    )
                    args.output.parent.mkdir(parents=True, exist_ok=True)
                    args.output.write_text(json.dumps(results, indent=2) + "\n")
    finally:
        driver.quit()
        server.shutdown()


def run_live(driver, args):
    results = []
    driver.set_page_load_timeout(60)
    for rep in range(args.repeats):
        for page, url in [
            ("promotions", "https://www.ocado.com/promotions"),
            ("milk", "https://www.ocado.com/search?q=milk"),
        ]:
            for variant in args.variants if rep % 2 == 0 else list(reversed(args.variants)):
                driver.get(url)
                WebDriverWait(driver, 40).until(
                    lambda d: (
                        d.execute_script('return document.querySelectorAll(".product-card-container").length') >= 40
                    )
                )
                time.sleep(2)
                assert not driver.execute_script('return Boolean(document.querySelector("#ocado-vegan-filter-style"))')
                before = driver.execute_script("""return {cards:document.querySelectorAll('.product-card-container').length,
                  elements:document.querySelectorAll('*').length, images:[...document.querySelectorAll('.product-card-container img')].map(i=>({loaded:i.complete && i.naturalWidth>0,w:i.naturalWidth,h:i.naturalHeight}))};""")
                init = driver.execute_script(
                    "const t=performance.now();(0,eval)(arguments[0]);return performance.now()-t;", source_for(variant)
                )
                time.sleep(1)
                initial = read_metrics(driver)
                driver.execute_script(
                    "window.__audit.runs=[];window.__audit.processCards=0;window.__audit.canvas=[];window.__audit.hydration=[];"
                )
                for _ in range(8):
                    driver.execute_script("window.scrollBy(0,650);")
                    time.sleep(0.4)
                time.sleep(0.5)
                scrolled = read_metrics(driver)
                row = {
                    "rep": rep,
                    "page": page,
                    "variant": variant,
                    "before": before,
                    "compile_and_init_ms": init,
                    "initial": initial,
                    "scrolled": scrolled,
                }
                results.append(row)
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps(results, indent=2) + "\n")
                print(
                    json.dumps(
                        {
                            "rep": rep,
                            "page": page,
                            "variant": variant,
                            "cards": before["cards"],
                            "initial": initial["runs"],
                            "canvasMs": sum(initial["canvas"]),
                            "scrollRuns": len(scrolled["runs"]),
                            "scrollMs": sum(scrolled["runs"]),
                            "maxScrollMs": max(scrolled["runs"], default=0),
                        }
                    ),
                    flush=True,
                )


if __name__ == "__main__":
    run()
