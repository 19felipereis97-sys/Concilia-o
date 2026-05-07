from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from _config import EXTRATO_PATH, FINANCEIRO_PATH, make_params, make_extrato_mapping, make_financeiro_mapping
from core.collision import resolve_collisions
from core.combo_search import find_combos
from core.engine import run_engine
from core.match_one_to_one import match_one_to_one
from core.normalize import normalize_extrato, normalize_financeiro
from core.scope import apply_financeiro_scope


def main() -> None:
    params = make_params()
    fin_mapping = make_financeiro_mapping()

    df_b = normalize_extrato(EXTRATO_PATH, make_extrato_mapping(), params)
    df_f = normalize_financeiro(FINANCEIRO_PATH, fin_mapping, params)
    df_b, _ = apply_financeiro_scope(df_b, fin_mapping.modalidade)

    df_step_b = df_b.copy()
    df_step_f = df_f.copy()
    df_step_b, df_step_f, pairs_d0 = match_one_to_one(df_step_b, df_step_f, params, offsets=[0])
    df_step_b, df_step_f = resolve_collisions(df_step_b, df_step_f, pairs_d0, params)
    target = df_step_b[
        (df_step_b["_data"].astype(str) == "2025-09-02")
        & df_step_b["_historico"].str.contains("POSTO TRES GARCAS", case=False, na=False)
    ].iloc[0]
    candidates = df_step_f[
        (df_step_f["_status"] == "IGNORADO_SEM_CONTRAPARTIDA")
        & (df_step_f["_data"].astype(str) == "2025-09-02")
        & (df_step_f["_valor"] < 0)
        & (df_step_f["_valor"].abs() <= abs(target["_valor"]))
    ].copy()
    matches = find_combos([float(v) for v in candidates["_valor"]], float(target["_valor"]), 0.0, params.max_group_size)
    print("ANTES DO PASSO 1:N D0")
    print("Alvo:", target["_id"], target["_valor"], target["_historico"])
    print("Candidatos livres no mesmo dia/sinal:", len(candidates))
    print("Combinações encontradas para o mesmo valor:", len(matches))
    for n, match in enumerate(matches[:5], 1):
        combo = candidates.iloc[list(match)]
        print(f"Combo {n}: qtd={len(combo)} soma={combo['_valor'].sum()} ids={';'.join(combo['_id'].astype(str))}")
        print("  historicos=", " | ".join(combo["_historico"].astype(str).head(12)))

    df_b, df_f = run_engine(df_b, df_f, params)

    mask_b = (
        (df_b["_data"].astype(str) == "2025-09-02")
        & df_b["_historico"].str.contains("POSTO TRES GARCAS", case=False, na=False)
    )
    print("BANCO alvo")
    print(df_b.loc[mask_b, ["_id", "_data", "_valor", "_historico", "_status", "_metodo", "_ids_fin"]].to_string(index=False))

    mask_f = (
        (df_f["_data"].astype(str) == "2025-09-02")
        & df_f["_historico"].str.contains("POSTO TRES GARCAS", case=False, na=False)
    )
    cols_f = ["_id", "_data", "_valor", "_historico", "_status", "_metodo", "_id_bnk", "PAGTO_PARCIAL", "NAT_DESCRIC"]
    print("\nFINANCEIRO POSTO 02/09")
    print(df_f.loc[mask_f, cols_f].to_string(index=False))
    print("Soma fin posto:", df_f.loc[mask_f, "_valor"].sum())

    ids_b = sorted(set(";".join(df_f.loc[mask_f, "_id_bnk"].astype(str)).split(";")) - {""})
    print("\nBANCOS ligados a esses financeiros:", ids_b)
    cols_b = ["_id", "_data", "_valor", "_historico", "_status", "_metodo", "_ids_fin"]
    print(df_b[df_b["_id"].isin(ids_b)][cols_b].sort_values(["_data", "_id"]).to_string(index=False))

    ids_fin = sorted(set(df_b[df_b["_id"].isin(ids_b)]["_ids_fin"].astype(str)) - {""})
    print("\nFINANCEIROS destino desses bancos:", ids_fin)
    print(df_f[df_f["_id"].isin(ids_fin)][cols_f].to_string(index=False))


if __name__ == "__main__":
    main()
