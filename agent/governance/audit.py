import json
import os

from datetime import datetime
from enum import Enum
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

from pydantic import (
    BaseModel,
    Field,
)

from agent.governance.models import (
    RiskLevel,
)


class AuditEventType(str, Enum):
    """
    Tool Governance 的审计事件类型。
    """

    POLICY_EVALUATED = "POLICY_EVALUATED"

    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"

    APPROVED = "APPROVED"

    REJECTED = "REJECTED"

    EXECUTED = "EXECUTED"

    FAILED = "FAILED"


class AuditRecord(BaseModel):
    """
    一条工具治理审计记录。
    """

    audit_id: str = Field(
        default_factory=lambda:
            str(uuid4())
    )

    timestamp: datetime = Field(
        default_factory=lambda:
            datetime.now().astimezone()
    )

    thread_id: str

    tool_name: str

    operation: str

    risk_level: RiskLevel

    event_type: AuditEventType

    requires_approval: bool

    reason: str

    args: dict[str, Any] = Field(
        default_factory=dict
    )

    decision: str | None = None

    success: bool | None = None

    error: str | None = None


class GovernanceAuditLogger:
    """
    Tool Governance 审计日志记录器。

    V1 使用 JSONL 文件：

    data/governance_audit.jsonl
    """

    def __init__(
        self,
        audit_file: Path | None = None,
    ):

        if audit_file is None:

            project_root = (
                Path(__file__)
                .resolve()
                .parents[2]
            )

            default_path = (
                project_root
                / "data"
                / "governance_audit.jsonl"
            )

            configured_path = (
                os.getenv(
                    "GOVERNANCE_AUDIT_PATH"
                )
            )

            audit_file = (
                Path(configured_path)
                if configured_path
                else default_path
            )


        self.audit_file = (
            audit_file.resolve()
        )


        self.audit_file.parent.mkdir(
            parents=True,
            exist_ok=True,
        )


        self._lock = RLock()


    def record(
        self,
        *,
        thread_id: str,
        tool_name: str,
        operation: str,
        risk_level: RiskLevel,
        event_type: AuditEventType,
        requires_approval: bool,
        reason: str,
        args: dict[str, Any] | None = None,
        decision: str | None = None,
        success: bool | None = None,
        error: str | None = None,
    ) -> AuditRecord:
        """
        写入一条审计记录。
        """

        record = AuditRecord(

            thread_id=thread_id,

            tool_name=tool_name,

            operation=operation,

            risk_level=risk_level,

            event_type=event_type,

            requires_approval=(
                requires_approval
            ),

            reason=reason,

            args=(
                args or {}
            ),

            decision=decision,

            success=success,

            error=error,
        )


        payload = (
            record.model_dump(
                mode="json"
            )
        )


        line = json.dumps(
            payload,
            ensure_ascii=False,
        )


        with self._lock:

            with self.audit_file.open(
                "a",
                encoding="utf-8",
            ) as file:

                file.write(
                    line
                    + "\n"
                )


        return record


    def read_recent(
        self,
        limit: int = 100,
        thread_id: str | None = None,
    ) -> list[AuditRecord]:
        """
        读取最近的审计记录。
        """

        if limit <= 0:
            return []


        if not self.audit_file.exists():
            return []


        records: list[
            AuditRecord
        ] = []


        with self._lock:

            with self.audit_file.open(
                "r",
                encoding="utf-8",
            ) as file:

                for raw_line in file:

                    line = (
                        raw_line.strip()
                    )


                    if not line:
                        continue


                    try:

                        payload = (
                            json.loads(
                                line
                            )
                        )


                        record = (
                            AuditRecord
                            .model_validate(
                                payload
                            )
                        )


                    except (
                        json.JSONDecodeError,
                        ValueError,
                    ):

                        continue


                    if (
                        thread_id is not None
                        and
                        record.thread_id
                        != thread_id
                    ):

                        continue


                    records.append(
                        record
                    )


        return records[
            -limit:
        ]


audit_logger = (
    GovernanceAuditLogger()
)