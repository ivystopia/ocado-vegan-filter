**Ocado's built-in vegan labelling is incomplete:** some products are explicitly described as suitable for vegans, or can be identified as vegan from available product data, but do not consistently show Ocado's vegan tag in product grids.

**This script improves the product grid experience by:**

- Keeping recognised vegan products looking normal, with Ocado's usual yellow **Add** button.
- Visually muting known non-vegan products and products without enough evidence for a safe classification, including fading/desaturating the product image and muting promotional red text.
- Restyling the **Add** button as **Not vegan** for affirmative non-vegan classifications or **Unknown vegan** when the available evidence is inconclusive. Both change to **Add anyway** on hover.
- Extending Ocado's built-in vegan product data with 14,090 additional products supported by manufacturer/name evidence or conservative ingredients/product-data classification, for 18,741 recognised vegan products in total.

Ocado's official vegan tag is authoritative and always takes precedence over the script's embedded evidence.
**Not vegan** means the offline audit found affirmative non-vegan evidence; **Unknown vegan** means the stored evidence is insufficient to establish either vegan or non-vegan status safely.

The filter is cosmetic only. Product links still work, and the real Ocado **Add** button remains clickable. The script does not change product pages, basket contents, checkout, prices, or Ocado account data.

Reddit feedback thread: https://redd.it/1t5afpp
