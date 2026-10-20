# Generalização em 32 dias (V2G, LCVF e Exato)

Resultados da grade descrita em `Codes/E_V2G/docs/13_task_padronizacao_instancias.md`, gerada por
`Codes/E_V2G/scripts/generalização_32dias/run_generalizacao.py`.

## Cenários: código da execução × nome no texto

Os cenários do Boulder são numerados em sequência (B1–B4), e a frota da Noruega é N1, só no texto do relatório; o texto não usa os códigos s1–s6. As pastas, os arquivos e os
scripts mantêm os códigos do dataset de origem (`evp_bld_s*_*.csv`), para preservar a ligação com os dados
do NREL e com o artigo do NSGA-III.

| Texto | Nome                 | Código (pastas e scripts) | Arquivo de origem             |
|-------|----------------------|---------------------------|-------------------------------|
| B1    | Boulder real         | `bld_s1`                  | `evp_bld_s1_baseline.csv`     |
| B2    | Boulder flexible     | `bld_s2`                  | `evp_bld_s2_flexible.csv`     |
| B3    | Boulder 8-h shifts   | `bld_s4`                  | `evp_bld_s4_8hours.csv`       |
| B4    | Boulder 24-h         | `bld_s5`                  | `evp_bld_s5_24hours.csv`      |
| N1    | Norway               | `nor_s1`                  | `evp_nor_s1_baseline.csv`     |

O mapeamento fica em um único lugar no código (`FROTAS` em `Articles/Energy/gerar_relatorio.py`), de onde saem as
tabelas, as figuras e as macros do relatório.

`bld_s3` e `bld_s6` (consumo por sessão 3,5× sobre as janelas de `bld_s2` e `bld_s1`) não entram na grade padrão:
o modelo deriva a demanda da bateria e do SoC, então as entradas e os resultados são idênticos aos de `bld_s2` e
`bld_s1`. A comparação completa (22 colunas, três técnicas, diferença máxima 0) está em
`analise/equivalencia_s3_s6.csv`, e os resultados da rodada que os incluiu, em `_redundantes_s3_s6/`.

## Pastas

| Pasta                    | Conteúdo                                                                      |
|--------------------------|-------------------------------------------------------------------------------|
| `v2g_nivelado/<código>/` | V2G (S1 + S2/S3 nivelado), uma pasta por instância                            |
| `lcvf_v2g/<código>/`     | LCVF direto (baseline)                                                        |
| `exact_gurobi/<código>/` | ótimo exato só de carga (todos os tamanhos, em sequência, com `recursos.csv`, `gurobi.log.gz` e `solucao_bits.npz`) |
| `analise/`               | CSVs consolidados, testes e figuras de comparação (`analise_generalizacao.py`) |
| `analise/casos_v2g_perde/`, `analise/benchmark_exato/` | material das docs 14 e 15                  |
| `_redundantes_s3_s6/`    | resultados de `bld_s3`/`bld_s6` (idênticos a `bld_s2`/`bld_s1`)                |
| `_n1_horizonte_cortado/` | N1 antes do dia periódico (fora do git; docs/13, Seção 7e)                    |
| `_sens_madrugada_n1/`    | execuções do teste de sensibilidade da madrugada (fora do git)                |

Grade de instâncias distintas: 768 por técnica (V2G, LCVF e Exato; 32 dias × 24 frotas/tamanhos). Registro das
execuções do Gurobi: `execucoes_gurobi_sequencial.csv`.

O relatório (tabelas, figuras, macros e o `.tex`) saiu daqui em 2026-10-09: agora é o manuscrito oficial em
`Articles/Energy/` (antes em `analise/relatorio/`).
