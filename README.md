# Three-Stage V2G Scheduling for City-Scale EV Fleets — reproduction package

This repository holds the code, input data and results behind the experiments of the manuscript

> **Vehicle-to-Grid Scheduling for City-Scale Electric Vehicle Fleets: A Three-Stage Peak-Shaving Algorithm with
> State-of-Charge Recovery**
> G. G. de Souza, S. K. T. Adania, A. M. C. Oliveira, R. B. Godoy, M. A. G. de Brito, R. R. dos Santos
> (submitted to *Energy*).

The paper compares three techniques on 768 instances:

| Technique | Role in the paper |
|---|---|
| Three-stage V2G algorithm | the proposed method |
| LCVF heuristic | charging-only baseline |
| Exact model (Gurobi) | optimum of the charging-only problem |

You can either analyse the stored results, which takes minutes, or rerun the experiments from the input data, which
takes hours for V2G and LCVF and days for the exact model.

---

## Repository layout

The folder hierarchy mirrors the original project. The scripts locate data through paths relative to the repository
root, so keep the structure unchanged.

```
.
├── Codes/E_V2G/scripts/generalização_32dias/   # algorithms and experiment runners
│   ├── v2g_stable.py, v2g_nivelado.py     # three-stage V2G (Stage 1 discharge; Stages 2–3 levelled recharge)
│   ├── lcvf_fast_v2g.py                   # LCVF baseline
│   ├── multiobj_c.c                       # exact charging-only model (Gurobi C API)
│   ├── selecao_dias.py                    # deterministic selection of the 32 load days
│   ├── run_generalizacao.py               # runs the instance grid (V2G, LCVF, Gurobi)
│   ├── run_gurobi_sequencial.py           # Gurobi one instance at a time, with memory/time logging
│   ├── analise_generalizacao.py           # consolidates the results and runs the paired tests
│   ├── solucao_npz.py                     # converts the Gurobi solution between .npz and .csv
│   └── …                                  # helper scripts (see each file's docstring)
├── Datasets/ProcessedData/
│   ├── Cities/CampoGrande/all_days/       # daily load profiles (pc_cgr_YYYYMMDD_<season>_<dow>.csv)
│   └── Vehicles/V2G/<fleet>/pp_<N>_<fleet>.csv   # preprocessed EV fleets
└── Results/V2G/CampoGrande/generalization/
    ├── v2g_nivelado/<fleet>/<instance>/   # three-stage V2G
    ├── lcvf_v2g/<fleet>/<instance>/       # LCVF
    ├── exact_gurobi/<fleet>/<instance>/   # exact model
    ├── analise/                           # consolidated CSVs and paired tests
    ├── execucoes.csv                      # wall time and memory of each run
    └── execucoes_gurobi_sequencial.csv    # sequential Gurobi runs (exact-model scalability)
```

Instance folders are named `inst_<N>_<fleet>_cgr_<YYYYMMDD>_<season>_<dow>`, for example
`inst_3000_bld_s1_cgr_20221003_spring_mon`.

### Fleet names: paper vs. code

The paper calls the fleets B1–B4 and N1. Folders and scripts keep the codes of the source datasets:

| Paper | Fleet | Code | Sizes $N$ |
|---|---|---|---|
| B1 | Boulder, real sessions | `bld_s1` | 3 000, 9 000, 18 000, 39 000, 50 336 |
| B2 | Boulder, flexible | `bld_s2` | same |
| B3 | Boulder, 8-h shifts | `bld_s4` | same |
| B4 | Boulder, 24-h | `bld_s5` | same |
| N1 | Norway | `nor_s1` | 3 000, 9 000, 18 000, 32 330 |

The grid has 24 fleet/size cells × 32 days = **768 instances per technique**.

`bld_s3` and `bld_s6` are also provided, but they are not part of the grid:
- They only change the energy consumed per session, which the model does not use.
- Their inputs and results are therefore identical to those of `bld_s2` and `bld_s1`.
- The check is in `Results/.../analise/equivalencia_s3_s6.csv`.

---

## Requirements

- **Python ≥ 3.10**, with `numpy`, `pandas`, `scipy` and `matplotlib`.
  - We used Python 3.14, numpy 2.4, pandas 3.0, scipy 1.17 and matplotlib 3.11.

    ```bash
    python3 -m venv .venv && source .venv/bin/activate
    pip install numpy pandas scipy matplotlib
    ```

- **For the exact model only:** Gurobi 11.0 with a valid licence and a C compiler. Free academic licences are
  available at <https://www.gurobi.com/academia/>.

All commands below run from the scripts folder:

```bash
cd Codes/E_V2G/scripts/generalização_32dias
```

---

## 1. Explore the stored results

### 1.1 Consolidate and test

```bash
python3 analise_generalizacao.py --sem-figuras
```

The script reads every instance under `Results/V2G/CampoGrande/generalization/` and writes the following files to
its `analise/` folder:

| File | Content |
|---|---|
| `consolidado.csv` | one row per instance × technique |
| `pareado.csv` | one row per instance (day × fleet × $N$), techniques side by side, with peak gaps |
| `resumo_frota_tamanho.csv`, `resumo_estacao.csv` | median, IQR and range over the days, by fleet/size and by season |
| `testes_pareados.csv` | paired Wilcoxon tests (V2G vs. LCVF; V2G and LCVF vs. exact), Holm-corrected |
| `correlacoes.csv` | Spearman correlations of the Stage 1 reduction with the day's load and with the share of EVs connected at the peak |
| `resumo.md` | key numbers |

Drop `--sem-figuras` to also draw comparison figures in `analise/comparacao/`. The stored `analise/` already holds
the output of this step.

**Main definitions**
- **Peak gap V2G vs. LCVF:** $100\,(\text{final V2G peak} / \text{final LCVF peak} - 1)$. A negative value means V2G
  has the lower peak.
- **Net V2G energy:** Stage 2 + Stage 3 recharge minus Stage 1 discharge (kWh).
- **EVs below 90 %:** vehicles that end the day below the 90 % SoC ceiling.

### 1.2 Files of one instance

| Technique | Main files |
|---|---|
| V2G | `summary_3stage.csv` (per-stage peak, energy and SoC counts), `en_profile_all_stages.csv` (load per slot after each stage), `results_s1_discharge.csv` (Stage 1 iterations), `results_s2_mincharge.csv`, `results_s3_fullcharge.csv` |
| LCVF | `en_profile_final.csv` (load per slot), plus the per-iteration table `<instance>.csv` in the fleet folder |
| Exact | `en_profile_final.csv`, `summary.csv`, `gurobi.log.gz`, `recursos.csv` (memory every 1 s), `solucao_bits.npz` |

Every instance also has a `concluido.json` with its wall time, peak memory and status.

**Exact solutions in CSV.** `solucao_bits.npz` stores the optimal EV-to-time-slot allocation as a compressed bit
matrix. To recreate the `solucao.csv` files (≈4 GB for all instances):

```bash
python3 solucao_npz.py restaurar
```

---

## 2. Rerun the experiments

The runners share two behaviours:
- **Restartable:** instances that already have a `concluido.json` are skipped. Use `--refazer` to force a rerun.
- **Default output:** `Results/V2G/CampoGrande/generalization/`. Pass `--saida` to keep the stored results
  untouched.

### 2.1 Test run on a single instance

```bash
python3 run_generalizacao.py --dry-run                     # lists the 768 instances
python3 run_generalizacao.py --tecnicas v2g lcvf \
        --dias 20221003 --frotas bld_s1 --tamanhos 3000 --saida /tmp/teste
```

### 2.2 V2G and LCVF on the full grid

```bash
python3 run_generalizacao.py --tecnicas v2g lcvf --jobs 4 --saida /path/to/new_results
```

### 2.3 Exact model (Gurobi)

```bash
export GUROBI_HOME=/opt/gurobi1103/linux64        # adjust to your installation
gcc multiobj_c.c -o multiobj_c -I$GUROBI_HOME/include -L$GUROBI_HOME/lib -lgurobi110 -lm

# N = 3 000 and 9 000 (two runs in parallel)
python3 run_generalizacao.py --tecnicas gurobi --tamanhos 3000 9000 --gurobi-jobs 2 --gurobi-threads 4 \
        --saida /path/to/new_results

# N >= 18 000: one instance at a time, with a memory limit in GB
python3 run_gurobi_sequencial.py --tamanho 18000 --memlimit 15 --saida /path/to/new_results
```

The largest instances (B4, $N = 50\,336$) need about 17 GB of RAM.

### 2.4 Analyse the new results

```bash
python3 analise_generalizacao.py --entrada /path/to/new_results
```

### How the 32 days are chosen

`selecao_dias.py` picks 8 working days per season, evenly spaced, from the daily profiles in `all_days/`. It
excludes:
- holidays;
- *pontos facultativos* (optional public-service days off);
- Brazil's 2022 World Cup match days;
- the working days adjacent to any of these.

The selection is deterministic. Run `python3 selecao_dias.py` to list it.

---

## Data

- **Conventional load:** aggregate load of three distribution substations of Campo Grande (MS, Brazil).
  - Period: 2 June 2022 – 31 May 2023.
  - Resolution: 10-min time slots (144 per day).
  - `Pconv` is given in W.
- **EV fleets:** charging sessions from Boulder, CO (NREL) and from Norway. Each fleet was preprocessed into one
  file per size $N$, with the fields the algorithms use:
  - arrival/departure time slot;
  - battery capacity;
  - current, minimum and maximum SoC;
  - charger power.
- **Lengthened windows:** sessions too short to serve their vehicle were lengthened during preprocessing (mostly in
  the Boulder fleets).
- **Periodic day (N1):** Norway sessions that cross midnight wrap to the first slots of the same day (`wrap_end_ts`).

## Citation

If you use this material, please cite the manuscript above. The full reference will be added once it is published.
