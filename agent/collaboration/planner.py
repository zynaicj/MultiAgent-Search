from langchain_core.messages import HumanMessage, SystemMessage

from agent.llm import model
from agent.collaboration.schemas import TaskPlan
from agent.collaboration.state import CollaborationState
from langchain_core.output_parsers import PydanticOutputParser
from api.monitor import monitor


PLANNER_SYSTEM_PROMPT = """
你是多智能体协作系统中的任务规划智能体 Planner。

你必须严格按照指定的 JSON Schema 输出结果，
最终结果必须是合法的 JSON 数据，不要输出 JSON 之外的额外文字。

你的职责不是直接回答用户问题，而是：
分析用户目标，并将复杂任务拆解为一个或多个专业智能体可以独立完成的子任务。

当前系统只有以下三个专业智能体：

1. 市场情报助手
   - 负责互联网公开信息检索
   - 适合行业趋势、竞品动态、市场新闻、消费者洞察等任务

2. 数据分析助手
   - 负责查询企业 MySQL 数据库
   - 适合商品销售、库存、订单、用户行为、经营指标等任务

3. 内部知识助手
   - 负责查询企业内部 RAGFlow 知识库
   - 适合运营 SOP、历史复盘、公司政策、内部制度等任务


规划规则：

1. 只拆解真正需要执行的任务，不要为了使用多个智能体而强行制造无关任务。

2. 一个复杂问题如果同时涉及外部市场信息、内部经营数据、
   企业知识文档，应分别交给对应的专业智能体。

3. 每个子任务必须目标明确，使对应 Worker 不需要再次猜测自己应该做什么。

4. expected_output 要明确说明该 Worker 应该返回哪些信息。

5. task_id 必须唯一，按照：
   task_1
   task_2
   task_3
   ...
   的格式生成。

6. 不允许创建系统中不存在的智能体。

7. 文件生成不是三个专业 Worker 的职责，
   不要创建“生成 Markdown”“生成 PDF”等子任务。

8. 当前第一版协作工作流只实现一层并行 Worker，
   因此所有任务的 depends_on 暂时都必须为空列表 []。

9. 如果问题只需要一个专业智能体即可解决，只创建一个任务。
   如果确实需要多个信息来源，则创建多个可以并行执行的任务。

10. 你只负责规划，不要直接回答用户问题。
"""


planner_parser = PydanticOutputParser(
    pydantic_object=TaskPlan
)

async def planner_node(
    state: CollaborationState
) -> dict:
    """
    根据用户问题生成结构化的多智能体任务计划。
    """

    query = state["query"]

    monitor._emit(
        "collaboration_planner_start",
        "Planner 开始分析并拆解任务",
        {}
    )

    format_instructions = (
        planner_parser.get_format_instructions()
    )

    response = await model.ainvoke(
        [
            SystemMessage(
                content=f"""
{PLANNER_SYSTEM_PROMPT}

你必须严格按照下面给出的 JSON 格式返回结果。

字段名称必须完全一致：
- 最外层必须包含 goal
- 最外层必须包含 tasks
- 禁止使用 sub_tasks、task_list 等其他字段名

{format_instructions}

只返回合法 JSON，不要输出 JSON 之外的任何文字。
"""
            ),
            HumanMessage(
                content=f"""
请为下面的用户任务生成执行计划：

{query}
"""
            ),
        ]
    )

    plan = planner_parser.parse(
        response.content
    )

    monitor._emit(
        "collaboration_plan_created",
        f"Planner 已生成 {len(plan.tasks)} 个子任务",
        {
            "goal": plan.goal,
            "tasks": [
                task.model_dump()
                for task in plan.tasks
            ],
        }
    )

    return {
        "plan": plan
    }