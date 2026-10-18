# Move your website onto Flower Store In a Box with Claude

This is a free Claude Code skill from Flower Store In a Box. It moves your existing florist website onto your new Flower Store In a Box site for you, without losing your Google rankings.

It works with **Shopify** and **WordPress / WooCommerce** websites. It is the same tooling our own team uses for Standard and Innovator customers, packaged so DIY customers can run it themselves.

## What comes across

- **Categories**, with your names, headings, text, SEO titles and descriptions
- **Products**, with prices (up to 8 price options each), descriptions, main images, galleries and colour options
- **Pages**: About, Contact, Delivery, Terms, Privacy, Refunds and your other content pages
- **Suburb delivery pages**
- **Blog posts**, with their images and categories
- **301 redirects** from every old address to its new home, so Google and your customers land in the right place

Your SEO titles and meta descriptions come across exactly as you wrote them. Your old page addresses, your titles and your page copy are what earned your Google rankings, and all three are kept.

## What you need

1. **Claude Code.** Claude's coding tool, in the Claude desktop app or the terminal. It needs a Claude Pro or Max plan. [Get Claude Code](https://claude.com/claude-code)
2. **Your new site's API key.** In your Flower Store In a Box dashboard go to **Settings > Advanced Settings > API**. Keep it private: it gives full access to your site.
3. **Your old website's address**, for example `https://www.yourshop.com.au`.
4. **Python 3.** Already on most Macs. Claude will check and tell you if it is missing.

## Install

In Claude Code, type these two commands:

```
/plugin marketplace add enflexion/fsiab-site-migration
/plugin install fsiab-site-migration@flowerstoreinabox
```

That's it. Claude Code keeps the skill up to date as we improve it.

<details>
<summary>Installing without plugins</summary>

Download this repository (green **Code** button > **Download ZIP**), unzip it, and copy the `plugins/fsiab-site-migration/skills/migrate-site` folder into `~/.claude/skills/` (on Windows, `%USERPROFILE%\.claude\skills\`). Download it again to update.

Or, if you use git:

```bash
git clone https://github.com/enflexion/fsiab-site-migration.git
```

and run `git pull` in that folder to update.
</details>

## Use it

1. Make a new, empty folder on your computer, for example `Website move`.
2. Open Claude Code in that folder.
3. Type something like: **"Move my old website across to my new Flower Store In a Box site."** Give Claude your old website's address and your new site's API key when it asks.

Claude will then:

1. **Read your old website.** Nothing on your old website is changed.
2. **Look at your new site** to see what's already there.
3. **Make a plan** and open a **review sheet** in your browser showing everything that will come across, what will be left out and why, and anything it needs you to decide.
4. **Wait for you to say go ahead.** Nothing is added to your new site until you've looked at the review sheet and agreed.
5. **Load everything** onto your new site. Allow about an hour for a typical florist website with 50 to 100 products. You can leave it running.
6. **Check every product, page and redirect**, and tell you what needs your attention.

If you change your mind about something afterwards, just ask Claude to change it. It updates the site in place rather than starting again.

## Good to know

- **Do it on your dev site before launch.** Your new site's address ends in `.fsiab.com` until you go live. Your redirects start working once your domain points at Flower Store In a Box.
- **Some things need you.** Gift vouchers (Flower Store In a Box has its own voucher feature), workshops, contact forms, videos and pages built by your designer are listed in the review sheet for you to set up in the new site's own way.
- **Wix, Squarespace and other platforms** aren't supported yet. Contact us and we'll help.
- **Your API key** is only ever typed into Claude Code. It is never saved in a file.

## Need a hand?

Create a support request in your Flower Store In a Box dashboard, call the team on **1300 730 560** or email **[support@enflexion.com.au](mailto:support@enflexion.com.au)**, and get a quick response!

Prefer us to do it? Our Standard and Innovator packages include moving your existing website across for you. [See the packages](https://www.flowerstoreinabox.com.au/pricing/)

---

Flower Store In a Box by enflexion Pty Ltd. See [CHANGELOG.md](CHANGELOG.md) for what's changed.
