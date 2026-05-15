"""
Conciliação 1:N — 1 linha bancária = soma de N linhas financeiras.

Suporta dois modos via parâmetro `offsets`:
  offsets=[0]          → Passo 2: 1:N no mesmo dia, fechamento total.
  offsets=[-1,1,-2,2]  → Passo 4: 1:N com variação de datas, fechamento total.
                          Candidatos de datas distintas podem compor uma combinação.

Em ambos os casos:
  - Somente lançamentos financeiros ainda não conciliados participam.
  - A soma deve fechar exatamente com o valor do extrato (dentro da tolerância).
  - Uma combinação única → CONCILIADO automático.
  - Múltiplas combinações → REVISAR (fila de revisão manual).
"""
from __future__ import annotations
import datetime
import time
from collections import defaultdict
from typing import List, Optional, Tuple

import pandas as pd

from .normalize import (
    STATUS_SEM_PAREAMENTO, STATUS_IGNORADO_SEM_PAR,
    STATUS_CONCILIADO, STATUS_REVISAR,
)
from .params import ConciliacaoParams
from .combo_search import find_combos
from .candidate_selection import limit_subset_candidates


def match_one_to_n(
    df_bnk: pd.DataFrame,
    df_fin: pd.DataFrame,
    params: ConciliacaoParams,
    offsets: Optional[List[int]] = None,
    fin_pos: Optional[dict] = None,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    offsets: deslocamentos de data a considerar para os candidatos financeiros.
             None ou [0] → somente mesmo dia.
             [-1,1,-2,2] → candidatos de datas vizinhas (podem misturar datas).

    Otimizações mantidas:
    - Filtra candidatos com |valor| > |alvo| antes da combinatória (Dica 3).
    - Usa _valor_f (float pré-computado) sem float() por linha (Dica 6).
    - Pre-agrupa candidatos financeiros por (data, sinal).
    - free_fin atualizado incrementalmente com .discard().
    - Pre-check de impossibilidade: pula se soma total < alvo.
    - Deadline por grupo (MITM O(2^(n/2)) garante performance sem cap de candidatos).
    """
    if offsets is None:
        offsets = [0]

    is_d0_only = offsets == [0]
    label_base = "1:N D" if is_d0_only else "1:N Dvar"

    tol = float(params.value_tolerance_cents) / 100
    use_deadline = params.combo_timeout_sec > 0

    fin_pos = fin_pos if fin_pos is not None else dict(zip(df_fin["_id"], df_fin.index))

    # Pré-agrupa financeiros livres por (data, sinal)
    fin_groups: dict = defaultdict(list)
    cols = ["_id", "_data", "_valor", "_valor_f"] if "_valor_f" in df_fin.columns else ["_id", "_data", "_valor"]
    for rec in df_fin.loc[df_fin["_status"] == STATUS_IGNORADO_SEM_PAR, cols].to_dict("records"):
        vf = rec.get("_valor_f", float(rec["_valor"]))
        sign = vf > 0
        fin_groups[(rec["_data"], sign)].append({
            "_id": rec["_id"], "_valor_f": vf, "_data": rec["_data"],
        })

    free_fin: set = set(df_fin.loc[df_fin["_status"] == STATUS_IGNORADO_SEM_PAR, "_id"])

    bnk_free_df = df_bnk[df_bnk["_status"] == STATUS_SEM_PAREAMENTO]
    bnk_cols = ["_id", "_data", "_valor", "_valor_f"] if "_valor_f" in df_bnk.columns else ["_id", "_data", "_valor"]

    for bi, row_b in zip(bnk_free_df.index, bnk_free_df[bnk_cols].to_dict("records")):
        target_f = row_b.get("_valor_f", float(row_b["_valor"]))
        sign = target_f > 0
        abs_target = abs(target_f)
        bnk_date = row_b["_data"]

        # Coleta candidatos de todos os offsets solicitados
        candidatos: list = []
        seen_ids: set = set()
        for offset in offsets:
            search_date = bnk_date + datetime.timedelta(days=offset)
            for c in fin_groups.get((search_date, sign), []):
                if c["_id"] in free_fin and c["_id"] not in seen_ids and abs(c["_valor_f"]) <= abs_target + tol:
                    candidatos.append(c)
                    seen_ids.add(c["_id"])

        if len(candidatos) < 2:
            continue

        # Pre-check: impossível atingir o alvo mesmo somando todos
        if sum(abs(c["_valor_f"]) for c in candidatos) < abs_target - tol:
            continue

        candidatos_busca, limited = limit_subset_candidates(
            candidatos,
            target_f,
            int(getattr(params, "max_candidates_per_group", 0) or 0),
            max_group_size=params.max_group_size,
        )
        if len(candidatos_busca) < 2:
            continue

        vals = [c["_valor_f"] for c in candidatos_busca]
        deadline = time.monotonic() + params.combo_timeout_sec if use_deadline else None
        search_start = time.monotonic()
        matches = find_combos(
            vals,
            target_f,
            tol,
            params.max_group_size,
            deadline=deadline,
        )
        timed_out = use_deadline and (time.monotonic() - search_start) >= (params.combo_timeout_sec * 0.95)

        if not matches:
            if (limited or timed_out) and df_bnk.at[bi, "_status"] == STATUS_SEM_PAREAMENTO:
                motivo = "tempo esgotado" if timed_out else "grupo grande"
                df_bnk.at[bi, "_metodo"] = f"{label_base} {motivo} ({len(candidatos)} candidatos)"
            continue

        if len(matches) == 1:
            combo_rows = [candidatos_busca[i] for i in matches[0]]
            ids_fin = [r["_id"] for r in combo_rows]
            metodo = f"{label_base} soma={len(ids_fin)}"

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
                ids_bloqueados = {
                    candidatos_busca[i]["_id"]
                    for match in matches
                    for i in match
                }
                df_bnk.at[bi, "_status"] = STATUS_REVISAR
                df_bnk.at[bi, "_metodo"] = f"{label_base} ambiguo"
                df_bnk.at[bi, "_ids_fin"] = ";".join(sorted(ids_bloqueados))
                for id_f in ids_bloqueados:
                    fi = fin_pos.get(id_f)
                    if fi is not None and df_fin.at[fi, "_status"] == STATUS_IGNORADO_SEM_PAR:
                        df_fin.at[fi, "_status"] = STATUS_REVISAR
                        df_fin.at[fi, "_metodo"] = f"bloqueado:{label_base} ambiguo"
                        df_fin.at[fi, "_id_bnk"] = row_b["_id"]
                        free_fin.discard(id_f)

    return df_bnk, df_fin
