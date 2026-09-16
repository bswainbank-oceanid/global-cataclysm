"""Standalone rules/combat engine for Global Cataclysm: 1972.

Pure Python, no rendering, no dependency on tools/ or the map pipeline.
Reads config from data/*.json (units, rules, territories, adjacency,
factions) and exposes one order-submission API (engine.engine.GameEngine)
used identically by a human client and a bot. See docs/GAME_ARCHITECTURE.md
for how this fits into the overall build.
"""
