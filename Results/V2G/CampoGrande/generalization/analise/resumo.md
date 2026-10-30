# Resumo da generalização

Instâncias concluídas: {'gurobi': 768, 'lcvf': 768, 'v2g': 768}. Faltantes: 0.

## Estágio 1 (V2G)

- Redução do pico convencional: mediana 13,9% (IQR 7,7–20,6%, mín–máx 1,8–40,5%).
- Redução da variância da carga: até 98,6%.

## V2G × LCVF (pico final)

- V2G com pico menor em 718 de 768 instâncias; gap mediano -3,54% (mín–máx -23,72% a 3,16%).
- Energia líquida do V2G relativa ao LCVF: mediana 0,00%, mínimo -11,07%.
- EVs abaixo de 90%: V2G mediana 0,18%, LCVF 0,17%; diferença máxima 27,58 p.p.

- Wilcoxon (pico final: V2G × LCVF, todas): n = 768, V2G menor em 718, p = 1.09e-118, efeito rank-biserial -0.97.
- Wilcoxon (energia líquida: V2G × LCVF, todas): n = 768, V2G menor em 240, p = 9.19e-09, efeito rank-biserial -0.33.
- Wilcoxon (EVs abaixo de 90%: V2G × LCVF, todas): n = 768, V2G menor em 244, p = 1.07e-09, efeito rank-biserial -0.41.

## Contra o ótimo exato (Gurobi, N = 3.000, 9.000, 18.000, 32.330, 39.000 e 50.336)

- V2G abaixo do ótimo de recarga em 727 de 768; gap mediano -3,55%, máximo 3,13%.
- LCVF acima do ótimo: mediana -0,01%, máximo 0,65%.

## Correlação (Spearman) com a redução do S1, todas as frotas

- razao_energia_carga: ρ = +0.89 (p = 3.9e-269, n = 768)
- frac_conectados_pico: ρ = +0.33 (p = 1.0e-20, n = 768)
- pico_conv_kW: ρ = -0.01 (p = 7.0e-01, n = 768)
