/* ═══════════════════════════════════════════════════════════
   RedFetcher v2 — Interaction Layer
   ═══════════════════════════════════════════════════════════ */
(function () {
    'use strict';

    // ── Utilities ──────────────────────────────────
    function debounce(fn, delay) {
        var timer;
        return function () {
            var args = arguments;
            var ctx = this;
            clearTimeout(timer);
            timer = setTimeout(function () { fn.apply(ctx, args); }, delay);
        };
    }

    function toast(msg, type) {
        type = type || 'info';
        var c = document.getElementById('toast-container');
        if (!c) return;
        var t = document.createElement('div');
        t.className = 'toast ' + type;
        t.textContent = msg;
        c.appendChild(t);
        setTimeout(function () {
            t.classList.add('removing');
            setTimeout(function () { if (t.parentNode) t.remove(); }, 200);
        }, 2500);
    }

    function formatDate(timestamp) {
        if (!timestamp) return '';
        var d = new Date(timestamp * 1000);
        var y = d.getFullYear();
        var m = String(d.getMonth() + 1).padStart(2, '0');
        var day = String(d.getDate()).padStart(2, '0');
        var h = String(d.getHours()).padStart(2, '0');
        var mi = String(d.getMinutes()).padStart(2, '0');
        return y + '-' + m + '-' + day + ' ' + h + ':' + mi;
    }

    function escapeHtml(str) {
        if (!str) return '';
        return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    function buildUrl(filters) {
        var params = new URLSearchParams();
        if (filters.subreddit) params.set('subreddit', filters.subreddit);
        if (filters.sort) params.set('sort', filters.sort);
        if (filters.start) params.set('start', filters.start);
        if (filters.end) params.set('end', filters.end);
        if (filters.q) params.set('q', filters.q);
        if (filters.limit) params.set('limit', filters.limit);
        params.set('page', filters.page || 1);
        return '/posts?' + params.toString();
    }

    // ── Toast (expose globally for bookmark.js) ────
    window.toast = toast;
    window.formatDate = formatDate;
    window.escapeHtml = escapeHtml;

    // ── Bookmark Toggle ────────────────────────────
    document.addEventListener('click', function (e) {
        var btn = e.target.closest('.bookmark-btn[data-post-id]');
        if (!btn || btn.classList.contains('busy')) return;
        btn.classList.add('busy');
        var postId = btn.dataset.postId;
        var isActive = btn.classList.contains('is-active');
        fetch('/bookmarks/' + postId, { method: isActive ? 'DELETE' : 'POST' })
            .then(function (res) { return res.ok; })
            .then(function (ok) {
                if (!ok) throw new Error();
                if (isActive) {
                    btn.classList.remove('is-active');
                    toast('Bookmark removed');
                    if (window.__bookmarked) {
                        window.__bookmarked = window.__bookmarked.filter(function (id) { return id !== postId; });
                    }
                    if (window.location.pathname === '/bookmarks') {
                        var item = btn.closest('.post-item');
                        if (item) {
                            item.style.transition = 'opacity 0.15s ease';
                            item.style.opacity = '0';
                            setTimeout(function () { item.remove(); }, 150);
                        }
                        var countEl = document.querySelector('.bookmarks-bar .count');
                        if (countEl) {
                            var m = countEl.textContent.match(/\d+/);
                            if (m) countEl.textContent = (parseInt(m[1]) - 1) + ' saved';
                        }
                    }
                } else {
                    btn.classList.add('is-active');
                    toast('Bookmarked', 'success');
                    if (window.__bookmarked) {
                        window.__bookmarked.push(postId);
                    }
                }
            })
            .catch(function () { toast('Failed to update bookmark', 'error'); })
            .finally(function () { btn.classList.remove('busy'); });
    });

    // ── Posts Page: Live Filtering ─────────────────
    if (document.getElementById('filter-bar')) {
        var filters = Object.assign(
            { subreddit: '', sort: 'oldest', start: '', end: '', q: '', limit: 50, page: 1 },
            window.__filters || {}
        );
        var total = window.__total || 0;
        var limit = filters.limit;

        function getTotalPages(t, l) { return l > 0 ? Math.ceil(t / l) : 1; }

        function renderPost(post, isBookmarked) {
            var url = post.url && post.url.startsWith('http') ? post.url : 'https://www.reddit.com' + (post.url || '');
            var title = escapeHtml(post.title);
            var date = formatDate(post.created_utc);
            var badge = '<a href="/posts?subreddit=' + encodeURIComponent(post.subreddit || '') + '" class="r-badge">r/' + escapeHtml(post.subreddit || '') + '</a>';
            var score = '<span class="r-score"><i class="fa-solid fa-arrow-up"></i> ' + (post.score || 0) + '</span>';
            var comments = '<span class="r-comment"><i class="fa-solid fa-comment"></i> ' + (post.num_comments || 0) + '</span>';
            var dt = '<span class="post-date">' + date + '</span>';
            var cls = isBookmarked ? ' is-active' : '';
            var btn = '<button class="bookmark-btn' + cls + '" data-post-id="' + escapeHtml(post.id || '') + '" title="Bookmark"><i class="fa-solid fa-bookmark"></i></button>';
            return '<div class="post-item" data-post-id="' + escapeHtml(post.id || '') + '">' +
                badge +
                '<div class="post-title"><a href="' + escapeHtml(url) + '" target="_blank" rel="noopener">' + title + '</a></div>' +
                dt + score + comments +
                '<span class="r-action">' + btn + '</span>' +
            '</div>';
        }

        function renderFeed(posts, bookmarked) {
            var feed = document.getElementById('post-feed');
            if (!feed) return;
            if (!posts || posts.length === 0) {
                feed.innerHTML = '<div class="empty-state"><div class="icon"><i class="fa-solid fa-inbox"></i></div><h2>No posts found</h2><p>Try different filters.</p></div>';
                return;
            }
            feed.innerHTML = posts.map(function (p) {
                return renderPost(p, bookmarked.indexOf(p.id) !== -1);
            }).join('');
        }

        function updateSortInfo(t, page, tp) {
            var info = document.getElementById('sort-info');
            if (!info) return;
            info.innerHTML = '<strong>' + t + '</strong> post' + (t !== 1 ? 's' : '') + ' &middot; page ' + page + ' of ' + tp;
        }

        function updatePagination(page, tp, filters) {
            var pag = document.getElementById('pagination');
            if (!pag || tp <= 1) { if (pag) pag.innerHTML = ''; return; }

            var html = '';
            // Prev
            if (page > 1) {
                html += '<a href="#" class="js-page" data-page="' + (page - 1) + '"><i class="fa-solid fa-chevron-left"></i> Prev</a>';
            } else {
                html += '<span class="disabled"><i class="fa-solid fa-chevron-left"></i> Prev</span>';
            }
            // Page numbers
            for (var p = 1; p <= tp; p++) {
                if (p === page) {
                    html += '<span class="current">' + p + '</span>';
                } else if (p === 1 || p === tp || Math.abs(p - page) <= 2) {
                    html += '<a href="#" class="js-page" data-page="' + p + '">' + p + '</a>';
                } else {
                    if (p === 2 || p === tp - 1) {
                        html += '<span class="disabled">&hellip;</span>';
                    }
                }
            }
            // Next
            if (page < tp) {
                html += '<a href="#" class="js-page" data-page="' + (page + 1) + '">Next <i class="fa-solid fa-chevron-right"></i></a>';
            } else {
                html += '<span class="disabled">Next <i class="fa-solid fa-chevron-right"></i></span>';
            }
            pag.innerHTML = html;
        }

        function fetchAndUpdate(page) {
            var f = Object.assign({}, filters, { page: page || 1 });
            var url = '/api/posts?' + new URLSearchParams({
                subreddit: f.subreddit || '',
                sort: f.sort || 'oldest',
                start: f.start || '',
                end: f.end || '',
                q: f.q || '',
                limit: f.limit || 50,
                page: f.page || 1,
            });

            // Update URL
            history.pushState({ filters: f }, '', buildUrl(f));

            // Fetch
            fetch(url)
                .then(function (res) { return res.json(); })
                .then(function (data) {
                    total = data.total;
                    var tp = getTotalPages(total, limit);
                    renderFeed(data.posts, window.__bookmarked || []);
                    updateSortInfo(total, f.page, tp);
                    updatePagination(f.page, tp, f);
                })
                .catch(function () { toast('Failed to load posts', 'error'); });
        }

        function onFilterChange(page) {
            filters.page = page || 1;
            fetchAndUpdate(filters.page);
        }

        // Attach filter listeners
        var subredditEl = document.getElementById('filter-subreddit');
        var sortEl = document.getElementById('filter-sort');
        var startEl = document.getElementById('filter-start');
        var endEl = document.getElementById('filter-end');
        var qEl = document.getElementById('filter-q');

        if (subredditEl) subredditEl.addEventListener('change', function () { filters.subreddit = subredditEl.value; onFilterChange(1); });
        if (sortEl) sortEl.addEventListener('change', function () { filters.sort = sortEl.value; onFilterChange(1); });
        if (startEl) startEl.addEventListener('change', function () { filters.start = startEl.value; onFilterChange(1); });
        if (endEl) endEl.addEventListener('change', function () { filters.end = endEl.value; onFilterChange(1); });
        if (qEl) qEl.addEventListener('input', debounce(function () { filters.q = qEl.value; onFilterChange(1); }, 200));

        // Pagination: intercept clicks
        document.addEventListener('click', function (e) {
            var link = e.target.closest('.js-page[data-page]');
            if (!link) return;
            e.preventDefault();
            var page = parseInt(link.dataset.page);
            if (page > 0) onFilterChange(page);
        });

        // Jump to page
        var jumpInput = document.getElementById('jump-page');
        if (jumpInput) {
            jumpInput.addEventListener('keydown', function (e) {
                if (e.key === 'Enter') {
                    var page = parseInt(jumpInput.value);
                    var tp = getTotalPages(total, limit);
                    if (page > 0 && page <= tp) onFilterChange(page);
                }
            });
        }

        // Browser back/forward
        window.addEventListener('popstate', function (e) {
            if (e.state && e.state.filters) {
                var f = e.state.filters;
                Object.assign(filters, f);
                // Update UI
                if (subredditEl) subredditEl.value = f.subreddit || '';
                if (sortEl) sortEl.value = f.sort || 'oldest';
                if (startEl) startEl.value = f.start || '';
                if (endEl) endEl.value = f.end || '';
                if (qEl) qEl.value = f.q || '';
                fetchAndUpdate(f.page || 1);
            }
        });

        // Save initial state
        history.replaceState({ filters: filters }, '', buildUrl(filters));
    }

})();
