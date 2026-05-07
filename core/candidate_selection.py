"""
Seleção adaptativa de candidatos para buscas combinatórias.

Em bases grandes, tentar todas as combinações de todos os candidatos pode
transformar uma conciliação simples em uma execução de minutos. Estes helpers
mantêm o motor determinístico, mas limitam grupos patológicos de forma explícita.
"""
from __future__ import annotations

from typing import Any


def limit_subset_candidates(
    candidates: list[dict[str, Any]],
    target_f: float,
    max_candidates: int,
    value_key: str = "_valor_f",
    max_group_size: int = 2,
) -> tuple[list[dict[str, Any]], bool]:
    """
    Retorna uma lista priorizada de candidatos para subset-sum.

    A prioridade favorece valores maiores e mais próximos do alvo absoluto,
    que são os que tendem a fechar grupos reais com menos lançamentos. Quando
    não há limite ou o grupo já é pequeno, devolve a lista original.
    """
    if max_candidates <= 0 or len(candidates) <= max_candidates:
        return candidates, False

    abs_target = abs(float(target_f))

    group_sizes = range(1, max(int(max_group_size or 2), 2) + 1)

    def score(candidate: dict[str, Any]) -> tuple[float, float, str]:
        value = abs(float(candidate.get(value_key, 0) or 0))
        best_group_distance = min(abs(abs_target / k - value) for k in group_sizes)
        return (
            best_group_distance,
            -value,
            str(candidate.get("_id", "")),
        )

    ranked = sorted(candidates, key=score)

    # Preserve também os maiores valores absolutos. Quando max_group_size é alto,
    # a heurística por target/k pode favorecer muitos lançamentos pequenos e
    # excluir o lançamento "âncora" que fecha grupos reais como 171000+8600+5500.
    anchor_count = min(len(candidates), max(1, min(max_candidates // 3, 20)))
    anchors = sorted(
        candidates,
        key=lambda c: (-abs(float(c.get(value_key, 0) or 0)), str(c.get("_id", ""))),
    )[:anchor_count]

    selected: list[dict[str, Any]] = []
    seen_ids: set[int] = set()
    for candidate in anchors + ranked:
        marker = id(candidate)
        if marker in seen_ids:
            continue
        selected.append(candidate)
        seen_ids.add(marker)
        if len(selected) >= max_candidates:
            break
    return selected, True
