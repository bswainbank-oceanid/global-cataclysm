# GLOBAL CATACLYSM: 1972 — Visual Style Guide

## 1. Brand character

The visual identity should feel like a fictional **Cold War strategy game produced in the 1970s**: military, geopolitical, bold, printed, and poster-like.

**Keywords:** Cold War · military · geopolitical · 1970s · high contrast · flat silhouettes · printed poster.

Avoid glossy modern UI, neon, glass effects, excessive gradients, and oversized rounded controls.

## 2. Core palette

| Color | Hex | Use |
|---|---|---|
| Deep Navy | `#0B2433` | Main text, silhouettes, UI |
| Navy Dark | `#071923` | Dark backgrounds |
| Navy Light | `#183D4F` | Secondary navy |
| Signal Red | `#C92A24` | Conflict, emphasis, primary CTA |
| Red Dark | `#9E201C` | Borders/shadows |
| Red Light | `#E3483F` | Highlights |
| Warm Cream | `#F4EBDD` | Poster/page background |
| Paper | `#E9E1D3` | Game surfaces |
| White | `#FFFDF8` | High contrast |
| Gray | `#6B747A` | Secondary text |

A useful overall balance is approximately **60% cream/paper, 25% navy, 10% red, 5% secondary colors**.

## 3. Typography

Use a heavy condensed display face for titles:
- Barlow Condensed Black / ExtraBold
- Roboto Condensed Black
- Arial Narrow Bold
- Impact as fallback

Titles are **ALL CAPS**, heavy, tightly leading, and generally stacked:

**GLOBAL**  
**CATACLYSM**  
**1972**

Make **CATACLYSM red** when the full wordmark is present.

Use Inter/Helvetica/Arial for body copy.

## 4. Logo

The 6F poster logo is the primary identity. Large formats can retain the globe, aircraft, tank, carrier, red rays, and stacked wordmark.

At small sizes simplify aggressively:
- remove text
- remove fine geographic detail
- remove texture
- retain the strongest silhouettes
- preserve strong contrast

## 5. Faction colors

Faction color is the **only color system used for military units**.

| Faction | Color |
|---|---|
| NAA | `#173F78` |
| UE | `#B68A20` |
| UER | `#A92B29` |
| PAF | `#3C7A35` |
| GPC | `#6A3788` |
| AAC | `#D8781B` |

Faction icons are flat, single-color symbols and should remain recognizable at **20×20 px**.

## 6. Unit icon system

There is **no unit-specific color palette**.

Every unit uses the color of its **owning faction**. The unit's identity comes from its silhouette, not its color.

For example:
- NAA Infantry = NAA blue
- NAA Armor = NAA blue
- NAA Carrier = NAA blue
- GPC Infantry = GPC purple
- GPC Fighter = GPC purple
- GPC Carrier = GPC purple

This creates a visually coherent map where **color communicates political ownership** and **shape communicates military type**.

### Unit silhouettes

The nine unit types are:

1. Infantry
2. Mech Infantry
3. Armor
4. Fighter
5. Bomber
6. Carrier
7. Submarine
8. Cruiser
9. Transport

Silhouettes must remain distinct without relying on color.

In particular:
- Infantry vs. Mech Infantry: different posture/equipment silhouette
- Mech Infantry vs. Armor: humanoid/mechanical form vs. tank profile
- Fighter vs. Bomber: compact swept-wing aircraft vs. larger/heavier aircraft
- Carrier vs. Cruiser: large flat flight deck vs. conventional warship superstructure
- Submarine vs. surface ships: low underwater hull silhouette
- Transport: recognizable cargo/transport profile

At **20×20 px**, remove decorative detail, rings, terrain, water, tiny people, and other elements that become visual noise.

## 7. UI

### Buttons

Primary:
- red fill
- cream/white text
- heavy condensed uppercase type
- small radius
- hard offset shadow

Secondary:
- navy fill
- cream text

Avoid pill-shaped controls and glossy gradients.

### Panels

Use cream/white backgrounds, 2px navy borders, small radii, and restrained shadows.

### Rules

2–3px horizontal navy/red rules. Double rules can frame years and major headings.

## 8. Map UI

The map should carry the strongest strategic visual identity.

Recommended:
- dark navy interface chrome
- territory colors based on faction ownership
- unit icons colored by owning faction
- cream/white labels
- red for active conflict/alerts
- clear silhouettes for unit type

**Color = faction. Shape = unit.**

This rule should be consistent everywhere in the game.

## 9. Texture

Use subtle print texture only on large promotional surfaces, splash screens, title screens, and loading screens.

Do **not** texture:
- 20px icons
- unit counters
- faction symbols
- dense gameplay UI
- tooltips

At small sizes, texture becomes noise.

## 10. Shape language

Prefer angular silhouettes, wedges, rays, horizontal bands, and strong symmetry.

Avoid:
- soft rounded geometry
- glossy effects
- hairline icons
- modern "tech" visual language

## 11. CSS implementation

`global-cataclysm.css` contains the reusable design tokens and components.

Example:

```html
<link rel="stylesheet" href="/css/global-cataclysm.css">

<h1 class="gc-title">
  GLOBAL <span class="accent">CATACLYSM</span>
</h1>

<button class="gc-button">Start Game</button>

<!-- Unit color comes from its faction, not its type. -->
<img class="gc-faction--naa gc-unit-icon gc-unit-icon--20"
     src="icons/armor.svg" alt="NAA armor">
```

The important implementation rule is:

```text
UNIT TYPE → determines silhouette
FACTION    → determines color
```

## 12. Guiding principle

> **Make it look like a game produced in 1972, not a game about 1972 produced in 2026.**

And for the map:

> **Color tells you who owns it. Shape tells you what it is.**
