## Formulas and conventions

Every number in the app comes from the formulas below, implemented in
`pricing_engine/simulation.py` and checked against the worked example in
`tests/test_worked_example.py`.

### 1. Price waterfall (per PPG, per unit)

| Item | Formula / source |
|---|---|
| List price `L` | input `base_list_price`, the "base price" every change is applied to |
| On-invoice discount rate `a` | input `on_invoice_disc_rate`, fraction of list price |
| Off-invoice trade rate `b` | input `off_invoice_trade_rate`, fraction of list price |
| Invoiced price | `L × (1 − a)` |
| Net price `P` | `L × (1 − a − b)` |
| Retail selling price `R` | input `base_rsp` (optional; only needed for TO = consumer offtake) |

Trade rates are held constant as a share of list price, so a list-price change of
x % moves invoiced price, net price and RSP by the same x % (full pass-through).

### 2. Price change

- Percent: `L₁ = L₀ × (1 + x)`
- Absolute: `L₁ = L₀ + Δ` (currency per unit, on list price)
- Price ratio `r = L₁ / L₀`; price change % `= r − 1`

A change set at any node applies to every PPG beneath it. When changes exist at
several levels, **the most specific one wins** for each PPG:
PPG > Brand > Sub-category > Category > All products. Setting 0 % on a PPG holds it
while its parent moves.

### 3. Volume: constant elasticity

`U₁ = U₀ × (1 + price change %)^ε = U₀ × r^ε`

`ε` per PPG = most specific override > value in the data > default (only if you
tick "apply default") — then multiplied by the scenario's sensitivity multiplier
(1.0 unless you change it). A missing elasticity with no override and the default
switched off is an error: the PPG is excluded, not guessed.

### 4. Financials

| Metric | Formula |
|---|---|
| NSV | `units × net price` |
| TO | `units × price chosen by the TO definition` (see below) |
| GP | `NSV − units × unit COGS` (unit COGS held constant) |
| GM % | `GP / NSV` |

TO definitions (pick yours in the sidebar; default is a placeholder):

| Option | TO = units × … |
|---|---|
| Gross Sales Value (GSV) | list price |
| Invoiced turnover (default) | list price × (1 − on-invoice rate) |
| Net sales (TO = NSV) | net price |
| Consumer offtake at RSP | retail selling price |

**Roll-ups.** Units, NSV, TO and GP are summed over included PPGs. GM % is always
recomputed as `ΣGP / ΣNSV`, never averaged.

**Deltas.** `Δ = scenario − base`, `Δ% = Δ / base`. For GM %, Δ is in percentage
points (pp) and Δ% is the relative change.

**Average net price change** (shown per node) is base-volume weighted:
`Σ(P₁ × U₀) / Σ(P₀ × U₀) − 1`.

### 5. Price vs volume decomposition

| | Price effect | Volume effect |
|---|---|---|
| NSV | `(P₁ − P₀) × U₁` | `(U₁ − U₀) × P₀` |
| GP | `(P₁ − P₀) × U₁` | `(U₁ − U₀) × (P₀ − COGS)` |

The two effects add up exactly to the total change for each PPG. Node totals sum
PPG-level effects, so there is no residual or mix term.

### 6. Worked example

One PPG: list price 100.00, on-invoice 5 %, off-invoice 10 % (net price 85.00),
1,000 base units, unit COGS 50.00, elasticity −2.0, RSP 130.00. Scenario: +10 %.

| Metric | Base | Scenario | Δ | Δ % |
|---|---:|---:|---:|---:|
| List price | 100.00 | 110.00 | +10.00 | +10.00 % |
| Net price | 85.00 | 93.50 | +8.50 | +10.00 % |
| Units (`1,000 × 1.1^−2`) | 1,000.00 | 826.45 | −173.55 | −17.36 % |
| NSV | 85,000.00 | 77,272.73 | −7,727.27 | −9.09 % |
| TO (invoiced: 95.00 → 104.50 per unit) | 95,000.00 | 86,363.64 | −8,636.36 | −9.09 % |
| GP | 35,000.00 | 35,950.41 | +950.41 | +2.72 % |
| GM % | 41.18 % | 46.52 % | +5.35 pp | +12.99 % |

NSV bridge: 85,000.00 + price effect 7,024.79 (8.50 × 826.45) + volume effect
−14,752.07 (−173.55 × 85.00) = 77,272.73.
GP bridge: 35,000.00 + 7,024.79 + (−173.55 × 35.00 = −6,074.38) = 35,950.41.

Check: with constant elasticity, NSV moves by `(1 + x)^(1 + ε) − 1` =
`1.1^(−1) − 1` = −9.09 %.

### 7. Limitations to keep in mind

- **Own-price only.** No cross-price elasticities, so volume lost by one PPG does not
  flow to its siblings (no cannibalisation or switching) and no competitor reaction.
- **Local approximation.** Constant elasticity is reliable near the base price;
  moves beyond ±30 % are flagged.
- **Full pass-through.** Shelf prices move by the same % as list price; retailer
  margin and price-point thresholds are not modelled.
- **Fixed unit COGS.** No scale effects or input-cost inflation in scenarios.
