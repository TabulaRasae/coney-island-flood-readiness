# Coney Island Flood Readiness Data Visualizations

Course: Anthropology 3135: American Urban Experience  
Department of Anthropology, Brooklyn College  
Professor: Christa Paterline  
Student: Mark Fartushniak  
Date: May 19, 2026

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
- U.S. Census Bureau ACS 2024 5-year tract estimates, accessed through Census Reporter

## Reproducing the Figures

Install the Python dependencies:

```bash
python3 -m pip install -r requirements.txt
```

Run the processing and figure-generation script:

```bash
python3 src/process_data_and_generate_figures.py
```

The script reads `data/processed/coney-island-buildings.geojson`, fetches ACS tract geometry and demographic context from Census Reporter, and writes updated summary files into `data/summary/` and figures into `output/figures/`.
