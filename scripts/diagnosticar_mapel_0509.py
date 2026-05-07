from pathlib import Path
import sys
import time

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from _config import EXTRATO_PATH, FINANCEIRO_PATH, make_extrato_mapping, make_financeiro_mapping, make_params
from core.engine import run_engine
from core.match_n_to_one import match_n_to_one
from core.normalize import normalize_extrato, normalize_financeiro
from core.scope import apply_financeiro_scope
from core.combo_search import find_combos
from core.candidate_selection import limit_subset_candidates


TARGET_DATE = "2025-09-05"
TARGET_TEXT = "MAPEL SERV"
TARGET_VALUE = -185100.00


def _money(v) -> str:
    return f"{float(v):,.2f}"


def main() -> None:
    params = make_params()
    print("PARAMS")
    print("max_group_size:", params.max_group_size)
    print("max_candidates_per_group:", params.max_candidates_per_group)
    print("n_to_one_max_candidates:", getattr(params, "n_to_one_max_candidates", None))
    print("combo_timeout_sec:", params.combo_timeout_sec)

    fin_mapping = make_financeiro_mapping()
    df_b = normalize_extrato(EXTRATO_PATH, make_extrato_mapping(), params)
    df_f = normalize_financeiro(FINANCEIRO_PATH, fin_mapping, params)
    df_b, scoped = apply_financeiro_scope(df_b, fin_mapping.modalidade)
    print("scoped:", scoped)

    day_b = df_b[df_b["_data"].astype(str) == TARGET_DATE].copy()
    day_f = df_f[df_f["_data"].astype(str) == TARGET_DATE].copy()

    mapel_b = day_b[day_b["_historico"].str.contains(TARGET_TEXT, case=False, na=False)].copy()
    target_fin = day_f[(day_f["_valor"].astype(float).round(2) == TARGET_VALUE)].copy()
    mapel_fin = day_f[day_f["_historico"].str.contains(TARGET_TEXT, case=False, na=False)].copy()

    print("\nBANCO MAPEL 05/09")
    print("qtd:", len(mapel_b), "soma:", _money(mapel_b["_valor"].sum()))
    print(mapel_b[["_id", "_data", "_valor", "_historico", "_status", "_metodo", "_ids_fin"]].to_string(index=False))

    print("\nFINANCEIRO valor -185100 no dia")
    print(target_fin[["_id", "_data", "_valor", "_historico", "_status", "_metodo", "_id_bnk"]].to_string(index=False))

    print("\nFINANCEIRO MAPEL no dia")
    print("qtd:", len(mapel_fin), "soma:", _money(mapel_fin["_valor"].sum()) if not mapel_fin.empty else "0.00")
    if not mapel_fin.empty:
        print(mapel_fin[["_id", "_data", "_valor", "_historico"]].to_string(index=False))

    if target_fin.empty:
        print("\nNao encontrei financeiro exatamente -185100.00 em 05/09.")
        return

    target = target_fin.iloc[0]
    candidates = [
        {"_id": r["_id"], "_valor_f": float(r["_valor"])}
        for r in day_b.to_dict("records")
        if str(r.get("_status", "")) == "SEM_PAREAMENTO"
        and float(r["_valor"]) < 0
        and abs(float(r["_valor"])) <= abs(float(target["_valor"]))
    ]
    vals = [c["_valor_f"] for c in candidates]
    print("\nCANDIDATOS N:1 antes do motor")
    print("qtd candidatos:", len(candidates))
    print("soma candidatos:", _money(sum(vals)))
    mapel_candidate_ids = set(mapel_b["_id"].astype(str))
    mapel_candidate_idx = [i for i, c in enumerate(candidates) if c["_id"] in mapel_candidate_ids]
    mapel_candidate_sum = sum(candidates[i]["_valor_f"] for i in mapel_candidate_idx)
    print("candidatos MAPEL dentro do grupo:", [candidates[i]["_id"] for i in mapel_candidate_idx])
    print("soma candidatos MAPEL:", _money(mapel_candidate_sum))

    limited_candidates, limited = limit_subset_candidates(
        candidates,
        float(target["_valor"]),
        int(getattr(params, "n_to_one_max_candidates", 30) or 0),
        max_group_size=params.max_group_size,
    )
    vals_limited = [c["_valor_f"] for c in limited_candidates]
    print("\nCANDIDATOS N:1 limitados")
    print("limited:", limited, "qtd:", len(limited_candidates))
    deadline = time.monotonic() + max(float(params.combo_timeout_sec), 1.0)
    matches_limited = find_combos(
        vals_limited,
        float(target["_valor"]),
        0.0,
        params.max_group_size,
        deadline=deadline,
        stop_after_first_k=True,
    )
    print("matches limitados:", len(matches_limited))
    for i, match in enumerate(matches_limited, 1):
        ids = [limited_candidates[j]["_id"] for j in match]
        total = sum(limited_candidates[j]["_valor_f"] for j in match)
        print(f"match limitado {i}: qtd={len(ids)} soma={_money(total)} ids={';'.join(ids)}")

    print("\nRODANDO MOTOR COMPLETO")
    df_b2, df_f2 = run_engine(df_b.copy(), df_f.copy(), params, include_partial=False)
    print("Banco MAPEL apos motor")
    print(df_b2[df_b2["_id"].isin(mapel_b["_id"])][["_id", "_valor", "_historico", "_status", "_metodo", "_ids_fin"]].to_string(index=False))
    print("Financeiro alvo apos motor")
    print(df_f2[df_f2["_id"].isin(target_fin["_id"])][["_id", "_valor", "_historico", "_status", "_metodo", "_id_bnk"]].to_string(index=False))

    print("\nRODANDO N:1 isolado no dia")
    iso_b, iso_f = match_n_to_one(day_b.copy(), day_f.copy(), params)
    print(iso_f[iso_f["_id"].isin(target_fin["_id"])][["_id", "_valor", "_historico", "_status", "_metodo", "_id_bnk"]].to_string(index=False))


if __name__ == "__main__":
    main()
