# Stored-claim routing impact

This inspection opened `ocado_products.sqlite` read-only. It first screened stored product-information fields and typed manufacturer-evidence rows containing vegan wording, then compared the 884 candidate rule-unknown products against classifier v32. All 884 now defer semantic assessment; 842 have the stored-current flag and 781 were previously positive under the historical canonical policy. Current flags are stored catalogue state, not a new live refresh.

`impact.json` retains exact IDs, prior status/reason, candidate fields and counts. The full catalogue baseline remains 54,418 all-known products and 49,951 stored-current products. These newly deferred rows are not measured promotions or newly completed unknowns.

The safety argument is that no positive decision follows from this token screen. Two independent evidence assessments must establish whole-product applicability, and every accepted result still passes the source-aware policy gate. Definite animal evidence, identity blocks, explicitly excluded components and simple statements solely about another product keep their conclusive handling. An unfamiliar applicable manufacturer assertion is therefore assessed rather than discarded by a narrow fast grammar. No canonical row or embedded product ID was changed.
