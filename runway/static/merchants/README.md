Merchant logos shown on transactions when Plaid has no logo for the merchant (matched by name in `runway/brands.py`,
`MERCHANT_PATTERNS`). They're bundled and served by Runway, so the browser never asks anyone else for them and no
merchant names leave Runway. The logos are trademarks of their owners and are used only to label your own
transactions.

- From [selfh.st/icons](https://github.com/selfhst/icons), licensed under
  [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/): every file not listed below. To add one, save
  `svg/<reference>.svg` from that repository here.
- From [Simple Icons](https://github.com/simple-icons/simple-icons), [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/),
  filled with the brand's color: adidas, airbnb, anthropic, booking-com, burger-king, cash-app, coinbase, expedia,
  h-and-m, hilton, ikea, instacart, kfc, lidl, macys, marriott, mcdonalds, peloton, shell, starbucks, taco-bell, tesla,
  uber-eats, uniqlo, venmo, zara, zelle. To add one, save `icons/<slug>.svg` from that repository here and add
  `fill="#<hex>"` (its color in `data/simple-icons.json`) to the `<svg>` tag.

Then add a pattern for it in `MERCHANT_PATTERNS`. Never add an SVG with scripts, event handlers or links to other
sites (`tests/test_brands.py` checks).
