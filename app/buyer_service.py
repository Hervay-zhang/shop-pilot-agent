from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from typing import TypedDict

from fastapi import HTTPException
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.cache import cache
from app.config import settings
from app.db import SessionLocal
from app.events import append_event
from app.llm import parser
from app.models import (
    AgentRun,
    Conversation,
    Order,
    OrderDraft,
    Product,
    ServiceRequest,
    User,
    new_id,
)
from app.pricing import choose_bundle, quote_products
from app.retrieval import retriever

PROMPT_INJECTION_PATTERNS = (
    r"忽略(?:之前|以上|所有)指令",
    r"(?:泄露|输出).{0,8}(?:系统提示|system prompt)",
    r"绕过.{0,8}(?:权限|限制|校验)",
    r"直接执行.{0,12}(?:退款|改价|下单)",
)
HIGH_RISK_TERMS = ("退款", "修改地址", "改地址", "代下单")


class BuyerState(TypedDict, total=False):
    request: str
    parsed: dict
    retrieval: dict
    selected_skus: list[str]
    quote: dict
    draft_id: str
    status: str
    error_code: str
    result: dict
    current_step: str


def mask_pii(text: str) -> str:
    text = re.sub(r"1[3-9]\d{9}", "***", text)
    text = re.sub(r"[\w.+-]+@[\w-]+\.[\w.]+", "***", text)
    text = re.sub(r"\b\d{17}[\dXx]\b", "***", text)
    return text


def unsafe_reason(text: str) -> str | None:
    lowered = text.lower()
    for pattern in PROMPT_INJECTION_PATTERNS:
        if re.search(pattern, lowered):
            return "PROMPT_INJECTION"
    if len(text) > 500:
        return "REQUEST_TOO_LONG"
    return None


class BuyerWorkflow:
    def __init__(self, db: Session, user: User, run: AgentRun, conversation: Conversation):
        self.db = db
        self.user = user
        self.run = run
        self.conversation = conversation
        cached_context = cache.get_json(self._cache_key(conversation.id))
        if cached_context:
            self.conversation.summary = cached_context.get("summary", conversation.summary)
            self.conversation.turns = cached_context.get("turns", conversation.turns)
        self.graph = self._build_graph()

    @staticmethod
    def _cache_key(conversation_id: str) -> str:
        return f"conversation:{conversation_id}:hot-context"

    def _build_graph(self):
        graph = StateGraph(BuyerState)
        graph.add_node("resume_router", lambda state: state)
        graph.add_node("guard", self._guard)
        graph.add_node("understand", self._understand)
        graph.add_node("retrieve", self._retrieve)
        graph.add_node("price", self._price)
        graph.add_node("draft", self._draft)
        graph.add_node("validate", self._validate)
        graph.add_edge(START, "resume_router")
        graph.add_conditional_edges(
            "resume_router",
            lambda state: state.get("resume_step", "guard"),
            {
                "guard": "guard",
                "understand": "understand",
                "retrieve": "retrieve",
                "price": "price",
                "draft": "draft",
                "validate": "validate",
            },
        )
        graph.add_conditional_edges(
            "guard",
            lambda state: "stop" if state.get("error_code") else "continue",
            {"stop": END, "continue": "understand"},
        )
        graph.add_conditional_edges(
            "understand",
            lambda state: "stop" if state.get("status") == "needs_input" else "continue",
            {"stop": END, "continue": "retrieve"},
        )
        graph.add_conditional_edges(
            "retrieve",
            lambda state: (
                "stop" if state.get("status") in {"needs_input", "no_match"} else "continue"
            ),
            {"stop": END, "continue": "price"},
        )
        graph.add_edge("price", "draft")
        graph.add_edge("draft", "validate")
        graph.add_edge("validate", END)
        return graph.compile(checkpointer=MemorySaver())

    def run_workflow(self, resume: bool = False) -> BuyerState:
        config = {"configurable": {"thread_id": self.run.id}}
        try:
            initial_state: BuyerState = (
                dict(self.run.state)
                if resume and self.run.state
                else {
                    "request": self.run.request_text,
                    "status": "running",
                    "current_step": "queued",
                }
            )
            next_step = {
                None: "guard",
                "queued": "guard",
                "guard": "understand",
                "understand": "retrieve",
                "retrieve": "price",
                "price": "draft",
                "draft": "validate",
            }.get(initial_state.get("current_step"), "guard")
            initial_state["resume_step"] = next_step
            if resume:
                append_event(
                    self.db,
                    self.run.id,
                    "run.resumed",
                    {
                        "from_step": self.run.current_step,
                        "message": "检测到未完成状态，已从持久化快照继续执行",
                    },
                )
            return self.graph.invoke(initial_state, config=config)
        except Exception as exc:
            self.run.status = "failed"
            self.run.error_code = "WORKFLOW_ERROR"
            self.run.result = {"message": "任务执行失败，已完成安全收尾。", "category": "system"}
            self.db.commit()
            append_event(
                self.db,
                self.run.id,
                "run.failed",
                {"code": "WORKFLOW_ERROR", "message": str(exc)[:160], "retryable": True},
            )
            raise

    def _checkpoint(self, state: BuyerState, step: str, label: str, detail: str) -> BuyerState:
        state["current_step"] = step
        state["status"] = state.get("status", "running")
        self.run.current_step = step
        self.run.state = state
        self.db.commit()
        append_event(
            self.db,
            self.run.id,
            "step",
            {"step": step, "label": label, "detail": detail, "status": "completed"},
        )
        return state

    def _guard(self, state: BuyerState) -> BuyerState:
        reason = unsafe_reason(state["request"])
        if reason:
            state.update(
                {
                    "error_code": reason,
                    "status": "blocked",
                    "result": {
                        "message": (
                            "该请求触发安全边界。我只处理合规的购物与经营需求，请换一种说法。"
                        ),
                        "category": "guardrail",
                    },
                }
            )
            self.run.status = "blocked"
            self.run.error_code = reason
            self.run.result = state["result"]
            self.db.commit()
            append_event(
                self.db,
                self.run.id,
                "run.failed",
                {"code": reason, "message": state["result"]["message"], "retryable": False},
            )
            return self._checkpoint(state, "guard", "安全预检", "入口拦截异常指令")
        state["status"] = "running"
        return self._checkpoint(state, "guard", "安全预检", "未发现越界指令或提示注入")

    def _understand(self, state: BuyerState) -> BuyerState:
        parsed = parser.parse(state["request"], self.user.member)
        state["parsed"] = parsed
        categories = parsed.get("categories", [])
        if not categories:
            state.update(
                {
                    "status": "needs_input",
                    "result": {
                        "message": "请补充要购买的商品类型，例如“16 寸电脑包和防晒霜”。",
                        "category": "clarification",
                        "missing_slots": ["categories"],
                    },
                }
            )
            self.run.status = "needs_input"
            self.run.result = state["result"]
            self.db.commit()
            append_event(
                self.db,
                self.run.id,
                "run.needs_input",
                {"message": state["result"]["message"], "missing_slots": ["categories"]},
            )
        return self._checkpoint(
            state,
            "understand",
            "需求理解",
            (
                f"识别品类 {', '.join(categories) or '未识别'}；"
                f"预算 {parsed['budget_cents'] / 100:.0f} 元"
            ),
        )

    def _retrieve(self, state: BuyerState) -> BuyerState:
        parsed = state["parsed"]
        grouped = retriever.search(
            self.db,
            self.user,
            state["request"],
            parsed["categories"],
            parsed["budget_cents"],
            parsed.get("city"),
            parsed.get("laptop_size"),
        )
        empty = [category for category, values in grouped.items() if not values]
        if empty:
            relaxed = "，".join(empty)
            state.update(
                {
                    "status": "needs_input",
                    "result": {
                        "message": (
                            f"{relaxed}在当前预算或发货地条件下暂无库存，"
                            "可放宽预算或优先考虑其他品类。"
                        ),
                        "category": "clarification",
                        "missing_slots": [f"relax:{item}" for item in empty],
                    },
                }
            )
            self.run.status = "needs_input"
            self.run.result = state["result"]
            self.db.commit()
            append_event(self.db, self.run.id, "run.needs_input", state["result"])
            return self._checkpoint(state, "retrieve", "混合检索", "约束过滤后无可用商品")

        state["retrieval"] = {
            category: [item.explain() for item in values[:4]]
            for category, values in grouped.items()
        }
        return self._checkpoint(
            state,
            "retrieve",
            "混合检索",
            f"关键词召回 + Qdrant 向量召回 + RRF + 行为重排；向量后端 {retriever.vector_status}",
        )

    def _price(self, state: BuyerState) -> BuyerState:
        parsed = state["parsed"]
        grouped_products: dict[str, list[Product]] = {}
        for category, values in state["retrieval"].items():
            skus = [item["sku"] for item in values]
            products = {
                item.sku: item
                for item in self.db.scalars(select(Product).where(Product.sku.in_(skus))).all()
            }
            grouped_products[category] = [products[sku] for sku in skus if sku in products]
        bundle = choose_bundle(self.db, grouped_products, self.user, int(parsed["budget_cents"]))
        if not bundle:
            state.update(
                {
                    "status": "needs_input",
                    "result": {
                        "message": "完整清单超出预算。我可以保留必买项，或把预算放宽 10% 再试。",
                        "category": "clarification",
                        "missing_slots": ["budget"],
                    },
                }
            )
            self.run.status = "needs_input"
            self.run.result = state["result"]
            self.db.commit()
            append_event(self.db, self.run.id, "run.needs_input", state["result"])
            return self._checkpoint(state, "price", "确定性算价", "组合超预算，返回放宽条件建议")
        selected, quote = bundle
        state["selected_skus"] = [item.sku for item in selected]
        state["quote"] = quote.to_dict()
        return self._checkpoint(
            state,
            "price",
            "确定性算价",
            (
                f"会员价与优惠券由固定函数计算，共 {len(selected)} 件，"
                f"应付 ¥{quote.total_cents / 100:.2f}"
            ),
        )

    def _draft(self, state: BuyerState) -> BuyerState:
        draft_payload = {
            "items": [
                {
                    "sku": line["sku"],
                    "name": line["name"],
                    "unit_price": line["unit_price"],
                    "quantity": line["quantity"],
                }
                for line in state["quote"]["lines"]
            ],
            "quote": state["quote"],
            "shipping_address": mask_pii(self.user.shipping_address),
            "status": "pending_confirmation",
        }
        draft = OrderDraft(
            run_id=self.run.id,
            user_id=self.user.id,
            payload=draft_payload,
            total_cents=int(state["quote"]["total_cents"]),
        )
        self.db.add(draft)
        self.db.commit()
        state["draft_id"] = draft.id
        return self._checkpoint(state, "draft", "生成待确认草稿", "模型无权直接落单")

    def _validate(self, state: BuyerState) -> BuyerState:
        products = self.db.scalars(
            select(Product).where(Product.sku.in_(state["selected_skus"]))
        ).all()
        quote = quote_products(self.db, products, self.user)
        issues = []
        if len(products) != len(state["selected_skus"]):
            issues.append("SKU_NOT_FOUND")
        if any(item.stock <= 0 or not item.active for item in products):
            issues.append("SKU_UNAVAILABLE")
        if quote.total_cents != int(state["quote"]["total_cents"]):
            issues.append("PRICE_CHANGED")
        if issues:
            state.update(
                {
                    "status": "validation_failed",
                    "error_code": issues[0],
                    "result": {
                        "message": "商品库存或价格发生变化，请重新生成方案。",
                        "category": "validation",
                        "issues": issues,
                    },
                }
            )
            self.run.status = "needs_input"
            self.run.error_code = issues[0]
            self.run.result = state["result"]
            self.db.commit()
            append_event(self.db, self.run.id, "run.needs_input", state["result"])
            return self._checkpoint(state, "validate", "后置校验", "草稿校验未通过")

        state["result"] = {
            "category": "order_draft",
            "title": "购物方案草稿",
            "message": "已按你的预算比较商品组合。订单尚未提交，请确认价格、地址和 SKU 后再落单。",
            "draft_id": state["draft_id"],
            "items": state["quote"]["lines"],
            "price_breakdown": state["quote"],
            "retrieval": state["retrieval"],
            "evidence": {
                "filter_order": ["预算", "尺码", "库存", "发货地", "语义相关性", "行为偏好"],
                "vector_backend": retriever.vector_status,
                "price_engine": "deterministic-v1",
                "safety": "草稿未落单；确认时由服务端重新校验",
            },
            "ui_schema": {
                "type": "order_confirmation",
                "actions": [
                    {"key": "confirm", "label": "确认订单"},
                    {"key": "adjust", "label": "调整条件"},
                ],
            },
            "requires_confirmation": True,
        }
        state["status"] = "completed"
        self.run.status = "completed"
        self.run.result = state["result"]
        self._save_conversation_turn(state)
        self.db.commit()
        append_event(self.db, self.run.id, "run.completed", state["result"])
        return self._checkpoint(state, "validate", "完成", "服务端已校验 SKU、库存、金额与地址")

    def _save_conversation_turn(self, state: BuyerState) -> None:
        turns = list(self.conversation.turns or [])
        turns.append(
            {
                "role": "user",
                "text": mask_pii(state["request"]),
                "at": datetime.now(UTC).isoformat(timespec="seconds"),
            }
        )
        turns.append(
            {
                "role": "assistant",
                "text": state["result"].get("message", ""),
                "draft_id": state.get("draft_id"),
                "at": datetime.now(UTC).isoformat(timespec="seconds"),
            }
        )
        if len(turns) > 6:
            older = turns[:-4]
            self.conversation.summary = (
                (self.conversation.summary + "；" if self.conversation.summary else "")
                + f"此前 {len(older)} 轮主要为购物需求确认"
            )[:500]
            turns = turns[-4:]
        self.conversation.turns = turns
        cache.set_json(
            self._cache_key(self.conversation.id),
            {"summary": self.conversation.summary, "turns": turns},
            ttl_seconds=1800,
        )


def create_run(db: Session, user: User, message: str, conversation_id: str | None) -> AgentRun:
    conversation = db.get(Conversation, conversation_id) if conversation_id else None
    if conversation and conversation.user_id != user.id:
        raise HTTPException(status_code=403, detail={"code": "CONVERSATION_FORBIDDEN"})
    if not conversation:
        conversation = Conversation(user_id=user.id, turns=[])
        db.add(conversation)
        db.flush()
    run = AgentRun(
        conversation_id=conversation.id,
        user_id=user.id,
        request_text=message,
        status="queued",
        state={},
    )
    db.add(run)
    db.commit()
    append_event(
        db,
        run.id,
        "run.started",
        {
            "run_id": run.id,
            "conversation_id": conversation.id,
            "message": "已创建可恢复的购物任务",
        },
    )
    return run


def process_run(run_id: str, resume: bool = False) -> None:
    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        if not run:
            return
        user = db.get(User, run.user_id)
        conversation = db.get(Conversation, run.conversation_id)
        if not user or not conversation:
            run.status = "failed"
            run.error_code = "CONTEXT_NOT_FOUND"
            db.commit()
            return
        run.status = "running"
        db.commit()
        workflow = BuyerWorkflow(db, user, run, conversation)
        final_state = workflow.run_workflow(resume=resume)
        run.state = final_state
        if final_state.get("status") == "needs_input":
            run.status = "needs_input"
        elif final_state.get("status") == "completed":
            run.status = "completed"
        elif final_state.get("error_code"):
            run.status = "blocked"
        db.commit()


def run_to_dict(db: Session, run: AgentRun) -> dict:
    return {
        "id": run.id,
        "conversation_id": run.conversation_id,
        "status": run.status,
        "current_step": run.current_step,
        "state": run.state,
        "result": run.result,
        "error_code": run.error_code,
        "created_at": run.created_at.isoformat(),
        "updated_at": run.updated_at.isoformat(),
    }


def derive_idempotency_key(user_id: str, skus: list[str], request_id: str) -> str:
    raw = f"{user_id}|{','.join(sorted(skus))}|{request_id}"
    return hashlib.sha256(raw.encode()).hexdigest()


def confirm_order(
    db: Session,
    user: User,
    draft_id: str,
    request_id: str,
    shipping_address: str,
    proof_token: str | None,
) -> tuple[Order, bool]:
    draft = db.get(OrderDraft, draft_id)
    if not draft or draft.user_id != user.id:
        raise HTTPException(status_code=404, detail={"code": "DRAFT_NOT_FOUND"})
    if draft.status == "confirmed":
        order_id = draft.payload.get("order_id")
        existing = db.get(Order, order_id) if order_id else None
        if existing and existing.user_id == user.id:
            return existing, True
        raise HTTPException(status_code=409, detail={"code": "DRAFT_ALREADY_CONFIRMED"})
    if draft.status != "pending_confirmation":
        raise HTTPException(status_code=409, detail={"code": "DRAFT_NOT_CONFIRMABLE"})

    skus = [item["sku"] for item in draft.payload["items"]]
    products = db.scalars(select(Product).where(Product.sku.in_(skus))).all()
    if len(products) != len(skus):
        raise HTTPException(status_code=409, detail={"code": "SKU_NOT_FOUND"})
    if any(not item.active or item.stock <= 0 for item in products):
        raise HTTPException(status_code=409, detail={"code": "SKU_UNAVAILABLE"})
    quote = quote_products(db, products, user)
    if quote.total_cents != draft.total_cents:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "PRICE_CHANGED",
                "message": "价格已变化，请重新生成草稿",
                "expected_cents": draft.total_cents,
                "actual_cents": quote.total_cents,
            },
        )
    if draft.total_cents > settings.high_value_order_cents and not proof_token:
        raise HTTPException(
            status_code=428,
            detail={
                "code": "HIGH_VALUE_PROOF_REQUIRED",
                "message": "该订单超过高金额阈值，需在服务端校验支付凭证",
            },
        )
    if draft.total_cents > settings.max_request_cents:
        raise HTTPException(status_code=403, detail={"code": "ORDER_AMOUNT_EXCEEDED"})

    idempotency_key = derive_idempotency_key(user.id, skus, request_id)
    existing = db.scalar(select(Order).where(Order.idempotency_key == idempotency_key))
    if existing:
        return existing, True
    order = Order(
        id=new_id(),
        user_id=user.id,
        sku_list=skus,
        total_cents=draft.total_cents,
        shipping_address=mask_pii(shipping_address),
        status="paid",
        idempotency_key=idempotency_key,
    )
    draft.payload = {**draft.payload, "order_id": order.id}
    draft.status = "confirmed"
    db.add(order)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(select(Order).where(Order.idempotency_key == idempotency_key))
        if existing:
            return existing, True
        raise HTTPException(status_code=409, detail={"code": "ORDER_CONFLICT"}) from None
    return order, False


def create_service_request(
    db: Session, user: User, request_type: str, order_id: str | None, details: dict
) -> ServiceRequest:
    item = ServiceRequest(
        user_id=user.id,
        request_type=request_type,
        payload={"order_id": order_id, "details": details},
        status="waiting_confirmation",
        requires_human_review=True,
    )
    db.add(item)
    db.commit()
    return item


def confirm_service_request(db: Session, user: User, request_id: str) -> ServiceRequest:
    item = db.get(ServiceRequest, request_id)
    if not item or item.user_id != user.id:
        raise HTTPException(status_code=404, detail={"code": "REQUEST_NOT_FOUND"})
    if item.status != "waiting_confirmation":
        raise HTTPException(status_code=409, detail={"code": "REQUEST_ALREADY_PROCESSED"})
    item.status = "pending_human_review"
    db.commit()
    return item
