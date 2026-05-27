// 화창하다 CS봇 - 채팅 클라이언트 (외부 CDN 없음)

const $chat = document.getElementById("chat");
const $form = document.getElementById("form");
const $input = document.getElementById("input");
const $send = document.getElementById("send");
const $clear = document.getElementById("clear-btn");
const $sugs = document.getElementById("suggestions");
const $statsLine = document.getElementById("stats-line");
const $srcLine = document.getElementById("src-line");

const STORAGE_KEY = "cs_bot_chat_history_v1";

// ========== 유틸 ==========
function escapeHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

// 마크다운 렌더링 (제한적)
function renderMarkdown(text) {
  if (!text) return "";
  let html = escapeHtml(text);

  // URL → 링크
  html = html.replace(
    /(https?:\/\/[^\s<]+)/g,
    '<a href="$1" target="_blank" rel="noopener">$1</a>'
  );

  // **굵게**
  html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");

  // 간단 테이블 |---|
  const lines = html.split("\n");
  const out = [];
  let i = 0;
  while (i < lines.length) {
    if (lines[i].trim().startsWith("|") && i + 1 < lines.length &&
        /^\|[\s:|-]+\|$/.test(lines[i + 1].trim())) {
      const headers = lines[i].split("|").filter(c => c.trim()).map(c => c.trim());
      i += 2;
      const rows = [];
      while (i < lines.length && lines[i].trim().startsWith("|")) {
        rows.push(lines[i].split("|").filter(c => c.trim()).map(c => c.trim()));
        i++;
      }
      let table = "<table><thead><tr>";
      for (const h of headers) table += `<th>${h}</th>`;
      table += "</tr></thead><tbody>";
      for (const r of rows) {
        table += "<tr>";
        for (const c of r) table += `<td>${c}</td>`;
        table += "</tr>";
      }
      table += "</tbody></table>";
      out.push(table);
    } else {
      out.push(lines[i]);
      i++;
    }
  }
  return out.join("\n");
}

// ========== 피드백 ==========
async function sendFeedback(payload) {
  try {
    const r = await fetch("/api/feedback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    return r.ok;
  } catch (e) { return false; }
}

function attachFeedbackBar(bubble, question, content, meta) {
  // 환영 메시지 / 타이핑 / 빈 메시지 / 사용자 메시지는 피드백 X
  if (!question || content === "__typing__") return;
  if (meta && meta.status === "empty") return;

  const bar = document.createElement("div");
  bar.className = "feedback";

  const good = document.createElement("button");
  good.className = "fb-btn";
  good.type = "button";
  good.innerHTML = "👍 도움됐어요";

  const bad = document.createElement("button");
  bad.className = "fb-btn";
  bad.type = "button";
  bad.innerHTML = "👎 답변 수정 필요";

  const status = document.createElement("span");
  status.className = "fb-status";

  const commentWrap = document.createElement("div");
  commentWrap.className = "fb-comment";
  commentWrap.style.display = "none";

  const guide = document.createElement("div");
  guide.className = "fb-guide";
  guide.innerHTML =
    "<b>🙏 어떤 답이 정확한지 꼭 작성 부탁드려요</b><br>" +
    "임원진이 직접 보고 답변을 수정합니다. " +
    "한 분의 의견 하나하나가 봇 정확도에 큰 도움이 됩니다.";

  const commentRow = document.createElement("div");
  commentRow.className = "fb-comment-row";
  const commentInput = document.createElement("textarea");
  commentInput.rows = 2;
  commentInput.placeholder = "이런 답이 맞다고 생각해요 — 정확한 답변 내용을 적어주세요 (꼭 부탁드립니다)";
  const commentSend = document.createElement("button");
  commentSend.className = "fb-btn primary";
  commentSend.type = "button";
  commentSend.textContent = "전송";
  commentRow.appendChild(commentInput);
  commentRow.appendChild(commentSend);

  commentWrap.appendChild(guide);
  commentWrap.appendChild(commentRow);

  const basePayload = () => ({
    question,
    matched_id: meta && meta.matched_id ? meta.matched_id : null,
    answer: content,
    confidence: meta && typeof meta.confidence === "number" ? meta.confidence : null,
  });

  good.addEventListener("click", async () => {
    good.disabled = true; bad.disabled = true;
    const ok = await sendFeedback({ ...basePayload(), rating: "good" });
    bar.innerHTML = "";
    status.textContent = ok ? "👍 피드백 감사합니다!" : "전송 실패";
    bar.appendChild(status);
  });

  bad.addEventListener("click", () => {
    bad.disabled = true; good.disabled = true;
    commentWrap.style.display = "flex";
    commentInput.focus();
  });

  commentSend.addEventListener("click", async () => {
    commentSend.disabled = true;
    const ok = await sendFeedback({
      ...basePayload(),
      rating: "bad",
      comment: commentInput.value.trim(),
    });
    bar.innerHTML = "";
    status.textContent = ok ? "👎 피드백 감사합니다! 관리자가 검토할게요." : "전송 실패";
    bar.appendChild(status);
  });
  commentInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") commentSend.click();
  });

  bar.appendChild(good);
  bar.appendChild(bad);
  bar.appendChild(status);
  bubble.appendChild(bar);
  bubble.appendChild(commentWrap);
}

// ========== 메시지 추가 ==========
function addMessage(role, content, meta, opts) {
  const wrap = document.createElement("div");
  wrap.className = `message ${role}`;

  const avatar = document.createElement("div");
  avatar.className = "avatar";
  avatar.textContent = role === "bot" ? "🤖" : "🙋";

  const bubble = document.createElement("div");
  bubble.className = "bubble";

  if (content === "__typing__") {
    bubble.innerHTML = '<span class="typing"><span></span><span></span><span></span></span>';
  } else {
    bubble.innerHTML = renderMarkdown(content);
  }

  if (meta) {
    const metaEl = document.createElement("div");
    metaEl.className = "meta";

    if (meta.category) {
      const t = document.createElement("span");
      t.className = "tag";
      t.textContent = "📂 " + meta.category;
      metaEl.appendChild(t);
    }
    if (meta.matched_id) {
      const t = document.createElement("span");
      t.className = "tag muted";
      t.textContent = meta.matched_id;
      metaEl.appendChild(t);
    }
    if (typeof meta.confidence === "number") {
      const t = document.createElement("span");
      const c = meta.confidence;
      t.className = "tag " + (c >= 70 ? "success" : c >= 50 ? "" : "warning");
      t.textContent = `확신도 ${c.toFixed(1)}%`;
      metaEl.appendChild(t);
    }
    if (meta.status === "trigger_manufacturing") {
      const t = document.createElement("span");
      t.className = "tag danger";
      t.textContent = "🔔 제조지원톡 자동 안내";
      metaEl.appendChild(t);
    }
    if (meta.status === "no_match") {
      const t = document.createElement("span");
      t.className = "tag warning";
      t.textContent = "매칭 실패";
      metaEl.appendChild(t);
    }
    if (meta.warning) {
      const w = document.createElement("div");
      w.style.marginTop = "6px";
      w.style.fontSize = "12px";
      w.style.color = "#92400e";
      w.textContent = "⚠️ " + meta.warning;
      metaEl.appendChild(w);
    }
    bubble.appendChild(metaEl);

    if (meta.alternatives && meta.alternatives.length > 0) {
      const alts = document.createElement("div");
      alts.className = "alts";
      const title = document.createElement("div");
      title.className = "alts-title";
      title.textContent = "📚 관련 질문:";
      alts.appendChild(title);
      const ul = document.createElement("ul");
      for (const a of meta.alternatives) {
        const li = document.createElement("li");
        li.textContent = `${a.question} (${a.confidence}%)`;
        li.addEventListener("click", () => {
          $input.value = a.question;
          $input.focus();
        });
        ul.appendChild(li);
      }
      alts.appendChild(ul);
      bubble.appendChild(alts);
    }
  }

  // 봇 답변에 피드백 바 (의도적 응답에만)
  if (role === "bot" && opts && opts.question) {
    attachFeedbackBar(bubble, opts.question, content, meta);
  }

  wrap.appendChild(avatar);
  wrap.appendChild(bubble);
  $chat.appendChild(wrap);
  $chat.scrollTop = $chat.scrollHeight;

  return wrap;
}

// ========== 히스토리 ==========
function saveHistory(role, content, meta) {
  try {
    const h = JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]");
    h.push({ role, content, meta, ts: Date.now() });
    localStorage.setItem(STORAGE_KEY, JSON.stringify(h.slice(-100)));
  } catch (e) { /* ignore */ }
}
function loadHistory() {
  try {
    const h = JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]");
    for (const m of h) {
      const opts = m.meta && m.meta._q ? { question: m.meta._q } : undefined;
      addMessage(m.role, m.content, m.meta, opts);
    }
    return h.length;
  } catch (e) { return 0; }
}
function clearHistory() {
  localStorage.removeItem(STORAGE_KEY);
  $chat.innerHTML = "";
  showWelcome();
}

// ========== 환영 메시지 ==========
function showWelcome() {
  addMessage(
    "bot",
    "안녕하세요! **화창하다 CS봇**입니다 ☀️\n\n" +
    "💡 **\"이것까지 물어봐도 되나?\"** 싶은 것들을 **부담 없이 다 물어보세요.**\n" +
    "화장품 사업, 제품 개발, 강의, 미션, 배송 등 무엇이든 24시간 답변드립니다.\n\n" +
    "정확한 답변이 어려운 경우 채널톡이나 본인 집중코칭방으로 안내드려요.\n" +
    "아래 추천 질문을 눌러보시거나 직접 입력해주세요!"
  );
}

// ========== 전송 ==========
async function send(question) {
  question = (question || "").trim();
  if (!question) return;

  addMessage("user", question);
  saveHistory("user", question, null);

  $input.value = "";
  resizeInput();
  $send.disabled = true;

  const typing = addMessage("bot", "__typing__");

  try {
    const res = await fetch("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    });

    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();

    typing.remove();
    const meta = {
      status: data.status,
      category: data.category,
      matched_id: data.matched_id,
      confidence: data.confidence,
      warning: data.warning,
      alternatives: data.alternatives,
    };
    addMessage("bot", data.answer, meta, { question });
    saveHistory("bot", data.answer, { ...meta, _q: question });

    $srcLine.textContent = "출처: " + (data.source || "");
  } catch (err) {
    typing.remove();
    addMessage(
      "bot",
      "❌ 서버와 통신 중 오류가 발생했어요.\n다시 시도해주세요.\n\n(" + err.message + ")"
    );
  } finally {
    $send.disabled = false;
    $input.focus();
  }
}

// ========== 입력 리사이즈 ==========
function resizeInput() {
  $input.style.height = "auto";
  $input.style.height = Math.min($input.scrollHeight, 120) + "px";
}

// ========== 이벤트 ==========
$form.addEventListener("submit", (e) => {
  e.preventDefault();
  send($input.value);
});

$input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    send($input.value);
  }
});

$input.addEventListener("input", resizeInput);

$clear.addEventListener("click", () => {
  if (confirm("대화 기록을 모두 지울까요?")) clearHistory();
});

$sugs.addEventListener("click", (e) => {
  const t = e.target;
  if (t.classList.contains("chip")) {
    send(t.dataset.q);
  }
});

// ========== 초기화 ==========
async function init() {
  const count = loadHistory();
  if (count === 0) showWelcome();

  try {
    const res = await fetch("/api/stats");
    const s = await res.json();
    $statsLine.textContent =
      `검증 FAQ ${s.verified_faq_count}개 · 변형 ${s.variant_count}개 · 카톡 폴백 ${s.kakao_fallback_count}개`;
  } catch (e) { /* ignore */ }

  $input.focus();
}

init();
