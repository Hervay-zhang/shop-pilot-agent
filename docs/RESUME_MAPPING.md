# 简历能力映射

本文档把简历“电商 Agent 平台”里的关键描述映射到当前演示项目，方便面试时打开代码佐证。

> 原则：演示项目不是把原项目完整复刻，而是抽取最能证明能力的闭环。无法在 Demo 中真实接入的
> 外部系统会明确标记为模拟或被裁剪，不把演示数据说成生产数据。

## 1. 共用工具网关

**简历描述：** 8 类能力封装为 14 个 MCP 工具，统一 JSON Schema、鉴权、超时重试、幂等键和错误码分级。

**项目落地：**

- 14 个工具定义：`app/tool_gateway.py`
- FastMCP 服务：`app/mcp_server.py`
- JSON Schema：Pydantic 输入模型
- 鉴权：JWT + role scope
- 超时：每个工具最多 2.5 秒
- 重试：可重试错误最多两次
- 错误码：`TOOL_SCOPE_FORBIDDEN`、`VALIDATION_ERROR`、`UPSTREAM_TIMEOUT` 等
- 幂等：购物车和订单请求可携带幂等键

**演示接口：**

- `GET /api/tools`
- `POST /api/tools/call`
- `python -m app.mcp_server`

## 2. 商品数据结构化

**简历描述：** 将 SPU 属性从富文本抽取为结构化字段，让“16 寸电脑包”直接参与过滤。

**项目落地：**

- `Product.rich_text` 保留富文本；
- `Product.attributes` 提供 `laptop_size`、`scene`、`water_resistant` 等 JSON 字段；
- 检索先按 `laptop_size=16` 过滤，再算相关性；
- 演示输入“16 寸电脑通勤包”最终只召回 16 寸商品。

**代码：**

- `app/models.py`
- `app/seed.py`
- `app/retrieval.py`

## 3. 长任务规划与恢复

**简历描述：** LangGraph 拆解找货、比价、凑单、分批下单和物流跟踪；checkpointer 保存每步状态。

**项目落地：**

- 当前 Demo 实现最小主链：安全 → 理解 → 检索 → 算价 → 草稿 → 校验；
- `StateGraph` 和 `MemorySaver` 在 `app/buyer_service.py`；
- 每个节点额外写 `AgentRun.state`，保证业务状态可观察；
- `resume_router` 可按最后完成节点继续执行，`POST /api/buyer/runs/{id}/resume` 验证续跑；
- SSE 事件持久化，支持断线续传；
- 单步失败有明确的 `failed / blocked / needs_input` 状态；
- 物流工具已经存在，完整“分批下单 + 物流跟单”在简历原系统中，Demo 中保留工具入口。

## 4. 混合检索

**简历描述：** 口语需求转成类目、属性约束和排序偏好；先过滤，再 Qdrant 语义召回和关键词召回做 RRF，并结合行为重排。

**项目落地：**

- 预算、尺寸、库存、发货地硬过滤；
- 关键词重叠分；
- Qdrant 向量分；
- RRF 融合；
- 加购和支付行为分重排；
- 召回不足时返回放宽条件建议。

**代码：**

- `app/retrieval.py`
- `app/llm.py`

## 5. 确定性算价

**简历描述：** 固定函数计算满减、优惠券和会员价，与结算页共用实现。

**项目落地：**

- `unit_price_for`
- `best_coupon`
- `quote_products`
- `choose_bundle`

演示结果中的 ¥468、会员节省 ¥30、券减 ¥50、应付 ¥418 全部由固定函数计算。

## 6. 交易安全

**简历描述：** 模型只生成草稿；服务端校验金额、地址和 SKU；幂等键由用户、SKU 集合和请求 ID 生成；高风险操作二次确认。

**项目落地：**

- `OrderDraft` 与 `Order` 分离；
- `confirm_order` 重新读取 SKU、检查库存、重新算价；
- `derive_idempotency_key` 生成 SHA-256 幂等键；
- 高金额订单要求 `proof_token`；
- `ServiceRequest` 二次确认后进入人工复核；
- 支付凭证不写入模型状态。

## 7. SSE 与 UI Schema

**简历描述：** 前端通过 SSE 增量更新，支持断线续传。

**项目落地：**

- 事件落库：`RunEvent`
- 流接口：`GET /api/buyer/runs/{run_id}/events`
- 事件编号：自增 `sequence`
- 断线续传：`Last-Event-ID`
- 心跳：空注释事件
- UI Schema：`order_confirmation`
- 前端：`app/static/app.js`

## 8. 商家指标语义层

**简历描述：** 统一 GMV、UV、支付转化率、退款率等 60 余个指标的口径和粒度。

**项目落地：**

- 抽取 6 个代表指标并完整实现；
- 每个指标带公式、粒度、数据源和备注；
- 结构可继续扩展为指标注册表；
- 面试时说明：60+ 来自原项目，Demo 选择代表指标，避免变成报表系统。

## 9. 诊断归因

**简历描述：** 沿曝光、点击、加购、支付、退款五环节对比，输出 SKU、时间段、前后差值和证据强度。

**项目落地：**

- 最近 2 天 vs 之前 3 天；
- 五段漏斗；
- 输出 SKU、商品名、当前/基线日均、变化率；
- 样本量决定 evidence strength；
- 样本不足返回 `insufficient_evidence`；
- 模型不直接计算指标。

## 10. 可回滚写入

**简历描述：** 改价/活动/预算建模为变更单，提交前计算影响和回滚参数；超阈值审批；执行前后审计。

**项目落地：**

- `ChangeOrder`
- `before_snapshot`
- `impact_scope`
- `approval_required`
- `transition_change`
- `AuditLog`
- 状态机：`pending_approval → approved → executed → rolled_back`

## 11. 效果回测

**简历描述：** 按实验组/对照组验证动作效果，生成日报和复盘。

**项目落地：**

- `ExperimentResult`
- 两组转化率；
- 相对提升；
- 两比例近似置信度；
- 退款率变化；
- `/api/merchant/report` 返回经营日报。

## 12. 买家行为回流

**简历描述：** 回流曝光未点击、加购未支付、退款原因，作为商家诊断证据。

**项目落地：**

- `BehaviorEvent` 同时服务检索行为重排和商家漏斗；
- Diagnosis 返回 `evidence`，分别标记行为回流、交易库和售后库；
- 首页闭环图展示“曝光未点击 → 加购未支付 → 退款原因 → 经营诊断”。

## 13. 明确裁剪的部分

以下能力在简历原文存在，但当前 Demo 为控制范围没有逐一做成完整业务页：

- 60+ 指标全量；
- 真实 ERP / 广告后台接入；
- 分批下单全流程；
- 完整物流轨迹同步；
- 真实支付凭证 OCR；
- Alembic 生产迁移；
- 多租户真实 IAM；
- 原 Java 商品和订单服务接入。

面试时建议直接说：

> 我把原项目中能证明 Agent 工程闭环的部分抽成可运行 Demo。外部系统用工具替身模拟，
> 但工具边界、状态机、幂等、审批和回滚都真实实现。这样面试官能在十分钟内看到完整链路，
> 而不是花时间部署十几个依赖。
