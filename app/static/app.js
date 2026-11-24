const state = {
  buyerToken: null,
  merchantToken: null,
  conversationId: null,
  currentRunId: null,
  currentResult: null,
  eventSource: null,
  lastFocused: null,
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

function escapeHtml(value = "") {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function money(value) {
  return new Intl.NumberFormat("zh-CN", {
    style: "currency",
    currency: "CNY",
    currencyDisplay: "narrowSymbol",
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  }).format(Number(value || 0));
}

function toast(message, type = "normal", duration = 3500) {
  const node = document.createElement("div");
  node.className = `toast ${type === "error" ? "error" : ""}`;
  node.setAttribute("role", type === "error" ? "alert" : "status");
  node.textContent = message;
  $("#toast-stack").append(node);
  setTimeout(() => node.remove(), duration);
}

async function api(path, options = {}, token = state.buyerToken) {
  const headers = { ...(options.headers || {}) };
  if (options.body && !(options.body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
  }
  if (token) headers.Authorization = `Bearer ${token}`;
  const response = await fetch(path, { ...options, headers });
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    payload = {};
  }
  if (!response.ok) {
    const detail = payload?.detail || payload;
    const message =
      typeof detail === "string"
        ? detail
        : detail?.message || detail?.code || `请求失败 (${response.status})`;
    const error = new Error(message);
    error.payload = payload;
    error.status = response.status;
    throw error;
  }
  return payload;
}

async function getToken(role) {
  const data = await api(`/api/auth/demo-token?role=${role}`, {}, null);
  return data.access_token;
}

async function bootstrap() {
  try {
    const [health, buyer, merchant] = await Promise.all([
      api("/api/health", {}, null),
      getToken("buyer"),
      getToken("merchant"),
    ]);
    state.buyerToken = buyer;
    state.merchantToken = merchant;
    const pill = $("#runtime-pill");
    pill.classList.toggle("online", health.status === "ok");
    pill.querySelector("span").textContent =
      health.status === "ok" ? "服务在线" : "服务状态异常";
    $("#runtime-pill").title = JSON.stringify(health, null, 2);
    const params = new URLSearchParams(window.location.search);
    const initialView = params.get("view");
    if (initialView === "merchant") switchView("merchant");
    if (params.get("scenario") === "buyer") {
      $("#buyer-input").value =
        "预算 800 元，买一个 16 寸电脑通勤包和防晒霜，上海发货";
      await submitBuyerPrompt($("#buyer-input").value);
    }
  } catch (error) {
    toast(`服务连接失败：${error.message}`, "error", 7000);
  }
}

function switchView(view) {
  $$(".nav-item").forEach((button) => {
    const active = button.dataset.view === view;
    button.classList.toggle("active", active);
    if (active) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  $$(".view").forEach((section) =>
    section.classList.toggle("active", section.id === `${view}-view`)
  );
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  window.scrollTo({ top: 0, behavior: reduceMotion ? "auto" : "smooth" });
  if (view === "merchant" && state.merchantToken) {
    loadMerchant();
  }
}

function addMessage(role, text, tags = []) {
  const node = document.createElement("div");
  node.className = `message ${role}`;
  node.innerHTML = `
    <div class="avatar">${role === "user" ? "你" : "S"}</div>
    <div class="bubble">
      <p>${escapeHtml(text)}</p>
      ${
        tags.length
          ? `<div class="inline-tags">${tags.map((tag) => `<span>${escapeHtml(tag)}</span>`).join("")}</div>`
          : ""
      }
    </div>`;
  $("#messages").append(node);
  $("#messages").scrollTop = $("#messages").scrollHeight;
}

function resetRunStrip() {
  $$(".run-step").forEach((step) => {
    step.classList.remove("running", "completed", "failed");
    step.querySelector("small").textContent = "等待任务";
  });
}

function setRunStep(stepName, status, detail = "") {
  const step = $(`.run-step[data-step="${stepName}"]`);
  if (!step) return;
  step.classList.remove("running", "completed", "failed");
  if (status) step.classList.add(status);
  step.querySelector("small").textContent =
    detail || (status === "completed" ? "已完成" : status === "running" ? "执行中" : "等待任务");
}

async function submitBuyerPrompt(message) {
  const cleanMessage = message.trim();
  if (!cleanMessage || $("#send-button").disabled) return;
  if (!state.buyerToken) {
    toast("服务尚未连接，请稍后重试", "error");
    return;
  }
  addMessage("user", cleanMessage);
  $("#buyer-input").value = "";
  $("#send-button").disabled = true;
  resetRunStrip();
  setRunStep("guard", "running");
  if (state.eventSource) state.eventSource.close();

  try {
    const run = await api("/api/buyer/runs", {
      method: "POST",
      body: JSON.stringify({
        message: cleanMessage,
        conversation_id: state.conversationId,
      }),
    });
    state.currentRunId = run.run_id;
    state.conversationId = run.conversation_id;
    followRun(run.run_id);
  } catch (error) {
    $("#send-button").disabled = false;
    setRunStep("guard", "failed", "创建失败");
    addMessage("assistant", `任务创建失败：${error.message}`);
  }
}

function followRun(runId) {
  const streamUrl =
    `/api/buyer/runs/${runId}/events?token=${encodeURIComponent(state.buyerToken)}`;
  const source = new EventSource(streamUrl);
  state.eventSource = source;

  source.addEventListener("step", (event) => {
    const data = JSON.parse(event.data);
    setRunStep(data.step, "completed", data.detail);
    const next = {
      guard: "understand",
      understand: "retrieve",
      retrieve: "price",
      price: "draft",
      draft: "validate",
    }[data.step];
    if (next && next !== "draft") setRunStep(next, "running");
  });

  source.addEventListener("run.completed", (event) => {
    const data = JSON.parse(event.data);
    state.currentResult = data;
    setRunStep("validate", "completed", "通过");
    addMessage("assistant", data.message, [
      "服务端复算金额",
      "SKU 状态校验",
      "未自动下单",
    ]);
    renderOrderDraft(data);
    finishRun();
  });

  source.addEventListener("run.needs_input", (event) => {
    const data = JSON.parse(event.data);
    addMessage("assistant", data.message || "还需要补充需求信息。");
    toast(data.message || "请补充条件");
    finishRun();
  });

  source.addEventListener("run.failed", (event) => {
    const data = JSON.parse(event.data);
    addMessage("assistant", data.message || "任务被安全拦截。", ["入口规则拦截"]);
    setRunStep("guard", "failed", "已拦截");
    finishRun();
  });

  source.onerror = async () => {
    source.close();
    setTimeout(async () => {
      try {
        const run = await api(`/api/buyer/runs/${runId}`);
        if (run.status === "completed" && run.result) {
          renderOrderDraft(run.result);
          addMessage("assistant", run.result.message);
        } else if (!["running", "queued"].includes(run.status)) {
          addMessage("assistant", run.result?.message || "任务已结束，请刷新重试。");
        }
      } catch {
        addMessage("assistant", "事件流已断开，但任务可能仍在执行，请稍后刷新。");
      }
      finishRun();
    }, 900);
  };
}

function finishRun() {
  $("#send-button").disabled = false;
  if (state.eventSource) {
    state.eventSource.close();
    state.eventSource = null;
  }
}

const productIcons = {
  电脑包:
    '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 8.5h14l-1.2 11H6.2z"/><path d="M9 8.5V6.8a3 3 0 0 1 6 0v1.7"/></svg>',
  防晒:
    '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="8" y="6" width="8" height="14" rx="2"/><path d="M10 3h4M11 9h2M11 12h2"/></svg>',
  晴雨伞:
    '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 12a8 8 0 0 1 16 0H4Z"/><path d="M12 12v5.5a2.5 2.5 0 0 0 5 0"/></svg>',
  充电器:
    '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="6" y="7" width="12" height="12" rx="2"/><path d="M9 3v4M15 3v4M10 12h4M12 12v4"/></svg>',
  耳机:
    '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 14v-2a7 7 0 0 1 14 0v2"/><rect x="4" y="13" width="4" height="7" rx="2"/><rect x="16" y="13" width="4" height="7" rx="2"/></svg>',
  保温杯:
    '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 7h8l-1 13H9L8 7Z"/><path d="M7 4h10M10 10h4"/></svg>',
  颈枕:
    '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 10a7 7 0 0 1 14 0v3a7 7 0 0 1-14 0v-3Z"/><path d="M9 13h6"/></svg>',
};

function productIcon(name) {
  const entry = Object.entries(productIcons).find(([key]) => name.includes(key));
  return entry
    ? productIcons[entry[0]]
    : '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m4 8 8-4 8 4-8 4-8-4Z"/><path d="m4 8 8 4 8-4v8l-8 4-8-4V8Z"/></svg>';
}

function renderOrderDraft(result) {
  $("#empty-result").classList.add("hidden");
  const container = $("#result-content");
  container.classList.remove("hidden");
  const quote = result.price_breakdown;
  const items = result.items || [];
  const evidenceItems = Object.entries(result.retrieval || {}).map(([category, values]) => {
    const top = values[0];
    return `
      <div class="evidence-item">
        <b>${escapeHtml(category)} · ${escapeHtml(top?.name || "无")}</b>
        <span>RRF ${Number(top?.score || 0).toFixed(3)}</span>
      </div>`;
  });

  container.innerHTML = `
    <div class="result-topline">
      <span>
        <i aria-hidden="true">
          <svg viewBox="0 0 16 16"><path d="m4 8 2.5 2.5L12 5"/></svg>
        </i>
        已完成服务端校验
      </span>
      <small>草稿 ${escapeHtml(result.draft_id?.slice(0, 8) || "")}</small>
    </div>
    <h3 class="draft-title">${escapeHtml(result.title || "购物方案草稿")}</h3>
    <p class="draft-copy">${escapeHtml(result.message || "")}</p>
    <div class="product-list">
      ${items
        .map(
          (item) => `
        <div class="product-row">
          <div class="product-visual">${productIcon(item.name)}</div>
          <div>
            <strong>${escapeHtml(item.name)}</strong>
            <small>${escapeHtml(item.sku)} · 数量 ${Number(item.quantity)}</small>
          </div>
          <div class="product-price">${money(item.line_total)}</div>
        </div>`
        )
        .join("")}
    </div>
    <div class="price-box">
      <div class="price-line"><span>商品小计</span><span>${money(quote.subtotal)}</span></div>
      <div class="price-line discount"><span>会员价节省</span><span>-${money(quote.member_saving)}</span></div>
      <div class="price-line discount">
        <span>${escapeHtml(quote.coupon_name || "优惠券")}</span>
        <span>-${money(quote.coupon_discount)}</span>
      </div>
      <div class="price-total"><span>应付金额</span><strong>${money(quote.total)}</strong></div>
    </div>
    <div class="security-line">
      <b>✓</b>
      <span>确认时会重新校验库存、价格和演示收货地址。</span>
    </div>
    <div class="result-actions">
      <button class="primary-button" id="confirm-order-button">确认订单</button>
      <button class="secondary-button" id="adjust-order-button">调整条件</button>
    </div>

    <details class="evidence-details">
      <summary>查看检索与安全证据</summary>
      <div class="evidence-list">
        ${evidenceItems.join("")}
        <div class="evidence-item"><b>过滤顺序</b><span>${escapeHtml(result.evidence?.filter_order?.join(" → ") || "")}</span></div>
        <div class="evidence-item"><b>向量后端</b><span>${escapeHtml(result.evidence?.vector_backend || "local")}</span></div>
        <div class="evidence-item"><b>算价引擎</b><span>${escapeHtml(result.evidence?.price_engine || "")}</span></div>
      </div>
    </details>`;

  const randomBytes = crypto.getRandomValues(new Uint8Array(16));
  const requestId = Array.from(randomBytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
  $("#confirm-order-button").addEventListener("click", () => confirmOrder(result, requestId));
  $("#adjust-order-button").addEventListener("click", () => {
    $("#buyer-input").focus();
    $("#buyer-input").value = "预算改为 1000 元，";
  });
}

async function confirmOrder(result, requestId) {
  const button = $("#confirm-order-button");
  button.disabled = true;
  button.textContent = "正在校验并落单…";
  try {
    const data = await api("/api/buyer/orders/confirm", {
      method: "POST",
      body: JSON.stringify({
        draft_id: result.draft_id,
        confirmed: true,
        request_id: requestId,
        shipping_address: "上海市浦东新区演示路 88 号",
      }),
    });
    button.textContent = `已确认 · ${data.order_id.slice(0, 8)}`;
    button.classList.remove("primary-button");
    button.classList.add("secondary-button");
    toast(`演示订单已创建 · ${data.order_id.slice(0, 8)}`);
    addMessage("assistant", `演示订单已创建，订单号 ${data.order_id.slice(0, 12)}。`);
  } catch (error) {
    button.disabled = false;
    button.textContent = "确认订单";
    toast(error.message, "error");
  }
}

async function loadMerchant() {
  if (!state.merchantToken) return;
  try {
    const [dashboard, diagnosis, changes, experiments, report] = await Promise.all([
      api("/api/merchant/dashboard", {}, state.merchantToken),
      api("/api/merchant/diagnose", {}, state.merchantToken),
      api("/api/merchant/changes", {}, state.merchantToken),
      api("/api/merchant/experiments", {}, state.merchantToken),
      api("/api/merchant/report", {}, state.merchantToken),
    ]);
    renderKpis(dashboard);
    renderFunnel(dashboard.funnel, diagnosis);
    renderChanges(changes.items);
    renderExperiment(experiments.experiments?.[0]);
    $("#merchant-headline").textContent = diagnosis.summary;
    $("#semantic-version").textContent = dashboard.semantic_layer_version;
    $("#report-time").textContent = report.generated_at?.replace("T", " ") || "刚刚更新";
  } catch (error) {
    toast(`商家数据加载失败：${error.message}`, "error");
  }
}

function renderKpis(data) {
  const footnotes = {
    gmv: "交易库 · 去退款前口径",
    uv: "买家行为回流 · 曝光去重近似",
    cvr: "支付订单 / 曝光",
    refund: "退款订单 / 支付订单",
  };
  $("#kpi-grid").innerHTML = data.cards
    .map((card) => {
      const value =
        card.format === "currency"
          ? money(card.value)
          : Number(card.value).toLocaleString("zh-CN", { maximumFractionDigits: 2 });
      const unit = card.format === "percent" ? "%" : "";
      const delta = Number(card.delta);
      const deltaIsNegative = card.key === "refund" ? delta > 0 : delta < 0;
      const deltaUnit = card.format === "percent" ? " 个百分点" : "%";
      return `
        <article class="kpi-card">
          <div class="kpi-top">
            <span class="kpi-label">${escapeHtml(card.label)}</span>
            <span class="kpi-delta ${deltaIsNegative ? "negative" : ""}">
              ${delta >= 0 ? "↑" : "↓"} ${Math.abs(delta).toFixed(2)}${deltaUnit} · 较上期
            </span>
          </div>
          <div class="kpi-value">${value}<small>${unit}</small></div>
          <div class="kpi-foot">${escapeHtml(footnotes[card.key] || "")}</div>
        </article>`;
    })
    .join("");
}

function renderFunnel(funnel, diagnosis) {
  const max = Math.max(...funnel.map((item) => item.value), 1);
  const primary = diagnosis.primary;
  $("#funnel").innerHTML = funnel
    .map((item) => {
      const width = Math.max(0, item.value / max * 100);
      const anomaly = primary?.stage_label && item.label.includes(primary.stage_label);
      return `
      <div class="funnel-stage ${anomaly ? "anomaly" : ""}">
        <div class="funnel-label"><span>${escapeHtml(item.label)}</span><b>${Number(item.value).toLocaleString("zh-CN")}</b></div>
        <div class="funnel-bar-track">
          <div class="funnel-bar" style="--fill:${width.toFixed(2)}%"></div>
        </div>
      </div>`;
    })
    .join("");
  const evidenceLabels = { high: "强", medium: "中", low: "弱" };
  const dateSpan = (range) => (range || []).map((day) => escapeHtml(day.slice(5))).join(" 至 ");
  $("#diagnosis-note").innerHTML = `
    <span class="note-label">${primary ? `主要异常 · 证据强度 ${escapeHtml(evidenceLabels[primary.evidence_strength] || "未分级")}` : "诊断结果"}</span>
    ${primary
      ? `<strong>${escapeHtml(primary.product_name)}</strong> 的${escapeHtml(primary.stage_label)}环节较基线
         ${primary.delta_pct > 0 ? "上升" : "下降"} <strong>${Math.abs(primary.delta_pct).toFixed(1)}%</strong>。
         样本量 ${Number(primary.sample_size).toLocaleString("zh-CN")}。
         <small class="note-window">${dateSpan(diagnosis.window?.current)} 对比 ${dateSpan(diagnosis.window?.baseline)}</small>`
      : escapeHtml(diagnosis.summary)}
  `;
}

function statusLabel(status) {
  return {
    pending_approval: "待审批",
    approved: "已审批",
    executed: "已执行",
    rolled_back: "已回滚",
    draft: "草稿",
  }[status] || status;
}

function renderChanges(changes) {
  if (!changes.length) {
    $("#change-list").innerHTML = `<div class="change-item"><small>暂无变更单</small></div>`;
    return;
  }
  $("#change-list").innerHTML = changes
    .map((change) => {
      const actions = [];
      if (change.status === "pending_approval") {
        actions.push(`<button class="mini-button" data-change="${change.id}" data-action="approve">审批</button>`);
      }
      if (change.status === "approved") {
        actions.push(`<button class="mini-button" data-change="${change.id}" data-action="execute">执行</button>`);
      }
      if (change.status === "executed") {
        actions.push(`<button class="mini-button danger" data-change="${change.id}" data-action="rollback">回滚</button>`);
      }
      const oldPrice = (change.before_snapshot?.price_cents / 100).toFixed(0);
      const newPrice = (change.payload?.new_price_cents / 100).toFixed(0);
      return `
        <div class="change-item">
          <div>
            <strong>${escapeHtml(change.sku)} · ¥${oldPrice} → ¥${newPrice}
              <span class="change-state ${escapeHtml(change.status)}">${escapeHtml(statusLabel(change.status))}</span>
            </strong>
            <small>
              <code class="trace-code">回执 ${escapeHtml(change.id.slice(0, 8))}</code>
              影响 ${change.impact_scope?.sku_count || 1} 个 SKU · 风险金额 ${money(change.impact_scope?.risk_amount)} · 预计 7 日 GMV 变化 ${money(change.expected_gmv_delta)}
            </small>
          </div>
          <div class="change-actions">${actions.join("")}</div>
        </div>`;
    })
    .join("");
  $$("[data-change][data-action]").forEach((button) => {
    button.addEventListener("click", () =>
      transitionChange(button.dataset.change, button.dataset.action, button)
    );
  });
}

async function transitionChange(changeId, action, button) {
  button.disabled = true;
  try {
    const result = await api(
      `/api/merchant/changes/${changeId}/${action}`,
      { method: "POST" },
      state.merchantToken
    );
    toast(`变更单 ${changeId.slice(0, 8)} · ${statusLabel(result.status)}`);
    await loadMerchant();
  } catch (error) {
    button.disabled = false;
    toast(error.message, "error");
  }
}

function renderExperiment(experiment) {
  if (!experiment) {
    $("#experiment-content").innerHTML = "<p class='draft-copy'>暂无实验数据</p>";
    return;
  }
  const control = experiment.control.conversion_rate;
  const treatment = experiment.treatment.conversion_rate;
  const max = Math.max(control, treatment, 0.01);
  $("#experiment-content").innerHTML = `
    <div class="experiment-card">
      <div class="experiment-title">${escapeHtml(experiment.name)}</div>
      <div class="variant-bars">
        <div class="variant-row">
          <span>对照组</span>
          <div class="variant-track"><div class="variant-fill" style="--fill:${(control / max) * 100}%"></div></div>
          <b>${control.toFixed(2)}%</b>
        </div>
        <div class="variant-row treatment">
          <span>实验组</span>
          <div class="variant-track"><div class="variant-fill" style="--fill:${(treatment / max) * 100}%"></div></div>
          <b>${treatment.toFixed(2)}%</b>
        </div>
      </div>
      <div class="experiment-summary">
        <div><span>相对提升</span><strong>+${experiment.relative_lift}%</strong></div>
        <div><span>置信度</span><strong>${experiment.confidence}%</strong></div>
        <div><span>退款率变化</span><strong>${experiment.refund_rate_delta.toFixed(2)} 个百分点</strong></div>
      </div>
    </div>`;
}

async function submitChange(event) {
  event.preventDefault();
  if (!state.merchantToken) return;
  const button = $("#change-form button");
  button.disabled = true;
  try {
    const data = await api(
      "/api/merchant/changes",
      {
        method: "POST",
        body: JSON.stringify({
          kind: "price_change",
          sku: $("#change-sku").value,
          new_price_cents: Math.round(Number($("#change-price").value) * 100),
          effective_from: "today",
        }),
      },
      state.merchantToken
    );
    const result = $("#proposal-result");
    result.classList.remove("hidden");
    result.innerHTML = `
      <strong>已生成变更单 ${escapeHtml(data.change_order_id.slice(0, 8))}</strong>
      ${data.approval_required ? "风险金额超过阈值，已进入审批。" : "变更已获准执行。"}
      预计 7 日 GMV 变化 ${money(data.expected_gmv_delta)}，回滚快照已保存。`;
    toast("变更单已生成，等待审批或执行");
    await loadMerchant();
  } catch (error) {
    toast(error.message, "error");
  } finally {
    button.disabled = false;
  }
}

async function showTools() {
  const modal = $("#tools-modal");
  state.lastFocused = document.activeElement;
  modal.classList.add("open");
  modal.setAttribute("aria-hidden", "false");
  $(".app-shell").inert = true;
  $("#tools-close").focus();
  try {
    const data = await api("/api/tools");
    $("#tool-grid").innerHTML = data.items
      .map(
        (tool) => `
        <div class="tool-card">
          <div class="tool-card-top">
            <code>${escapeHtml(tool.name)}</code>
            <small>${escapeHtml(tool.category)} · ${tool.idempotent ? "可重复执行" : "写操作"}</small>
          </div>
          <p>${escapeHtml(tool.description)}</p>
        </div>`
      )
      .join("");
  } catch (error) {
    $("#tool-grid").innerHTML = `<p>${escapeHtml(error.message)}</p>`;
  }
}

function closeTools() {
  const modal = $("#tools-modal");
  if (!modal.classList.contains("open")) return;
  modal.classList.remove("open");
  modal.setAttribute("aria-hidden", "true");
  $(".app-shell").inert = false;
  state.lastFocused?.focus?.();
  state.lastFocused = null;
}

$$(".nav-item").forEach((button) =>
  button.addEventListener("click", () => switchView(button.dataset.view))
);

$$(".scenario-chip").forEach((button) =>
  button.addEventListener("click", () => {
    $("#buyer-input").value = button.dataset.prompt;
    $("#buyer-input").focus();
  })
);

$("#buyer-form").addEventListener("submit", (event) => {
  event.preventDefault();
  submitBuyerPrompt($("#buyer-input").value);
});

$("#buyer-input").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing && event.keyCode !== 229) {
    event.preventDefault();
    $("#buyer-form").requestSubmit();
  }
});

$("#change-form").addEventListener("submit", submitChange);
$("#refresh-merchant").addEventListener("click", loadMerchant);
$("#tools-button").addEventListener("click", showTools);
$$("[data-close-modal]").forEach((button) => button.addEventListener("click", closeTools));
document.addEventListener("keydown", (event) => {
  const modal = $("#tools-modal");
  if (!modal.classList.contains("open")) return;
  if (event.key === "Escape") {
    event.preventDefault();
    closeTools();
    return;
  }
  if (event.key !== "Tab") return;
  const focusable = [
    ...modal.querySelectorAll(
      'button:not(:disabled), [href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])'
    ),
  ].filter((node) => !node.hidden);
  if (!focusable.length) {
    event.preventDefault();
    $(".modal-card").focus();
    return;
  }
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
});

bootstrap();
