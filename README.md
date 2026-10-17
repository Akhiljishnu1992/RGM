# RGM Pricing Simulator

A pricing simulation app for Revenue Growth Management. Pick any node in a
Category → Sub-category → Brand → PPG hierarchy, propose a price change, and see
the volume response (constant elasticity) and the resulting NSV, TO, GP and GM %
for several scenarios side by side, with a price-vs-volume bridge and CSV export.

The calculation engine (`pricing_engine/`) has no UI dependency and is covered by
70 unit tests, including a worked example checked to the cent.

---

## Quick start

```bash
cd rgm-pricing-simulator
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

streamlit run app.py               # opens http://localhost:8501
pytest                             # 70 tests, ~30 s
```

Requires Python 3.10+. Tested on Python 3.13 with Streamlit 1.66, pandas 3.0 and
2.2, numpy 2.5 and plotly 7.1.

## Project structure

```
rgm-pricing-simulator/
├── app.py                      Streamlit UI (state, widgets, charts) — no business logic
├── pricing_engine/             Calculation engine — no Streamlit imports
│   ├── models.py               Node, PriceChange, Scenario, TO definitions, config + placeholder defaults
│   ├── schema.py               Column names, required/optional columns, header aliases
│   ├── data_loader.py          CSV / JSON loading
│   ├── validation.py           Type coercion and per-PPG checks (errors / warnings / info)
│   ├── hierarchy.py            Tree building, node selection, "most specific wins" resolution
│   ├── simulation.py           Elasticity resolution, scenario maths, roll-ups, decomposition
│   └── export.py               CSV-ready result tables
├── data/
│   ├── sample_hierarchy.csv    36 PPGs: 3 categories × 2 sub-categories × 2 brands × 3 PPGs
│   └── sample_hierarchy.json   Same data as JSON records
├── docs/FORMULAS.md            Formulas, conventions, worked example, limitations
├── tests/                      pytest suite (engine, validation, hierarchy, export, UI smoke test)
├── .streamlit/config.toml
├── pyproject.toml              pytest configuration
└── requirements.txt
```

## Using the app

**Sidebar**
- **Data** — load your own CSV/JSON, reset to the sample, or download the template.
- **Hierarchy** — four cascading selectors. Stop at any level ("All brands", say)
  to select that whole node; inputs then apply to every PPG beneath it.
- **Assumptions** — TO definition, default elasticity (and whether to apply it to
  PPGs that have none), currency label and display scale (default: crores).

**Simulate tab**
- **Scenarios** — choose the scenario you're editing; add, duplicate, rename or
  delete scenarios; set a per-scenario *elasticity sensitivity multiplier* for
  stress tests (1.25 = 25 % more price-sensitive).
- **Price change** — percent of list price or absolute currency per unit, set for
  the selected node in the selected scenario. The most specific setting wins, so
  you can move a whole sub-category +6 % and hold one LUP at 0 %.
- **Elasticity** — shows the elasticity in use and its source (NSV-weighted for a
  parent node) and lets you override it at the node. Overrides are shared by all
  scenarios.
- **Results** — scenario comparison table (base, value, Δ, Δ % for every metric),
  price-vs-volume driver charts across scenarios, NSV and GP bridges for one
  scenario, breakdown by the next level down, PPG-level detail, and three CSV
  exports.

**Hierarchy tab** — the full tree with PPG counts, base NSV and GM %, elasticity and
source, overrides (◆), price changes set at each node, and each node's change for a
chosen scenario. Includes a sunburst of base NSV.

**Data & validation tab** — edit base inputs in place (every tab recalculates) and
see every error, warning and assumption.

**Formulas tab** — renders `docs/FORMULAS.md`.

The app opens with three example scenarios on the sample data ("PC +5%", "Laundry
+6%, LUP held", and "PC +5%" with elasticity ×1.25). They are cleared when you load
your own data.

## Data format

One row per PPG. CSV or JSON (a list of records, or `{"ppgs": [...]}`).

| Column | Required | Meaning |
|---|---|---|
| `category` | yes | Level 1 |
| `sub_category` | yes | Level 2 |
| `brand` | yes | Level 3 |
| `ppg_id` | yes | Level 4, unique id (kept as text, so `007` stays `007`) |
| `ppg_name` | no | Display name (defaults to `ppg_id`) |
| `base_units` | yes | Base-period volume in units (e.g. last 52 weeks) |
| `base_list_price` | yes | Base list price per unit — the price changes apply to |
| `unit_cogs` | yes | COGS per unit |
| `elasticity` | yes* | Own-price elasticity, normally negative. *A blank cell is allowed but flagged; supply it via override or the opt-in default |
| `elasticity_source` | no | Where the elasticity came from (shown in the UI) |
| `on_invoice_disc_rate` | no | On-invoice discount, fraction of list price (0.05 = 5 %) |
| `off_invoice_trade_rate` | no | Off-invoice trade spend, fraction of list price |
| `base_rsp` | no | Consumer retail selling price per unit; required only for TO = consumer offtake |

Headers are matched case-insensitively and common aliases are accepted (for example
`Sub-Category`, `PPG`, `Base Price`, `COGS`, `Units`, `Price Elasticity`, `RSP`).
Thousands separators in numbers are fine. The full alias list is in
`pricing_engine/schema.py`.

**To use your own data:** export one row per PPG with the columns above, then either
upload it in the sidebar or replace `data/sample_hierarchy.csv`. No code changes are
needed for a different hierarchy, number of levels filled, or number of PPGs.

## Formulas (summary)

Full detail and limitations: [`docs/FORMULAS.md`](docs/FORMULAS.md).

```
net price           P  = list price × (1 − on-invoice rate − off-invoice rate)
price ratio         r  = new list price / base list price
new units           U₁ = U₀ × r^ε            (ε = elasticity × scenario multiplier)
NSV                    = units × net price
TO                     = units × price per the chosen TO definition
GP                     = NSV − units × unit COGS
GM %                   = GP / NSV            (roll-ups: ΣGP / ΣNSV)
NSV price effect       = (P₁ − P₀) × U₁      NSV volume effect = (U₁ − U₀) × P₀
GP  price effect       = (P₁ − P₀) × U₁      GP  volume effect = (U₁ − U₀) × (P₀ − COGS)
```

### Worked example (verified in `tests/test_worked_example.py`)

List price 100.00, on-invoice 5 %, off-invoice 10 % → net price 85.00; 1,000 units;
COGS 50.00; elasticity −2.0; +10 % price.

| | Base | Scenario | Δ |
|---|---:|---:|---:|
| Units | 1,000.00 | 826.45 | −17.36 % |
| NSV | 85,000.00 | 77,272.73 | −9.09 % |
| TO (invoiced) | 95,000.00 | 86,363.64 | −9.09 % |
| GP | 35,000.00 | 35,950.41 | +2.72 % |
| GM % | 41.18 % | 46.52 % | +5.35 pp |

NSV bridge: price effect +7,024.79, volume effect −14,752.07.
GP bridge: price effect +7,024.79, volume effect −6,074.38.

## Assumptions and placeholders — replace these with your own

| Placeholder | Value | Where to change |
|---|---|---|
| TO definition | Invoiced turnover (GSV − on-invoice discounts) | Sidebar, or `DEFAULT_TO_BASIS` in `pricing_engine/models.py` |
| Default elasticity | −1.5, applied only when you opt in | Sidebar, or `DEFAULT_ELASTICITY` in `models.py` |
| Large-move warning | ±30 % price change | `SimulationConfig.large_change_threshold` |
| Extreme-elasticity warning | more negative than −5.0 | `SimulationConfig.extreme_elasticity_threshold` |
| Currency and scale | INR, crores (display only) | Sidebar, or `DEFAULT_CURRENCY` / `DEFAULT_SCALE` in `app.py` |
| Sample data | Fictional brands; all prices, volumes, costs and elasticities are illustrative | `data/sample_hierarchy.csv` |
| Example scenarios | Three starter scenarios | `_example_scenarios()` in `app.py` |

Modelling assumptions (see `docs/FORMULAS.md` → Limitations): trade rates held
constant as % of list price; full pass-through to shelf price; unit COGS fixed;
own-price elasticity only (no cannibalisation or competitor response).

The −1.5 default sits between typical category-level elasticities and the brand-level
averages of roughly −1.8 to −2.6 reported in published meta-analyses. Treat it as a
stand-in until you have your own estimates.

### Deliberate issues in the sample data

Three rows show how validation works:

| PPG | Issue | Result |
|---|---|---|
| `NIM-SHP-650` | Elasticity blank | Error: excluded until you set an override or tick "apply default" |
| `CHF-SAU-1KG` | Unit COGS blank | Error: excluded until you fill it in (Data & validation tab) |
| `AZU-LP-4KG` | Elasticity +0.30 | Warning: calculated, flagged for review |

## Validation rules

| Check | Severity | Effect |
|---|---|---|
| Missing required column | Schema error | File rejected with a message listing required columns |
| Missing or non-numeric price, units or COGS | Error | PPG excluded from all scenarios; never defaulted |
| List price ≤ 0, negative units or COGS | Error | PPG excluded |
| Missing elasticity (no override, default off) | Error | PPG excluded |
| Trade rate outside [0, 1), or rates summing to ≥ 100 % | Error | PPG excluded (hint if you typed 5 for 5 %) |
| Duplicate PPG id, blank hierarchy field | Error | PPG excluded |
| Missing RSP when TO = consumer offtake | Error | PPG excluded |
| New price ≤ 0 in a scenario (e.g. too large a cut) | Scenario error | Scenario not calculated; offending PPGs named |
| Positive or zero elasticity | Warning | Calculated, flagged |
| Elasticity more negative than −5 | Warning | Calculated, flagged |
| COGS ≥ net price | Warning | Calculated (negative margin), flagged |
| Zero base units | Warning | Calculated, adds nothing |
| Blank trade-rate cell | Warning | Assumed 0 %, flagged on the row |
| Optional column absent | Info | Assumption listed (e.g. rates = 0) |
| Price move beyond ±30 % | Scenario warning | Calculated, flagged |

Excluded PPGs are counted ("34 of 36 included") wherever totals are shown.

## Using the engine without the UI

```python
from pricing_engine import *

config = SimulationConfig(to_basis=TOBasis.GSV)
clean, data_issues = validate_dataset(load_dataset("data/sample_hierarchy.csv"), config)
base, elasticity_issues = prepare_base(
    clean, [ElasticityOverride(Node(("Home Care", "Laundry Powder", "Azura")), -1.4)], config
)
scenario = Scenario(
    "Laundry +6%, LUP held",
    [
        PriceChange(Node(("Home Care", "Laundry Powder")), ChangeType.PERCENT, 0.06),
        PriceChange(Node(("Home Care", "Laundry Powder", "Freshgen", "FRG-LP-090")), ChangeType.PERCENT, 0.0),
    ],
)
result = simulate(base, scenario, config)
print(aggregate(result.ppg, Node(("Home Care",)), group_depth=2)[["sub_category", "d_nsv", "d_gp", "d_gm"]])
```

## Extending

- **Another TO convention:** add a `TOBasis` member and `TODefinition` in
  `models.py`; if it needs a new per-unit price, derive `base_<field>` and
  `new_<field>` in `simulation.py`.
- **Cost inflation scenarios:** add a COGS change to `Scenario` and apply it where
  `new_gp` is computed; the GP bridge would then gain a cost effect.
- **Cross-price elasticities:** replace the single-PPG volume line in `simulate()`
  with a matrix product over the PPGs in a sub-category.
