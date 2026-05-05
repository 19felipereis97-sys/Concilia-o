from __future__ import annotations

import json
import os
import pickle
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import pandas as pd

from .params import ConciliacaoParams


PROJECT_ROOT = Path(__file__).resolve().parents[1]
JOBS_DIR = PROJECT_ROOT / "data" / "jobs"


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
    path = _status_path(job_id)
    if not path.exists():
        return {"job_id": job_id, "status": "missing", "message": "Job não encontrado."}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"job_id": job_id, "status": "error", "message": f"Status inválido: {exc}"}


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
                "df_bnk": df_bnk,
                "df_fin": df_fin,
                "params": params,
                "modalidade_str": modalidade_str,
            },
            f,
            protocol=pickle.HIGHEST_PROTOCOL,
        )

    write_status(job_id, "queued", "Job criado. Aguardando início.")
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    proc = subprocess.Popen(
        [sys.executable, "-m", "core.conciliation_worker", job_id],
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
    )
    write_status(job_id, "running", "Processo iniciado.", pid=proc.pid)
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
