/* ═══════════════════════════════════════════════════════════
   RedFetcher v2 — Interaction Layer
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
    setTimeout(function () {
      t.classList.add("removing");
      setTimeout(function () {
        if (t.parentNode) t.remove();
      }, 200);
    }, 2500);
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
        t.classList.add("removing");
        setTimeout(function () {
          if (t.parentNode) t.remove();
        }, 200);
      });
      t.appendChild(undoBtn);
    }
    c.appendChild(t);
    setTimeout(function () {
      t.classList.add("removing");
      setTimeout(function () {
        if (t.parentNode) t.remove();
      }, 200);
    }, 4000);
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

  function buildApiQuery(filters, page) {
    var params = new URLSearchParams();
    if (filters.subreddit) params.set("subreddit", filters.subreddit);
    if (filters.sort) params.set("sort", filters.sort);
    if (filters.start) params.set("start", filters.start);
    if (filters.end) params.set("end", filters.end);
    if (filters.q) params.set("q", filters.q);
    if (filters.limit) params.set("limit", filters.limit);
    params.set("page", page || filters.page || 1);
    return params.toString();
  }

  function buildUrl(filters) {
    var params = new URLSearchParams();
    if (filters.subreddit) params.set("subreddit", filters.subreddit);
    if (filters.sort) params.set("sort", filters.sort);
    if (filters.start) params.set("start", filters.start);
    if (filters.end) params.set("end", filters.end);
    if (filters.q) params.set("q", filters.q);
    if (filters.limit) params.set("limit", filters.limit);
    params.set("page", filters.page || 1);
    return pageBase + "?" + params.toString();
  }

  // ── Toast (expose globally for bookmark.js) ────
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

  function isBookmarksPage() {
    return window.location.pathname === "/bookmarks";
  }

  function updateBookmarksCount(delta) {
    var countEl = document.querySelector(".bookmarks-bar .count");
    if (!countEl) return;
    var m = countEl.textContent.match(/\d+/);
    if (!m) return;
    countEl.textContent = parseInt(m[0], 10) + delta + " saved";
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

  // Per-page membership policy: a row stays iff it still belongs to the list.
  var BOOKMARK_POLICY = {
    // /posts — membership is "matches filters", never affected by bookmark state
    feed: function (postId, nowBookmarked, btn) {
      if (btn) setBookmarkButtonState(btn, nowBookmarked);
    },
    // /bookmarks — membership IS the bookmark set
    bookmarks: function (postId, nowBookmarked, btn) {
      if (nowBookmarked) {
        reloadBookmarksFeed(); // re-inserts the row + count from server
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
            reloadBookmarksFeed(); // last item removed — let it render the empty state
          }
        }, 150);
        updateBookmarksCount(-1);
      } else {
        reloadBookmarksFeed(); // row already gone — let server reconcile count
      }
    },
  };

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
    var policy = isBookmarksPage()
      ? BOOKMARK_POLICY.bookmarks
      : BOOKMARK_POLICY.feed;
    policy(postId, nowBookmarked, btn);
  }

  // ── Bookmark Toggle ────────────────────────────
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
          toastWithUndo(
            "Bookmarked",
            "success",
            undoBookmark(postId, "remove"),
          );
        } else {
          toastWithUndo(
            "Bookmark removed",
            "info",
            undoBookmark(postId, "add"),
          );
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
      '<a href="/posts?subreddit=' +
      encodeURIComponent(post.subreddit || "") +
      '" class="r-badge">r/' +
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

  function currentBookmarkQuery() {
    return buildApiQuery(window.__filters || {}, window.__page || 1);
  }

  function replaceBookmarkPageUrl(page) {
    var params = new URLSearchParams(window.location.search);
    params.set("page", page);
    window.__filters = Object.assign({}, window.__filters || {}, { page: page });
    history.replaceState(
      { filters: window.__filters },
      "",
      window.location.pathname + "?" + params.toString(),
    );
  }

  function updateSortInfoBar(total, page, limit) {
    var si = document.getElementById("sort-info");
    if (!si) return;
    if (total <= 0) {
      si.style.display = "none";
      return;
    }
    var currentPage = page || 1;
    var pageLimit = limit || 50;
    var tp = Math.max(1, Math.ceil(total / pageLimit));
    si.style.display = "";
    si.innerHTML =
      "<strong>" +
      total +
      "</strong> bookmark" +
      (total !== 1 ? "s" : "") +
      " &middot; page " +
      currentPage +
      " of " +
      tp;
  }

  function reloadBookmarksFeed() {
    var requestedPage = window.__page || 1;
    fetch("/api/bookmarks?" + currentBookmarkQuery())
      .then(function (res) {
        return res.json().then(function (data) {
          return { ok: res.ok, data: data };
        });
      })
      .then(function (response) {
        if (!response.ok) {
          throw new Error(
            (response.data && response.data.error) || "Failed to load bookmarks",
          );
        }
        var data = response.data;
        var currentPage = data.page || requestedPage;
        var currentLimit = (window.__filters && window.__filters.limit) || 50;
        window.__page = currentPage;
        if (currentPage !== requestedPage) replaceBookmarkPageUrl(currentPage);

        var feed = document.getElementById("post-feed");
        if (feed) {
          if (data.posts.length === 0) {
            feed.innerHTML =
              '<div class="empty-state"><div class="icon"><i class="fa-regular fa-bookmark"></i></div><h2>No bookmarks yet</h2><p>Browse the <a href="/posts">posts feed</a> and save what interests you.</p></div>';
            var pag = document.getElementById("pagination");
            if (pag) pag.innerHTML = "";
            updateSortInfoBar(0, currentPage, currentLimit);
            var emptyCount = document.querySelector(".bookmarks-bar .count");
            if (emptyCount) emptyCount.textContent = data.total + " saved";
            return;
          }
          feed.innerHTML = data.posts
            .map(function (p) {
              return renderPost(p, true);
            })
            .join("");
        }
        var countEl = document.querySelector(".bookmarks-bar .count");
        if (countEl) countEl.textContent = data.total + " saved";
        updateSortInfoBar(data.total, currentPage, currentLimit);
      })
      .catch(function (error) {
        if (!showFeedError("Couldn't load bookmarks", reloadBookmarksFeed)) {
          toast(error.message || "Failed to reload bookmarks", "error");
        }
      });
  }

  // ── Posts Page: Live Filtering ─────────────────
  if (document.getElementById("filter-bar")) {
    var isBookmarks = window.location.pathname === "/bookmarks";
    var apiBase = isBookmarks ? "/api/bookmarks" : "/api/posts";
    var pageBase = isBookmarks ? "/bookmarks" : "/posts";
    var filters = Object.assign(
      {
        subreddit: "",
        sort: "oldest",
        start: "",
        end: "",
        q: "",
        limit: 50,
        page: 1,
      },
      window.__filters || {},
    );
    filters.page = window.__page || 1;
    var total = window.__total || 0;
    var Q_MIN_LEN = 2;

    function setSearchHint(visible) {
      var hint = document.getElementById("filter-q-hint");
      if (hint) hint.hidden = !visible;
    }

    function isSearchTooShort(value) {
      return value.length > 0 && value.length < Q_MIN_LEN;
    }

    function replaceFilterUrl() {
      history.replaceState(
        { filters: Object.assign({}, filters, { page: filters.page || 1 }) },
        "",
        buildUrl(filters),
      );
    }

    function getTotalPages(t, l) {
      return l > 0 ? Math.ceil(t / l) : 1;
    }

    function renderFeed(posts, bookmarked) {
      var feed = document.getElementById("post-feed");
      if (!feed) return;
      if (!posts || posts.length === 0) {
        feed.innerHTML =
          '<div class="empty-state"><div class="icon"><i class="fa-solid fa-inbox"></i></div><h2>No posts found</h2><p>Try different filters.</p></div>';
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
      var label = isBookmarks ? "bookmark" : "post";
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
      ann.textContent =
        total + " " + label + (total !== 1 ? "s" : "") + " loaded";
    }

    function updatePagination(page, tp, filters) {
      var pag = document.getElementById("pagination");
      if (!pag) return;
      if (tp <= 1) {
        pag.innerHTML = "";
        return;
      }

      var html = "";
      // Prev
      if (page > 1) {
        html +=
          '<a href="#" class="js-page" data-page="' +
          (page - 1) +
          '"><i class="fa-solid fa-chevron-left"></i> Prev</a>';
      } else {
        html +=
          '<span class="disabled"><i class="fa-solid fa-chevron-left"></i> Prev</span>';
      }
      // Page numbers
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
      // Next
      if (page < tp) {
        html +=
          '<a href="#" class="js-page" data-page="' +
          (page + 1) +
          '">Next <i class="fa-solid fa-chevron-right"></i></a>';
      } else {
        html +=
          '<span class="disabled">Next <i class="fa-solid fa-chevron-right"></i></span>';
      }
      // Jump input
      html += '<div class="jump">';
    html +=
      '<input type="number" min="1" max="' +
      tp +
      '" placeholder="' +
      tp +
      '" id="jump-page" aria-label="Jump to page" />';
      html += "</div>";

      pag.innerHTML = html;

      // Attach jump input handler
      var jumpInput = document.getElementById("jump-page");
      if (jumpInput) {
        jumpInput.addEventListener("keydown", function (e) {
          if (e.key === "Enter") {
            var targetPage = parseInt(jumpInput.value);
            if (targetPage > 0 && targetPage <= tp) {
              onFilterChange(targetPage, "push");
            }
            jumpInput.value = "";
          }
        });
      }
    }

    var spinner = document.getElementById("spinner-container");

    function showSpinner() {
      if (spinner) spinner.classList.add("visible");
    }

    function hideSpinner() {
      if (spinner) spinner.classList.remove("visible");
    }

    function focusResultsStatus() {
      var el = document.getElementById("sort-info") || document.getElementById("post-feed");
      if (!el) return;
      if (!el.hasAttribute("tabindex")) el.setAttribute("tabindex", "-1");
      el.focus();
    }

    function fetchAndUpdate(page, historyMode) {
      var f = Object.assign({}, filters, { page: page || 1 });
      showSpinner();
      var feed = document.getElementById("post-feed");
      if (feed) feed.classList.add("fading");
      var url = apiBase + "?" + buildApiQuery(f, f.page);

      if (historyMode === "push") {
        history.pushState({ filters: f }, "", buildUrl(f));
      } else if (historyMode === "replace") {
        history.replaceState({ filters: f }, "", buildUrl(f));
      }

      // Fetch
      fetch(url)
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
          var effectivePage = data.page || f.page;
          if (effectivePage !== f.page) {
            f.page = effectivePage;
            filters.page = effectivePage;
            history.replaceState({ filters: f }, "", buildUrl(f));
          }
          window.__filters = Object.assign({}, f);
          window.__page = effectivePage;
          total = data.total;
          var tp = getTotalPages(total, f.limit);
          // capture pagination focus before innerHTML destroys the node
          var active = document.activeElement;
          var restoreJump = false;
          var restorePage = null;
          if (active && active.closest && active.closest("#pagination")) {
            if (active.id === "jump-page") restoreJump = true;
            else if (active.dataset && active.dataset.page) restorePage = active.dataset.page;
            else restorePage = String(f.page);
          }
          if (data.bookmarked_ids) {
            var returned = {};
            data.posts.forEach(function (p) {
              returned[p.id] = true;
            });
            window.__bookmarked = (window.__bookmarked || [])
              .filter(function (id) {
                return !returned[id];
              })
              .concat(data.bookmarked_ids);
          }
          renderFeed(data.posts, window.__bookmarked || []);
          updateSortInfo(total, effectivePage, tp);
          updatePagination(effectivePage, tp, f);
          if (restoreJump) {
            var jump = document.getElementById("jump-page");
            if (jump) jump.focus();
            else focusResultsStatus();
          } else if (restorePage !== null) {
            var link = document.querySelector(
              '#pagination .js-page[data-page="' + restorePage + '"]',
            );
            if (link) link.focus();
            else focusResultsStatus();
          }
          var label = isBookmarks ? "bookmark" : "post";
          announceResults(total, label);
          hideSpinner();
          if (feed) feed.classList.remove("fading");
        })
        .catch(function (error) {
          if (error && error.status) {
            toast(error.message, "error");
          } else if (
            !showFeedError("Couldn't load posts", function () {
              fetchAndUpdate(f.page, historyMode);
            })
          ) {
            toast("Failed to load posts", "error");
          }
          hideSpinner();
          if (feed) feed.classList.remove("fading");
        });
    }

    function onFilterChange(page, historyMode) {
      if (isSearchTooShort(filters.q)) {
        setSearchHint(true);
        return;
      }
      filters.page = page || 1;
      fetchAndUpdate(filters.page, historyMode || "replace");
    }

    // Prevent form submission — let JS handle filtering via fetch
    var filterForm = document.getElementById("filter-form");
    if (filterForm) {
      filterForm.addEventListener("submit", function (e) {
        e.preventDefault();
        if (qEl) filters.q = qEl.value;
        if (isSearchTooShort(filters.q)) {
          setSearchHint(true);
          if (qEl) qEl.focus();
          return;
        }
        setSearchHint(false);
        onFilterChange(1, "replace");
      });
    }

    // Attach filter listeners
    var subredditEl = document.getElementById("filter-subreddit");
    var sortEl = document.getElementById("filter-sort");
    var startEl = document.getElementById("filter-start");
    var endEl = document.getElementById("filter-end");
    var qEl = document.getElementById("filter-q");

    if (subredditEl)
      subredditEl.addEventListener("change", function () {
        filters.subreddit = subredditEl.value;
        onFilterChange(1, "replace");
      });
    if (sortEl)
      sortEl.addEventListener("change", function () {
        filters.sort = sortEl.value;
        onFilterChange(1, "replace");
      });
    if (startEl)
      startEl.addEventListener("change", function () {
        filters.start = startEl.value;
        onFilterChange(1, "replace");
      });
    if (endEl)
      endEl.addEventListener("change", function () {
        filters.end = endEl.value;
        onFilterChange(1, "replace");
      });
    if (qEl) {
      qEl.addEventListener(
        "input",
        debounce(function () {
          filters.q = qEl.value;
          if (isSearchTooShort(filters.q)) {
            setSearchHint(true);
            return;
          }
          setSearchHint(false);
          onFilterChange(1, "none");
        }, 200),
      );
      qEl.addEventListener("blur", function () {
        if (!isSearchTooShort(filters.q)) replaceFilterUrl();
      });
    }

    // Pagination: intercept clicks
    document.addEventListener("click", function (e) {
      var link = e.target.closest(".js-page[data-page]");
      if (link) {
        e.preventDefault();
        var page = parseInt(link.dataset.page);
        if (page > 0) onFilterChange(page, "push");
      }
      var jump = document.getElementById("jump-page");
      if (
        jump &&
        (e.target.closest(".pagination a") || e.target.closest(".js-page"))
      ) {
        jump.value = "";
      }
    });

    // Jump to page
    var jumpInput = document.getElementById("jump-page");
    if (jumpInput) {
      jumpInput.addEventListener("keydown", function (e) {
        if (e.key === "Enter") {
          var page = parseInt(jumpInput.value);
          var tp = getTotalPages(total, filters.limit);
          if (page > 0 && page <= tp) onFilterChange(page, "push");
        }
      });
    }

    // Browser back/forward
    window.addEventListener("popstate", function (e) {
      if (e.state && e.state.filters) {
        var f = e.state.filters;
        Object.assign(filters, f);
        // Update UI
        if (subredditEl) subredditEl.value = f.subreddit || "";
        if (sortEl) sortEl.value = f.sort || "oldest";
        if (startEl) startEl.value = f.start || "";
        if (endEl) endEl.value = f.end || "";
        if (qEl) qEl.value = f.q || "";
        setSearchHint(isSearchTooShort(f.q || ""));
        fetchAndUpdate(f.page || 1, "none");
      }
    });

    // Save initial state
    history.replaceState({ filters: filters }, "", buildUrl(filters));
  }

  // ── Keyboard Shortcuts ──────────────────────
  document.addEventListener("keydown", function (e) {
    // Ctrl+K or / to focus search
    if (
      (e.ctrlKey && e.key === "k") ||
      (e.key === "/" &&
        !["INPUT", "TEXTAREA", "SELECT"].includes(
          document.activeElement.tagName,
        ))
    ) {
      e.preventDefault();
      // #filter-q exists only on pages that hide .header-search — prefer the visible input
      var searchInput =
        document.getElementById("filter-q") ||
        document.getElementById("header-search-input");
      if (searchInput) searchInput.focus();
    }
  });
})();
