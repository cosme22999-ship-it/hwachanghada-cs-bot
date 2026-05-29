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

  // ===== 미매칭 로그 (👀 답 못 한 질문) =====
  const $umList = $("unmatched-list");
  let __unmatched = [];

  async function loadUnmatched() {
    if (!$umList) return;
    try {
      const data = await api("/admin/api/unmatched");
      __unmatched = data.unmatched || [];
      renderUnmatched();
    } catch (e) {
      $umList.innerHTML =
        '<div class="fb-empty">미매칭 로그 로드 실패: ' + escapeHtml(e.message) + "</div>";
    }
  }

  function renderUnmatched() {
    if (!__unmatched.length) {
      $umList.innerHTML =
        '<div class="fb-empty">아직 미매칭 질문이 없습니다 🎉</div>';
      return;
    }
    const html = __unmatched
      .map((u) => {
        const badge = u.status === "no_match" ? "🔴 매칭실패" : "🟡 저확신";
        const confStr = u.confidence ? ` (${u.confidence.toFixed(1)}%)` : "";
        const alt = u.top_alt_question
          ? `<div class="student-comment empty">가장 가까운 FAQ: <b>[${escapeHtml(
              u.top_alt_id || ""
            )}]</b> ${escapeHtml(u.top_alt_question)} (${u.top_alt_confidence?.toFixed(
              1
            )}%)</div>`
          : "";
        const resolvedClass = u.resolved ? " resolved" : "";
        const resolvedBtnText = u.resolved ? "↩️ 미해결로" : "✅ 처리완료";
        const resolvedBadge = u.resolved
          ? '<span style="color:#10b981;font-weight:600">✅ 처리완료</span>'
          : "";
        return `
        <div class="fb-row${resolvedClass}" data-id="${u.id}">
          <div class="meta">
            <span class="ts">${escapeHtml(u.created_at)}</span>
            <span class="id">${badge}${confStr}</span>
            ${resolvedBadge}
          </div>
          <div class="q">${escapeHtml(u.question)}</div>
          ${alt}
          <div class="admin-note">
            <label>📝 관리자 메모</label>
            <textarea data-um-note="${u.id}" placeholder="예: 비슷한 질문 자주 나옴 → Q22 별칭 보강 완료">${escapeHtml(
          u.admin_note || ""
        )}</textarea>
            <div class="row-actions">
              <button class="btn primary" data-um-make-faq="${u.id}">📝 이 질문으로 FAQ 만들기</button>
              <button class="btn" data-um-save-note="${u.id}">💾 메모 저장</button>
              <button class="btn" data-um-toggle="${u.id}">${resolvedBtnText}</button>
              <span class="saved" data-um-saved="${u.id}"></span>
            </div>
          </div>
        </div>`;
      })
      .join("");
    $umList.innerHTML = html;
  }

  if ($umList) {
    $umList.addEventListener("click", async (e) => {
      const t = e.target;

      // FAQ 만들기 - 폼에 자동 채움
      if (t.dataset.umMakeFaq) {
        const id = t.dataset.umMakeFaq;
        const u = __unmatched.find((x) => x.id == id);
        if (!u) return;
        resetForm();
        $q.value = u.question;
        $a.focus();
        window.scrollTo({ top: 0, behavior: "smooth" });
        toast("질문이 폼에 입력됨 → 답변 작성 후 + 추가");
        return;
      }

      // 메모 저장
      if (t.dataset.umSaveNote) {
        const id = t.dataset.umSaveNote;
        const ta = $umList.querySelector(`textarea[data-um-note="${id}"]`);
        if (!ta) return;
        t.disabled = true;
        try {
          await api("/admin/api/unmatched/" + id, {
            method: "PATCH",
            body: JSON.stringify({ admin_note: ta.value }),
          });
          const saved = $umList.querySelector(`[data-um-saved="${id}"]`);
          if (saved) {
            saved.textContent = "저장됨";
            setTimeout(() => (saved.textContent = ""), 2000);
          }
          const u = __unmatched.find((x) => x.id == id);
          if (u) u.admin_note = ta.value;
        } catch (err) {
          toast("저장 실패: " + err.message);
        } finally {
          t.disabled = false;
        }
        return;
      }

      // 처리완료 토글
      if (t.dataset.umToggle) {
        const id = t.dataset.umToggle;
        const u = __unmatched.find((x) => x.id == id);
        if (!u) return;
        t.disabled = true;
        try {
          await api("/admin/api/unmatched/" + id, {
            method: "PATCH",
            body: JSON.stringify({ resolved: !u.resolved }),
          });
          u.resolved = !u.resolved;
          renderUnmatched();
        } catch (err) {
          toast("실패: " + err.message);
        } finally {
          t.disabled = false;
        }
        return;
      }
    });
  }

  // ===== 검증 FAQ 수정 (104개) =====
  const $vList = $("verified-list");
  const $vSearch = $("verified-search");
  let __verified = [];

  async function loadVerified() {
    if (!$vList) return;
    try {
      const data = await api("/admin/api/verified-faqs");
      __verified = data.faqs || [];
      renderVerified($vSearch ? $vSearch.value : "");
    } catch (e) {
      $vList.innerHTML =
        '<div class="fb-empty">검증 FAQ 로드 실패: ' + escapeHtml(e.message) + "</div>";
    }
  }

  function renderVerified(filter) {
    const q = (filter || "").trim().toLowerCase();
    const items = q
      ? __verified.filter(
          (f) =>
            f.id.toLowerCase().includes(q) ||
            (f.question || "").toLowerCase().includes(q) ||
            (f.answer || "").toLowerCase().includes(q) ||
            (f.category || "").toLowerCase().includes(q) ||
            (f.aliases || []).some((a) => a.toLowerCase().includes(q))
        )
      : __verified;

    if (!items.length) {
      $vList.innerHTML =
        '<div class="fb-empty">검색 결과 없음</div>';
      return;
    }

    const html = items
      .slice(0, 50)
      .map((f) => {
        const aliasCount = (f.aliases || []).length;
        const badge = f.custom
          ? '<span class="id" style="background:#fff5f5;color:#D60019">관리자수정됨</span>'
          : "";
        return `
        <div class="faq-list-row" data-vid="${escapeHtml(f.id)}">
          <div class="head">
            <div>
              <span class="id">${escapeHtml(f.id)}</span>
              <span style="margin-left:6px">${escapeHtml(f.category || "")}</span>
              ${badge}
              <span class="cnt" style="margin-left:6px">별칭 ${aliasCount}개</span>
            </div>
            <div>
              <button class="btn" data-edit-verified="${escapeHtml(f.id)}">✏️ 수정</button>
            </div>
          </div>
          <div class="question">${escapeHtml(f.question)}</div>
          <div class="answer">${escapeHtml(f.answer.slice(0, 200))}${f.answer.length > 200 ? "..." : ""}</div>
        </div>`;
      })
      .join("");
    const more =
      items.length > 50
        ? `<div class="fb-empty">${items.length - 50}개 더 있음 — 검색으로 좁혀주세요</div>`
        : "";
    $vList.innerHTML = html + more;
  }

  if ($vList) {
    $vList.addEventListener("click", (e) => {
      const t = e.target;
      if (t.dataset.editVerified) {
        const id = t.dataset.editVerified;
        const f = __verified.find((x) => x.id === id);
        if (!f) return;
        $editId.value = id;
        $q.value = f.question || "";
        $a.value = f.answer || "";
        $al.value = (f.aliases || []).join(", ");
        $cat.value = f.category || "관리자 추가";
        $saveBtn.textContent = "💾 검증 FAQ 수정 저장";
        $editInfo.textContent = "검증 FAQ 편집 중: " + id;
        window.scrollTo({ top: 0, behavior: "smooth" });
        toast(id + " 폼에 채워짐 — 수정 후 저장");
      }
    });
  }

  if ($vSearch) {
    let debounce;
    $vSearch.addEventListener("input", () => {
      clearTimeout(debounce);
      debounce = setTimeout(() => renderVerified($vSearch.value), 200);
    });
  }

  // ===== 초기화 =====
  loadFaqs();
  loadFeedback();
  loadUnmatched();
  loadVerified();
})();
