"""
Parâmetros globais e tolerâncias da conciliação.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List


@dataclass
class ConciliacaoParams:
    date_offsets: List[int] = field(default_factory=lambda: [0, 1, -1, 2, -2])
    max_group_size: int = 30
    # Teto de candidatos por grupo usado diretamente no passe 1:N.
    # find_combos usa MITM quando C(n,k) > 10.000, mantendo custo O(2^(n/2))
    # independente de max_group_size, então o cap não precisa ser adaptativo.
    max_candidates_per_group: int = 40
    # Teto de candidatos para N:1 na Fase 2 (k≥3). 0 = sem limite.
    # A Fase 1 (k=2) usa sempre todos os candidatos, garantindo captura de
    # pares assimétricos (ex: 19.328 + 3.900 = 23.228) independente deste valor.
    # O combo_timeout_sec é o guardião de performance para grupos patológicos.
    n_to_one_max_candidates: int = 30
    value_tolerance_cents: int = 0
    # Timeout em segundos por chamada find_combos (0 = desabilitado).
    # Grupos patológicos retornam resultado parcial (marcado REVISAR) em vez
    # de travar a conciliação.
    combo_timeout_sec: float = 1.0
    discard_patterns: List[str] = field(default_factory=lambda: [
        "SDO", "SALDO", "S/D", "SALDO ANTERIOR", "SALDO DO DIA"
    ])
    hist_separator: str = " - "
    hist_prefix: str = ""
    default_year: int = 0  # 0 = usa o ano corrente ao parsear datas DD/MM sem ano
    enable_n_to_one: bool = True

    def offset_label(self, k: int) -> str:
        if k == 0:
            return "D"
        return f"D{'+' if k > 0 else ''}{k}"
