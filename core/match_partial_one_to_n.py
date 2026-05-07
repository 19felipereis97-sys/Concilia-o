"""
Conciliação parcial 1:N — Passo 5 do motor.

Executado somente após todas as tentativas de fechamento integral (Passos 1-4).
Para cada lançamento do extrato que ainda permanece pendente, busca o maior
somatório possível de lançamentos financeiros do mesmo dia e mesmo sinal,
sem ultrapassar o valor do extrato.

Regras:
  - Somente D0 (mesmo dia do extrato).
  - Mesma natureza (sinal compatível).
  - Apenas lançamentos financeiros ainda não conciliados.
  - Combinação única → PARCIALMENTE CONCILIADO automático.
  - Múltiplas combinações com o mesmo máximo → REVISAR (fila de revisão).
  - Somatório zero (nenhum candidato útil) → mantém SEM_PAREAMENTO.
  - Se o somatório fechar exatamente o valor do extrato → CONCILIADO (achado
    no Passo 5 com candidatos que não estavam disponíveis nos passos anteriores).

Para cada PARCIALMENTE CONCILIADO, é gerada uma linha "pendente" com:
  - Mesmo histórico e data do extrato original.
  - Valor = diferença não conciliada (com mesmo sinal).
  - Status = PENDENTE DE CONCILIAÇÃO PARCIAL.
"""
from __future__ import annotations
import time
from collections import defaultdict
from decimal import Decimal
from typing import List, Tuple

import pandas as pd

from .normalize import (
    STATUS_SEM_PAREAMENTO, STATUS_IGNORADO_SEM_PAR,
    STATUS_CONCILIADO, STATUS_REVISAR,
    STATUS_PARCIAL, STATUS_PENDENTE_PARCIAL,
)
from .params import ConciliacaoParams
from .combo_search import find_max_partial
from .candidate_selection import limit_subset_candidates


def match_partial_one_to_n(
    df_bnk: pd.DataFrame,
    df_fin: pd.DataFrame,
    params: ConciliacaoParams,
) -> Tuple[pd.DataFrame, pd.DataFrame, List[dict]]:
    """
    Retorna (df_bnk, df_fin, pending_rows).
    pending_rows: lista de dicts representando linhas virtuais de pendência,
                  prontas para concatenar ao df_bnk após o engine.
    """
    tol = float(params.value_tolerance_cents) / 100
    use_deadline = params.combo_timeout_sec > 0

    fin_pos = dict(zip(df_fin["_id"], df_fin.index))

    # Pré-agrupa financeiros livres por (data, sinal)
    fin_groups: dict = defaultdict(list)
    cols = ["_id", "_data", "_valor", "_valor_f"] if "_valor_f" in df_fin.columns else ["_id", "_data", "_valor"]
    for rec in df_fin.loc[df_fin["_status"] == STATUS_IGNORADO_SEM_PAR, cols].to_dict("records"):
        vf = rec.get("_valor_f", float(rec["_valor"]))
        sign = vf > 0
        fin_groups[(rec["_data"], sign)].append({"_id": rec["_id"], "_valor_f": vf})

    free_fin: set = set(df_fin.loc[df_fin["_status"] == STATUS_IGNORADO_SEM_PAR, "_id"])

    # Exclui entradas onde o N:1 já tentou e não concluiu (timeout / grupo limitado).
    # Essas entradas ficam SEM_PAREAMENTO mas não devem ir para parcial —
    # não são ambiguidade, são apenas casos que o N:1 não terminou de avaliar.
    bnk_free_df = df_bnk[
        (df_bnk["_status"] == STATUS_SEM_PAREAMENTO) &
        ~df_bnk["_metodo"].str.startswith("N:1", na=False)
    ]
    bnk_cols = ["_id", "_data", "_valor", "_valor_f", "_historico"] if "_valor_f" in df_bnk.columns \
        else ["_id", "_data", "_valor", "_historico"]

    pending_rows: List[dict] = []

    for bi, row_b in zip(bnk_free_df.index, bnk_free_df[bnk_cols].to_dict("records")):
        target_f = row_b.get("_valor_f", float(row_b["_valor"]))
        sign = target_f > 0
        abs_target = abs(target_f)
        bnk_date = row_b["_data"]

        # Candidatos do mesmo dia, mesmo sinal, valor individual ≤ alvo
        candidatos = [
            c for c in fin_groups.get((bnk_date, sign), [])
            if c["_id"] in free_fin and 0 < abs(c["_valor_f"]) <= abs_target + tol
        ]

        if not candidatos:
            continue

        candidatos_busca, limited = limit_subset_candidates(
            candidatos,
            target_f,
            int(getattr(params, "max_candidates_per_group", 0) or 0),
            max_group_size=params.max_group_size,
        )
        if not candidatos_busca:
            continue

        vals_abs = [abs(c["_valor_f"]) for c in candidatos_busca]
        deadline = time.monotonic() + params.combo_timeout_sec if use_deadline else None

        best_abs, combos, timed_out = find_max_partial(
            vals_abs, abs_target, params.max_group_size, deadline=deadline,
        )

        if timed_out or best_abs == 0 or not combos:
            if (timed_out or limited) and df_bnk.at[bi, "_status"] == STATUS_SEM_PAREAMENTO:
                df_bnk.at[bi, "_status"] = STATUS_REVISAR
                df_bnk.at[bi, "_metodo"] = f"1:N D parcial grupo grande ({len(candidatos)} candidatos)"
            continue

        # Se o somatório fecha exatamente → tratado como conciliação integral
        is_full = abs(best_abs - abs_target) <= tol

        if len(combos) > 1:
            # Ambiguidade → revisão manual
            if df_bnk.at[bi, "_status"] == STATUS_SEM_PAREAMENTO:
                ids_bloqueados = {
                    candidatos_busca[i]["_id"]
                    for combo in combos
                    for i in combo
                }
                df_bnk.at[bi, "_status"] = STATUS_REVISAR
                df_bnk.at[bi, "_metodo"] = "1:N D parcial ambiguo"
                df_bnk.at[bi, "_ids_fin"] = ";".join(sorted(ids_bloqueados))
                for id_f in ids_bloqueados:
                    fi = fin_pos.get(id_f)
                    if fi is not None and df_fin.at[fi, "_status"] == STATUS_IGNORADO_SEM_PAR:
                        df_fin.at[fi, "_status"] = STATUS_REVISAR
                        df_fin.at[fi, "_metodo"] = "bloqueado:1:N D parcial ambiguo"
                        df_fin.at[fi, "_id_bnk"] = row_b["_id"]
                        free_fin.discard(id_f)
            continue

        # Combinação única
        combo = combos[0]
        combo_rows = [candidatos_busca[i] for i in combo]
        ids_fin = [r["_id"] for r in combo_rows]

        if is_full:
            metodo = f"1:N D soma={len(ids_fin)}"
            status_bnk = STATUS_CONCILIADO
        else:
            metodo = f"1:N D parcial soma={len(ids_fin)}"
            status_bnk = STATUS_PARCIAL

        df_bnk.at[bi, "_status"] = status_bnk
        df_bnk.at[bi, "_metodo"] = metodo
        df_bnk.at[bi, "_ids_fin"] = ";".join(ids_fin)

        for r in combo_rows:
            fi = fin_pos[r["_id"]]
            df_fin.at[fi, "_status"] = STATUS_PARCIAL if not is_full else STATUS_CONCILIADO
            df_fin.at[fi, "_metodo"] = metodo
            df_fin.at[fi, "_id_bnk"] = row_b["_id"]
            free_fin.discard(r["_id"])

        # Cria linha virtual de pendência (apenas para conciliação parcial)
        if not is_full:
            sign_dec = Decimal("1") if sign else Decimal("-1")
            pending_val = Decimal(str(round(abs_target - best_abs, 2))) * sign_dec
            pending_row = {
                "_id": f"PND_{row_b['_id']}",
                "_data": bnk_date,
                "_valor": pending_val,
                "_valor_f": float(pending_val),
                "_historico": row_b.get("_historico", ""),
                "_classif": "",
                "_status": STATUS_PENDENTE_PARCIAL,
                "_metodo": f"pendente:{row_b['_id']}",
                "_ids_fin": "",
            }
            pending_rows.append(pending_row)

    return df_bnk, df_fin, pending_rows
