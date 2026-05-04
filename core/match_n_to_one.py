"""
Conciliação N:1 — soma de N linhas bancárias = 1 linha financeira.
Caso típico: SISPAG agrupa vários pagamentos em um único lançamento no extrato.

Executado somente após a passagem 1:1; portanto, todas as linhas bancárias e
financeiras aqui presentes já são confirmadamente sem pareamento naquele passo.
A análise combinatória considera exclusivamente o mesmo dia do extrato (sem D±2).
"""
from __future__ import annotations
import time
from collections import defaultdict
from typing import Tuple

import pandas as pd

from .normalize import (
    STATUS_SEM_PAREAMENTO, STATUS_IGNORADO_SEM_PAR,
    STATUS_CONCILIADO, STATUS_REVISAR,
)
from .params import ConciliacaoParams
from .combo_search import find_combos
from .candidate_selection import limit_subset_candidates


def match_n_to_one(
    df_bnk: pd.DataFrame,
    df_fin: pd.DataFrame,
    params: ConciliacaoParams,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Para cada linha financeira livre, busca combinações de linhas bancárias
    livres (mesma data, mesmo sinal) cuja soma bate com o valor financeiro.

    Otimizações aplicadas:
    - Dica 3: filtra candidatos cujo |valor| > |alvo| antes da combinatória.
    - Dica 6: usa _valor_f (float pré-computado) sem chamar float() por linha.
    - Pre-agrupa candidatos bancários por (data, sinal) — O(n) vs O(n²).
    - Conjunto free_bnk atualizado incrementalmente com .discard().
    - Pre-check de impossibilidade: pula grupo se soma total < alvo.
    - Deadline por grupo: interrompe find_combos se demorar demais (MITM O(2^(n/2))).
    - Combinatória delegada a combo_search.
    """
    tol = float(params.value_tolerance_cents) / 100
    use_deadline = params.combo_timeout_sec > 0

    bnk_pos = dict(zip(df_bnk["_id"], df_bnk.index))

    # Pré-agrupa linhas bancárias LIVRES por (data, sinal) — dica 6: usa _valor_f
    bnk_groups: dict = defaultdict(list)
    cols = ["_id", "_data", "_valor", "_valor_f"] if "_valor_f" in df_bnk.columns else ["_id", "_data", "_valor"]
    for rec in df_bnk.loc[df_bnk["_status"] == STATUS_SEM_PAREAMENTO, cols].to_dict("records"):
        vf = rec.get("_valor_f", float(rec["_valor"]))
        sign = vf > 0
        bnk_groups[(rec["_data"], sign)].append({"_id": rec["_id"], "_valor_f": vf})

    free_bnk: set = set(df_bnk.loc[df_bnk["_status"] == STATUS_SEM_PAREAMENTO, "_id"])

    fin_free_df = df_fin[df_fin["_status"] == STATUS_IGNORADO_SEM_PAR]
    fin_cols = ["_id", "_data", "_valor", "_valor_f"] if "_valor_f" in df_fin.columns else ["_id", "_data", "_valor"]
    for fi, row_f in zip(fin_free_df.index, fin_free_df[fin_cols].to_dict("records")):
        target_f = row_f.get("_valor_f", float(row_f["_valor"]))
        sign = target_f > 0
        abs_target = abs(target_f)

        # Candidatos bancários na mesma data e mesmo sinal, ainda livres
        # Dica 3: descarta candidatos cujo |valor| > |alvo| (nunca entram numa soma válida)
        candidatos = [
            c for c in bnk_groups.get((row_f["_data"], sign), [])
            if c["_id"] in free_bnk and abs(c["_valor_f"]) <= abs_target + tol
        ]
        if len(candidatos) < 2:
            continue

        # Pre-check: impossível atingir o alvo mesmo somando todos os candidatos
        if sum(abs(c["_valor_f"]) for c in candidatos) < abs_target - tol:
            continue

        candidatos_busca, limited = limit_subset_candidates(
            candidatos,
            target_f,
            int(getattr(params, "max_candidates_per_group", 0) or 0),
        )
        if len(candidatos_busca) < 2:
            continue

        vals = [c["_valor_f"] for c in candidatos_busca]
        deadline = time.monotonic() + params.combo_timeout_sec if use_deadline else None
        matches = find_combos(vals, target_f, tol, params.max_group_size, deadline=deadline)

        if not matches:
            continue

        if len(matches) == 1:
            combo_rows = [candidatos_busca[i] for i in matches[0]]
            ids_bnk = [r["_id"] for r in combo_rows]
            metodo = f"N:1 soma={len(ids_bnk)}"

            for r in combo_rows:
                bi = bnk_pos[r["_id"]]
                df_bnk.at[bi, "_status"] = STATUS_CONCILIADO
                df_bnk.at[bi, "_metodo"] = metodo
                df_bnk.at[bi, "_ids_fin"] = row_f["_id"]
                free_bnk.discard(r["_id"])

            df_fin.at[fi, "_status"] = STATUS_CONCILIADO
            df_fin.at[fi, "_metodo"] = metodo
            df_fin.at[fi, "_id_bnk"] = ";".join(ids_bnk)
        else:
            seen = set()
            for combo_idx in matches:
                for i in combo_idx:
                    rid = candidatos_busca[i]["_id"]
                    if rid in seen:
                        continue
                    seen.add(rid)
                    bi = bnk_pos[rid]
                    if df_bnk.at[bi, "_status"] == STATUS_SEM_PAREAMENTO:
                        df_bnk.at[bi, "_status"] = STATUS_REVISAR
                        df_bnk.at[bi, "_metodo"] = "N:1 grupo limitado" if limited else "N:1 ambiguo"

    return df_bnk, df_fin
