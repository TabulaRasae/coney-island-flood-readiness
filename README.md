# Coney Island Flood Readiness Screening Pipeline

Course: Anthropology 3135: American Urban Experience  
Department of Anthropology, Brooklyn College  
Professor: Christa Paterline  
Student: Mark Fartushniak  
Date: May 20, 2026

## Project

This repository contains the public-data processing and visualization files used to generate the flood-readiness figures for the Coney Island fieldwork project.

The project studies Coney Island as a coastal neighborhood where tourism, housing, flood exposure, and uneven preparedness intersect. The figures compare tract-level income context with building-level flood-elevation screening results.

## Repository Layout

```text
data/
  config/       Source URLs, study-area settings, and screening rules
  processed/    Building-level public-data screening GeoJSON and metadata
  summary/      Tract-level summary files produced by the processing script

src/
  build_screening_dataset.py
  process_data_and_generate_figures.py

output/
  figures/      Generated PNG figures used in the written project
```

## Data Sources

- NYC Building Elevation and Subgrade (BES)
- NYC Building Footprints
- FEMA National Flood Hazard Layer (NFHL)
- NYC 2020 Neighborhood Tabulation Areas (NTAs), used only for the project study-boundary context
- U.S. Census Bureau ACS 2024 5-year tract estimates, accessed through Census Reporter release `acs2024_5yr`
- Census Reporter TIGER 2024 tract geometry

## Reproducing the Dataset and Figures

Install the Python dependencies:

```bash
python3 -m pip install -r requirements.txt
```

Rebuild the building-level screening GeoJSON from official public services:

```bash
python3 src/build_screening_dataset.py
```

Generate the tract summaries and figures:

```bash
python3 src/process_data_and_generate_figures.py
```

The raw builder fetches NYC BES records, NYC Building Footprints, FEMA NFHL flood-hazard zones, and FEMA NFHL BFE context for the configured screening area. It writes `data/processed/coney-island-buildings.geojson` and `data/processed/coney-island-buildings.metadata.json`.

The figure script reads the rebuilt GeoJSON, fetches TIGER 2024 tract geometry from Census Reporter, fetches fixed ACS 2024 demographic context from Census Reporter release `acs2024_5yr`, fetches NYC 2020 NTA geometry for the project study boundary, and writes updated summary files into `data/summary/` and figures into `output/figures/`.

The generated files are:

- `output/figures/fig_income_vs_vulnerability_maps.png`
- `output/figures/fig_income_tier_vulnerability.png`
- `output/figures/fig_data_methods_summary.png`
- `data/summary/tract_flood_demographic_summary.csv`
- `data/summary/parts_bcd_summary.json`

## How the Tract Map Is Composed

`fig_income_vs_vulnerability_maps.png` is not a ZIP-code map, parcel map, Google place boundary, or outline copied from the field photographs. It is a census-tract screening map clipped to a project study boundary:

- The project study boundary uses official NYC 2020 NTA polygons for Coney Island-Sea Gate, Brighton Beach, and the bbox-intersecting part of Sheepshead Bay-Manhattan Beach-Gerritsen Beach.
- TIGER 2024 census tracts are selected when they intersect that study boundary.
- Displayed tract geometry is intersected with the NTA-based study boundary so the map follows the Coney Island peninsula context instead of a rectangular bbox.
- Tract colors show tract-level ACS income and tract-level below-BFE share for buildings assigned to each tract.
- The map is projected into local miles before plotting so the east-west and north-south proportions are not distorted by raw longitude/latitude degrees.

The figure is meant to show tract context for the fieldwork geography. NYC states that NTA names roughly correspond with many commonly recognized neighborhoods, but NTAs are not definitive neighborhood boundaries.

## Trust and Limitations

This repository can regenerate the paper's building-level screening GeoJSON and visualization figures from official public services and pinned ACS/Census Reporter release inputs.

The output should still be read as a presentation-screening dataset, not an engineering certification, insurance determination, or flood-code compliance finding. Rebuilding from raw services improves reproducibility, auditability, and freshness; it does not turn public screening data into a certified elevation study.

The raw builder and figure script validate the building data before generating figures. They fail if:

- feature counts differ from the metadata,
- measured/estimated/unverified counts differ from the metadata,
- FEMA match or BFE-context counts differ from the metadata,
- suspect BES `0/0` records are labeled as trusted measured elevations.

The metadata records the raw fetch counts. The latest rebuild fetched:

- 14,895 NYC BES records,
- 17,867 NYC Building Footprints,
- 86 FEMA flood-hazard-zone features,
- 0 FEMA BFE line features in the bbox, with BFE context still populated from FEMA flood-zone `STATIC_BFE` where available.

The pipeline intentionally separates measured and estimated first-floor values. Estimates are useful for public presentation and comparison, but they should not be presented as surveyed elevation facts.

The validation check found no suspect BES `0/0` records treated as measured. A small number of BES-measured records remain effectively at grade because `z_floor` and `z_grade` are nearly equal and the BES note reports a successful measurement; these are retained as at-grade screening records, not exact elevation certificates.
