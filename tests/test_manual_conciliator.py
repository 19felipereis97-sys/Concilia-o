from __future__ import annotations

import datetime
from decimal import Decimal

from core.params import ConciliacaoParams
from ui.manual_conciliator import _date_sort_key, _score_fin_candidate


def _row(id_: str, data: str, valor: str, historico: str = "") -> dict:
    return {
        "_id": id_,
        "_data": datetime.date.fromisoformat(data),
        "_valor": Decimal(valor),
        "_historico": historico,
    }


def test_financeiro_date_sort_uses_full_date_not_day_only() -> None:
    datas = ["19/05/2026", "20/04/2026", "02/06/2026"]

    ordenadas = sorted(datas, key=_date_sort_key)

    assert ordenadas == ["20/04/2026", "19/05/2026", "02/06/2026"]


def test_affinity_is_zero_when_candidate_exceeds_remaining_amount() -> None:
    params = ConciliacaoParams()
    bank_row = _row("B1", "2026-05-19", "-100.00", "FOLHA")
    fin_row = _row("F1", "2026-05-19", "-50.00", "FOLHA")

    score, reasons, diff = _score_fin_candidate(
        bank_row,
        fin_row,
        params,
        selected_fin_sum=Decimal("-60.00"),
    )

    assert score == 0
    assert "acima do restante" in reasons
    assert diff == Decimal("10.00")


def test_affinity_recalculates_against_remaining_amount() -> None:
    params = ConciliacaoParams()
    bank_row = _row("B1", "2026-05-19", "-100.00", "FOLHA")
    exact_remaining = _row("F1", "2026-05-19", "-40.00", "FOLHA")
    lower_candidate = _row("F2", "2026-05-19", "-30.00", "FOLHA")

    exact_score, exact_reasons, _ = _score_fin_candidate(
        bank_row,
        exact_remaining,
        params,
        selected_fin_sum=Decimal("-60.00"),
    )
    lower_score, _, _ = _score_fin_candidate(
        bank_row,
        lower_candidate,
        params,
        selected_fin_sum=Decimal("-60.00"),
    )

    assert "fecha restante" in exact_reasons
    assert exact_score > lower_score
    assert exact_score >= 90
