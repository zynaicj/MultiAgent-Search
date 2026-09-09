from threading import RLock

from agent.governance.models import (
    ApprovalStatus,
    PendingApproval,
)


class ApprovalRegistry:
    """
    保存当前所有待人工审批的 Tool Call。

    第一版设计：
    一个 thread_id 同一时刻只允许存在
    一个待审批操作。
    """

    def __init__(self):

        self._approvals: dict[
            str,
            PendingApproval,
        ] = {}

        # 修改：
        # Tool 可能运行在线程池，
        # FastAPI API 又运行在事件循环中，
        # 因此这里使用线程锁保护共享字典。
        self._lock = RLock()


    def register(
        self,
        approval: PendingApproval,
    ) -> tuple[
        PendingApproval,
        bool,
    ]:
        """
        注册待审批操作。

        返回：
        (approval, is_new)

        is_new=True：
            第一次注册。

        is_new=False：
            interrupt Resume 后节点重新执行，
            发现还是同一个审批。
        """

        with self._lock:

            existing = self._approvals.get(
                approval.thread_id
            )

            if existing is not None:

                same_request = (
                    existing.tool_name
                    == approval.tool_name
                    and existing.operation
                    == approval.operation
                    and existing.args
                    == approval.args
                )

                if same_request:
                    return existing, False

                # 修改：
                # 当前 thread 已有别的敏感操作等待审批，
                # 不允许后来的请求直接覆盖它。
                raise RuntimeError(
                    "当前会话已经存在其他待审批操作"
                )

            self._approvals[
                approval.thread_id
            ] = approval

            return approval, True


    def get(
        self,
        thread_id: str,
    ) -> PendingApproval | None:
        """
        查询指定 thread 当前的待审批操作。
        """

        with self._lock:

            approval = self._approvals.get(
                thread_id
            )

            if approval is None:
                return None

            return approval.model_copy(
                deep=True
            )


    def mark_resuming(
        self,
        thread_id: str,
    ) -> PendingApproval | None:
        """
        用户已经提交审批，
        Graph 正准备 Resume。
        """

        with self._lock:

            approval = self._approvals.get(
                thread_id
            )

            if approval is None:
                return None

            if (
                approval.status
                != ApprovalStatus.PENDING
            ):
                return None

            approval.status = (
                ApprovalStatus.RESUMING
            )

            return approval.model_copy(
                deep=True
            )


    def mark_pending(
        self,
        thread_id: str,
    ):
        """
        Resume 如果发生系统异常，
        将审批重新恢复到 PENDING，
        允许用户再次操作。
        """

        with self._lock:

            approval = self._approvals.get(
                thread_id
            )

            if approval is not None:

                approval.status = (
                    ApprovalStatus.PENDING
                )


    def clear(
        self,
        thread_id: str,
    ):
        """
        审批已经消费完成后清除。
        """

        with self._lock:

            self._approvals.pop(
                thread_id,
                None,
            )


# 修改：全局唯一审批注册表
approval_registry = ApprovalRegistry()