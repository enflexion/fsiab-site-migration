#!/bin/zsh
# Edit an already-imported site in place to match a rebuilt manifest.
# Usage: update_site.sh WORKDIR KEY SITE_URL
# Order: rehost new images, sync (edit/rename/delete), load anything new, refresh page content, verify.
set -u
W="$1"; K="$2"; SITE="$3"
cd "$(dirname "$0")"
LOG="$W/update-$(date +%Y%m%d-%H%M).out"
{
  echo "### images";  python3 -u load.py "$W" "$K" --only images
  echo "### sync";    python3 -u sync.py "$W" "$K" --apply
  echo "### load";    python3 -u load.py "$W" "$K" --only categories,products,seo,pages,posts,redirects
  echo "### refresh"; python3 -u load.py "$W" "$K" --only pages --refresh-pages
  echo "### refresh posts and suburb pages (category links)"; python3 -u load.py "$W" "$K" --only posts,seo --refresh-posts --refresh-seo
  echo "### verify";  python3 -u verify.py "$W" "$K" "$SITE" --redirects
  echo "### done"
} > "$LOG" 2>&1
echo "$LOG"
