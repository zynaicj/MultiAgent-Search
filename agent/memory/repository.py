import os

from dotenv import load_dotenv
from pymongo import AsyncMongoClient

from agent.memory.schemas import MemoryRecord


load_dotenv()


MONGO_URI = os.getenv(
    "MONGO_URI",
    "mongodb://localhost:27017",
)

MONGO_DB_NAME = os.getenv(
    "MONGO_DB_NAME",
    "multiagent_memory",
)

MEMORY_COLLECTION_NAME = "memories"


class MemoryRepository:
    """
    长期记忆 MongoDB 存储层。

    这里只负责：
    1. 建立 MongoDB 连接
    2. 保存 Memory
    3. 查询 Memory

    不负责 LLM 提取、
    Prompt 构建和 Workflow 调度。
    """

    def __init__(self):
        self.client = AsyncMongoClient(
            MONGO_URI
        )

        self.database = self.client[
            MONGO_DB_NAME
        ]

        self.collection = self.database[
            MEMORY_COLLECTION_NAME
        ]

    async def ping(self) -> bool:
        """
        测试 MongoDB 是否能够正常连接。
        """

        await self.client.admin.command(
            "ping"
        )

        return True

    async def save_memory(
        self,
        memory: MemoryRecord,
    ) -> str:
        """
        保存一条长期记忆。

        返回 MongoDB 生成的 _id。
        """

        document = memory.model_dump()

        result = await self.collection.insert_one(
            document
        )

        return str(
            result.inserted_id
        )

    async def get_all_memories(
        self,
    ) -> list[dict]:
        """
        查询当前所有长期记忆。

        第一版用于功能验证。
        后续做 Vector Search 后，
        这里会升级成按相关度召回。
        """

        cursor = self.collection.find(
            {}
        ).sort(
            "created_at",
            -1,
        )

        memories = await cursor.to_list(
            length=100
        )

        for memory in memories:
            memory["_id"] = str(
                memory["_id"]
            )

        return memories

    async def close(self):
        """
        关闭 MongoDB Client。
        """

        await self.client.close()


memory_repository = MemoryRepository()