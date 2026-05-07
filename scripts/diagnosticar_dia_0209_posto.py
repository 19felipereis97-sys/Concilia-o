from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from _config import EXTRATO_PATH, FINANCEIRO_PATH, make_params, make_extrato_mapping, make_financeiro_mapping
from core.collision import resolve_collisions
from core.combo_search import find_combos
from core.engine import run_engine
from core.match_one_to_one import match_one_to_one
from core.match_one_to_n import match_one_to_n
from core.normalize import normalize_extrato, normalize_financeiro
from core.scope import apply_financeiro_scope

TARGET_DATE = "2025-09-02"
TARGET_TEXT = "POSTO TRES GARCAS"


def _load():
    params = make_params()
    fin_mapping = make_financeiro_mapping()
    df_b = normalize_extrato(EXTRATO_PATH, make_extrato_mapping(), params)
    df_f = normalize_financeiro(FINANCEIRO_PATH, fin_mapping, params)
    df_b, scoped = apply_financeiro_scope(df_b, fin_mapping.modalidade)
    return df_b, df_f, params, scoped


def _target_bank(df_b):
    rows = df_b[
        (df_b["_data"].astype(str) == TARGET_DATE)
        & df_b["_historico"].str.contains(TARGET_TEXT, case=False, na=False)
    ]
    if rows.empty:
        raise RuntimeError("Lancamento bancario alvo nao encontrado.")
    return rows.iloc[0]


def _print_target_result(title, df_b, df_f):
    print(f"\n{title}")
    target = _target_bank(df_b)
    print(df_b.loc[[target.name], ["_id", "_data", "_valor", "_historico", "_status", "_metodo", "_ids_fin"]].to_string(index=False))
    ids = [x for x in str(target.get("_ids_fin", "")).split(";") if x]
    if ids:
        print(df_f[df_f["_id"].isin(ids)][["_id", "_data", "_valor", "_historico", "_status", "_metodo", "_id_bnk"]].to_string(index=False))


def main() -> None:
    df_b, df_f, params, scoped = _load()
    target = _target_bank(df_b)

    day_b = df_b[df_b["_data"].astype(str) == TARGET_DATE].copy()
    day_f = df_f[df_f["_data"].astype(str) == TARGET_DATE].copy()
    posto_f = day_f[day_f["_historico"].str.contains(TARGET_TEXT, case=False, na=False)].copy()

    print("BASE DO DIA 02/09")
    print("Banco total no dia:", len(day_b), "soma:", day_b["_valor"].sum())
    print("Financeiro total no dia:", len(day_f), "soma:", day_f["_valor"].sum())
    print("Financeiro POSTO no dia:", len(posto_f), "soma:", posto_f["_valor"].sum())
    print("Banco alvo:", target["_id"], target["_valor"], target["_historico"])

    print("\nFINANCEIROS POSTO DO DIA")
    print(posto_f[["_id", "_data", "_valor", "_historico", "_classif"]].to_string(index=False))

    step_b = df_b.copy()
    step_f = df_f.copy()
    step_b, step_f, pairs = match_one_to_one(step_b, step_f, params, offsets=[0])
    step_b, step_f = resolve_collisions(step_b, step_f, pairs, params)
    step_target = _target_bank(step_b)
    candidates = step_f[
        (step_f["_status"] == "IGNORADO_SEM_CONTRAPARTIDA")
        & (step_f["_data"].astype(str) == TARGET_DATE)
        & (step_f["_valor"] < 0)
        & (step_f["_valor"].abs() <= abs(step_target["_valor"]))
    ].copy()
    matches = find_combos([float(v) for v in candidates["_valor"]], float(step_target["_valor"]), 0.0, params.max_group_size)
    print("\nANTES DO 1:N D0 NA EXECUCAO COMPLETA")
    print("Candidatos financeiros livres no mesmo dia/sinal:", len(candidates))
    print("Combos exatos encontrados:", len(matches))
    for n, match in enumerate(matches, 1):
        combo = candidates.iloc[list(match)]
        print(f"Combo {n}: qtd={len(combo)} soma={combo['_valor'].sum()}")
        print(combo[["_id", "_valor", "_historico"]].to_string(index=False))

    day_b_run, day_f_run = run_engine(day_b.copy(), day_f.copy(), make_params())
    _print_target_result("RESULTADO RODANDO APENAS 02/09 COM TODOS OS LANCAMENTOS DO DIA", day_b_run, day_f_run)

    target_only_b = day_b[day_b["_id"] == target["_id"]].copy()
    posto_only_b, posto_only_f = run_engine(target_only_b.copy(), posto_f.copy(), make_params())
    _print_target_result("RESULTADO RODANDO BANCO ALVO CONTRA SOMENTE OS 8 FINANCEIROS POSTO", posto_only_b, posto_only_f)

    d0_b, d0_f = match_one_to_n(target_only_b.copy(), posto_f.copy(), make_params(), offsets=[0])
    _print_target_result("RESULTADO DO PASSO 1:N D0 ISOLADO COM SOMENTE POSTO", d0_b, d0_f)


if __name__ == "__main__":
    main()
