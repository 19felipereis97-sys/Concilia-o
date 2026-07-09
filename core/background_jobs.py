from __future__ import annotations

import json
import os
import pickle
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import pandas as pd
import psutil

from .params import ConciliacaoParams


PROJECT_ROOT = Path(__file__).resolve().parents[1]
JOBS_DIR = PROJECT_ROOT / "data" / "jobs"

# Teto de conciliações rodando ao mesmo tempo no servidor. Cada job é um
# subprocesso Python + pandas que pode saturar um núcleo inteiro durante a
# busca combinatória — sem esse limite, vários usuários iniciando conciliação
# ao mesmo tempo derrubam o desempenho de tudo mais no servidor.
# Ajustável via variável de ambiente conforme o hardware real do servidor.
MAX_CONCURRENT_JOBS = int(os.environ.get("CONCILIADOR_MAX_JOBS", "2") or "2")

# Serializa a promoção de jobs da fila para evitar que duas sessões do
# Streamlit, fazendo polling ao mesmo tempo, estourem o teto acima.
_promote_lock = threading.Lock()


def _job_dir(job_id: str) -> Path:
    return JOBS_DIR / job_id


def _status_path(job_id: str) -> Path:
    return _job_dir(job_id) / "status.json"


def _input_path(job_id: str) -> Path:
    return _job_dir(job_id) / "input.pkl"


def _result_path(job_id: str) -> Path:
    return _job_dir(job_id) / "result.pkl"


def write_status(job_id: str, status: str, message: str = "", **extra: Any) -> None:
    payload = {
        "job_id": job_id,
        "status": status,
        "message": message,
        "updated_at": time.time(),
        **extra,
    }
    path = _status_path(job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.stem}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    for attempt in range(5):
        try:
            tmp.replace(path)
            return
        except OSError:
            if attempt == 4:
                raise
            time.sleep(0.05)


def read_status(job_id: str) -> dict:
    # Toda leitura de status (chamada a cada ~1s pelo polling da UI) também
    # tenta promover jobs da fila — não exige um daemon separado para isso.
    _promote_queued_jobs()
    path = _status_path(job_id)
    if not path.exists():
        return {"job_id": job_id, "status": "missing", "message": "Job não encontrado."}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"job_id": job_id, "status": "error", "message": f"Status inválido: {exc}"}


def _pid_alive(pid: int) -> bool:
    if not pid:
        return False
    try:
        return psutil.pid_exists(int(pid))
    except Exception:
        return False


def _spawn_worker(job_id: str) -> None:
    """Inicia o subprocesso do worker com prioridade de CPU reduzida.

    Prioridade abaixo do normal (nice 10 no POSIX, BELOW_NORMAL no Windows)
    faz o worker ceder CPU para outros processos do servidor quando há
    contenção, em vez de competir em pé de igualdade com tudo mais na máquina.
    """
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    proc = subprocess.Popen(
        [sys.executable, "-m", "core.conciliation_worker", job_id],
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
    )
    try:
        p = psutil.Process(proc.pid)
        if os.name == "nt":
            p.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
        else:
            p.nice(10)
    except Exception:
        pass  # prioridade reduzida é um bônus — segue com prioridade padrão se falhar
    write_status(job_id, "running", "Processo iniciado.", pid=proc.pid)


def _promote_queued_jobs() -> None:
    """Conta jobs realmente ativos e inicia jobs enfileirados até o teto."""
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    with _promote_lock:
        active = 0
        queued: list[tuple[float, str]] = []
        for job_dir in JOBS_DIR.iterdir():
            if not job_dir.is_dir():
                continue
            job_id = job_dir.name
            status = _raw_status(job_id)
            state = status.get("status")
            if state == "running":
                pid = status.get("pid")
                if _pid_alive(pid):
                    active += 1
                else:
                    # Worker morreu sem atualizar o status (crash) — libera o slot.
                    write_status(job_id, "error", "Processo do worker encerrou inesperadamente.")
            elif state == "queued":
                queued.append((float(status.get("queued_at", 0) or 0), job_id))

        if active >= MAX_CONCURRENT_JOBS or not queued:
            return

        queued.sort(key=lambda item: item[0])
        for _, job_id in queued[: MAX_CONCURRENT_JOBS - active]:
            _spawn_worker(job_id)


def _raw_status(job_id: str) -> dict:
    """Lê o status sem disparar _promote_queued_jobs (evita recursão)."""
    path = _status_path(job_id)
    if not path.exists():
        return {"job_id": job_id, "status": "missing"}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"job_id": job_id, "status": "error"}


def _enqueue(job_id: str) -> None:
    write_status(job_id, "queued", "Job criado. Aguardando início.", queued_at=time.time())
    _promote_queued_jobs()


def start_conciliation_job(
    df_bnk: pd.DataFrame,
    df_fin: pd.DataFrame,
    params: ConciliacaoParams,
    modalidade_str: str,
) -> str:
    job_id = uuid.uuid4().hex
    job_path = _job_dir(job_id)
    job_path.mkdir(parents=True, exist_ok=True)
    with _input_path(job_id).open("wb") as f:
        pickle.dump(
            {
                "mode": "full",
                "df_bnk": df_bnk,
                "df_fin": df_fin,
                "params": params,
                "modalidade_str": modalidade_str,
            },
            f,
            protocol=pickle.HIGHEST_PROTOCOL,
        )
    _enqueue(job_id)
    return job_id


def start_rerun_job(
    df_bnk: pd.DataFrame,
    df_fin: pd.DataFrame,
    params: ConciliacaoParams,
    protected_bnk: set | None = None,
    protected_fin: set | None = None,
) -> str:
    """Reexecuta run_engine em segundo plano (usado após decisões de revisão manual).

    Antes, esse recálculo — e a restauração de itens liberados na revisão
    manual + reconstrução da fila — rodava direto no processo do Streamlit e
    travava a sessão de todos os usuários simultâneos (Python puro segurando
    o GIL durante a busca combinatória). Agora passa pelo mesmo isolamento em
    subprocesso + fila usado na conciliação inicial; os IDs protegidos vão
    junto no payload para que o worker faça a restauração também isolada.
    """
    job_id = uuid.uuid4().hex
    job_path = _job_dir(job_id)
    job_path.mkdir(parents=True, exist_ok=True)
    with _input_path(job_id).open("wb") as f:
        pickle.dump(
            {
                "mode": "rerun",
                "df_bnk": df_bnk,
                "df_fin": df_fin,
                "params": params,
                "protected_bnk": protected_bnk or set(),
                "protected_fin": protected_fin or set(),
            },
            f,
            protocol=pickle.HIGHEST_PROTOCOL,
        )
    _enqueue(job_id)
    return job_id


def start_report_job(
    df_bnk: pd.DataFrame,
    df_fin: pd.DataFrame,
    depara_dict: dict,
    conta_banco: str,
    hist_mode: str,
) -> str:
    """Gera o relatório Excel em segundo plano.

    build_report escreve célula a célula via openpyxl em até 7 abas — em
    bases grandes isso é CPU-bound o bastante para travar as sessões de
    todos os usuários se rodasse dentro do processo do Streamlit (mesma
    classe de problema do run_engine). Passa pelo mesmo isolamento em
    subprocesso + fila.
    """
    job_id = uuid.uuid4().hex
    job_path = _job_dir(job_id)
    job_path.mkdir(parents=True, exist_ok=True)
    with _input_path(job_id).open("wb") as f:
        pickle.dump(
            {
                "mode": "report",
                "df_bnk": df_bnk,
                "df_fin": df_fin,
                "depara_dict": depara_dict,
                "conta_banco": conta_banco,
                "hist_mode": hist_mode,
            },
            f,
            protocol=pickle.HIGHEST_PROTOCOL,
        )
    _enqueue(job_id)
    return job_id


def load_result(job_id: str) -> dict:
    with _result_path(job_id).open("rb") as f:
        return pickle.load(f)


def cancel_job(job_id: str) -> bool:
    status = read_status(job_id)
    pid = status.get("pid")
    if not pid:
        write_status(job_id, "cancelled", "Job cancelado antes de iniciar.")
        return True
    try:
        os.kill(int(pid), signal.SIGTERM)
    except OSError:
        pass
    write_status(job_id, "cancelled", "Job cancelado pelo usuário.", pid=pid)
    return True
