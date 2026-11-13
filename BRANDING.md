# Branding

`icon.png` is Finvalda's own FVS mark, used only to identify this integration with
Finvalda in Flow Steward.

## Source

- Brand: Finvalda (FVS), Finansų valdymo sistemos, UAB — <https://finvalda.lt/>.
- Official asset: the square app icon that finvalda.lt publishes for its own site,
  <https://finvalda.lt/wp-content/themes/finvalda/assets/fav/android-icon-192x192.png>
  (192 × 192 PNG, navy mark on a transparent background).
- SHA-256 of the source: `a9fbaf1327ac9ebfa08ea010f55189747186b76c161ec6b6e7d45680950ff111`
  (downloaded 2026-11-13).

finvalda.lt also publishes a larger site logo
(<https://finvalda.lt/wp-content/themes/finvalda/assets/img/finvalda_logo.png>, 499 × 594),
but it is the mark with the FINVALDA wordmark in black on an opaque white, non-square
background. The square app icon is the brand's own artwork for icon use, and it is
reduced here, not enlarged.

## What was done

- Resized proportionally from 192 × 192 to 128 × 128 with Lanczos resampling (Pillow),
  keeping the transparent background; no other change to colours, strokes or shape:

  ```python
  Image.open("android-icon-192x192.png").convert("RGBA").resize((128, 128), Image.LANCZOS).save("icon.png", optimize=True)
  ```

- SHA-256 of `icon.png`: `dafc106ffc949b2ee459e662e96f76504b1b528abfb9f89efc9e5a4265c15714`.

## Use

Finvalda and the FVS mark belong to Finansų valdymo sistemos, UAB ("© Finansų valdymo
sistemos, UAB. Visos teisės saugomos." — <https://finvalda.lt/>). finvalda.lt publishes no
brand or logo usage guidelines (checked 2026-11-13). The mark is used unaltered, only to
show which product this integration connects to; it does not imply endorsement by
Finansų valdymo sistemos, UAB.
