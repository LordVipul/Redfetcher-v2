/* ═══════════════════════════════════════════════════════════
   RedFetcher v2 — Interaction Layer
   Single-page feed: sidebar groups + bookmarks view + filters.
   URL is the source of truth; every state change fetches the
   matching API and rewrites the query string.
   ═══════════════════════════════════════════════════════════ */
(function () {
  "use strict";

  // ── Utilities ──────────────────────────────────
  function debounce(fn, delay) {
    var timer;
    return function () {
      var args = arguments;
      var ctx = this;
      clearTimeout(timer);
      timer = setTimeout(function () {
        fn.apply(ctx, args);
      }, delay);
    };
  }

  function toast(msg, type) {
    type = type || "info";
    var c = document.getElementById("toast-container");
    if (!c) return;
    var t = document.createElement("div");
    t.className = "toast " + type;
    t.textContent = msg;
    c.appendChild(t);
    removeToast(t, 2500);
  }

  function toastWithUndo(msg, type, undoAction) {
    type = type || "info";
    var c = document.getElementById("toast-container");
    if (!c) return;
    var t = document.createElement("div");
    t.className = "toast " + type;
    var msgSpan = document.createElement("span");
    msgSpan.textContent = msg;
    t.appendChild(msgSpan);
    if (undoAction) {
      var undoBtn = document.createElement("button");
      undoBtn.className = "toast-undo";
      undoBtn.textContent = "Undo";
      undoBtn.addEventListener("click", function () {
        undoAction();
        dismissToast(t);
      });
      t.appendChild(undoBtn);
    }
    c.appendChild(t);
    removeToast(t, 4000);
  }

  function dismissToast(t) {
    t.classList.add("removing");
    setTimeout(function () {
      if (t.parentNode) t.remove();
    }, 200);
  }

  function removeToast(t, delay) {
    setTimeout(function () {
      dismissToast(t);
    }, delay);
  }

  function formatDate(timestamp) {
    if (!timestamp) return "";
    var d = new Date(timestamp * 1000);
    var y = d.getFullYear();
    var m = String(d.getMonth() + 1).padStart(2, "0");
    var day = String(d.getDate()).padStart(2, "0");
    var h = String(d.getHours()).padStart(2, "0");
    var mi = String(d.getMinutes()).padStart(2, "0");
    return y + "-" + m + "-" + day + " " + h + ":" + mi;
  }

  function escapeHtml(str) {
    if (!str) return "";
    return str
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  // Minimum score/comments: null and "" mean "no filter"; 0 is a real filter.
  function hasMinFilter(v) {
    return v !== null && v !== undefined && v !== "";
  }

  function setMinParams(params, filters) {
    if (hasMinFilter(filters.min_score))
      params.set("min_score", filters.min_score);
    if (hasMinFilter(filters.min_comments))
      params.set("min_comments", filters.min_comments);
  }

  // ── Toast / dates (exposed for inline handlers if needed) ──
  window.toast = toast;
  window.formatDate = formatDate;
  window.escapeHtml = escapeHtml;

  function setBookmarkButtonState(btn, active) {
    btn.classList.toggle("is-active", active);
    btn.setAttribute("aria-pressed", active ? "true" : "false");
    var label = active ? "Remove bookmark" : "Bookmark this post";
    btn.setAttribute("title", label);
    btn.setAttribute("aria-label", label);
  }

  // ── Bookmarks ──────────────────────────────────
  function isBookmarksView() {
    return state.view === "bookmarks";
  }

  function updateBookmarkCount(delta) {
    var el = document.getElementById("bookmark-count");
    if (!el) return;
    var m = el.textContent.match(/\d+/);
    if (m) el.textContent = parseInt(m[0], 10) + delta;
  }

  function syncBookmarkUI(postId, nowBookmarked, btn) {
    if (window.__bookmarked) {
      if (nowBookmarked) {
        if (window.__bookmarked.indexOf(postId) === -1) {
          window.__bookmarked.push(postId);
        }
      } else {
        window.__bookmarked = window.__bookmarked.filter(function (id) {
          return id !== postId;
        });
      }
    }
    if (!btn) {
      btn = document.querySelector(
        '.bookmark-btn[data-post-id="' + postId + '"]',
      );
    }
    updateBookmarkCount(nowBookmarked ? 1 : -1);
    if (isBookmarksView()) {
      if (nowBookmarked) {
        fetchFeed(state.page, "replace"); // re-inserts the row + count from server
        return;
      }
      var row = btn && btn.closest(".post-item");
      if (row) {
        row.style.transition = "opacity 0.15s ease";
        row.style.opacity = "0";
        setTimeout(function () {
          if (row.parentNode) row.remove();
          var feed = document.getElementById("post-feed");
          if (feed && !feed.querySelector(".post-item")) {
            fetchFeed(1, "replace"); // last item removed — let it render the empty state
          }
        }, 150);
      } else {
        fetchFeed(state.page, "replace"); // row already gone — let server reconcile count
      }
    } else if (btn) {
      setBookmarkButtonState(btn, nowBookmarked);
    }
  }

  document.addEventListener("click", function (e) {
    var btn = e.target.closest(".bookmark-btn[data-post-id]");
    if (!btn || btn.classList.contains("busy")) return;
    btn.classList.add("busy");
    var postId = btn.dataset.postId;
    var nowBookmarked = !btn.classList.contains("is-active");
    fetch("/bookmarks/" + postId, {
      method: nowBookmarked ? "POST" : "DELETE",
    })
      .then(function (res) {
        return res.ok;
      })
      .then(function (ok) {
        if (!ok) throw new Error();
        syncBookmarkUI(postId, nowBookmarked, btn);
        if (nowBookmarked) {
          toastWithUndo("Bookmarked", "success", undoBookmark(postId, "remove"));
        } else {
          toastWithUndo("Bookmark removed", "info", undoBookmark(postId, "add"));
        }
      })
      .catch(function () {
        toast("Failed to update bookmark", "error");
      })
      .finally(function () {
        btn.classList.remove("busy");
      });
  });

  function undoBookmark(postId, action) {
    return function () {
      var nowBookmarked = action === "add";
      fetch("/bookmarks/" + postId, {
        method: nowBookmarked ? "POST" : "DELETE",
      })
        .then(function (res) {
          return res.ok;
        })
        .then(function (ok) {
          if (!ok) throw new Error();
          syncBookmarkUI(postId, nowBookmarked, null);
        })
        .catch(function () {
          toast("Failed to undo", "error");
        });
    };
  }

  function renderPost(post, isBookmarked) {
    var url =
      post.url && post.url.startsWith("http")
        ? post.url
        : "https://www.reddit.com" + (post.url || "");
    var title = escapeHtml(post.title);
    var date = formatDate(post.created_utc);
    var badge =
      '<a href="/?subreddit=' +
      encodeURIComponent(post.subreddit || "") +
      '" class="r-badge js-subreddit" data-subreddit="' +
      escapeHtml(post.subreddit || "") +
      '">r/' +
      escapeHtml(post.subreddit || "") +
      "</a>";
    var score =
      '<span class="r-score"><i class="fa-solid fa-arrow-up"></i> ' +
      (post.score || 0) +
      "</span>";
    var comments =
      '<span class="r-comment"><i class="fa-solid fa-comment"></i> ' +
      (post.num_comments || 0) +
      "</span>";
    var dt = '<span class="post-date">' + date + "</span>";
    var cls = isBookmarked ? " is-active" : "";
    var pressed = isBookmarked ? "true" : "false";
    var btn =
      '<button class="bookmark-btn' +
      cls +
      '" data-post-id="' +
      escapeHtml(post.id || "") +
      '" title="' +
      (isBookmarked ? "Remove bookmark" : "Bookmark this post") +
      '" aria-pressed="' +
      pressed +
      '" aria-label="' +
      (isBookmarked ? "Remove bookmark" : "Bookmark this post") +
      '"><i class="fa-solid fa-bookmark"></i></button>';
    return (
      '<div class="post-item" role="listitem" data-post-id="' +
      escapeHtml(post.id || "") +
      '">' +
      badge +
      '<div class="post-title"><a href="' +
      escapeHtml(url) +
      '" target="_blank" rel="noopener" title="' +
      escapeHtml(title) +
      '">' +
      title +
      "</a></div>" +
      dt +
      score +
      comments +
      '<span class="r-action">' +
      btn +
      "</span>" +
      "</div>"
    );
  }

  // ── State ──────────────────────────────────────
  var state = {
    view: "feed",
    group: "all",
    filters: {
      subreddit: "",
      sort: "oldest",
      start: "",
      end: "",
      q: "",
      limit: 50,
      min_score: null,
      min_comments: null,
    },
    page: 1,
  };

  function stateFromUrl() {
    var params = new URLSearchParams(window.location.search);
    var view = params.get("view") === "bookmarks" ? "bookmarks" : "feed";
    var group = params.get("group") || "all";
    state.view = view;
    state.group = group;
    state.filters.subreddit = params.get("subreddit") || "";
    state.filters.sort = params.get("sort") || "oldest";
    state.filters.start = params.get("start") || "";
    state.filters.end = params.get("end") || "";
    state.filters.q = params.get("q") || "";
    state.filters.limit = parseInt(params.get("limit"), 10) || 50;
    state.filters.min_score = params.has("min_score") && params.get("min_score") !== ""
      ? parseInt(params.get("min_score"), 10)
      : null;
    state.filters.min_comments = params.has("min_comments") && params.get("min_comments") !== ""
      ? parseInt(params.get("min_comments"), 10)
      : null;
    state.page = parseInt(params.get("page"), 10) || 1;
  }

  function buildQuery(page) {
    var params = new URLSearchParams();
    if (!isBookmarksView() && state.group && state.group !== "all")
      params.set("group", state.group);
    if (isBookmarksView()) params.set("view", "bookmarks");
    if (state.filters.subreddit) params.set("subreddit", state.filters.subreddit);
    if (state.filters.sort && state.filters.sort !== "oldest")
      params.set("sort", state.filters.sort);
    if (state.filters.start) params.set("start", state.filters.start);
    if (state.filters.end) params.set("end", state.filters.end);
    if (state.filters.q) params.set("q", state.filters.q);
    if (state.filters.limit && state.filters.limit !== 50)
      params.set("limit", state.filters.limit);
    setMinParams(params, state.filters);
    params.set("page", page || state.page || 1);
    return params.toString();
  }

  function apiBase() {
    return isBookmarksView() ? "/api/bookmarks" : "/api/posts";
  }

  function syncUrl(historyMode) {
    var url = window.location.pathname + "?" + buildQuery();
    if (historyMode === "push") history.pushState({ state: snapshot() }, "", url);
    else history.replaceState({ state: snapshot() }, "", url);
  }

  function snapshot() {
    return JSON.parse(JSON.stringify({ view: state.view, group: state.group, filters: state.filters, page: state.page }));
  }

  // ── Feed rendering ─────────────────────────────
  var spinner = document.getElementById("spinner-container");

  function showSpinner() {
    if (spinner) spinner.classList.add("visible");
  }

  function hideSpinner() {
    if (spinner) spinner.classList.remove("visible");
  }

  function announceResults(total, label) {
    var ann = document.getElementById("sr-announcer");
    if (!ann) {
      ann = document.createElement("div");
      ann.id = "sr-announcer";
      ann.setAttribute("aria-live", "polite");
      ann.setAttribute("aria-atomic", "true");
      ann.className = "sr-only";
      document.body.appendChild(ann);
    }
    ann.textContent = total + " " + label + (total !== 1 ? "s" : "") + " loaded";
  }

  function showFeedError(message, retry) {
    var feed = document.getElementById("post-feed");
    if (!feed) return false;
    feed.classList.remove("fading");
    feed.innerHTML =
      '<div class="empty-state" role="alert">' +
      '<div class="icon"><i class="fa-solid fa-triangle-exclamation"></i></div>' +
      "<h2>" +
      message +
      "</h2>" +
      "<p>Check your connection and try again.</p>" +
      '<button type="button" class="retry-btn">Retry</button>' +
      "</div>";
    var btn = feed.querySelector(".retry-btn");
    if (btn) {
      btn.addEventListener("click", retry);
      btn.focus();
    }
    return true;
  }

  function renderFeed(posts, bookmarked) {
    var feed = document.getElementById("post-feed");
    if (!feed) return;
    if (!posts || posts.length === 0) {
      var empty = isBookmarksView()
        ? '<div class="empty-state"><div class="icon"><i class="fa-regular fa-bookmark"></i></div><h2>No bookmarks yet</h2><p>Bookmark posts from any group to see them here.</p></div>'
        : '<div class="empty-state"><div class="icon"><i class="fa-solid fa-inbox"></i></div><h2>No posts found</h2><p>Try different filters.</p></div>';
      feed.innerHTML = empty;
      return;
    }
    feed.innerHTML = posts
      .map(function (p) {
        return renderPost(p, bookmarked.indexOf(p.id) !== -1);
      })
      .join("");
  }

  function updateSortInfo(t, page, tp) {
    var info = document.getElementById("sort-info");
    if (!info) return;
    var label = isBookmarksView() ? "bookmark" : "post";
    info.innerHTML =
      "<strong>" +
      t +
      "</strong> " +
      label +
      (t !== 1 ? "s" : "") +
      " &middot; page " +
      page +
      " of " +
      tp;
  }

  function getTotalPages(t, l) {
    return l > 0 ? Math.ceil(t / l) : 1;
  }

  function updatePagination(page, tp) {
    var pag = document.getElementById("pagination");
    if (!pag) return;
    if (tp <= 1) {
      pag.innerHTML = "";
      return;
    }

    var html = "";
    if (page > 1) {
      html +=
        '<a href="#" class="js-page" data-page="' +
        (page - 1) +
        '"><i class="fa-solid fa-chevron-left"></i> Prev</a>';
    } else {
      html +=
        '<span class="disabled"><i class="fa-solid fa-chevron-left"></i> Prev</span>';
    }
    for (var p = 1; p <= tp; p++) {
      if (p === page) {
        html += '<span class="current" aria-current="page">' + p + "</span>";
      } else if (p === 1 || p === tp || Math.abs(p - page) <= 2) {
        html +=
          '<a href="#" class="js-page" data-page="' + p + '">' + p + "</a>";
      } else {
        if (p === 2 || p === tp - 1) {
          html += '<span class="disabled">&hellip;</span>';
        }
      }
    }
    if (page < tp) {
      html +=
        '<a href="#" class="js-page" data-page="' +
        (page + 1) +
        '">Next <i class="fa-solid fa-chevron-right"></i></a>';
    } else {
      html +=
        '<span class="disabled">Next <i class="fa-solid fa-chevron-right"></i></span>';
    }
    html += '<div class="jump">';
    html +=
      '<input type="number" min="1" max="' +
      tp +
      '" placeholder="' +
      tp +
      '" id="jump-page" aria-label="Jump to page" />';
    html += "</div>";

    pag.innerHTML = html;

    var jumpInput = document.getElementById("jump-page");
    if (jumpInput) {
      jumpInput.addEventListener("keydown", function (e) {
        if (e.key === "Enter") {
          var targetPage = parseInt(jumpInput.value, 10);
          if (targetPage > 0 && targetPage <= tp) {
            fetchFeed(targetPage, "push");
          }
          jumpInput.value = "";
        }
      });
    }
  }

  function fetchFeed(page, historyMode) {
    var p = page || 1;
    state.page = p; // before syncUrl so the history URL carries the requested page
    showSpinner();
    var feed = document.getElementById("post-feed");
    if (feed) feed.classList.add("fading");
    syncUrl(historyMode);

    fetch(apiBase() + "?" + buildQuery(p))
      .then(function (res) {
        return res.json().then(function (data) {
          return { ok: res.ok, status: res.status, data: data };
        });
      })
      .then(function (response) {
        if (!response.ok) {
          var error = new Error(
            (response.data && response.data.error) || "Request failed",
          );
          error.status = response.status;
          throw error;
        }
        var data = response.data;
        var effectivePage = data.page || p;
        state.page = effectivePage;
        if (effectivePage !== p) syncUrl("replace");

        if (Array.isArray(data.subreddits)) rebuildSubredditSelect(data.subreddits);
        if (data.bookmarked_ids) {
          var returned = {};
          data.posts.forEach(function (post) {
            returned[post.id] = true;
          });
          window.__bookmarked = (window.__bookmarked || [])
            .filter(function (id) {
              return !returned[id];
            })
            .concat(data.bookmarked_ids);
        }
        renderFeed(data.posts, window.__bookmarked || []);
        window.__total = data.total;
        updateSortInfo(data.total, effectivePage, data.total_pages);
        updatePagination(effectivePage, data.total_pages);
        restorePaginationFocus(effectivePage);
        announceResults(data.total, isBookmarksView() ? "bookmark" : "post");
        hideSpinner();
        if (feed) feed.classList.remove("fading");
      })
      .catch(function (error) {
        if (error && error.status) {
          toast(error.message, "error");
        } else if (!showFeedError("Couldn't load posts", function () {
          fetchFeed(state.page, historyMode);
        })) {
          toast("Failed to load posts", "error");
        }
        hideSpinner();
        if (feed) feed.classList.remove("fading");
      });
  }

  // Capture pagination focus before innerHTML destroys the node.
  var _restoreJump = false;
  var _restorePage = null;
  function capturePaginationFocus() {
    var active = document.activeElement;
    _restoreJump = false;
    _restorePage = null;
    if (active && active.closest && active.closest("#pagination")) {
      if (active.id === "jump-page") _restoreJump = true;
      else if (active.dataset && active.dataset.page) _restorePage = active.dataset.page;
      else _restorePage = String(state.page);
    }
  }

  function restorePaginationFocus(effectivePage) {
    if (_restoreJump) {
      var jump = document.getElementById("jump-page");
      if (jump) jump.focus();
      else focusResultsStatus();
    } else if (_restorePage !== null) {
      var link = document.querySelector(
        '#pagination .js-page[data-page="' + _restorePage + '"]',
      );
      if (link) link.focus();
      else focusResultsStatus();
    }
    _restoreJump = false;
    _restorePage = null;
  }

  function focusResultsStatus() {
    var el = document.getElementById("sort-info") || document.getElementById("post-feed");
    if (!el) return;
    if (!el.hasAttribute("tabindex")) el.setAttribute("tabindex", "-1");
    el.focus();
  }

  // ── Sidebar ─────────────────────────────────────
  function setActiveGroupButton(key) {
    document.querySelectorAll(".sidebar .group-btn").forEach(function (btn) {
      var isActive = btn.dataset.group === key && state.view === "feed";
      btn.classList.toggle("active", isActive);
      if (isActive) btn.setAttribute("aria-current", "page");
      else btn.removeAttribute("aria-current");
    });
    var bmBtn = document.querySelector(".bookmarks-btn");
    if (bmBtn) bmBtn.classList.toggle("active", state.view === "bookmarks");
  }

  function setGroup(key) {
    if (state.group === key && state.view === "feed") return;
    state.group = key;
    state.view = "feed";
    state.filters.subreddit = ""; // a new group invalidates a per-sub filter
    state.page = 1;
    syncViewUi();
    setActiveGroupButton(key);
    fetchFeed(1, "push");
  }

  function setView(view) {
    if (state.view === view) return;
    state.view = view;
    state.page = 1;
    syncViewUi();
    setActiveGroupButton(state.group);
    fetchFeed(1, "push");
  }

  function syncViewUi() {
    document.body.dataset.view = state.view;
    var sortField = document.getElementById("sort-field");
    if (sortField) sortField.hidden = state.view === "bookmarks";
    setActiveGroupButton(state.group);
  }

  document.addEventListener("click", function (e) {
    var groupBtn = e.target.closest(".sidebar .group-btn[data-group]");
    if (groupBtn) {
      e.preventDefault();
      setGroup(groupBtn.dataset.group);
      return;
    }
    var bmBtn = e.target.closest(".bookmarks-btn");
    if (bmBtn) {
      e.preventDefault();
      setView("bookmarks");
      return;
    }
    var badge = e.target.closest(".r-badge.js-subreddit");
    if (badge) {
      e.preventDefault();
      state.filters.subreddit = badge.dataset.subreddit || "";
      state.page = 1;
      var subEl = document.getElementById("filter-subreddit");
      if (subEl) subEl.value = state.filters.subreddit;
      capturePaginationFocus();
      fetchFeed(1, "push");
    }
  });

  function rebuildSubredditSelect(subs) {
    var el = document.getElementById("filter-subreddit");
    if (!el) return;
    var current = state.filters.subreddit;
    var keep = current && subs.indexOf(current) !== -1 ? current : "";
    state.filters.subreddit = keep;
    var allLabel = el.dataset.allLabel || "All";
    var html = '<option value="">' + escapeHtml(allLabel) + "</option>";
    html += subs
      .map(function (s) {
        return (
          '<option value="' +
          escapeHtml(s) +
          '"' +
          (s === keep ? " selected" : "") +
          ">" +
          escapeHtml(s) +
          "</option>"
        );
      })
      .join("");
    el.innerHTML = html;
  }

  // ── Filter bar ──────────────────────────────────
  var filterBar = document.getElementById("filter-bar");
  if (filterBar) {
    var Q_MIN_LEN = 2;
    var subredditEl = document.getElementById("filter-subreddit");
    var sortEl = document.getElementById("filter-sort");
    var limitEl = document.getElementById("filter-limit");
    var startEl = document.getElementById("filter-start");
    var endEl = document.getElementById("filter-end");
    var qEl = document.getElementById("filter-q");
    var minScoreEl = document.getElementById("filter-min-score");
    var minCommentsEl = document.getElementById("filter-min-comments");

    function setSearchHint(visible) {
      var hint = document.getElementById("filter-q-hint");
      if (hint) hint.hidden = !visible;
    }

    function isSearchTooShort(value) {
      return value.length > 0 && value.length < Q_MIN_LEN;
    }

    function replaceFilterUrl() {
      syncUrl("replace");
    }

    // Keep only the leading run of digits, so pasted or typed text, signs,
    // decimals and exponent forms can never reach the query string.
    function sanitizeMinInput(el) {
      if (!el) return;
      var match = el.value.trim().match(/^[0-9]*/);
      var cleaned = match ? match[0] : "";
      if (cleaned !== el.value) el.value = cleaned;
    }

    function readMinFilter(el) {
      if (!el) return null;
      var raw = el.value.trim();
      if (raw === "") return null;
      var n = parseInt(raw, 10);
      return isNaN(n) || n < 0 ? null : n;
    }

    function syncMinFilters() {
      state.filters.min_score = readMinFilter(minScoreEl);
      state.filters.min_comments = readMinFilter(minCommentsEl);
    }

    if (minScoreEl || minCommentsEl) {
      [minScoreEl, minCommentsEl].forEach(function (el) {
        if (!el) return;
        el.addEventListener("paste", function () {
          sanitizeMinInput(el);
        });
        el.addEventListener(
          "input",
          debounce(function () {
            sanitizeMinInput(el);
            syncMinFilters();
            capturePaginationFocus();
            fetchFeed(1, "replace");
          }, 250),
        );
        el.addEventListener("change", function () {
          sanitizeMinInput(el);
          syncMinFilters();
          capturePaginationFocus();
          fetchFeed(1, "replace");
        });
        el.addEventListener("keydown", function (e) {
          var k = e.key;
          if (
            k.length === 1 &&
            !/[0-9]/.test(k) &&
            !e.ctrlKey &&
            !e.metaKey &&
            k !== "Backspace" &&
            k !== "Delete" &&
            k !== "Tab" &&
            k !== "ArrowLeft" &&
            k !== "ArrowRight"
          ) {
            e.preventDefault();
          }
        });
      });
    }

    if (subredditEl)
      subredditEl.addEventListener("change", function () {
        state.filters.subreddit = subredditEl.value;
        state.page = 1;
        capturePaginationFocus();
        fetchFeed(1, "replace");
      });
    if (sortEl)
      sortEl.addEventListener("change", function () {
        state.filters.sort = sortEl.value;
        state.page = 1;
        capturePaginationFocus();
        fetchFeed(1, "replace");
      });
    if (limitEl)
      limitEl.addEventListener("change", function () {
        state.filters.limit = parseInt(limitEl.value, 10) || 50;
        state.page = 1;
        capturePaginationFocus();
        fetchFeed(1, "replace");
      });
    if (startEl)
      startEl.addEventListener("change", function () {
        state.filters.start = startEl.value;
        state.page = 1;
        capturePaginationFocus();
        fetchFeed(1, "replace");
      });
    if (endEl)
      endEl.addEventListener("change", function () {
        state.filters.end = endEl.value;
        state.page = 1;
        capturePaginationFocus();
        fetchFeed(1, "replace");
      });
    if (qEl) {
      qEl.addEventListener(
        "input",
        debounce(function () {
          state.filters.q = qEl.value;
          if (isSearchTooShort(state.filters.q)) {
            setSearchHint(true);
            return;
          }
          setSearchHint(false);
          capturePaginationFocus();
          fetchFeed(1, "replace");
        }, 200),
      );
      qEl.addEventListener("blur", function () {
        if (!isSearchTooShort(state.filters.q)) replaceFilterUrl();
      });
    }

    var filterForm = document.getElementById("filter-form");
    if (filterForm) {
      filterForm.addEventListener("submit", function (e) {
        e.preventDefault();
        if (qEl) state.filters.q = qEl.value;
        syncMinFilters();
        if (isSearchTooShort(state.filters.q)) {
          setSearchHint(true);
          if (qEl) qEl.focus();
          return;
        }
        setSearchHint(false);
        capturePaginationFocus();
        fetchFeed(1, "replace");
      });
    }

    // Pagination: intercept clicks
    document.addEventListener("click", function (e) {
      var link = e.target.closest(".js-page[data-page]");
      if (link) {
        e.preventDefault();
        var page = parseInt(link.dataset.page, 10);
        if (page > 0) {
          capturePaginationFocus();
          fetchFeed(page, "push");
        }
      }
      var jump = document.getElementById("jump-page");
      if (jump && (e.target.closest(".pagination a") || e.target.closest(".js-page"))) {
        jump.value = "";
      }
    });

    // Jump to page
    document.addEventListener("keydown", function (e) {
      var jumpInput = document.getElementById("jump-page");
      if (jumpInput && e.key === "Enter" && document.activeElement === jumpInput) {
        var page = parseInt(jumpInput.value, 10);
        var tp = getTotalPages(window.__total || 0, state.filters.limit);
        if (page > 0 && page <= tp) {
          capturePaginationFocus();
          fetchFeed(page, "push");
        }
      }
    });

    // Sync form controls from a state snapshot (used by popstate).
    function syncControlsFromState(f, view) {
      if (subredditEl) subredditEl.value = f.subreddit || "";
      if (sortEl) sortEl.value = f.sort || "oldest";
      if (limitEl) limitEl.value = String(f.limit || 50);
      if (startEl) startEl.value = f.start || "";
      if (endEl) endEl.value = f.end || "";
      if (qEl) qEl.value = f.q || "";
      if (minScoreEl) minScoreEl.value = hasMinFilter(f.min_score) ? f.min_score : "";
      if (minCommentsEl)
        minCommentsEl.value = hasMinFilter(f.min_comments) ? f.min_comments : "";
      setSearchHint(isSearchTooShort(f.q || ""));
      if (view === "bookmarks") syncViewUi();
    }

    // Browser back/forward
    window.addEventListener("popstate", function (e) {
      if (e.state && e.state.state) {
        state.view = e.state.state.view || "feed";
        state.group = e.state.state.group || "all";
        state.filters = Object.assign(state.filters, e.state.state.filters || {});
        state.page = e.state.state.page || 1;
        syncControlsFromState(state.filters, state.view);
        setActiveGroupButton(state.group);
        fetchFeed(state.page, "none");
      } else {
        stateFromUrl();
        syncControlsFromState(state.filters, state.view);
        setActiveGroupButton(state.group);
        fetchFeed(state.page, "none");
      }
    });

    // No initial syncUrl here: this block registers listeners before boot
    // reads the URL, and rewriting state first would destroy ?group / ?view.
  }

  // ── Settings menu & preferences ─────────────────
  (function initSettings() {
    var btn = document.getElementById("settingsBtn");
    var menu = document.getElementById("settingsMenu");
    if (!btn || !menu) return;

    function close() {
      menu.hidden = true;
      btn.setAttribute("aria-expanded", "false");
    }
    function open() {
      menu.hidden = false;
      btn.setAttribute("aria-expanded", "true");
    }
    btn.addEventListener("click", function (e) {
      e.stopPropagation();
      menu.hidden ? open() : close();
    });
    document.addEventListener("click", function (e) {
      if (!menu.hidden && !menu.contains(e.target) && e.target !== btn) close();
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && !menu.hidden) close();
    });

    var toggleSidebar = document.getElementById("toggleSidebar");
    function applySidebarPref(hidden) {
      document.body.classList.toggle("sidebar-hidden", hidden);
      if (toggleSidebar) {
        toggleSidebar.setAttribute("aria-pressed", hidden ? "true" : "false");
        toggleSidebar.textContent = hidden ? "Show sidebar" : "Hide sidebar";
      }
      try {
        localStorage.setItem("rf-hide-sidebar", hidden ? "1" : "0");
      } catch (err) {}
    }
    if (toggleSidebar) {
      var saved = null;
      try {
        saved = localStorage.getItem("rf-hide-sidebar");
      } catch (err) {}
      applySidebarPref(saved === "1");
      toggleSidebar.addEventListener("click", function () {
        applySidebarPref(!document.body.classList.contains("sidebar-hidden"));
      });
    }

    var textSize = document.getElementById("textSize");
    function applyTextSize(size) {
      document.body.classList.remove("text-s", "text-m", "text-l");
      document.body.classList.add("text-" + size);
      if (textSize) textSize.value = size;
      try {
        localStorage.setItem("rf-text-size", size);
      } catch (err) {}
    }
    if (textSize) {
      var savedSize = "m";
      try {
        savedSize = localStorage.getItem("rf-text-size") || "m";
      } catch (err) {}
      applyTextSize(savedSize);
      textSize.addEventListener("change", function () {
        applyTextSize(textSize.value);
      });
    }

    var reduceMotion = document.getElementById("reduceMotion");
    function applyMotion(reduced) {
      document.body.classList.toggle("reduce-motion", reduced);
      if (reduceMotion) reduceMotion.checked = reduced;
      try {
        localStorage.setItem("rf-reduce-motion", reduced ? "1" : "0");
      } catch (err) {}
    }
    if (reduceMotion) {
      var savedMotion = null;
      try {
        savedMotion = localStorage.getItem("rf-reduce-motion");
      } catch (err) {}
      applyMotion(savedMotion === "1");
      reduceMotion.addEventListener("change", function () {
        applyMotion(reduceMotion.checked);
      });
    }
  })();

  // ── Keyboard shortcuts ──────────────────────────
  document.addEventListener("keydown", function (e) {
    if (
      (e.ctrlKey && e.key === "k") ||
      (e.key === "/" &&
        !["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName))
    ) {
      e.preventDefault();
      var searchInput =
        document.getElementById("filter-q") ||
        document.getElementById("header-search-input");
      if (searchInput) searchInput.focus();
    }
  });

  // ── Boot ────────────────────────────────────────
  stateFromUrl();
  syncViewUi();
  setActiveGroupButton(state.group);
  // Server already rendered the first page; only rewrite the URL to the
  // canonical form so shareable links match the API contract.
  syncUrl("replace");
})();
