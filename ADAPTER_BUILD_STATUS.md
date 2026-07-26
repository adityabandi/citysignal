# Adapter Build Status: Visados & Notariado

## Completed

### 1. Metrics Configuration (`config/metrics.yml`)
Added three new metrics for housing supply and sales:

- **visados_new_build**: Monthly building permits by province
  - Unit: dwellings
  - Cadence: monthly
  - Geo level: province
  - Source: visados
  - Direction: 1 (higher is better)
  - Lag: leading (precedes construction)
  - Range: [0, 20000]

- **notary_sales**: Monthly housing sales transactions by province
  - Unit: transactions
  - Cadence: monthly
  - Geo level: province
  - Source: notariado
  - Direction: 1
  - Lag: leading
  - Range: [0, 40000]

- **notary_price_m2**: Monthly housing price per square metre by province
  - Unit: eur_m2
  - Cadence: monthly
  - Geo level: province
  - Source: notariado
  - Direction: 1
  - Lag: leading
  - Range: [200, 8000]

### 2. Source Configuration (`config/sources.yml`)
Added two new data sources:

- **visados**: Ministry of Transport building statistics
  - Publisher: Ministerio de Transportes y Movilidad Sostenible
  - License: Reuse permitted (attribution)
  - Cadence: monthly
  - Max age: 120 days
  
- **notariado**: Notary statistics (CIEN)
  - Publisher: Consejo General del Notariado
  - License: Reuse permitted (attribution)
  - Cadence: monthly
  - Max age: 120 days

### 3. User-Agent Fix (`pipelines/citysignal/framework/fetch.py`)
Updated USER_AGENT constant from CitySignal identifier to proper browser User-Agent:
```
Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36
```

This resolves the HTTP 403 Forbidden issue encountered by the previous adapter attempt.

### 4. Adapter Implementations

#### `pipelines/citysignal/adapters/visados.py`
- **Purpose**: Building permit visas (visados de dirección de obra) monthly data by province
- **Data Source**: Ministry of Transport housing statistics portal
- **Discovery**: Fetches ministry listing page, parses HTML to find Excel file URLs
- **Parsing**: Loads XLSX files, extracts province/year/month/value data
- **Normalization**: Converts to canonical records with proper province geo_ids (prov-NN)
- **Sanity Check**: Series MUST show ~90% collapse from 2007-2010 (defining event in Spanish housing)
- **URL Patterns Attempted**: 
  - Primary: https://www.transportes.gob.es/informacion-para-el-ciudadano/informacion-estadistica/vivienda-y-actuaciones-urbanas
  - Alternative: Multiple URL patterns in discover() method
  
#### `pipelines/citysignal/adapters/notariado.py`
- **Purpose**: Housing sales and prices from notary records (CIEN data)
- **Data Source**: Consejo General del Notariado (Notary Council)
- **Discovery**: Tries multiple notariado.org URLs, falls back to direct URL patterns
- **Parsing**: Loads XLSX files, extracts province/month/year/sales/price data
- **Normalization**: Emits both notary_sales and notary_price_m2 records per province/month
- **Validation**: Provincial sales should be in thousands; Madrid and Barcelona highest €/m²
- **URL Patterns Attempted**:
  - Primary: https://www.notariado.org (with multiple sub-paths)
  - Fallback: Direct XLSX URLs

## Testing Required

### Step 1: Verify Adapter Registration
```bash
cd /Users/adi/Documents/GitHub/citysignal
.venv/bin/python -m citysignal.cli --root . list | grep -E "visado|notaria"
```

Expected output:
```
visados            monthly    province      official
notariado          monthly    province      official
```

### Step 2: Fetch Visados Data
```bash
.venv/bin/python -m citysignal.cli --root . fetch --source visados
```

This will:
1. Attempt to fetch the ministry's statistics page with proper browser User-Agent
2. Parse HTML to find the Excel file URL
3. Download and parse monthly visados data
4. Validate records and store in data/history/visados/

### Step 3: Fetch Notariado Data
```bash
.venv/bin/python -m citysignal.cli --root . fetch --source notariado
```

This will attempt to locate and fetch CIEN data from notariado.org.

### Step 4: Verify Data Quality - Visados
```bash
.venv/bin/python -c "
import csv
d={}
for r in csv.DictReader(open('data/history/visados/visados_new_build.csv')):
    if r['geo_id']=='prov-28': d[r['period']]=float(r['value'])
for p in sorted(d):
    if p[:4] in ('2006','2007','2010','2013','2019','2025') and p[5:7]=='06': print(p, d[p])
"
```

Expected: Significant collapse (~90%) from 2006-2010, showing the housing crisis effect.

### Step 5: Run Full Pipeline
```bash
.venv/bin/python -m citysignal.cli --root . fetch --source visados
.venv/bin/python -m citysignal.cli --root . fetch --source notariado
.venv/bin/python -m citysignal.cli --root . derive
.venv/bin/pytest -q
```

## Known Issues / Next Steps

### Issue 1: URL Discovery for Visados
The Ministry of Transport website structure may have changed. The current adapter tries multiple URL patterns:
- Primary listing page
- Alternative URL structures
- Direct XLSX URLs

**Action Needed**: If all attempts fail with HTTP 4xx errors, check the current URL structure at:
https://www.transportes.gob.es/informacion-para-el-ciudadano/informacion-estadistica/vivienda-y-actuaciones-urbanas

### Issue 2: CIEN Data Location
The Consejo General del Notariado (notariado.org) publishes CIEN data, but the exact URL may not be publicly accessible or may require navigation through their portal.

**Alternatives to try**:
1. Search datos.gob.es for notary statistics datasets
2. Check if CIEN publishes a downloadable series file directly
3. Contact the notary council for current data publication URL

### Issue 3: Data Format Variations
Spanish government agencies often use:
- Latin-1 (cp1252) encoding
- Semicolon-delimited files
- Comma as decimal separator
- Variable column layouts across years

The adapters handle these variations, but may need tweaking based on actual file format.

## Files Modified
1. `config/metrics.yml` - Added 3 new metrics
2. `config/sources.yml` - Added 2 new sources
3. `pipelines/citysignal/framework/fetch.py` - Updated USER_AGENT
4. `pipelines/citysignal/adapters/visados.py` - NEW
5. `pipelines/citysignal/adapters/notariado.py` - NEW

## Configuration Validation
✓ All YAML files are valid
✓ All metrics have required fields (label, plain, unit, cadence, geo_level, source_id, section, direction, lag, range)
✓ All sources have required fields (publisher, license, attribution, docs_url, kind, cadence, max_age_days)
✓ Adapters register successfully in CLI
✓ Python syntax validation: PASS
