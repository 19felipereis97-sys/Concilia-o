"""
Conciliação 1:N — 1 linha bancária = soma de N linhas financeiras.
Caso típico: extrato agrega vários recebimentos/pagamentos em um único lançamento.

Executado somente após as passagens 1:1 e N:1; portanto, todas as linhas aqui
presentes já são confirmadamente sem pareamento naqueles passos.
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


def match_one_to_n(
    df_bnk: pd.DataFrame,
    df_fin: pd.DataFrame,
    params: ConciliacaoParams,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Para cada linha bancária livre, busca combinações de linhas financeiras
    livres (mesma data, mesmo sinal) cuja soma bate com o valor bancário.

    Otimizações aplicadas:
    - Dica 3: filtra candidatos cujo |valor| > |alvo| antes da combinatória.
    - Dica 6: usa _valor_f (float pré-computado) sem chamar float() por linha.
    - Pre-agrupa candidatos financeiros por (data, sinal) — O(n) vs O(n²).
    - Conjunto free_fin atualizado incrementalmente com .discard().
    - Cap adaptativo via params.effective_max_candidates() (limita C(n,k)).
    - Pre-check de impossibilidade: pula grupo se soma total < alvo.
    - Deadline por grupo: interrompe find_combos se demorar demais.
    - Combinatória delegada a combo_search.
    """
    tol = float(params.value_tolerance_cents) / 100
    use_deadline = params.combo_timeout_sec > 0

    fin_pos = dict(zip(df_fin["_id"], df_fin.index))

    # Pré-agrupa linhas financeiras LIVRES por (data, sinal) — dica 6: usa _valor_f
    fin_groups: dict = defaultdict(list)
    cols = ["_id", "_data", "_valor", "_valor_f"] if "_valor_f" in df_fin.columns else ["_id", "_data", "_valor"]
    for rec in df_fin.loc[df_fin["_status"] == STATUS_IGNORADO_SEM_PAR, cols].to_dict("records"):
        vf = rec.get("_valor_f", float(rec["_valor"]))
        sign = vf > 0
        fin_groups[(rec["_data"], sign)].append({"_id": rec["_id"], "_valor_f": vf})

    free_fin: set = set(df_fin.loc[df_fin["_status"] == STATUS_IGNORADO_SEM_PAR, "_id"])

    bnk_free_df = df_bnk[df_bnk["_status"] == STATUS_SEM_PAREAMENTO]
    bnk_cols = ["_id", "_data", "_valor", "_valor_f"] if "_valor_f" in df_bnk.columns else ["_id", "_data", "_valor"]
    for bi, row_b in zip(bnk_free_df.index, bnk_free_df[bnk_cols].to_dict("records")):
        target_f = row_b.get("_valor_f", float(row_b["_valor"]))
        sign = target_f > 0
        abs_target = abs(target_f)

        # Candidatos financeiros na mesma data e mesmo sinal, ainda livres
        # Dica 3: descarta candidatos cujo |valor| > |alvo| (nunca entram numa soma válida)
        candidatos = [
            c for c in fin_groups.get((row_b["_data"], sign), [])
            if c["_id"] in free_fin and abs(c["_valor_f"]) <= abs_target + tol
        ]

        if len(candidatos) < 2:
            continue

        # Pre-check: impossível atingir o alvo mesmo somando todos os candidatos
        if sum(abs(c["_valor_f"]) for c in candidatos) < abs_target - tol:
            continue

        vals = [c["_valor_f"] for c in candidatos]
        deadline = time.monotonic() + params.combo_timeout_sec if use_deadline else None
        matches = find_combos(vals, target_f, tol, params.max_group_size, deadline=deadline)

        if not matches:
            continue

        if len(matches) == 1:
            combo_rows = [candidatos[i] for i in matches[0]]
            ids_fin = [r["_id"] for r in combo_rows]
            metodo = f"1:N soma={len(ids_fin)}"

            df_bnk.at[bi, "_status"] = STATUS_CONCILIADO
            df_bnk.at[bi, "_metodo"] = metodo
            df_bnk.at[bi, "_ids_fin"] = ";".join(ids_fin)

            for r in combo_rows:
                fi = fin_pos[r["_id"]]
                df_fin.at[fi, "_status"] = STATUS_CONCILIADO
                df_fin.at[fi, "_metodo"] = metodo
                df_fin.at[fi, "_id_bnk"] = row_b["_id"]
                free_fin.discard(r["_id"])
        else:
            if df_bnk.at[bi, "_status"] == STATUS_SEM_PAREAMENTO:
                df_bnk.at[bi, "_status"] = STATUS_REVISAR
                df_bnk.at[bi, "_metodo"] = "1:N ambiguo"

    return df_bnk, df_fin
