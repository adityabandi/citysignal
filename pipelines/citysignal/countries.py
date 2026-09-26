"""Explicit market universe, shared by ingestion, geography checks and display.

A curated set of major markets, not a claim of universal country coverage.
ISO codes follow the World Bank country metadata; regions are display groups.
"""
import json
from pathlib import Path

MARKETS = json.loads((Path(__file__).resolve().parents[2] / "config/countries.json").read_text())
COUNTRIES = {code: meta["name"] for code, meta in MARKETS.items()}
ISO3_TO_ISO2 = {meta["iso3"]: code for code, meta in MARKETS.items()}
