"""
Busca combinatória otimizada para conciliação N:1 e 1:N.

Otimizações aplicadas:
  1. Early-exit com poda: valores ordenados por magnitude; poda quando a soma
     parcial já ultrapassa o alvo ou quando nem o mínimo alcançável chega lá.
  2. Bounds check por k: descarta k inteiros sem tentar combinação alguma.
  3. Prefix/suffix sums pré-computados em _brute_k: consultas O(1) por poda
     (vs O(rem) do sum() inline anterior).
  4. Meet-in-the-middle adaptativo: ativa quando C(n,k) > _MITM_COMBO_THRESHOLD
     (via math.comb, limiar 10.000) em vez dos thresholds estáticos anteriores;
     ganho típico de 50-100x em grupos grandes.
  5. Poda de left_map no MITM: combos cuja soma já excede alvo+tol são
     descartados na construção, reduzindo memória e lookups.
  6. Busca binária para tolerância no MITM: O(log|sums|) em vez de O(2·tol)
     ao varrer somas do lado esquerdo que casam com o lado direito.
  7. Cache por conteúdo: grupos com mesmo conjunto de valores e alvo são
     computados uma única vez por execução do engine.
  8. array.array para sv: acesso indexado em C-level, evitando boxing de
     PyObject* a cada acesso a elementos do vetor de candidatos.
  9. Deadline por chamada: interrompe a busca após o tempo limite sem travar
     o processo; resultados parciais não são cacheados.
"""
from __future__ import annotations
import array as _array_mod
import bisect
import itertools
import math
import time
import threading
from collections import defaultdict
from typing import List, Optional, Tuple

# Ativa MITM quando C(n,k) supera este valor — avaliado via math.comb (O(k)).
_MITM_COMBO_THRESHOLD = 10_000

_lock = threading.Lock()
_combo_cache: dict = {}


class _TimeoutExceeded(Exception):
    """Sinaliza que o deadline da busca foi ultrapassado."""


def clear_cache() -> None:
    """Limpa cache entre execuções (chamado pelo engine a cada rodada)."""
    with _lock:
        _combo_cache.clear()


def _use_mitm(n: int, k: int) -> bool:
    """Ativa MITM quando brute-force excederia _MITM_COMBO_THRESHOLD combinações."""
    return k >= 3 and math.comb(n, k) > _MITM_COMBO_THRESHOLD


def find_combos(
    vals: List[float],
    target: float,
    tol: float,
    max_k: int,
    deadline: Optional[float] = None,
    stop_after_first_k: bool = False,
) -> List[Tuple[int, ...]]:
    """
    Retorna até 2 tuplas de índices em `vals` cuja soma ≈ target ± tol.
    Retornar 2 indica ambiguidade — o chamador decide como tratar.

    deadline: valor de time.monotonic() após o qual a busca é abandonada;
    resultados parciais são retornados sem serem cacheados.
    """
    n = len(vals)
    if n < 2:
        return []

    vals_c = [round(v * 100) for v in vals]
    target_c = round(target * 100)
    tol_c = max(round(tol * 100), 0)

    sign = 1 if target_c >= 0 else -1
    abs_vals_c = [sign * v for v in vals_c]
    abs_target_c = sign * target_c

    order = sorted(range(n), key=lambda i: abs_vals_c[i])
    sv = _array_mod.array('q', (abs_vals_c[order[i]] for i in range(n)))

    cache_key = (tuple(sv), abs_target_c, tol_c, max_k)
    with _lock:
        cached = _combo_cache.get(cache_key)
    if cached is not None:
        return [_remap(c, order) for c in cached]

    # Prefix sums para consultas O(1) nas podas de _brute_k
    prefix = _array_mod.array('q', [0] * (n + 1))
    for i in range(n):
        prefix[i + 1] = prefix[i] + sv[i]

    matches_internal: List[Tuple] = []
    timed_out = False
    try:
        for k in range(2, min(max_k, n) + 1):
            min_sum = prefix[k]                     # sum(sv[:k])
            max_sum = prefix[n] - prefix[n - k]     # sum(sv[n-k:])
            if abs_target_c < min_sum - tol_c or abs_target_c > max_sum + tol_c:
                continue

            if _use_mitm(n, k):
                new = _mitm_k(sv, n, k, abs_target_c, tol_c, deadline)
            else:
                new = _brute_k(sv, n, k, abs_target_c, tol_c, prefix, deadline)

            matches_internal.extend(new)
            if stop_after_first_k and new:
                break
            if len(matches_internal) > 1:
                break
    except _TimeoutExceeded:
        timed_out = True

    if not timed_out:
        with _lock:
            _combo_cache[cache_key] = matches_internal

    return [_remap(c, order) for c in matches_internal]


def _remap(combo: Tuple[int, ...], order: List[int]) -> Tuple[int, ...]:
    """Converte índices no espaço ordenado para índices no espaço original."""
    return tuple(order[i] for i in combo)


def _brute_k(
    sv: _array_mod.array,
    n: int,
    k: int,
    target_c: int,
    tol_c: int,
    prefix: _array_mod.array,
    deadline: Optional[float],
) -> List[Tuple]:
    """
    Força bruta com podas O(1) via prefix/suffix sums pré-computados.

    prefix[i] = sum(sv[:i])  →  sum(sv[a:b]) = prefix[b] - prefix[a]  (O(1))
    suffix_max[rem] = prefix[n] - prefix[n-rem]  →  máximo alcançável com rem
    elementos (upper bound conservador independente de start).
    """
    suffix_max = _array_mod.array('q', [0] * (k + 1))
    for rem in range(1, k + 1):
        suffix_max[rem] = prefix[n] - prefix[n - rem]

    matches: List[Tuple] = []
    call_count = 0

    def _rec(start: int, rem: int, cur: list, partial: int) -> None:
        nonlocal call_count
        if len(matches) > 1:
            return
        if rem == 0:
            if abs(partial - target_c) <= tol_c:
                matches.append(tuple(cur))
            return
        if n - start < rem:
            return
        if partial > target_c + tol_c:
            return
        # O(1): mínimo alcançável = soma dos rem menores a partir de start
        if partial + prefix[start + rem] - prefix[start] > target_c + tol_c:
            return
        # O(1): máximo alcançável = soma dos rem maiores globais (upper bound)
        if partial + suffix_max[rem] < target_c - tol_c:
            return
        # Verifica deadline a cada 256 entradas (time.monotonic só se necessário)
        if deadline is not None:
            call_count += 1
            if (call_count & 0xFF) == 0 and time.monotonic() > deadline:
                raise _TimeoutExceeded()

        for i in range(start, n - rem + 1):
            if partial + sv[i] > target_c + tol_c:
                break  # sv ordenado: todos os seguintes também ultrapassam
            cur.append(i)
            _rec(i + 1, rem - 1, cur, partial + sv[i])
            cur.pop()
            if len(matches) > 1:
                return

    _rec(0, k, [], 0)
    return matches


def find_valid_indices(
    vals: List[float],
    target: float,
    tol: float,
    max_k: int,
) -> set:
    """
    Retorna o conjunto de índices de `vals` que aparecem em pelo menos
    uma combinação válida (soma ≈ target ± tol, tamanho 2..max_k).

    Sem limite de resultados — coleta todos os índices participantes.
    Usada pela revisão manual para exibir somente candidatos relevantes.
    """
    n = len(vals)
    if n < 2:
        return set()

    vals_c = [round(v * 100) for v in vals]
    target_c = round(target * 100)
    tol_c = max(round(tol * 100), 0)

    sign = 1 if target_c >= 0 else -1
    abs_vals_c = [sign * v for v in vals_c]
    abs_target_c = sign * target_c

    order = sorted(range(n), key=lambda i: abs_vals_c[i])
    sv = _array_mod.array('q', (abs_vals_c[order[i]] for i in range(n)))

    prefix = _array_mod.array('q', [0] * (n + 1))
    for i in range(n):
        prefix[i + 1] = prefix[i] + sv[i]

    valid_sorted: set = set()
    for k in range(2, min(max_k, n) + 1):
        min_sum = prefix[k]
        max_sum = prefix[n] - prefix[n - k]
        if abs_target_c < min_sum - tol_c or abs_target_c > max_sum + tol_c:
            continue
        if _use_mitm(n, k):
            _mitm_k_indices(sv, n, k, abs_target_c, tol_c, valid_sorted)
        else:
            _brute_k_indices(sv, n, k, abs_target_c, tol_c, valid_sorted, prefix)
        if len(valid_sorted) == n:
            break
    return {order[i] for i in valid_sorted}


def _brute_k_indices(
    sv: _array_mod.array,
    n: int,
    k: int,
    target_c: int,
    tol_c: int,
    valid: set,
    prefix: _array_mod.array,
) -> None:
    """Força bruta coletando índices de TODAS as combinações válidas (sem limite de 2)."""
    suffix_max = _array_mod.array('q', [0] * (k + 1))
    for rem in range(1, k + 1):
        suffix_max[rem] = prefix[n] - prefix[n - rem]

    def _rec(start: int, rem: int, cur: list, partial: int) -> None:
        if rem == 0:
            if abs(partial - target_c) <= tol_c:
                valid.update(cur)
            return
        if n - start < rem:
            return
        if partial > target_c + tol_c:
            return
        if partial + prefix[start + rem] - prefix[start] > target_c + tol_c:
            return
        if partial + suffix_max[rem] < target_c - tol_c:
            return
        for i in range(start, n - rem + 1):
            if partial + sv[i] > target_c + tol_c:
                break
            cur.append(i)
            _rec(i + 1, rem - 1, cur, partial + sv[i])
            cur.pop()
    _rec(0, k, [], 0)


def _mitm_k_indices(
    sv: _array_mod.array,
    n: int,
    k: int,
    target_c: int,
    tol_c: int,
    valid: set,
) -> None:
    """MITM coletando índices de TODAS as combinações válidas."""
    m = n // 2
    left_map: dict = defaultdict(list)
    left_sums_by_j: dict = defaultdict(set)
    for j in range(0, min(k, m) + 1):
        for combo in itertools.combinations(range(m), j):
            s = sum(sv[i] for i in combo)
            if s <= target_c + tol_c:
                left_map[(j, s)].append(combo)
                if tol_c > 0:
                    left_sums_by_j[j].add(s)

    sorted_sums_by_j: dict = {}
    if tol_c > 0:
        for j, sums in left_sums_by_j.items():
            sorted_sums_by_j[j] = sorted(sums)

    for j_r in range(0, min(k, n - m) + 1):
        j_l = k - j_r
        if j_l < 0 or j_l > m:
            continue
        for combo_r in itertools.combinations(range(m, n), j_r):
            s_r = sum(sv[i] for i in combo_r)
            if s_r > target_c + tol_c:
                continue
            need = target_c - s_r
            if tol_c == 0:
                for combo_l in left_map.get((j_l, need), []):
                    valid.update(combo_l)
                    valid.update(combo_r)
            else:
                sums = sorted_sums_by_j.get(j_l, [])
                if sums:
                    lo = bisect.bisect_left(sums, need - tol_c)
                    hi = bisect.bisect_right(sums, need + tol_c)
                    for s_l in sums[lo:hi]:
                        for combo_l in left_map.get((j_l, s_l), []):
                            if abs(s_r + sum(sv[i] for i in combo_l) - target_c) <= tol_c:
                                valid.update(combo_l)
                                valid.update(combo_r)


def _mitm_k(
    sv: _array_mod.array,
    n: int,
    k: int,
    target_c: int,
    tol_c: int,
    deadline: Optional[float] = None,
) -> List[Tuple]:
    """
    Meet-in-the-middle para exatamente k elementos.

    Divide os índices em metade esquerda [0, m) e direita [m, n).
    Pré-computa somas da metade esquerda num dict; consulta para cada
    subconjunto da metade direita.

    Otimizações adicionais vs versão anterior:
    - Poda na construção do left_map: soma > target+tol descartada.
    - Busca binária (bisect) quando tol > 0: O(log|sums|) vs O(2·tol).
    - Deadline checado após cada j_r para interrupção rápida.
    """
    m = n // 2

    left_map: dict = defaultdict(list)
    left_sums_by_j: dict = defaultdict(set)
    for j in range(0, min(k, m) + 1):
        for combo in itertools.combinations(range(m), j):
            s = sum(sv[i] for i in combo)
            if s <= target_c + tol_c:
                left_map[(j, s)].append(combo)
                if tol_c > 0:
                    left_sums_by_j[j].add(s)

    sorted_sums_by_j: dict = {}
    if tol_c > 0:
        for j, sums in left_sums_by_j.items():
            sorted_sums_by_j[j] = sorted(sums)

    matches: List[Tuple] = []
    for j_r in range(0, min(k, n - m) + 1):
        j_l = k - j_r
        if j_l < 0 or j_l > m:
            continue
        for combo_r in itertools.combinations(range(m, n), j_r):
            s_r = sum(sv[i] for i in combo_r)
            if s_r > target_c + tol_c:
                continue
            need = target_c - s_r
            if tol_c == 0:
                for combo_l in left_map.get((j_l, need), []):
                    matches.append(combo_l + combo_r)
                    if len(matches) > 1:
                        return matches
            else:
                sums = sorted_sums_by_j.get(j_l, [])
                if sums:
                    lo = bisect.bisect_left(sums, need - tol_c)
                    hi = bisect.bisect_right(sums, need + tol_c)
                    for s_l in sums[lo:hi]:
                        for combo_l in left_map.get((j_l, s_l), []):
                            if abs(s_r + sum(sv[i] for i in combo_l) - target_c) <= tol_c:
                                matches.append(combo_l + combo_r)
                                if len(matches) > 1:
                                    return matches
        if deadline is not None and time.monotonic() > deadline:
            raise _TimeoutExceeded()

    return matches


# ---------------------------------------------------------------------------
# Busca completa — retorna até max_results combinações (usada na revisão manual)
# ---------------------------------------------------------------------------

def find_max_partial(
    vals_abs: List[float],
    target_abs: float,
    max_k: int,
    deadline: Optional[float] = None,
) -> tuple:
    """
    Encontra o maior somatório possível ≤ target_abs usando até max_k valores
    de vals_abs (todos positivos, já filtrados pelo chamador).

    Retorna (best_sum_float, combos, timed_out) onde:
      best_sum_float: maior soma alcançada (0.0 se nenhuma).
      combos: lista de tuplas de índices (espaço original) que atingem best_sum.
              Múltiplas tuplas indicam ambiguidade → chamador direciona p/ revisão.
      timed_out: True se o deadline foi atingido antes do término (resultado parcial).

    Algoritmo: DFS com poda via upper-bound ganancioso (soma dos maiores restantes).
    Trabalha em centavos (int) para aritmética exata.
    """
    if not vals_abs or target_abs <= 0:
        return 0.0, [], False

    # Converte para centavos
    vals_c = [round(v * 100) for v in vals_abs]
    target_c = round(target_abs * 100)
    n = len(vals_c)

    # Ordena decrescente: maiores valores primeiro → melhor poda
    order = sorted(range(n), key=lambda i: vals_c[i], reverse=True)
    sv = [vals_c[order[i]] for i in range(n)]

    # Prefix sums sobre sv (decrescente) para upper-bound O(1)
    prefix = [0] * (n + 1)
    for i in range(n):
        prefix[i + 1] = prefix[i] + sv[i]

    best_c = [0]
    best_combos_sv: list = []   # índices no espaço sv
    timed_out = [False]

    def dfs(idx: int, k_rem: int, partial: int, combo: list) -> None:
        # Registra se melhora ou empata o melhor
        if partial > best_c[0]:
            best_c[0] = partial
            best_combos_sv.clear()
            best_combos_sv.append(combo[:])
        elif partial == best_c[0] and partial > 0:
            best_combos_sv.append(combo[:])

        if k_rem == 0 or idx >= n:
            return

        # Upper bound: somar os k_rem maiores restantes a partir de idx
        avail = min(k_rem, n - idx)
        upper = prefix[idx + avail] - prefix[idx]
        if partial + upper <= best_c[0]:
            return  # não consegue melhorar

        if deadline is not None and time.monotonic() > deadline:
            timed_out[0] = True
            return

        for i in range(idx, n):
            v = sv[i]
            if partial + v > target_c:
                continue  # valor isolado já ultrapassa — tenta menores
            combo.append(i)
            dfs(i + 1, k_rem - 1, partial + v, combo)
            combo.pop()
            if timed_out[0]:
                return

    dfs(0, max_k, 0, [])

    # Remapeia índices sv → índices originais
    result_combos = [tuple(order[i] for i in c) for c in best_combos_sv]
    return best_c[0] / 100.0, result_combos, timed_out[0]


def find_all_combos(
    vals: List[float],
    target: float,
    tol: float,
    max_k: int,
    max_results: int = 20,
) -> List[Tuple[int, ...]]:
    """
    Retorna até max_results combinações de índices em `vals` cuja soma ≈ target ± tol.
    Inclui k=1 (correspondência exata) e k≥2 (combinações parciais).
    Não cacheado — usado apenas na fila de revisão manual, onde o conjunto de
    candidatos já é pequeno (mesma data, mesmo sinal).
    """
    n = len(vals)
    if n < 1:
        return []

    vals_c = [round(v * 100) for v in vals]
    target_c = round(target * 100)
    tol_c = max(round(tol * 100), 0)

    sign = 1 if target_c >= 0 else -1
    abs_vals_c = [sign * v for v in vals_c]
    abs_target_c = sign * target_c

    order = sorted(range(n), key=lambda i: abs_vals_c[i])
    sv = _array_mod.array('q', (abs_vals_c[order[i]] for i in range(n)))

    prefix = _array_mod.array('q', [0] * (n + 1))
    for i in range(n):
        prefix[i + 1] = prefix[i] + sv[i]

    matches_internal: List[Tuple] = []

    for k in range(1, min(max_k, n) + 1):
        min_sum = prefix[k]
        max_sum = prefix[n] - prefix[n - k]
        if abs_target_c < min_sum - tol_c or abs_target_c > max_sum + tol_c:
            continue

        remaining = max_results - len(matches_internal)
        if _use_mitm(n, k):
            new = _mitm_k_all(sv, n, k, abs_target_c, tol_c, remaining)
        else:
            new = _brute_k_all(sv, n, k, abs_target_c, tol_c, prefix, remaining)

        matches_internal.extend(new)
        if len(matches_internal) >= max_results:
            break

    return [_remap(c, order) for c in matches_internal]


def _brute_k_all(
    sv: _array_mod.array,
    n: int,
    k: int,
    target_c: int,
    tol_c: int,
    prefix: _array_mod.array,
    max_results: int,
) -> List[Tuple]:
    """Força bruta coletando até max_results combinações válidas (sem early-exit em 2)."""
    suffix_max = _array_mod.array('q', [0] * (k + 1))
    for rem in range(1, k + 1):
        suffix_max[rem] = prefix[n] - prefix[n - rem]

    matches: List[Tuple] = []

    def _rec(start: int, rem: int, cur: list, partial: int) -> None:
        if len(matches) >= max_results:
            return
        if rem == 0:
            if abs(partial - target_c) <= tol_c:
                matches.append(tuple(cur))
            return
        if n - start < rem:
            return
        if partial > target_c + tol_c:
            return
        if partial + prefix[start + rem] - prefix[start] > target_c + tol_c:
            return
        if partial + suffix_max[rem] < target_c - tol_c:
            return
        for i in range(start, n - rem + 1):
            if partial + sv[i] > target_c + tol_c:
                break
            cur.append(i)
            _rec(i + 1, rem - 1, cur, partial + sv[i])
            cur.pop()
            if len(matches) >= max_results:
                return

    _rec(0, k, [], 0)
    return matches


def _mitm_k_all(
    sv: _array_mod.array,
    n: int,
    k: int,
    target_c: int,
    tol_c: int,
    max_results: int,
) -> List[Tuple]:
    """Meet-in-the-middle coletando até max_results combinações válidas."""
    m = n // 2

    left_map: dict = defaultdict(list)
    left_sums_by_j: dict = defaultdict(set)
    for j in range(0, min(k, m) + 1):
        for combo in itertools.combinations(range(m), j):
            s = sum(sv[i] for i in combo)
            if s <= target_c + tol_c:
                left_map[(j, s)].append(combo)
                if tol_c > 0:
                    left_sums_by_j[j].add(s)

    sorted_sums_by_j: dict = {}
    if tol_c > 0:
        for j, sums in left_sums_by_j.items():
            sorted_sums_by_j[j] = sorted(sums)

    matches: List[Tuple] = []
    for j_r in range(0, min(k, n - m) + 1):
        j_l = k - j_r
        if j_l < 0 or j_l > m:
            continue
        for combo_r in itertools.combinations(range(m, n), j_r):
            s_r = sum(sv[i] for i in combo_r)
            if s_r > target_c + tol_c:
                continue
            need = target_c - s_r
            if tol_c == 0:
                for combo_l in left_map.get((j_l, need), []):
                    matches.append(combo_l + combo_r)
                    if len(matches) >= max_results:
                        return matches
            else:
                sums = sorted_sums_by_j.get(j_l, [])
                if sums:
                    lo = bisect.bisect_left(sums, need - tol_c)
                    hi = bisect.bisect_right(sums, need + tol_c)
                    for s_l in sums[lo:hi]:
                        for combo_l in left_map.get((j_l, s_l), []):
                            if abs(s_r + sum(sv[i] for i in combo_l) - target_c) <= tol_c:
                                matches.append(combo_l + combo_r)
                                if len(matches) >= max_results:
                                    return matches
    return matches
