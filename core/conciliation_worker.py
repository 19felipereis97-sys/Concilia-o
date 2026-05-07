from __future__ import annotations

import pickle
import sys
import time

from .background_jobs import _input_path, _result_path, write_status
from .engine import run_engine
from .execution_agents import supervisor_agent
from .manual_review import build_review_queue
from .scope import apply_financeiro_scope


def _perf_add(timings: list[dict], etapa: str, inicio: float, extra: str = "") -> None:
    timings.append({
        "Etapa": etapa,
        "Tempo (s)": round(time.perf_counter() - inicio, 3),
        "Detalhes": extra,
    })


def _compute_balance_warning(df_bnk, df_fin, modalidade_str: str) -> dict:
    bnk_deb = float(df_bnk.loc[df_bnk["_valor"] < 0, "_valor"].sum())
    bnk_cre = float(df_bnk.loc[df_bnk["_valor"] > 0, "_valor"].sum())
    fin_neg = float(df_fin.loc[df_fin["_valor"] < 0, "_valor"].sum())
    fin_pos = float(df_fin.loc[df_fin["_valor"] > 0, "_valor"].sum())

    resultado = {
        "bnk_deb": bnk_deb, "bnk_cre": bnk_cre,
        "fin_neg": fin_neg, "fin_pos": fin_pos,
        "modalidade": modalidade_str,
        "diverge_deb": False, "diverge_cre": False,
    }
    tol_rel = 0.01
    if bnk_deb != 0 and abs(fin_neg) > 0:
        resultado["diverge_deb"] = abs((bnk_deb - fin_neg) / bnk_deb) > tol_rel
    if bnk_cre != 0 and fin_pos > 0:
        resultado["diverge_cre"] = abs((bnk_cre - fin_pos) / bnk_cre) > tol_rel
    return resultado


def run(job_id: str) -> None:
    write_status(job_id, "running", "Carregando dados do job.", progress=5, stage="normalizacao")
    with _input_path(job_id).open("rb") as f:
        payload = pickle.load(f)

    df_bnk = payload["df_bnk"]
    df_fin = payload["df_fin"]
    params = payload["params"]
    modalidade_str = payload.get("modalidade_str", "COMPLETO")
    timings: list[dict] = []

    t0 = time.perf_counter()
    write_status(job_id, "running", "Validando saldos normalizados.", progress=12, stage="normalizacao")
    balance_warning = _compute_balance_warning(df_bnk, df_fin, modalidade_str)
    _perf_add(timings, "Analise de saldos", t0)

    write_status(job_id, "running", "Aplicando escopo do financeiro.", progress=16, stage="escopo")
    t0 = time.perf_counter()
    df_bnk, scoped_count = apply_financeiro_scope(df_bnk, modalidade_str)
    if scoped_count:
        _perf_add(timings, "Escopo do financeiro", t0, f"{scoped_count} banco fora do confronto")

    write_status(job_id, "running", "Rodando motor de conciliacao.", progress=18, stage="motor")
    t0 = time.perf_counter()
    df_bnk, df_fin = run_engine(
        df_bnk,
        df_fin,
        params,
        progress=lambda message, pct: write_status(
            job_id,
            "running",
            message,
            progress=pct,
            stage="motor",
        ),
        include_partial=False,
    )
    _perf_add(timings, "Motor de conciliacao", t0, f"{len(df_bnk)} banco / {len(df_fin)} financeiro")

    write_status(job_id, "running", "Montando fila de revisao.", progress=97, stage="revisao")
    t0 = time.perf_counter()
    cards = build_review_queue(df_bnk, df_fin, params)
    _perf_add(timings, "Fila de revisao", t0, f"{len(cards)} card(s)")

    write_status(job_id, "running", "Preparando diagnostico e relatorio.", progress=99, stage="relatorio")
    agent_report = supervisor_agent(df_bnk, df_fin, params, timings)

    with _result_path(job_id).open("wb") as f:
        pickle.dump(
            {
                "df_bnk": df_bnk,
                "df_fin": df_fin,
                "review_cards": cards,
                "balance_warning": balance_warning,
                "performance_timings": timings,
                "agent_report": agent_report,
            },
            f,
            protocol=pickle.HIGHEST_PROTOCOL,
        )

    write_status(job_id, "done", "Conciliacao concluida. Relatorio pode ser gerado mesmo com pendencias.", progress=100, stage="relatorio")


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Uso: python -m core.conciliation_worker <job_id>")
    job_id = sys.argv[1]
    try:
        run(job_id)
    except Exception as exc:
        write_status(job_id, "error", str(exc))
        raise


if __name__ == "__main__":
    main()
