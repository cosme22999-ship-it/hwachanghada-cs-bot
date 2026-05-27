// 화창하다 CS봇 관리자 페이지 클라이언트 로직
// (FAQ CRUD + 피드백 코멘트/처리완료)

(function () {
  "use strict";

  // ===== 공용 =====
  const $ = (id) => document.getElementById(id);
  const $toast = $("toast");

  function toast(msg) {
    if (!$toast) return;
    $toast.textContent = msg;
    $toast.classList.add("show");
    setTimeout(() => $toast.classList.remove("show"), 2500);
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  async function api(url, opts) {
    opts = opts || {};
    opts.credentials = "include";
    opts.headers = Object.assign(
      { "Content-Type": "application/json" },
      opts.headers || {}
    );
    const r = await fetch(url, opts);
    if (!r.ok) {
      let detail = `HTTP ${r.status}`;
      try {
        const err = await r.json();
        if (err && err.detail) detail = err.detail;
      } catch (_) {}
      throw new Error(detail);
    }
    return r.json();
  }

  // ===== FAQ CRUD =====
  const $list = $("faq-list");
  const $q = $("faq-question");
  const $a = $("faq-answer");
  const $al = $("faq-aliases");
  const $cat = $("faq-category");
  const $editId = $("faq-edit-id");
  const $saveBtn = $("faq-save-btn");
  const $editInfo = $("faq-edit-info");

  let __faqs = [];

  async function loadFaqs() {
    try {
      const data = await api("/admin/api/faqs");
      __faqs = data.faqs || [];
      renderFaqs();
    } catch (e) {
      toast("FAQ 목록 로드 실패: " + e.message);
    }
  }

  function renderFaqs() {
    if (!__faqs.length) {
      $list.innerHTML =
        '<div id="faq-empty">아직 추가된 FAQ가 없습니다. 위 폼에서 추가해보세요!</div>';
      return;
    }
    const html = __faqs
      .map((f) => {
        const aliases = (f.aliases || [])
          .map((a) => `<span class="alias-chip">${escapeHtml(a)}</span>`)
          .join("");
        return `
        <div class="faq-list-row">
          <div class="head">
            <div>
              <span class="id">${escapeHtml(f.id)}</span>
              <span style="margin-left:6px">${escapeHtml(f.category || "")}</span>
            </div>
            <div>
              <button class="btn" data-edit="${escapeHtml(f.id)}">✏️ 편집</button>
              <button class="btn danger" data-delete="${escapeHtml(f.id)}">🗑️ 삭제</button>
            </div>
          </div>
          <div class="question">${escapeHtml(f.question)}</div>
          <div class="answer">${escapeHtml(f.answer)}</div>
          <div class="aliases">${aliases}</div>
        </div>`;
      })
      .join("");
    $list.innerHTML = html;
  }

  $list.addEventListener("click", (e) => {
    const t = e.target;
    if (t.dataset.edit) editFaq(t.dataset.edit);
    if (t.dataset.delete) deleteFaq(t.dataset.delete);
  });

  function resetForm() {
    $editId.value = "";
    $q.value = "";
    $a.value = "";
    $al.value = "";
    $cat.value = "관리자 추가";
    $saveBtn.textContent = "+ 추가";
    $editInfo.textContent = "";
    $q.focus();
  }

  function editFaq(id) {
    const f = __faqs.find((x) => x.id === id);
    if (!f) return;
    $editId.value = id;
    $q.value = f.question || "";
    $a.value = f.answer || "";
    $al.value = (f.aliases || []).join(", ");
    $cat.value = f.category || "관리자 추가";
    $saveBtn.textContent = "💾 수정 저장";
    $editInfo.textContent = "편집 중: " + id;
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  async function saveFaq() {
    const question = $q.value.trim();
    const answer = $a.value.trim();
    if (!question || !answer) {
      toast("질문과 답변은 필수입니다");
      return;
    }
    const aliases = $al.value
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    const category = $cat.value.trim() || "관리자 추가";
    const editId = $editId.value;

    $saveBtn.disabled = true;
    const originalText = $saveBtn.textContent;
    $saveBtn.textContent = "저장 중...";

    try {
      let url = "/admin/api/faqs";
      let method = "POST";
      if (editId) {
        url = "/admin/api/faqs/" + encodeURIComponent(editId);
        method = "PUT";
      }
      const data = await api(url, {
        method,
        body: JSON.stringify({ question, answer, aliases, category }),
      });
      toast(
        editId
          ? editId + " 수정됨 — 즉시 검색 가능"
          : data.id + " 추가됨 — 즉시 검색 가능"
      );
      resetForm();
      await loadFaqs();
    } catch (e) {
      toast("저장 실패: " + e.message);
    } finally {
      $saveBtn.disabled = false;
      $saveBtn.textContent = $editId.value ? "💾 수정 저장" : "+ 추가";
    }
  }

  async function deleteFaq(id) {
    if (!confirm(id + " 삭제할까요? 봇이 더 이상 이 FAQ를 사용하지 않습니다."))
      return;
    try {
      await api("/admin/api/faqs/" + encodeURIComponent(id), {
        method: "DELETE",
      });
      toast(id + " 삭제됨");
      if ($editId.value === id) resetForm();
      await loadFaqs();
    } catch (e) {
      toast("삭제 실패: " + e.message);
    }
  }

  $saveBtn.addEventListener("click", saveFaq);
  const $resetBtn = document.querySelector('[data-action="reset"]');
  if ($resetBtn) $resetBtn.addEventListener("click", resetForm);

  // ===== 피드백 (👎 답변 수정 필요) =====
  const $fbList = $("feedback-list");
  let __feedback = [];

  async function loadFeedback() {
    if (!$fbList) return;
    try {
      const data = await api("/admin/api/feedback");
      __feedback = data.feedback || [];
      renderFeedback();
    } catch (e) {
      $fbList.innerHTML =
        '<div class="fb-empty">피드백 로드 실패: ' + escapeHtml(e.message) + "</div>";
    }
  }

  function renderFeedback() {
    if (!__feedback.length) {
      $fbList.innerHTML =
        '<div class="fb-empty">아직 수정 필요 피드백이 없습니다 🎉</div>';
      return;
    }
    const html = __feedback
      .map((f) => {
        const conf =
          typeof f.confidence === "number"
            ? `(매칭 ${f.confidence.toFixed(1)}%)`
            : "";
        const matchedId = f.matched_id
          ? `<span class="id">${escapeHtml(f.matched_id)}</span>`
          : '<span class="id">매칭X</span>';
        const studentComment = f.comment
          ? `<div class="student-comment"><b>학생 코멘트:</b><br>${escapeHtml(
              f.comment
            )}</div>`
          : '<div class="student-comment empty">학생이 코멘트를 작성하지 않음</div>';
        const resolvedClass = f.resolved ? " resolved" : "";
        const resolvedBtnText = f.resolved ? "↩️ 미해결로" : "✅ 처리완료";
        return `
        <div class="fb-row${resolvedClass}" data-id="${f.id}">
          <div class="meta">
            <span class="ts">${escapeHtml(f.created_at)}</span>
            ${matchedId}
            <span class="conf">${conf}</span>
            ${f.resolved ? '<span style="color:#10b981;font-weight:600">✅ 처리완료</span>' : ""}
          </div>
          <div class="q">${escapeHtml(f.question)}</div>
          ${studentComment}
          <div class="admin-note">
            <label>📝 관리자 메모 (어떻게 처리했는지 / 무엇을 수정할지)</label>
            <textarea data-note="${f.id}" placeholder="예: Q106 답변에 부자재 발주 단계 추가 (CUSTOM-3으로 보강)">${escapeHtml(
          f.admin_note || ""
        )}</textarea>
            <div class="row-actions">
              <button class="btn primary" data-save-note="${f.id}">💾 메모 저장</button>
              <button class="btn" data-toggle-resolved="${f.id}">${resolvedBtnText}</button>
              <span class="saved" data-saved="${f.id}"></span>
            </div>
          </div>
        </div>`;
      })
      .join("");
    $fbList.innerHTML = html;
  }

  if ($fbList) {
    $fbList.addEventListener("click", async (e) => {
      const t = e.target;
      if (t.dataset.saveNote) {
        const id = t.dataset.saveNote;
        const ta = $fbList.querySelector(`textarea[data-note="${id}"]`);
        if (!ta) return;
        t.disabled = true;
        try {
          await api("/admin/api/feedback/" + id, {
            method: "PATCH",
            body: JSON.stringify({ admin_note: ta.value }),
          });
          const saved = $fbList.querySelector(`[data-saved="${id}"]`);
          if (saved) {
            saved.textContent = "저장됨";
            setTimeout(() => (saved.textContent = ""), 2000);
          }
          // 로컬 데이터 갱신 (재로드 안 함 - UX 안정)
          const f = __feedback.find((x) => x.id == id);
          if (f) f.admin_note = ta.value;
        } catch (err) {
          toast("저장 실패: " + err.message);
        } finally {
          t.disabled = false;
        }
      }
      if (t.dataset.toggleResolved) {
        const id = t.dataset.toggleResolved;
        const f = __feedback.find((x) => x.id == id);
        if (!f) return;
        t.disabled = true;
        try {
          await api("/admin/api/feedback/" + id, {
            method: "PATCH",
            body: JSON.stringify({ resolved: !f.resolved }),
          });
          f.resolved = !f.resolved;
          renderFeedback();
        } catch (err) {
          toast("실패: " + err.message);
        } finally {
          t.disabled = false;
        }
      }
    });
  }

  // ===== 초기화 =====
  loadFaqs();
  loadFeedback();
})();
