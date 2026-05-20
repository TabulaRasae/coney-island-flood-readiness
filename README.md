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
