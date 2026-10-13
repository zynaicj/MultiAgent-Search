import os
from datetime import datetime, timezone
from bson import ObjectId
from dotenv import load_dotenv
from pymongo import AsyncMongoClient
from agent.memory.schemas import MemoryRecord, MemoryType
from agent.memory.native import DEFAULT_USER_ID, memory_namespace


load_dotenv()


MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "multiagent_memory")
MEMORY_COLLECTION_NAME = "memories"


class MemoryRepository:
    """
    长期记忆 MongoDB 存储层。

    这里只负责：
    1. 建立 MongoDB 连接
    2. 保存 Memory
    3. 查询 Memory
    4. 更新 Memory

    不负责 LLM 提取、
    Prompt 构建和 Workflow 调度。
    """

    def __init__(self):
        self.client = AsyncMongoClient(MONGO_URI)
        self.database = self.client[MONGO_DB_NAME]
        self.collection = self.database[MEMORY_COLLECTION_NAME]

    async def ping(self) -> bool:
        """
        测试 MongoDB 是否能够正常连接。
        """

        await self.client.admin.command("ping")
        return True

    async def save_memory(self, memory: MemoryRecord, user_id: str = DEFAULT_USER_ID) -> str:
        """
        保存一条长期记忆。

        返回 MongoDB 生成的 _id。
        """

        memory_namespace(user_id)  # Validate the trusted local user identifier.
        document = {**memory.model_dump(), "owner_id": user_id}
        result = await self.collection.insert_one(document)
        return str(result.inserted_id)

    async def get_memories_by_type(self, memory_type: MemoryType, limit: int = 50, user_id: str = DEFAULT_USER_ID) -> list[dict]:
        """
        查询指定类型的长期记忆。

        当前主要提供给 Memory Manager
        做重复和更新判断。

        后续引入向量检索后，
        会改成只召回语义相关的记忆。
        """

        memory_namespace(user_id)
        cursor = self.collection.find({"owner_id": user_id, "memory_type": memory_type}).sort("created_at", -1)
        memories = await cursor.to_list(length=limit)

        for memory in memories:
            memory["_id"] = str(memory["_id"])

        return memories

    async def update_memory(self, memory_id: str, content: str, user_id: str = DEFAULT_USER_ID) -> bool:
        """
        更新指定长期记忆的内容。
        """

        memory_namespace(user_id)
        result = await self.collection.update_one({"_id": ObjectId(memory_id), "owner_id": user_id}, {"$set": {"content": content, "updated_at": datetime.now(timezone.utc)}})
        return result.matched_count > 0

    async def get_all_memories(self, user_id: str = DEFAULT_USER_ID) -> list[dict]:
        """
        查询当前所有长期记忆。

        第一版用于功能验证。
        后续做 Vector Search 后，
        这里会升级成按相关度召回。
        """

        memory_namespace(user_id)
        # Deliberately do NOT read legacy records with no owner_id.
        # They need explicit backup/review/migration before use.
        cursor = self.collection.find({"owner_id": user_id}).sort("created_at", -1)
        memories = await cursor.to_list(length=100)

        for memory in memories:
            memory["_id"] = str(memory["_id"])

        return memories

    async def close(self):
        """
        关闭 MongoDB Client。
        """

        await self.client.close()


memory_repository = MemoryRepository()