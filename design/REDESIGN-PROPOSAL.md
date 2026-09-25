# Addify redesign proposal: Apple clean, premium like Linemate, no emojis

For Roham to approve before any code changes. The live app (engine/crate.html) is
deliberately left untouched in this job. Nothing here has shipped.

## The direction

Target an Apple Human Interface look crossed with the premium dark feel of Linemate.
That means:

- A near black canvas (#0B0B0F), not the current purple washed dark. Flat, no big
  radial glow bleeding across the screen.
- One restrained accent, our brand purple #6B5FE0, used only where it earns attention
  (scan button, primary buttons, active tab). Mint green stays for success only, never
  on a normal control.
- SF Pro type with Apple large titles at the top of each tab. Tight letter spacing on
  big headings, secondary gray (#9A9AA2) for support copy.
- Hairline dividers between list rows instead of a stack of heavy separate cards.
- Real content (artwork, song rows) instead of empty states and repeated logos.
- No emoji anywhere. Every icon is a drawn line glyph at one consistent stroke weight,
  the way SF Symbols behave.

Preview mockups live next to this file. The `.svg` files are the viewable previews
(open them in any browser or image viewer); the `.html` files are the interactive source
they were drawn from:
- Home: `preview/home.svg` (source `preview/home.html`)
- Onboarding: `preview/onb.svg` (source `preview/onb.html`)
- Search: `preview/search.svg` (source `preview/search.html`)

The three preview screens carry the pattern that then repeats across every face.

---

## Per screen: what reads as vibe coded, and the fix

### 1. Home
What reads as vibe coded now:
- The big glowing purple orb floating dead center is the number one signature of a
  generated app mockup. The blurred halo behind it makes it worse.
- The squiggle mark appears three times on one screen (header, scan orb, playlist card
  avatar). Repeating the one asset where real art should go reads like placeholder.
- The playlist card play button is mint green on a purple card, a color clash.
- Trending rows are fine now that they load real artwork, but the ranking chips and the
  round plus buttons all sit on separate heavy cards, which looks busy.

Proposed changes:
- Flatten the scan button to a clean solid purple disc, drop the halo. Keep the wave
  glyph.
- Apple large title "Addify" top left, a quiet "5 scans" pill top right.
- Playlist card gets a real 2x2 artwork mosaic and a purple play button, not mint.
- Trending becomes divider rows on one surface instead of four floating cards.
- See `preview/home.svg`.

### 2. Search
What reads as vibe coded now:
- The subhead "Lost the reel? Describe it instead." is in bright accent purple, which
  reads templated.
- Three near identical cards, each a pill plus gray helper plus big bold example plus a
  chevron, is the repetitive card list pattern.
- The leading glyphs are emoji or emoji like: a quote mark, a sparkle, an @. The sparkle
  is exactly the cheap emoji next to real icons problem. Their weight does not match the
  SF chevrons.
- The bottom "What comes back" block looks identical to the tappable rows but is not
  tappable, so the hierarchy is confusing.

Proposed changes:
- Drop the subhead to secondary gray.
- Replace the emoji with drawn line icons (lines for lyric, a small waveform for vibe, a
  person for creator) at the same stroke weight as the chevrons.
- Turn the three cards into clean divider rows, the Apple settings and Linemate list
  pattern.
- Make the info block visually distinct so it does not look like a fourth button.
- See `preview/search.svg`.

### 3. Finds
What reads as vibe coded now:
- An empty screen with floating filter pills over a dashed placeholder box looks
  unfinished and broken, not intentional.

Proposed changes:
- Replace the dashed box with a solid, quiet empty state, and show one greyed sample row
  so a new user sees the shape of a find instead of a dotted rectangle.
- Once finds exist, rows use artwork, title, artist and a version chip, grouped by day,
  matching the same row style as Home recent finds.

### 4. You (Profile)
What reads as vibe coded now:
- A long stack of identical full width rounded rows, all the same height and color, is
  the generated settings list look.
- The upgrade card doubles a purple outline with a purple button.

Proposed changes:
- Group rows into fewer sections with hairline dividers inside one surface, Apple
  Settings style, instead of many separate pills.
- Give the upgrade card a single clear emphasis (one filled treatment), not outline plus
  fill.
- Keep the real brand icons (Spotify, Apple Music), they already look right.

### 5. Trending
What reads as vibe coded now:
- Every row is its own heavy card, so the list looks busy and boxy.
- The loading state is a dashed placeholder box, which reads unfinished.

Proposed changes:
- Rows become divider rows on one surface, rank number, artwork, title, artist, a plus.
- Loading shows skeleton rows that match the real row shape, not a dashed box.

### 6. Onboarding (all four steps: pitch, three taps, connect, pin)
What reads as vibe coded now:
- Every onboarding card floats over a blurred screenshot of the home screen, with the
  orb bleeding through. That blurred app behind a modal is the single most templated
  onboarding look there is, and it repeats on all four steps.
- The pitch step is the textbook icon tile, headline, subtext, button stack, centered.

Proposed changes:
- Remove the blurred home background on all four steps. Solid canvas, full screen
  layout, the product mark floating in clean space.
- Apple large headline, muted subtext, one filled capsule button, page dots.
- Drop the stray "1" badge on the Addify tile in the pin step.
- Keep the real brand icons (Instagram, Spotify, Apple, SoundCloud, Messages, Mail).
- See `preview/onb.svg`.

---

## The three tells that run across every face
1. The blurred home screenshot behind all four onboarding cards.
2. Dashed placeholder boxes on Home, Finds and Trending.
3. Emoji and mismatched glyphs, plus default system type with accent purple subheads.

## Build order once approved
1. Flatten the palette to near black with a single purple accent, remove the radial glow.
2. Remove the blurred home background from all four onboarding steps.
3. Replace every dashed placeholder box with real rows or clean solid empty states.
4. Remove all emoji, replace with drawn line icons at one stroke weight.
5. Recolor the home play button from mint to purple, keep mint for success only.
6. Adopt Apple large titles and hairline divider lists across the tabs.
7. Real artwork mosaics on cards instead of the repeated squiggle mark.
