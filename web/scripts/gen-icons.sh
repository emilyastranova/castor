#!/bin/sh
# App icons for installing Castor (static/manifest.webmanifest, app.html), from static/favicon.svg:
# 192 and 512 px as they are, a full-bleed 512 px "maskable" one (the columns inside the centre
# safe zone, so a circle or squircle crop never cuts them) and Apple's 180 px touch icon.
# Needs ImageMagick 7 (`magick`). Run from web/: sh scripts/gen-icons.sh
set -eu
out=static/icons
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$out"
# The favicon is its rounded background followed by the columns: keep the columns.
glyph=$(sed 's/^<svg[^>]*><rect width="64" height="64" rx="16" fill="[^"]*"\/>//; s/<\/svg>[[:space:]]*$//' static/favicon.svg)
background=$(sed -n 's/.*<rect width="64" height="64" rx="16" fill="\([^"]*\)".*/\1/p' static/favicon.svg)
printf '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" fill="%s"/><g transform="translate(32 32) scale(0.72) translate(-32 -32)">%s</g></svg>' \
	"$background" "$glyph" > "$tmp/maskable.svg"
render() { magick -background none -density "$3" "$1" -resize "$2x$2" -depth 8 "PNG32:$4"; }
render static/favicon.svg 192 600 "$out/icon-192.png"
render static/favicon.svg 512 1600 "$out/icon-512.png"
render "$tmp/maskable.svg" 512 1600 "$out/icon-maskable-512.png"
render "$tmp/maskable.svg" 180 600 "$out/apple-touch-icon.png"
echo "wrote $out/icon-192.png, icon-512.png, icon-maskable-512.png, apple-touch-icon.png"
