/* Copyright 2026, Gurobi Optimization, LLC */

/* CÓPIA CONGELADA de Codes/Exact_Alg/gurobi_alg/multiobj_c.c (commit ef994364 + correções de 2026-10-03), usada só pela generalização de 32 dias (run_generalizacao.py). Caminhos ajustados para esta pasta e saídas padrão em Results/V2G/CampoGrande/generalization/. Correções no pipeline principal não chegam aqui sozinhas: repita-as e registre na docs/13.
   Diferenças: SEASON = arquivo pc_cgr_*.csv de all_days/; SEASON desconhecida dá erro;
   saída em Results/V2G/CampoGrande/generalization/exact_gurobi/.
   Compilar (nesta pasta):
   gcc multiobj_c.c -o multiobj_c -I$GUROBI_HOME/include -L$GUROBI_HOME/lib -lgurobi110 -lm */

#include <assert.h>
#include <stdlib.h>
#include <stdio.h>
#include <math.h>
#include <string.h>
#include "gurobi_c.h"

#define MAXSTR 128
/* Aponta direto para a fonte canônica usada por Codes/E_V2G/scripts/
   {lcvf_fast_v2g,v2g_stable}.py (EV_DIR/CITY_DIR) — garante que o exato e
   o LCVF leem o MESMO arquivo por construção (verificado MD5-idêntico em
   2026-09-21, ver gurobi_alg/README.md §10 P1.1). Até 2026-09-22 havia uma
   cópia local em Codes/Exact_Alg/dataset_ev|dataset_conv/ usada pelos
   módulos heurísticos legados (cvf_mod/lcvf_mod/nsgaiii_mod/ovf_mod);
   removida junto com eles — nada em Exact_Alg lê mais essa cópia. */
#define DATASET_PATH "../../../Datasets/ProcessedData/Vehicles/V2G/"
/* Número de colunas do novo CSV de EVs (formato lcvf_*). */
#define NUM_FIELDS_EV 24   /* 21 colunas do pp_*.csv + wrap_end_ts (opcional) + folga */

/* Alvo de carga: mesmo modelo de Codes/E_V2G/scripts/lcvf_fast_v2g.py
   (CHARGER_POWER_KW, SOC_FULL_TARGET, ENERGY_PER_SLOT_KWH) — ver
   docs/04_mapeamento_validacao_manuscrito.md P3.3. NÃO usar mais
   lcvf_power_kW/lcvf_energy_kWhTS (legado, pré-modelo V2G, descartado
   pelo próprio lcvf_fast_v2g.py). ENERGY_PER_SLOT_KWH replica o
   double-rounding de round(7.0 * round(24/144, 5), 5) feito em Python
   (1.16669, não o 1.16667 "exato" de 7*24/144). */
#define CHARGER_POWER_KW 7.0
#define SOC_FULL_TARGET 0.90
#define ENERGY_PER_SLOT_KWH 1.16669
/* round(24.0/144.0, 5) em Python — mesma convenção usada em ENERGY_PER_SLOT_KWH. */
#define TS_HOURS 0.16667

/* Parâmetros de cenário — defaults abaixo, sobrescritos por argv em main()
   se fornecidos: ./multiobj_c [N] [BOULDER] [SEASON] (ver README.md §8). */
int N = 9000;
char BOULDER[MAXSTR] = "bld_s5";
char SEASON[MAXSTR] = "baseline";

int compara(const void *a, const void *b)
{
    double x = *(const double *)a;
    double y = *(const double *)b;

    if (x < y) return -1;
    if (x > y) return 1;
    return 0;
}

//Valor de theta
double mediana(const double *v, int n) {
    if (n <= 0)
        return -1.0;

    double *copia = malloc(n * sizeof(double));
    if (copia == NULL) {
        return -1.0;
    }

    memcpy(copia, v, n * sizeof(double));
    qsort(copia, n, sizeof(double), compara);

    double resultado;

    if (n % 2 == 1) {
        resultado = copia[n / 2];
    } else {
        resultado = (copia[n / 2 - 1] + copia[n / 2]) / 2.0;
    }

    free(copia);
    return resultado;
}

/* Divide uma linha de CSV em tokens separados por vírgula. */
static int split_csv_line(char *line, char *fields[], int max_fields)
{
    int count = 0;
    char *tok = strtok(line, ",\n\r");

    while (tok != NULL && count < max_fields) {
        fields[count++] = tok;
        tok = strtok(NULL, ",\n\r");
    }

    return count;
}

/* Restrições dos EVs nos seus respectivos intervalos.
   Agora recebe ev_slots[] já calculado, para reaproveitar o mesmo valor
   no cálculo de L_bar (média temporal) fora desta função. */
void build_ev_constraints(GRBmodel *model,
                           int *cind, double *cval,
                           double *power, int *ev_slots,
                           int *start, int *end, int *wrap)
{
    /* Janela do EV: [start, end] e, no dia periódico (2026-10-08), também a
       continuação na madrugada [1, wrap] de sessões que passam da meia-noite
       (wrap = 0 sem continuação), a mesma de v2g_stable.build_avail. */
    for (int i = 0; i < N; i++) {
        int n = 0;
        int s_idx = start[i] - 1;
        int e_idx = end[i] - 1;

        for (int j = 0; j < 144; j++) {
            if ((j >= s_idx && j <= e_idx) || j < wrap[i]) {
                cind[n] = (i * 144) + j;
                cval[n] = 1.0;
                n++;
            }
        }

        char name[64];
        sprintf(name, "EV_%d", i);
        GRBaddconstr(model, n, cind, cval, GRB_EQUAL, (double)ev_slots[i], name);

        for (int j = 0; j < 144; j++) {
            if ((j < s_idx || j > e_idx) && j >= wrap[i]) {
                int var = (i * 144) + j;
                char cname[64];
                sprintf(cname, "EV_%d_off_%d", i, j);
                int one_ind = var;
                double one_val = 1.0;
                GRBaddconstr(model, 1, &one_ind, &one_val, GRB_EQUAL, 0.0, cname);
            }
        }
    }
}

/* F1: Restrições para Minimização de Pico (maxLoad) */
void build_peak_constraints(GRBmodel *model,
                            int *cind,
                            double *cval,
                            double *power,
                            double *pconv,
                            int maxLoad)
{
    for (int j = 0; j < 144; j++) {
        int n = 0;

        cind[n] = maxLoad;
        cval[n] = 1.0;
        n++;

        for (int i = 0; i < N; i++) {
            cind[n] = (i * 144) + j;
            cval[n] = -power[i];
            n++;
        }

        char name[64];
        sprintf(name, "Peak_%d", j);
        GRBaddconstr(model, n, cind, cval, GRB_GREATER_EQUAL, pconv[j], name);
    }
}

/* F2 Restrições para Minimização da soma dos desvios absolutos
   em relação à média temporal da carga total.

   f2(X) = (1/10) * sum_t |L^tot(t) - L_bar|

   Para cada slot j, cria uma variável de folga dev_j >= 0 e duas
   restrições que linearizam o valor absoluto:

     dev_j >= L^tot(j) - L_bar
     dev_j >= L_bar - L^tot(j)

   onde L^tot(j) = pconv[j] + sum_i power[i]*x[i][j].
   L_bar é uma CONSTANTE (calculada fora desta função), pois a energia
   total entregue por todos os EVs ao longo do dia é fixa. */
void build_deviation_constraints(GRBmodel *model,
                                  int *cind,
                                  double *cval,
                                  double *power,
                                  double *pconv,
                                  int *devIndex,
                                  double Lbar)
{
    for (int j = 0; j < 144; j++) {
        int n = 0;

        cind[n] = devIndex[j];
        cval[n] = 1.0;
        n++;

        for (int i = 0; i < N; i++) {
            cind[n] = (i * 144) + j;
            cval[n] = -power[i];
            n++;
        }

        char name_pos[64];
        sprintf(name_pos, "Dev_pos_%d", j);
        GRBaddconstr(model, n, cind, cval, GRB_GREATER_EQUAL, pconv[j] - Lbar, name_pos);

        n = 0;
        cind[n] = devIndex[j];
        cval[n] = 1.0;
        n++;

        for (int i = 0; i < N; i++) {
            cind[n] = (i * 144) + j;
            cval[n] = power[i];
            n++;
        }

        char name_neg[64];
        sprintf(name_neg, "Dev_neg_%d", j);
        GRBaddconstr(model, n, cind, cval, GRB_GREATER_EQUAL, Lbar - pconv[j], name_neg);
    }
}

/* F3: Restrições para Valley Utilization Penalty (slack de vale) */
void build_valley_constraints(GRBmodel *model,
                               int *cind,
                               double *cval,
                               double *power,
                               double *pconv,
                               int *slackValley,
                               double theta)
{
    for (int j = 0; j < 144; j++) {
        int n = 0;

        cind[n] = slackValley[j];
        cval[n] = 1.0;
        n++;

        for (int i = 0; i < N; i++) {
            cind[n] = (i * 144) + j;
            cval[n] = power[i];
            n++;
        }

        char name[64];
        sprintf(name, "Valley_%d", j);

        GRBaddconstr(model, n, cind, cval, GRB_GREATER_EQUAL, theta - pconv[j], name);
    }
}

static int set_obj_safe(GRBmodel *model,
                        int index,
                        int priority,
                        int var,
                        double coef,
                        const char *name)
{
    int lind[1];
    double lval[1];

    lind[0] = var;
    lval[0] = coef;

    return GRBsetobjectiven(model,
                            index,
                            priority,
                            1.0,
                            0.0,
                            0.0,
                            (char*)name,
                            0.0,
                            1,
                            lind,
                            lval);
}

int main(int argc, char *argv[])
{
    if (argc > 1) N = atoi(argv[1]);
    if (argc > 2) { strncpy(BOULDER, argv[2], MAXSTR - 1); BOULDER[MAXSTR - 1] = '\0'; }
    if (argc > 3) { strncpy(SEASON, argv[3], MAXSTR - 1); SEASON[MAXSTR - 1] = '\0'; }
    if (argc > 4) {
        fprintf(stderr, "Uso: %s [N] [BOULDER] [SEASON]\n", argv[0]);
        return 1;
    }
    printf("[CONFIG] N=%d BOULDER=%s SEASON=%s\n", N, BOULDER, SEASON);

    GRBenv   *env   = NULL;
    GRBenv   *menv  = NULL;
    GRBmodel *model = NULL;

    int error = 0;
    int *cind = NULL;
    double *cval = NULL;
    int status;
    int maxLoad;

    /* Alocados no heap, não na stack: em N grande (ex. N=18000,
       solution[N][144] sozinho passa de 10MB) essas VLAs estouravam a
       stack padrão (8MB no Linux) e causavam segfault silencioso — sem
       nenhum erro do Gurobi, o processo só morria. */
    int (*solution)[144]  = NULL;
    int *slackValley       = NULL;
    int *devIndex          = NULL;
    double *pconv          = NULL;
    double *energy         = NULL;
    double *power          = NULL;
    int *start             = NULL;
    int *end               = NULL;
    int *wrap              = NULL;
    int *ev_slots          = NULL;
    double *ts_remaining   = NULL;

    solution     = malloc((size_t)N * sizeof(*solution));
    slackValley  = malloc(144 * sizeof(int));
    devIndex     = malloc(144 * sizeof(int));
    pconv        = malloc(144 * sizeof(double));
    energy       = malloc((size_t)N * sizeof(double));
    power        = malloc((size_t)N * sizeof(double));
    start        = malloc((size_t)N * sizeof(int));
    end          = malloc((size_t)N * sizeof(int));
    wrap         = calloc((size_t)N, sizeof(int));
    ev_slots     = malloc((size_t)N * sizeof(int));
    ts_remaining = malloc((size_t)N * sizeof(double));

    if (!solution || !slackValley || !devIndex || !pconv || !energy ||
        !power || !start || !end || !wrap || !ev_slots || !ts_remaining) {
        printf("Erro de malloc nos arrays de EVs/slots (N=%d)\n", N);
        error = 1;
        goto QUIT;
    }
    /* ==========================================
       CAMINHOS DOS ARQUIVOS
       ========================================== */

    char dataset_path_ev[MAXSTR + 64];
    snprintf(dataset_path_ev, sizeof(dataset_path_ev), DATASET_PATH "%s/", BOULDER);
    char dataset_path_conv[MAXSTR];
    snprintf(dataset_path_conv, sizeof(dataset_path_conv),
             "../../../Datasets/ProcessedData/Cities/CampoGrande/");

    char path_f1[256];
    char path_f2[512];
    const char *filename_f1;
    /* SEASON = nome de um arquivo diário (pc_cgr_YYYYMMDD_<estação>_<dia>.csv)
       lê de Cities/CampoGrande/all_days/ — usado pela generalização de 32 dias
       (selecao_dias.py, run_generalizacao.py, nesta pasta). */
    if (strncmp(SEASON, "pc_", 3) == 0) {
        filename_f1 = SEASON;
        strncat(dataset_path_conv, "all_days/",
                sizeof(dataset_path_conv) - strlen(dataset_path_conv) - 1);
    } else if (strcmp(SEASON, "winter") == 0) {
        filename_f1 = "pc_cgr_20220707_winter_thu.csv";
    } else if (strcmp(SEASON, "baseline") == 0) {
        filename_f1 = "pc_cgr_20220611_baseline.csv";
    } else if (strcmp(SEASON, "spring") == 0) {
        filename_f1 = "pc_cgr_20221005_spring_wed.csv";
    } else if (strcmp(SEASON, "summer") == 0) {
        filename_f1 = "pc_cgr_20230104_summer_wed.csv";
    } else if (strcmp(SEASON, "autumn") == 0) {
        filename_f1 = "pc_cgr_20230405_autumn_wed.csv";
    } else {
        fprintf(stderr, "SEASON desconhecida: %s (use baseline/winter/spring/summer/autumn "
                        "ou um arquivo pc_cgr_*.csv de all_days/)\n", SEASON);
        return 1;
    }

    /* Nome de instância no mesmo padrão de Codes/E_V2G/scripts/
       lcvf_fast_v2g.py (inst_<N>_<BOULDER>_<schema>) — schema é
       filename_f1 sem o prefixo "pc_" e o sufixo ".csv". */
    char schema[64];
    {
        size_t len = strlen(filename_f1);
        size_t core_len = (len > 7) ? len - 3 - 4 : 0;  /* "pc_" + ".csv" */
        if (core_len >= sizeof(schema)) core_len = sizeof(schema) - 1;
        memcpy(schema, filename_f1 + 3, core_len);
        schema[core_len] = '\0';
    }
    char inst_dir[MAXSTR * 3];
    snprintf(inst_dir, sizeof(inst_dir),
             "../../../Results/V2G/CampoGrande/generalization/exact_gurobi/%s/inst_%d_%s_%s",
             BOULDER, N, BOULDER, schema);
    {
        /* BOULDER/SEASON só vêm de argv digitado localmente por quem roda
           o programa (não é input de rede/usuário remoto) — aceitável
           passar por mkdir -p via system() neste contexto. */
        char mkdir_cmd[MAXSTR * 4];
        snprintf(mkdir_cmd, sizeof(mkdir_cmd), "mkdir -p '%s'", inst_dir);
        system(mkdir_cmd);
    }

    snprintf(path_f1, sizeof(path_f1), "%s%s", dataset_path_conv, filename_f1);
    snprintf(path_f2, sizeof(path_f2), "%spp_%d_%s.csv", dataset_path_ev, N, BOULDER);

    /* ==========================================
       1. LEITURA DO CSV DE CARGA CONVENCIONAL (pconv)
       ========================================== */

    printf("[DEBUG] Lendo %s...\n", path_f1);

    FILE *f1 = fopen(path_f1, "r");

    if (!f1) {
        printf("Erro ao abrir f1: %s\n", path_f1);
        return 1;
    }

    char linha[200];

    fgets(linha, sizeof(linha), f1); /* pula cabeçalho */

    int idx_f1 = 0;

    while (fgets(linha, sizeof(linha), f1) && idx_f1 < 144) {

        int TS;
        double PEv, Pmax;

        if (sscanf(linha,
                   "%d,%*[^,],%lf,%lf,%lf",
                   &TS,
                   &pconv[idx_f1],
                   &PEv,
                   &Pmax) < 2) {

            printf("[AVISO] Linha do f1 inválida ou incompleta: %s\n",
                   linha);
        }

        idx_f1++;
    }

    fclose(f1);

    /* ==========================================
       2. LEITURA DO CSV DE EVs (novo formato lcvf_*)
       ========================================== */

    FILE *f2 = fopen(path_f2, "r");

    if (!f2) {
        printf("Erro ao abrir f2: %s\n", path_f2);
        return 1;
    }

    char linha2[512];

    /* NOVO: índices de coluna lidos do cabeçalho por NOME, não fixos —
       nem todo pp_*.csv usa o mesmo layout (ex.: wscc tem uma coluna
       "date" a mais e outras 2 a menos que bld_s1-s6/nor_s1; índices
       fixos faziam battery_capacity_kWh/energy_adjusted_kWh/start_ts/
       end_ts serem lidos da coluna errada, e o `nf < 20` rejeitava TODA
       linha do wscc silenciosamente — o modelo "resolvia" com 0 EVs
       carregados sem nenhum erro do Gurobi). */
    char header_line[512];
    if (!fgets(header_line, sizeof(header_line), f2)) {
        printf("Erro ao ler cabecalho de %s\n", path_f2);
        fclose(f2);
        return 1;
    }
    char *header_fields[NUM_FIELDS_EV];
    int n_header = split_csv_line(header_line, header_fields, NUM_FIELDS_EV);

    int idx_battery_capacity = -1, idx_energy_adjusted = -1, idx_start_ts = -1, idx_end_ts = -1;
    int idx_wrap_end_ts = -1;   /* opcional: sem a coluna, wrap = 0 */
    for (int k = 0; k < n_header; k++) {
        if (strcmp(header_fields[k], "battery_capacity_kWh") == 0) idx_battery_capacity = k;
        else if (strcmp(header_fields[k], "energy_adjusted_kWh") == 0) idx_energy_adjusted = k;
        else if (strcmp(header_fields[k], "start_ts") == 0) idx_start_ts = k;
        else if (strcmp(header_fields[k], "end_ts") == 0) idx_end_ts = k;
        else if (strncmp(header_fields[k], "wrap_end_ts", 11) == 0) idx_wrap_end_ts = k;
    }
    if (idx_battery_capacity < 0 || idx_energy_adjusted < 0 || idx_start_ts < 0 || idx_end_ts < 0) {
        printf("[ERRO] Coluna obrigatoria ausente no cabecalho de %s "
               "(battery_capacity_kWh=%d energy_adjusted_kWh=%d start_ts=%d end_ts=%d, -1=ausente)\n",
               path_f2, idx_battery_capacity, idx_energy_adjusted, idx_start_ts, idx_end_ts);
        fclose(f2);
        return 1;
    }
    int min_fields_needed = idx_battery_capacity;
    if (idx_energy_adjusted > min_fields_needed) min_fields_needed = idx_energy_adjusted;
    if (idx_start_ts        > min_fields_needed) min_fields_needed = idx_start_ts;
    if (idx_end_ts          > min_fields_needed) min_fields_needed = idx_end_ts;
    min_fields_needed += 1;

    int idx_f2 = 0;

    while (fgets(linha2, sizeof(linha2), f2) && idx_f2 < N) {

        char *fields[NUM_FIELDS_EV];
        int nf = split_csv_line(linha2, fields, NUM_FIELDS_EV);

        if (nf < min_fields_needed) {
            printf("[AVISO] Linha do f2 com menos campos que o esperado (%d, precisa de %d): %s\n",
                   nf, min_fields_needed, linha2);
            continue;
        }

        double battery_capacity_kWh = atof(fields[idx_battery_capacity]);
        double energy_adjusted_kWh  = atof(fields[idx_energy_adjusted]);
        start[idx_f2] = atoi(fields[idx_start_ts]);  /* start_ts — janela real de estacionamento */
        end[idx_f2]   = atoi(fields[idx_end_ts]);    /* end_ts   (não lcvf_start_2/lcvf_end_2,
                                               janela "legada" mais estreita — ver P3.3) */

        power[idx_f2] = CHARGER_POWER_KW;
        double energy_target = battery_capacity_kWh * SOC_FULL_TARGET - energy_adjusted_kWh;
        if (energy_target < 0.0) energy_target = 0.0;
        energy[idx_f2] = energy_target;

        if (idx_wrap_end_ts >= 0 && idx_wrap_end_ts < nf) wrap[idx_f2] = atoi(fields[idx_wrap_end_ts]);

        int s1 = start[idx_f2] > 1 ? start[idx_f2] : 1;
        int window_len = end[idx_f2] - s1 + 1;
        if (window_len < 0) window_len = 0;
        window_len += wrap[idx_f2];

        double ts_needed = ceil(energy_target / ENERGY_PER_SLOT_KWH);
        ts_remaining[idx_f2] = (ts_needed < window_len) ? ts_needed : (double)window_len;

        idx_f2++;
    }

    fclose(f2);

    if (idx_f2 < N) {
        printf("[AVISO] Apenas %d de %d EVs foram lidos de %s (verifique o dataset).\n",
               idx_f2, N, path_f2);
    }

    for (int i = 0; i < N; i++) {
        ev_slots[i] = (int)lround(ts_remaining[i]);

        if (power[i] > 0) {
            /* energy[i]/power[i] daria "horas", não slots — power[i] é kW real,
               não kWh/slot (era isso no esquema legado). Conversão certa usa
               ENERGY_PER_SLOT_KWH. Mismatch aqui = EV com janela mais curta que
               o necessário pro alvo de 90% SoC (esperado em <1% dos EVs). */
            int slots_por_energia = (int)ceil(energy[i] / ENERGY_PER_SLOT_KWH);
            if (slots_por_energia != ev_slots[i]) {
                printf("[AVISO] EV %d: ts_remaining=%d difere de round(energy/ENERGY_PER_SLOT_KWH)=%d (janela mais curta que o alvo)\n",
                       i, ev_slots[i], slots_por_energia);
            }
        }
    }

    double soma_pconv = 0.0;
    for (int j = 0; j < 144; j++) soma_pconv += pconv[j];

    double soma_energia_evs = 0.0;
    for (int i = 0; i < N; i++) soma_energia_evs += power[i] * ev_slots[i];

    double Lbar = (soma_pconv + soma_energia_evs) / 144.0;
    printf("[DEBUG] L_bar (media temporal da carga total) = %.4f\n", Lbar);

    /* 2. Ambiente e modelo */
    error = GRBloadenv(&env, "charging.log");
    if (error) goto QUIT;

    error = GRBnewmodel(env, &model, "charging_model", 0, NULL, NULL, NULL, NULL, NULL);
    if (error) goto QUIT;

    menv = GRBgetenv(model);
    assert(menv != NULL);

    /* 3. Variáveis binárias da matriz de solução */
    for (int r = 0; r < N; r++) {
        for (int c = 0; c < 144; c++) {
            char nome[32];
            sprintf(nome, "solution_%d_%d", r, c);
            error = GRBaddvar(model, 0, NULL, NULL, 0.0, 0.0, 1.0, GRB_BINARY, nome);
            if (error) goto QUIT;
            solution[r][c] = r * 144 + c;
        }
    }

    error = GRBaddvar(model, 0, NULL, NULL, 0.0, 0.0, GRB_INFINITY, GRB_CONTINUOUS, "maxLoad");
    if (error) goto QUIT;

    for (int j = 0; j < 144; j++) {
        char vname[32];
        sprintf(vname, "dev_%d", j);
        error = GRBaddvar(model, 0, NULL, NULL, 0.0, 0.0, GRB_INFINITY, GRB_CONTINUOUS, vname);
        if (error) goto QUIT;
    }

    for (int j = 0; j < 144; j++) {
        char vname[32];
        sprintf(vname, "slack_valley_%d", j);
        error = GRBaddvar(model, 0, NULL, NULL, 0.0, 0.0, GRB_INFINITY, GRB_CONTINUOUS, vname);
        if (error) goto QUIT;
    }

    error = GRBupdatemodel(model);
    if (error) goto QUIT;

    GRBgetvarbyname(model, "maxLoad", &maxLoad);
    for (int j = 0; j < 144; j++) {
        char vname[32];
        sprintf(vname, "dev_%d", j);
        GRBgetvarbyname(model, vname, &devIndex[j]);
    }
    for (int j = 0; j < 144; j++) {
        char vname[32];
        sprintf(vname, "slack_valley_%d", j);
        GRBgetvarbyname(model, vname, &slackValley[j]);
    }

    /* 4. Alocação dos scratchpads */
    int max_terms = N * 144;
    cind = malloc(sizeof(int) * max_terms);
    cval = malloc(sizeof(double) * max_terms);
    if (!cind || !cval) {
        printf("Erro de falta de memória (malloc)\n");
        goto QUIT;
    }

    /* 5. Restrições */
    build_ev_constraints(model, cind, cval, power, ev_slots, start, end, wrap);
    build_peak_constraints(model, cind, cval, power, pconv, maxLoad);
    build_deviation_constraints(model, cind, cval, power, pconv, devIndex, Lbar);

    double theta = mediana(pconv, 144);
    build_valley_constraints(model, cind, cval, power, pconv, slackValley, theta);

    /* 6. Multiobjetivo */
    error = set_obj_safe(model, 0, 1, maxLoad, 1.0, "f1");
    if (error) goto QUIT;

    {
        double *lval2 = malloc(sizeof(double) * 144);
        for (int j = 0; j < 144; j++) lval2[j] = 0.1;

        error = GRBsetobjectiven(model,
                                  1,
                                  0,
                                  1.0,
                                  0.0,
                                  0.0,
                                  "f2",
                                  0.0,
                                  144,
                                  devIndex,
                                  lval2);
        free(lval2);
        if (error) goto QUIT;
    }

    {
        double *lval3 = malloc(sizeof(double) * 144);
        for (int j = 0; j < 144; j++) lval3[j] = 0.1;

        error = GRBsetobjectiven(model,
                                  2,
                                  0,
                                  1.0,
                                  0.0,
                                  0.0,
                                  "f3",
                                  0.0,
                                  144,
                                  slackValley,
                                  lval3);
        free(lval3);
        if (error) goto QUIT;
    }

    /* 7. Otimização */
    error = GRBwrite(model, "multiobj_c.lp");
    if (error) goto QUIT;

    error = GRBoptimize(model);
    if (error) goto QUIT;

    error = GRBgetintattr(model, "Status", &status);
    if (error) goto QUIT;

    if (status == GRB_INF_OR_UNBD || status == GRB_INFEASIBLE || status == GRB_UNBOUNDED) {
        printf("The model cannot be solved because it is infeasible or unbounded\n");
        goto QUIT;
    }

    /* 8. Soluções */
    double soma = 0;
    if (status == GRB_OPTIMAL || status == GRB_SUBOPTIMAL) {
        int total_vars = N * 144;
        double *sol_vals = malloc(total_vars * sizeof(double));
        if (sol_vals) {
            error = GRBgetdblattrarray(model, GRB_DBL_ATTR_X, 0, total_vars, sol_vals);
            if (!error) {
                /* NOVO: aloc EV-por-slot da solução em CSV (mesmo diretório de
                   en_profile_final.csv/summary.csv) — igual à impressão em
                   stdout abaixo, só que persistido pra não precisar reabrir o
                   log inteiro pra ver quem foi alocado onde. */
                char solucao_path[MAXSTR * 4];
                snprintf(solucao_path, sizeof(solucao_path), "%s/solucao.csv", inst_dir);
                FILE *fsol = fopen(solucao_path, "w");
                if (fsol) fprintf(fsol, "EV,TS,Power_kW\n");

                printf("\n=== SOLUÇÃO ENCONTRADA ===\n");
                for (int r = 0; r < N; r++) {
                    for (int c = 0; c < 144; c++) {
                        int idx = solution[r][c];
                        if (sol_vals[idx] > 0.9) {
                            printf("EV %d alocado no TS %d\n", r, c);
                            if (fsol) fprintf(fsol, "%d,%d,%.4f\n", r, c + 1, power[r]);
                            soma += power[r];
                        }
                    }
                }
                if (fsol) {
                    fclose(fsol);
                    printf("[salvo] %s\n", solucao_path);
                } else {
                    printf("[AVISO] Nao foi possivel abrir %s para escrita\n", solucao_path);
                }
            }
            free(sol_vals);
        }
    } else {
        printf("Optimization stopped with status: %d\n", status);
    }
    printf("%.4f", soma);

    /* 9. CÁLCULO SEPARADO DE f1, f2 E f3 A PARTIR DA SOLUÇÃO ÓTIMA */
    if (status == GRB_OPTIMAL || status == GRB_SUBOPTIMAL) {

        int total_vars_f = N * 144;
        double *x_vals = malloc(total_vars_f * sizeof(double));
        if (!x_vals) { printf("Erro de malloc em x_vals\n"); goto QUIT; }

        error = GRBgetdblattrarray(model, GRB_DBL_ATTR_X, 0, total_vars_f, x_vals);
        if (error) { free(x_vals); goto QUIT; }

        double Ltot[144];
        for (int j = 0; j < 144; j++) {
            double s = pconv[j];
            for (int i = 0; i < N; i++) {
                s += power[i] * x_vals[(i * 144) + j];
            }
            Ltot[j] = s;
        }

        /* NOVO: para cada slot, verifica se pelo menos um EV está alocado
           nele (não apenas a carga convencional pconv). */
        int has_ev[144];
        for (int j = 0; j < 144; j++) {
            has_ev[j] = 0;
            for (int i = 0; i < N; i++) {
                if (x_vals[(i * 144) + j] > 0.5) {
                    has_ev[j] = 1;
                    break;
                }
            }
        }

        /* NOVO: pico real recalculado diretamente de Ltot (a partir da
           solução final), e pico restrito aos slots que têm pelo menos
           um EV alocado — é o que foi pedido: "a energia total do maior
           time slot que contenha pelo menos uma carga de EV". */
        double f1_real = -INFINITY;
        int j_peak_real = -1;
        double peak_com_ev = -INFINITY;
        int j_peak_com_ev = -1;

        for (int j = 0; j < 144; j++) {
            if (Ltot[j] > f1_real) {
                f1_real = Ltot[j];
                j_peak_real = j;
            }
            if (has_ev[j] && Ltot[j] > peak_com_ev) {
                peak_com_ev = Ltot[j];
                j_peak_com_ev = j;
            }
        }

        double f1_val = 0.0;
        error = GRBgetdblattrelement(model, GRB_DBL_ATTR_X, maxLoad, &f1_val);
        if (error) { printf("Erro ao ler maxLoad\n"); free(x_vals); goto QUIT; }

        /* f2: (1/10) * soma dos desvios absolutos em relação a Lbar */
        double f2_val = 0.0;
        for (int j = 0; j < 144; j++) {
            f2_val += fabs(Ltot[j] - Lbar);
        }
        f2_val *= 0.1;

        double dev_sum = 0.0;
        for (int j = 0; j < 144; j++) {
            double dv = 0.0;
            GRBgetdblattrelement(model, GRB_DBL_ATTR_X, devIndex[j], &dv);
            dev_sum += dv;
        }

        double f3_val = 0.0;
        for (int j = 0; j < 144; j++) {
            printf("\n%d: %.4f", j, Ltot[j]);
            double diff = theta - Ltot[j];
            if (diff > 0.0) f3_val += diff;
        }
        f3_val *= 0.1;

        printf("\n=== VALORES DOS OBJETIVOS ===\n");

        /* CORRIGIDO: a variável maxLoad do Gurobi pode ficar "folgada" e
           não bater com o pico real da alocação final. Isso acontece
           porque maxLoad só aparece: (1) nas restrições
           "maxLoad >= L_tot(j)" (uma por slot) e (2) no objetivo de
           prioridade 1 (f1). Depois que f1 é otimizado, o Gurobi passa a
           otimizar f2+f3 (prioridade 0) mantendo apenas a restrição
           "maxLoad <= valor_ótimo_de_f1" (já que abstol=reltol=0). Como
           maxLoad não aparece em f2 nem em f3, nada obriga o solver a
           reduzi-lo até o pico verdadeiro da alocação final escolhida
           nesse segundo estágio — ele pode ficar maior que o pico real
           (a alocação final, no entanto, nunca pode ULTRAPASSAR o valor
           de maxLoad, pois a restrição continua valendo). Por isso agora
           recalculamos f1 diretamente de Ltot[] (que vem da própria
           solução ótima) e usamos ESSE valor (f1_real) como o correto;
           f1_val (variável) é mantido só para referência/depuração. */
        printf("f1 (variavel maxLoad do Gurobi)       = %.4f kW\n", f1_val);
        printf("f1 (pico real recalculado da solucao) = %.4f kW  (slot %d)\n",
               f1_real, j_peak_real);
        if (fabs(f1_val - f1_real) > 1e-4) {
            printf("[AVISO] maxLoad (%.4f) nao bate com o pico real da solucao (%.4f).\n"
                   "        Motivo: maxLoad nao entra no objetivo de f2/f3, entao o\n"
                   "        solver nao tem por que apertar essa variavel no segundo\n"
                   "        estagio da otimizacao hierarquica. Use f1_real acima como\n"
                   "        o valor confiavel de f1.\n",
                   f1_val, f1_real);
        }

        /* NOVO: energia total (carga) do maior slot que contém pelo menos
           uma carga de EV alocada (pedido do usuário). */
        if (j_peak_com_ev >= 0) {
            printf("Maior slot com >=1 EV alocado          = slot %d, carga total = %.4f kW\n",
                   j_peak_com_ev, peak_com_ev);
        } else {
            printf("Nenhum slot com EV alocado foi encontrado na solucao.\n");
        }

        printf("f2 (desvio da media, x0.1) = %.4f  |  soma dev_j (sem peso): %.4f\n", f2_val, dev_sum);
        printf("f3 (penalidade de vale, x0.1)    = %.4f\n", f3_val);
        printf("theta usado                = %.4f kW\n", theta);
        printf("L_bar usado                = %.4f kW\n", Lbar);

        /* NOVO: pico sozinho nao e uma medida justa de qualidade da alocacao
           (ex.: uma solucao poderia "esconder" EVs sem carregar pra nao
           mexer no pico). Reporta tambem quanta energia foi de fato
           entregue contra o total demandado (ver docs/04 P3.3 sobre o
           alvo de 90% SoC por EV). */
        double total_energy_demanded = 0.0, total_energy_delivered = 0.0;
        for (int i = 0; i < N; i++) {
            total_energy_demanded  += energy[i];
            total_energy_delivered += ev_slots[i] * power[i] * TS_HOURS;
        }
        double pct_delivered = (total_energy_demanded > 1e-9)
            ? (100.0 * total_energy_delivered / total_energy_demanded) : 100.0;
        printf("Energia demandada (alvo 90%% SoC) = %.4f kWh\n", total_energy_demanded);
        printf("Energia efetivamente entregue    = %.4f kWh (%.2f%%)\n",
               total_energy_delivered, pct_delivered);

        /* NOVO: perfil por timeslot (Pconv_inicial/Pconv_final/energia
           alocada), no MESMO formato de en_profile_final.csv de
           lcvf_fast_v2g.py e en_profile_all_stages.csv de v2g_stable.py —
           salvo dentro de Results/V2G/CampoGrande/exact_gurobi/, ao lado
           de lcvf_v2g/ e do V2G 3 estágios, pra comparar os três
           pipelines lado a lado (mesmas colunas, mesma estrutura de
           pasta por instância). */
        char csv_path[MAXSTR * 4];
        snprintf(csv_path, sizeof(csv_path), "%s/en_profile_final.csv", inst_dir);
        FILE *fcsv = fopen(csv_path, "w");
        if (fcsv) {
            fprintf(fcsv, "TS,Pconv_inicial,Pconv_final,Energia_alocada_kWh\n");
            for (int j = 0; j < 144; j++) {
                double ev_kwh = (Ltot[j] - pconv[j]) * TS_HOURS;
                fprintf(fcsv, "%d,%.5f,%.5f,%.5f\n", j + 1, pconv[j], Ltot[j], ev_kwh);
            }
            fclose(fcsv);
            printf("[salvo] %s\n", csv_path);
        } else {
            printf("[AVISO] Nao foi possivel abrir %s para escrita\n", csv_path);
        }

        /* NOVO: resumo de uma linha por execucao, pra juntar varios
           cenarios numa tabela sem reabrir cada en_profile_final.csv. */
        char summary_path[MAXSTR * 4];
        snprintf(summary_path, sizeof(summary_path), "%s/summary.csv", inst_dir);
        FILE *fsum = fopen(summary_path, "w");
        if (fsum) {
            fprintf(fsum,
                "N,BOULDER,SEASON,Pico_kW,Slot_pico,Energia_demandada_kWh,"
                "Energia_entregue_kWh,Pct_entregue,F2_desvio_media,F3_penalidade_vale\n");
            fprintf(fsum, "%d,%s,%s,%.4f,%d,%.4f,%.4f,%.4f,%.4f,%.4f\n",
                    N, BOULDER, SEASON, f1_real, j_peak_real + 1,
                    total_energy_demanded, total_energy_delivered, pct_delivered,
                    f2_val, f3_val);
            fclose(fsum);
            printf("[salvo] %s\n", summary_path);
        }

        free(x_vals);
    }

QUIT:
    if (cind != NULL)  free(cind);
    if (cval != NULL)  free(cval);
    free(solution);
    free(slackValley);
    free(devIndex);
    free(pconv);
    free(energy);
    free(power);
    free(start);
    free(end);
    free(wrap);
    free(ev_slots);
    free(ts_remaining);
    if (model != NULL) GRBfreemodel(model);
    if (env != NULL)   GRBfreeenv(env);

    return error;
}