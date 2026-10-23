#!/usr/bin/env bash
# Move the workout data to a fresh secret gist and delete the old one.
# Use it if the gist's ID ever leaks: a secret gist is readable by anyone who has its ID.
# The app finds the new gist by itself on the next sync (it looks it up by description).
set -euo pipefail
cd "$(dirname "$0")/.."
DESC="Workout tracker data"
OLD=$(gh gist list --limit 100 | awk -F'\t' -v d="$DESC" '$2==d {print $1}' | head -1)
[ -n "$OLD" ] || { echo "No \"$DESC\" gist found."; exit 1; }
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
for f in workout.json health.json reviews.json; do
  gh gist view "$OLD" -f "$f" -r > "$TMP/$f" 2>/dev/null && [ -s "$TMP/$f" ] || rm -f "$TMP/$f"
done
echo "Copying from $OLD: $(cd "$TMP" && ls | tr '\n' ' ')"
NEW_URL=$(gh gist create --desc "$DESC" "$TMP"/*.json)          # secret unless --public is passed
NEW=${NEW_URL##*/}
gh secret set GIST_ID --body "$NEW"
echo "New secret gist created and stored as the GIST_ID repo secret."
read -r -p "Permanently delete the old gist now? [y/N] " a
if [ "$a" = "y" ] || [ "$a" = "Y" ]; then gh gist delete "$OLD" --yes && echo "Old gist deleted. Open the app once on each device so it re-links."
else echo "Kept it. Delete it later at https://gist.github.com/$OLD"; fi
