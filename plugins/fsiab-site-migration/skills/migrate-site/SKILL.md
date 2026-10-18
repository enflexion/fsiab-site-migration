---
name: migrate-site
description: Move a florist's existing Shopify or WordPress/WooCommerce website onto their new Flower Store In a Box site through the Flower Store In a Box API, carrying over categories, products (prices, images, galleries, SEO), pages, suburb pages, blog posts and 301 redirects. Use when the user says "migrate my site", "import my old website", "move my products across", "move off Shopify", "move off WordPress" or "move off WooCommerce".
---

# Move an existing florist website onto Flower Store In a Box

## Goal
Get the florist's new Flower Store In a Box site to parity with their old website in an afternoon, without losing their Google rankings. Rankings live in three things, and all three come across: the page addresses (through 301 redirects), the SEO titles and meta descriptions they wrote, and their page copy.

Work with the florist in plain English. They are not developers. Explain what you are about to do before each step, and never load anything onto their site until they have looked at the review sheet and said go ahead.

## What you need before starting
1. **The old website's address.** Their live Shopify or WordPress/WooCommerce site.
2. **The API key for the new site.** In the Flower Store In a Box dashboard: Settings > Advanced Settings > API. This is usually their dev site (`something.fsiab.com`) before launch.
3. **Python 3.** Check with `python3 --version`. The scripts use only Python's standard library, nothing to install.
4. **A fresh or near-fresh new site.** The template pages and categories that come with a new site are fine; the build step spots clashes and asks what to do.

**The API key gives full access to the site.** Never write it into any file, never paste it into the review sheet or a message, and pass it to the scripts on the command line only. If the florist pastes it into the chat, use it, but remind them to keep it private.

## Where things live
- The scripts are in the `scripts/` folder next to this file. Run every command from inside that folder.
- Create a work folder for this migration in the folder Claude Code was opened in, for example `./site-migration/`, and pass its full path as `WORK`. It will hold `raw/`, `source.json`, `decisions.json`, `manifest.json`, `state.json` (every ID created, used to resume), `load-log.jsonl` and `verify.json`.
- Keep the work folder until the new site has launched. It is how a load resumes after an interruption and how changes are made later.

## The steps

### 1. Work out the platform
- WordPress: `SITE/wp-json/` answers with a list of namespaces. It is WooCommerce if `wc/store/v1` is listed, and uses Yoast SEO if `yoast/v1` is listed.
- Shopify: `SITE/products.json` answers.
- Anything else (Wix, Squarespace, Square Online and so on): not supported by this skill yet. Say so, and suggest the florist contacts Flower Store In a Box support.

### 2. Look at the new site
```bash
python3 snapshot_target.py KEY
```
Note the existing categories, the placeholder pages (content equal to the title, section 'Next Pages'), and the designer pages (pages built from a `dynamic` section such as Hero Banner Florist Story, Contact Card, Premium Event Florals). Never fill a designer page: skip the old page, redirect its old address to the designer page, and leave its copy in the review sheet so the florist can decide what to do with it.

### 3. Read the old website (read only, slowed down to be polite)
```bash
# WordPress / WooCommerce
python3 wp_fetch.py https://site.com.au "WORK"
python3 wp_cpt_fetch.py https://site.com.au "WORK" location wedding event   # only if the site has custom post types (check SITE/wp-json/wp/v2/types)
python3 wp_seo_fetch.py "WORK"     # only if Yoast is NOT listed (Rank Math, AIOSEO, SEOPress): reads the live <title> and meta description; slow on uncached sites
python3 wp_extract.py "WORK"
# Shopify (about 2.5 seconds per request; a 200-page store takes 15 to 20 minutes)
python3 shopify_fetch.py https://store.com.au "WORK"
```
Both write the same `WORK/source.json`. Tell the florist roughly how long it will take.

WordPress notes:
- Custom post types (suburb pages under `/location/`, real weddings, events, awards) become pages in source.json with `post_type` set, so `page_rules` can route them. Suburb post types often use addresses like `x-same-day-flower-delivery` or `flower-delivery-x`; write a rule for each pattern and an explicit `pages` entry for odd ones. Gallery-style post types (weddings, events) usually keep their photos in custom fields the WordPress API does not expose; skip them and redirect them to the new site's weddings or events page.
- Category membership is merged from `wp/v2/product_cat`, because the Store API can hide categories.
- wp_fetch removes duplicates from Store API paging drift and fetches any product it missed by id.

### 4. Write decisions.json (this is the thinking part)
Read `source.json` and the snapshot of the new site, then write `WORK/decisions.json`. Ask the florist whenever a decision is theirs to make (for example whether a category is still wanted). Keys:

- `site_name`, `location`, `source_url`, `import_batch` (for example `shopname-shopify-2026-10-18`), `single_price_label` (default "Standard").
- `categories`: one entry per source category slug.
  - Import: `{"cat": "occasion|flower|addon"}`. Names, SEO titles and meta descriptions come across **exactly** as the florist had them; do not rename or rewrite them. Only `override_name`, `override_title` and `override_description` change them, and only when the florist asks. A title is generated only when the old site had none.
  - **Category names hold letters, digits and spaces only.** The name builds the category address and the shop looks the category up by it, so `&`, `'`, `$`, `-` and the like break the page. build.py applies this automatically: `&` becomes "and", apostrophes are dropped, anything else becomes a space ("Candles & Diffusers" becomes "Candles and Diffusers"). The florist's exact wording, with the `&` or `'`, goes in the heading, and their SEO title is kept as is. Each renamed category is listed as a WARN.
  - `cat` decides the address (occasion `/x-flowers/`, flower `/flower-x/`, addon `/addons/x/`) and, for `addon`, that the products are tick-box extras. **addon = small extras only** (chocolates, candles, balloons, cards, wine, teddies). Hampers, plants and gift sets sold as main products are `flower` (a product-type category). Occasions are `occasion`.
  - `"landing_page": "page-slug"`: on WordPress the real category page is often a normal page (for example `/blooms/` with a product grid) rather than `/product-category/blooms/`. Set it and the category takes that page's content (product grid removed), SEO title and meta description, and every product the page showed; the page itself is not created and its address redirects to the category. Check every page with a product grid for this.
  - **Price-band categories are never imported** ("Flowers Under $100", "$60 & Under", "Over $150", "$80-$120"). Flower Store In a Box builds these automatically from the price at `/under-N/`, `/over-N/` and `/N-M/`. Skip the category and redirect its old address to the matching page: `{"skip": "price band; built-in feed", "redirect_to": "/under-100/"}`. Pick N to match the florist's own threshold.
  - Map onto a category the new site already has: `{"map_to_existing": "Mothers Day"}`.
  - Skip: `{"skip": "reason", "redirect_to": "/path" | "redirect_to_category": "Name" | "redirect_to_product": "slug"}`. Skip empty and "Uncategorized" categories, but always redirect their old address somewhere sensible.
- `pages`: per source slug. `{"skip": "reason", "redirect_to": "/"}` for system pages (cart, checkout, account, wishlist, shop index, blog index, home page). `{"fill_placeholder": "about"}` to send a page into a given page on the new site.
  - Automatic: an old page (or Shopify policy) whose address or usual name matches a page on the new site (about, contact, terms, privacy, refund, delivery, weddings) fills it if it is a placeholder, and is skipped with a redirect if it is a designer page; its copy is listed in the review sheet.
- `page_rules`: regex rules over page slugs, first match wins.
  - `{"match": "-copy$", "skip": "draft copy"}`
  - `{"match": "^copy-of-(?P<of>.+)$", "alias_of": "\\g<of>"}` redirects a true duplicate to wherever its original went. Check the original exists first; if not, the copy is the real page.
  - `{"match": "^(copy-of-)?florist-(?P<suburb>[a-z0-9-]+?)(-nsw.*)?$", "suburb_page": {"city": "Sydney"}}` routes suburb landing pages into Flower Store In a Box suburb pages.
- `products`: per source slug, `{"skip": true}` or `{"note": "..."}` for anything to flag (gift vouchers: Flower Store In a Box has its own voucher feature). `{"keep": true}` imports a product the voucher or workshop rules would skip, for example greeting cards named "Gift Card".
- `fill_placeholder`: give the new site's page **slug** (`"about"`), not its id. An id forces the fill and skips the designer-page check.

### 5. Build the plan and review it with the florist
```bash
python3 build.py "WORK" KEY
python3 review.py "WORK" "WORK/Site Migration Review.html"
```
Read every WARN line and every product note yourself first. Then open the review sheet for the florist (`open "WORK/Site Migration Review.html"` on a Mac, `start` on Windows) and walk them through it in plain English: what will come across, what will be skipped and why, and anything that needs their decision. **Do not load anything until the florist says go ahead.** Change decisions.json and rebuild as often as they like.

### 6. Dry run, then load
```bash
python3 load.py "WORK" KEY --dry-run
python3 -u load.py "WORK" KEY          # run it in the background
```
Loading takes a while: about 1 minute per product with a gallery, 10 seconds per image and 5 seconds per suburb page (77 products and 200 suburb pages took about 70 minutes). Tell the florist, and give them progress updates.

Order: categories, products (main image, then gallery one file at a time), content images, suburb pages, pages, blog posts, redirects. `--only categories,products,images,seo,pages,posts,redirects` runs a subset. It resumes from `state.json` after any failure, so if it stops, just run the same command again. `--refresh-pages` re-pushes page content.

### Changing a site that is already loaded (better than starting again)
After a decisions change, rebuild the plan and edit the site in place. Keep the plan the site was loaded from as `manifest-run1.json` first (sync uses it to spot renamed categories).
```bash
cp "WORK/manifest.json" "WORK/manifest-run1.json"     # only if it is not already there
python3 build.py "WORK" KEY --assume-clean             # plan, ignoring what this import created
python3 sync.py "WORK" KEY                             # lists every edit, delete and create; show the florist
./update_site.sh "WORK" KEY https://yoursite.fsiab.com # images, sync --apply, load new items, refresh, verify
```
sync.py updates categories (renamed ones are recreated and products relinked), products (name, copy, SEO, all 8 price tiers, stock, address, categories, alcohol; IDs, images and galleries kept) and removes products and pages that no longer belong. It is safe to run again. `update_site.sh` needs a Mac or Linux shell; on Windows run the commands inside it one at a time.

### Starting again
```bash
python3 rollback.py "WORK" KEY                      # lists what it would delete
python3 rollback.py "WORK" KEY --yes --keep seo     # deletes; keeps suburb pages and uploaded images
```
Only run the `--yes` version after the florist has seen the list and agreed. Products go to the dashboard trash first. Use `--all-redirects` only on a dev site whose redirects all came from this import. Then rebuild with `build.py WORK KEY` and load again.

### 7. Check everything
```bash
python3 verify.py "WORK" KEY https://yoursite.fsiab.com --redirects
python3 review.py "WORK" "WORK/Site Migration Review.html"
```
Checks every product (prices, labels, categories, gallery count, page title, images showing), every category page, every page (and whether anything still points at the old website), a sample of content images, and every redirect.

## Rules the scripts follow (so you can explain them)
- **Prices.** Up to 8 price tiers per product. If a product's combinations fit, every combination is a tier (it is also how the customer chooses). Over 8: options that never change the price are dropped and listed; then the least-conflicting option is merged at the lowest price and the product is flagged CHECK.
- **WooCommerce options listed but never priced** are not imported; they are noted.
- **Images.** The first image is the product image; the rest go to the gallery in the same order. Everything is converted to JPEG. Images inside page content are copied into the new site's image library and the links updated.
- **Page layout code.** Page-builder markup (Elementor, Divi, Shopify sections) is stripped to clean headings, paragraphs, lists, links, images and tables. Forms, videos and background banners are dropped and listed, to be added back in the new site's own way.
- **Links.** Links between pages are updated to the new addresses. Phone and email links are kept.
- **Category text** comes across only if it was visible on the old category page. Shopify themes often hide the collection description, and importing it would add text that was never on the site.
- **Colours.** A Shopify option named colour, or whose values are mostly colours, becomes Flower Store In a Box colour options (with the variant photo) instead of price tiers.
- **Add-ons.** A product in an add-on category with several prices is split into one add-on per price ("Balloons - Helium"), because add-ons are one tick at one price.
- **Alcohol.** Products containing wine, champagne, beer, spirits and so on are marked as alcohol. They are listed in the review sheet for checking.
- **Gift vouchers and gift cards** are not imported as products; Flower Store In a Box has its own voucher feature. The old address redirects to the voucher page. Listed under "Needs setting up by hand".
- **Workshops** belong in the Flower Store In a Box Workshops feature, not the shop. Not imported; the copy is kept in the review sheet; listed under "Needs setting up by hand".
- **Blog posts** are imported to `/blog/{slug}/` with their featured image, content images and categories; old post addresses redirect.
- **Policies.** Shopify's `/policies/*` pages (terms, privacy, refund, shipping) fill the new site's Terms, Privacy, Refund and Delivery pages when those are placeholders.
- **Product addresses.** The old address is kept unless it is a draft copy (`-copy`, `copy-of-`), an options-app product (`option-set-`), or shares no words with the product name; then the address comes from the name and the old one is redirected.
- **Reserved addresses.** gallery, tags, blog, flowers, occasions, addons, the price bands, cart, checkout and similar are built-in Flower Store In a Box pages. An old page there becomes `/our-x`.
- **Suburb pages.** Matched to the new site's existing suburb page by suburb and city and updated, otherwise created at `/{city}-flowers/{suburb}.shtml`.
- **Redirects** are saved as site-relative paths, so they keep working when the site moves from the dev address to the florist's own domain.

## Known limits (report them, do not work around them)
- The API can return text it was sent with special characters (accents, curly quotes) double-encoded. The website itself shows it correctly; verify.py and sync.py allow for it. Never read text back through the API and send it again.
- Images that were already broken on the old website are left out and listed in `state.json` under `broken_images`.
- Creating products and categories takes about 5 seconds each, and each image about 30 seconds.
- Pages answer without a trailing slash; products and categories only with one. The redirects cover the difference.
- A category name containing the digit 0 shows no products. Check any category name with a 0 in it.
- A gallery upload that ends in "read operation timed out" may still have been saved. If verify then shows one more gallery image than the old site, delete the extra one.
- Blog header images and colour images can overwrite each other in rare cases. The loader skips a blog image that would clash and flags it.

## When it is finished
Tell the florist, in plain terms:
- what came across, with the numbers;
- what needs them (the review sheet's notes: designer pages, vouchers, workshops, forms, videos, anything flagged CHECK);
- any images that could not be copied;
- that the redirects only start working once their domain points at Flower Store In a Box, at launch.

Then suggest they look over the new site page by page, and point them to Bloomkeeper, which takes over getting them found on Google and in AI answers once the site is live on their domain.

If something goes wrong that you cannot fix, tell them to create a support request in their dashboard, call 1300 730 560 or email support@enflexion.com.au, and include the work folder's `load-log.jsonl`.
