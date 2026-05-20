# Coney Island Flood Readiness Data Visualizations

Course: Anthropology 3135: American Urban Experience  
Department of Anthropology, Brooklyn College  
Professor: Christa Paterline  
Student: Mark Fartushniak  
Date: May 20, 2026

## Project

This repository contains the data-processing and visualization files used to generate the flood-readiness figures for the Coney Island fieldwork project.

The project studies Coney Island as a coastal neighborhood where tourism, housing, flood exposure, and uneven preparedness intersect. The figures compare tract-level income context with building-level flood-elevation screening results.

## Repository Layout

```text
data/
  processed/    Building-level public-data screening GeoJSON and metadata
  summary/      Tract-level summary files produced by the processing script

src/
  process_data_and_generate_figures.py

output/
  figures/      Generated PNG figures used in the written project
```

## Data Sources

- NYC Building Elevation and Subgrade
- NYC Building Footprints
- FEMA National Flood Hazard Layer
- U.S. Census Bureau ACS 2024 5-year tract estimates, accessed through Census Reporter release `acs2024_5yr`
- Census Reporter TIGER 2024 tract geometry

## Reproducing the Figures

Install the Python dependencies:

```bash
python3 -m pip install -r requirements.txt
```

Run the processing and figure-generation script:

```bash
python3 src/process_data_and_generate_figures.py
```

The script reads `data/processed/coney-island-buildings.geojson`, fetches TIGER 2024 tract geometry from Census Reporter, fetches fixed ACS 2024 demographic context from Census Reporter release `acs2024_5yr`, and writes updated summary files into `data/summary/` and figures into `output/figures/`.

The generated files are:

- `output/figures/fig_income_vs_vulnerability_maps.png`
- `output/figures/fig_income_tier_vulnerability.png`
- `output/figures/fig_data_methods_summary.png`
- `data/summary/tract_flood_demographic_summary.csv`
- `data/summary/parts_bcd_summary.json`

## Trust and Limitations

This repository is sufficient to regenerate the paper's visualization figures from the included building-level screening GeoJSON and live ACS 2024 tract data.

It does not rebuild the building-level screening GeoJSON from raw NYC and FEMA services. The included GeoJSON should be read as a presentation-screening dataset, not an engineering certification, insurance determination, or flood-code compliance finding.

The processing script validates the bundled building data before generating figures. It fails if:

- feature counts differ from the metadata,
- measured/estimated/unverified counts differ from the metadata,
- FEMA match or BFE-context counts differ from the metadata,
- suspect BES `0/0` records are labeled as trusted measured elevations.

The pipeline intentionally separates measured and estimated first-floor values. Estimates are useful for public presentation and comparison, but they should not be presented as surveyed elevation facts.

The validation check found no suspect BES `0/0` records treated as measured. A small number of BES-measured records remain effectively at grade because `z_floor` and `z_grade` are nearly equal and the BES note reports a successful measurement; these are retained as at-grade screening records, not exact elevation certificates.
