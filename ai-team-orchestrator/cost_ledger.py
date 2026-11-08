"""AI Team 本地费用保险丝（第一阶段）：跨进程 SQLite 原子预留，默认拒绝。

只提供离线预留契约，不读取密钥，不发网络请求，也未接入生产 Worker。
每条已预留请求都永久占用原始额度；超时或进程重启不得自动返还。
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_MAX_AMOUNT = 9_000_000_000_000_000


class CostGateDenied(RuntimeError):
    """仅暴露安全错误码，不泄漏敏感参数。"""


@dataclass(frozen=True)
class Reservation:
    request_id: str
    estimated_micros: int
    may_execute_once: bool
    state: str


def _id(value: str) -> bool:
    return isinstance(value, str) and bool(_IDENTIFIER.fullmatch(value))


def _amount(value: int, *, allow_zero: bool = False) -> bool:
    return (type(value) is int and (0 if allow_zero else 1) <= value <= _MAX_AMOUNT)


class BudgetLedger:
    """跨进程共享的离线预算账本；额度设置不能因进程重启再次执行。"""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        # 目录必须由运维预先创建；不因服务启动而自动创建/重置费用数据库。
        if not self.path.parent.is_dir():
            raise CostGateDenied("ledger_directory_missing")

    def _open(self):
        db = None
        try:
            db = sqlite3.connect(str(self.path), timeout=3, isolation_level=None)
            db.execute("PRAGMA busy_timeout=3000")
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("""CREATE TABLE IF NOT EXISTS cost_policy (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                currency TEXT NOT NULL CHECK (currency IN ('CNY','USD')),
                total_cap INTEGER NOT NULL CHECK (total_cap >= 0),
                approval_ref TEXT NOT NULL,
                enabled INTEGER NOT NULL CHECK (enabled IN (0,1))
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS cost_provider_cap (
                provider TEXT PRIMARY KEY,
                amount INTEGER NOT NULL CHECK (amount >= 0)
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS cost_reservations (
                request_id TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                task_id TEXT NOT NULL,
                currency TEXT NOT NULL,
                estimated_micros INTEGER NOT NULL CHECK (estimated_micros > 0),
                state TEXT NOT NULL CHECK (state = 'reserved_unsettled'),
                created_at TEXT NOT NULL
            )""")
            return db
        except (OSError, sqlite3.Error) as exc:
            if db is not None:
                db.close()
            raise CostGateDenied("ledger_unavailable") from exc

    def install_policy(self, *, currency: str, total_cap_micros: int,
                       provider_caps: dict[str, int],
                       approval_ref: str, approved_by_user: bool = False) -> None:
        """只允许一次性部署经人工确认的配置；后续进程启动不可充值或重置。

        approved_by_user 只代表调用方声明，不能代替上层真实身份与审批核验。
        生产 API 必须单独实现身份鉴别；现阶段仅供隔离测试。
        """
        if not (approved_by_user is True and _id(approval_ref)
                and currency in ("CNY", "USD")
                and _amount(total_cap_micros, allow_zero=True)
                and isinstance(provider_caps, dict) and provider_caps
                and all(_id(k) and _amount(v, allow_zero=True)
                        for k, v in provider_caps.items())):
            raise CostGateDenied("approval_or_policy_invalid")
        db = self._open()
        try:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM cost_policy WHERE singleton=1").fetchone():
                raise CostGateDenied("policy_already_installed")
            # DB 空白也不是授权：必须显式获批且持久记录批准依据。
            db.execute("INSERT INTO cost_policy VALUES (1, ?, ?, ?, 1)",
                       (currency, total_cap_micros, approval_ref))
            for provider, cap in provider_caps.items():
                db.execute("INSERT INTO cost_provider_cap VALUES (?, ?)", (provider, cap))
            db.execute("COMMIT")
        except (sqlite3.Error, CostGateDenied) as exc:
            # BEGIN IMMEDIATE 本身也可能因锁冲突失败；不得让二次回滚错误掩盖主故障。
            if db.in_transaction:
                try:
                    db.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
            if isinstance(exc, CostGateDenied):
                raise
            raise CostGateDenied("ledger_unavailable") from exc
        finally:
            db.close()

    def reserve(self, *, request_id: str, task_id: str, provider: str,
                currency: str, unit_price_micros: int | None,
                units: int = 1) -> Reservation:
        """唯一执行许可：仅本次新预留返回 may_execute_once=True。

        重放请求不会再次获得执行权，即使参数完全一致。
        未完成请求始终占用预算，不在重启/超时后自动释放。
        """
        if not all(_id(k) for k in (request_id, task_id, provider)):
            raise CostGateDenied("invalid_reservation_identity")
        if currency not in ("CNY", "USD"):
            raise CostGateDenied("unsupported_currency")
        if not _amount(unit_price_micros) or not _amount(units):
            raise CostGateDenied("price_unknown_or_invalid")
        estimate = unit_price_micros * units
        if not _amount(estimate):
            raise CostGateDenied("cost_overflow")
        db = self._open()
        try:
            db.execute("BEGIN IMMEDIATE")
            policy = db.execute(
                "SELECT currency, total_cap, enabled FROM cost_policy WHERE singleton=1"
            ).fetchone()
            if policy is None or policy[2] != 1:
                raise CostGateDenied("budget_not_approved")
            if policy[0] != currency:
                raise CostGateDenied("currency_mismatch")
            cap_row = db.execute(
                "SELECT amount FROM cost_provider_cap WHERE provider=?", (provider,)
            ).fetchone()
            if cap_row is None:
                raise CostGateDenied("provider_not_approved")
            existing = db.execute(
                """SELECT provider, task_id, currency, estimated_micros
                   FROM cost_reservations WHERE request_id=?""", (request_id,)
            ).fetchone()
            if existing:
                if tuple(existing) != (provider, task_id, currency, estimate):
                    raise CostGateDenied("request_id_conflict")
                # 不能向可能已经真实扣费的同一调用授予第二次执行权。
                db.execute("COMMIT")
                return Reservation(request_id, estimate, False, "already_reserved")
            global_used = db.execute(
                "SELECT COALESCE(SUM(estimated_micros),0) FROM cost_reservations"
            ).fetchone()[0]
            provider_used = db.execute(
                """SELECT COALESCE(SUM(estimated_micros),0)
                   FROM cost_reservations WHERE provider=?""", (provider,)
            ).fetchone()[0]
            if global_used + estimate > policy[1]:
                raise CostGateDenied("global_budget_exceeded")
            if provider_used + estimate > cap_row[0]:
                raise CostGateDenied("provider_budget_exceeded")
            db.execute(
                """INSERT INTO cost_reservations
                   (request_id,provider,task_id,currency,estimated_micros,state,created_at)
                   VALUES (?,?,?,?,?,'reserved_unsettled',?)""",
                (request_id, provider, task_id, currency, estimate,
                 datetime.now(timezone.utc).isoformat()),
            )
            db.execute("COMMIT")
            return Reservation(request_id, estimate, True, "reserved_unsettled")
        except (sqlite3.Error, CostGateDenied) as exc:
            try:
                db.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            if isinstance(exc, CostGateDenied):
                raise
            raise CostGateDenied("ledger_unavailable") from exc
        finally:
            db.close()

    def summary(self) -> dict[str, object]:
        """只读状态：不给出审批凭据、密钥或任务正文。"""
        db = self._open()
        try:
            policy = db.execute(
                "SELECT currency,total_cap,enabled FROM cost_policy WHERE singleton=1"
            ).fetchone()
            if policy is None:
                return {"status": "unconfigured", "available_micros": 0}
            spent = db.execute(
                "SELECT COALESCE(SUM(estimated_micros),0),COUNT(*) FROM cost_reservations"
            ).fetchone()
            return {"status": "active" if policy[2] else "disabled",
                    "currency": policy[0], "cap_micros": policy[1],
                    "reserved_micros": spent[0], "requests": spent[1],
                    "available_micros": max(0, policy[1] - spent[0])}
        except sqlite3.Error as exc:
            raise CostGateDenied("ledger_unavailable") from exc
        finally:
            db.close()
