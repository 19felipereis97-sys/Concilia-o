"""
Parâmetros globais e tolerâncias da conciliação.
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import List


@dataclass
class ConciliacaoParams:
    date_offsets: List[int] = field(default_factory=lambda: [0, 1, -1, 2, -2])
    max_group_size: int = 5
    # Teto de candidatos por grupo. effective_max_candidates() aplica um cap
    # adaptativo abaixo deste valor quando max_group_size é grande, mantendo
    # C(cap, max_group_size) <= 400.000 para limitar o pior caso combinatorial.
    max_candidates_per_group: int = 30
    value_tolerance_cents: int = 0
    # Timeout em segundos por chamada find_combos (0 = desabilitado).
    # Grupos patológicos retornam resultado parcial (marcado REVISAR) em vez
    # de travar a conciliação.
    combo_timeout_sec: float = 0.0
    discard_patterns: List[str] = field(default_factory=lambda: [
        "SDO", "SALDO", "S/D", "SALDO ANTERIOR", "SALDO DO DIA"
    ])
    hist_separator: str = " - "
    hist_prefix: str = ""
    default_year: int = 0  # 0 = usa o ano corrente ao parsear datas DD/MM sem ano

    def offset_label(self, k: int) -> str:
        if k == 0:
            return "D"
        return f"D{'+' if k > 0 else ''}{k}"

    def effective_max_candidates(self) -> int:
        """
        Cap adaptativo: maior n tal que C(n, max_group_size) <= 400.000,
        limitado pelo max_candidates_per_group configurado pelo usuário.

        Para max_group_size=5 retorna o cap configurado (geralmente 30).
        Para max_group_size=8 retorna ~22; para 10 retorna ~21.
        """
        k = self.max_group_size
        n = k
        while n < self.max_candidates_per_group and math.comb(n + 1, k) <= 400_000:
            n += 1
        return max(n, k + 1)
