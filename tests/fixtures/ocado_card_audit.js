(() => {
  const roots = ["__INITIAL_STATE__", "__QUERY_INITIAL_STATE__", "__staticRouterHydrationData", "__staticRouterHydrationData__"]
    .map((k) => window[k])
    .filter((v) => v && typeof v === "object");
  const seen = new WeakSet(),
    products = new Map(),
    stack = roots.slice();
  const veganAttributes = (attrs) =>
    Array.isArray(attrs) &&
    attrs.some(
      (a) =>
        a &&
        typeof a === "object" &&
        [a.icon || a.file, a.label].some(
          (v) =>
            String(v || "")
              .trim()
              .toLowerCase() === "vegan",
        ),
    );
  while (stack.length) {
    const v = stack.pop();
    if (!v || typeof v !== "object" || seen.has(v)) continue;
    seen.add(v);
    if (v.retailerProductId) products.set(String(v.retailerProductId), v);
    for (const x of Object.values(v)) if (x && typeof x === "object") stack.push(x);
  }
  const cards = [
    ...new Set(
      [...document.querySelectorAll('.product-card-container,[data-test^="fop-wrapper:"],[data-testid^="fop-wrapper:"]')].map((c) =>
        c.matches(".product-card-container") ? c : c.querySelector(".product-card-container") || c,
      ),
    ),
  ];
  const inView = (r) => r.width > 0 && r.height > 0 && r.left >= 0 && r.right <= innerWidth && r.top > 230 && r.bottom < innerHeight;
  const rows = cards.map((c) => {
    const a = c.querySelector('a[href*="/products/"]'),
      b = c.querySelector('button[data-test="counter-button"],button[data-testid="counter-button"]'),
      image = c.querySelector('.image-container img, img[data-test="lazy-load-image"],a[href*="/products/"] img');
    const id =
      String(c.getAttribute("data-test") || c.getAttribute("data-testid") || "").match(/(?:^|:)(\d+)(?:$|:)/)?.[1] ||
      String(a?.href || "").match(/\/products\/(?:[^/?#]*[-/])?(\d+)(?:\/details)?(?:[/?#]|$)/)?.[1] ||
      null;
    let title = "";
    for (const sel of [
      '[data-test="fop-title"]',
      '[data-testid="fop-title"]',
      '[data-test="product-title"]',
      '[data-testid="product-title"]',
      'a[data-test="fop-product-link"]',
      'a[data-testid="fop-product-link"]',
      'a[href*="/products/"]',
    ]) {
      title = c.querySelector(sel)?.textContent.trim() || "";
      if (title) break;
    }
    const slug =
      String(a?.getAttribute("href") || "")
        .match(/\/products\/([^/?#]+)/)?.[1]
        ?.replace(/[-_]+/g, " ") || "";
    const official =
      !!c.querySelector('[data-test="product-card-lifestyle-vegan"],[data-testid="product-card-lifestyle-vegan"],svg#vegan,svg[data-icon="icon__vegan"]') ||
      [...c.querySelectorAll("use")].some((u) => (u.getAttribute("href") || u.getAttribute("xlink:href") || "").endsWith("#vegan"));
    const p = products.get(id),
      muted = c.classList.contains("ocado-vegan-filter-muted"),
      label = b?.textContent.trim(),
      add = !!b && /^Add\b/i.test(b.getAttribute("aria-label") || "");
    const imageRect = image?.getBoundingClientRect(),
      buttonRect = b?.getBoundingClientRect();
    return {
      id,
      official,
      hydration: !!p && (veganAttributes(p.attributes) || veganAttributes(p.iconAttributes)),
      explicitName: /\bvegan\b/i.test(title + " " + slug),
      muted,
      label,
      add,
      disabled: !!b?.disabled,
      marked: !!b?.classList.contains("ocado-vegan-filter-muted-add"),
      buttonOverflow: add && b.scrollWidth > b.clientWidth,
      filter: image && getComputedStyle(image).filter,
      opacity: image && getComputedStyle(image).opacity,
      imageLinkHit:
        imageRect && inView(imageRect) && image.complete && image.naturalWidth
          ? !!document.elementFromPoint(imageRect.x + imageRect.width / 2, imageRect.y + imageRect.height / 2)?.closest('a[href*="/products/"]')
          : null,
      addHit:
        add && inView(buttonRect)
          ? document.elementFromPoint(buttonRect.x + buttonRect.width / 2, buttonRect.y + buttonRect.height / 2)?.closest("button") === b
          : null,
    };
  });
  return {
    url: location.origin + location.pathname + location.search,
    width: innerWidth,
    height: innerHeight,
    timeOrigin: performance.timeOrigin,
    styles: document.querySelectorAll("#ocado-vegan-filter-style").length,
    legacyImageMarkers: document.querySelectorAll("[data-ocado-vegan-filter-image]").length,
    rows,
  };
})();
