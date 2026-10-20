# Resumo da generalização

Instâncias concluídas: {'gurobi': 768, 'lcvf': 768, 'v2g': 768}. Faltantes: 0.

## Estágio 1 (V2G)

- Redução do pico convencional: mediana 15,1% (IQR 8,3–24,5%, mín–máx 1,8–40,7%).
- Redução da variância da carga: até 98,9%.

## V2G × LCVF (pico final)

- V2G com pico menor em 670 de 768 instâncias; gap mediano -3,81% (mín–máx -23,72% a 3,87%).
- Energia líquida do V2G relativa ao LCVF: mediana -0,02%, mínimo -13,84%.
- EVs abaixo de 90%: V2G mediana 0,52%, LCVF 0,17%; diferença máxima 31,51 p.p.

- Wilcoxon (pico final: V2G × LCVF, todas): n = 768, V2G menor em 670, p = 3.32e-101, efeito rank-biserial -0.89.
- Wilcoxon (energia líquida: V2G × LCVF, todas): n = 768, V2G menor em 601, p = 7.58e-104, efeito rank-biserial -0.98.
- Wilcoxon (EVs abaixo de 90%: V2G × LCVF, todas): n = 768, V2G menor em 0, p = 2.67e-91, efeito rank-biserial +1.00.

## Contra o ótimo exato (Gurobi, N = 3.000, 9.000, 18.000, 32.330, 39.000 e 50.336)

- V2G abaixo do ótimo de recarga em 689 de 768; gap mediano -3,81%, máximo 3,83%.
- LCVF acima do ótimo: mediana -0,01%, máximo 0,65%.

## Correlação (Spearman) com a redução do S1, todas as frotas

- razao_energia_carga: ρ = +0.92 (p = 0.0e+00, n = 768)
- frac_conectados_pico: ρ = +0.26 (p = 6.0e-13, n = 768)
- pico_conv_kW: ρ = -0.03 (p = 4.1e-01, n = 768)
