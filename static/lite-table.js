// LiteTable: a dependency-free paginated table.
//
// Why this exists: Tabulator was spending 20–60s booting on the
// big lists (men's marathon ~6.3k rows, athlete index ~29k rows).
// Most of that was Tabulator's own overhead — Row/Cell components,
// virtual DOM, header filters, function-based sorters. Replacing it
// with a plain <table> and a pagination slice renders any of our
// pages in well under 100ms.
//
// API mirrors the bits of Tabulator we actually used:
//   columns: [{ title, field, formatter?, sorter?, sortField?,
//               headerFilter?, align?, width?, minWidth?,
//               sortable?, descFirst? }]
//   pageSize, indexField, initialSort: {field, dir}
//   onCount(n), onRowClick(row)
//   .setData(rows), .setCustomFilter(fn), .setColumnFilter(field, q)
//   .selectRow(key), .deselectRow()
(function () {
    "use strict";

    function escHtml(s) {
        return String(s).replace(/[&<>"']/g, function (c) {
            return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
        });
    }

    function defaultCmp(field) {
        return function (a, b) {
            var av = a[field], bv = b[field];
            if (av == null && bv == null) return 0;
            if (av == null) return 1;
            if (bv == null) return -1;
            if (typeof av === "number" && typeof bv === "number") return av - bv;
            return String(av).localeCompare(String(bv));
        };
    }

    function LiteTable(el, opts) {
        this.el = typeof el === "string" ? document.querySelector(el) : el;
        this.cols = opts.columns;
        this.pageSize = opts.pageSize || 100;
        this.indexField = opts.indexField || null;
        this.onCount = opts.onCount || null;
        this.onRowClick = opts.onRowClick || null;
        this.allRows = [];
        this.viewRows = [];
        this.page = 0;
        this.sortField = opts.initialSort ? opts.initialSort.field : null;
        this.sortDir = opts.initialSort ? (opts.initialSort.dir || "asc") : "asc";
        this.filters = {};
        this.customFilter = null;
        this.selectedKey = null;
        this._build();
    }

    LiteTable.prototype._build = function () {
        this.el.innerHTML = "";
        this.el.classList.add("lt-host");

        var wrap = document.createElement("div");
        wrap.className = "lt-wrap";
        var tbl = document.createElement("table");
        tbl.className = "lt-table";

        var thead = document.createElement("thead");
        var titleRow = document.createElement("tr");
        titleRow.className = "lt-titles";
        var filterRow = document.createElement("tr");
        filterRow.className = "lt-filters";
        var hasAnyFilter = false;

        var self = this;
        this.cols.forEach(function (col, i) {
            var th = document.createElement("th");
            if (col.minWidth) th.style.minWidth = col.minWidth + "px";
            if (col.width) th.style.width = col.width + "px";
            if (col.align === "right") th.style.textAlign = "right";

            var label = document.createElement("span");
            label.className = "lt-th-label";
            label.textContent = col.title;
            th.appendChild(label);

            var arrow = document.createElement("span");
            arrow.className = "lt-th-arrow";
            arrow.setAttribute("aria-hidden", "true");
            th.appendChild(arrow);

            if (col.sortable !== false) {
                th.classList.add("lt-sortable");
                th.tabIndex = 0;
                th.setAttribute("role", "button");
                th.setAttribute("aria-sort", "none");
                th.addEventListener("click", function () { self._toggleSort(col.field); });
                th.addEventListener("keydown", function (e) {
                    if (e.key === "Enter" || e.key === " " || e.key === "Spacebar") {
                        e.preventDefault();
                        self._toggleSort(col.field);
                    }
                });
            }
            titleRow.appendChild(th);

            var fth = document.createElement("th");
            if (col.headerFilter) {
                hasAnyFilter = true;
                var inp = document.createElement("input");
                inp.type = "search";
                inp.className = "lt-filter";
                inp.setAttribute("aria-label", "Filter " + col.title);
                inp.dataset.field = col.field;
                inp.addEventListener("input", function () {
                    self.setColumnFilter(col.field, inp.value);
                });
                fth.appendChild(inp);
            }
            filterRow.appendChild(fth);
        });

        thead.appendChild(titleRow);
        if (hasAnyFilter) thead.appendChild(filterRow);

        var tbody = document.createElement("tbody");
        tbl.appendChild(thead);
        tbl.appendChild(tbody);
        wrap.appendChild(tbl);

        var pager = document.createElement("div");
        pager.className = "lt-pager";

        this.el.appendChild(wrap);
        this.el.appendChild(pager);

        this._titleRow = titleRow;
        this._tbody = tbody;
        this._pager = pager;
        this._wrap = wrap;
    };

    LiteTable.prototype.setData = function (rows) {
        this.allRows = rows || [];
        this.page = 0;
        this._refresh();
    };

    LiteTable.prototype.setCustomFilter = function (fn) {
        this.customFilter = fn || null;
        this.page = 0;
        this._refresh();
    };

    LiteTable.prototype.setColumnFilter = function (field, value) {
        var v = String(value || "").trim().toLowerCase();
        if (v) this.filters[field] = v;
        else delete this.filters[field];
        // reflect in the input box if it wasn't the trigger
        var inp = this._titleRow.parentNode.querySelector('.lt-filter[data-field="' + field + '"]');
        if (inp && inp.value.trim().toLowerCase() !== v) inp.value = value;
        this.page = 0;
        this._refresh();
    };

    LiteTable.prototype.clearFilters = function () {
        this.filters = {};
        this._titleRow.parentNode.querySelectorAll(".lt-filter").forEach(function (i) { i.value = ""; });
        this.page = 0;
        this._refresh();
    };

    LiteTable.prototype.setSort = function (field, dir) {
        this.sortField = field || null;
        this.sortDir = dir || "asc";
        this._refresh();
    };

    LiteTable.prototype.getViewCount = function () {
        return this.viewRows.length;
    };

    LiteTable.prototype.selectRow = function (key) {
        this.selectedKey = key;
        if (this.indexField == null) return;
        var idx = -1;
        for (var i = 0; i < this.viewRows.length; i++) {
            if (this.viewRows[i][this.indexField] === key) { idx = i; break; }
        }
        if (idx < 0) {
            // selected row is filtered out — just paint the highlight if it
            // returns; nothing to scroll to.
            this._renderBody();
            return;
        }
        var targetPage = Math.floor(idx / this.pageSize);
        if (targetPage !== this.page) {
            this.page = targetPage;
            this._renderBody();
            this._renderPager();
        } else {
            this._renderBody();
        }
        var tr = this._tbody.querySelector('tr[data-key="' + CSS.escape(String(key)) + '"]');
        if (tr) tr.scrollIntoView({ block: "nearest", behavior: "smooth" });
    };

    LiteTable.prototype.deselectRow = function () {
        this.selectedKey = null;
        this._renderBody();
    };

    LiteTable.prototype._toggleSort = function (field) {
        var col = this._col(field);
        if (!col || col.sortable === false) return;
        if (this.sortField === field) {
            this.sortDir = this.sortDir === "asc" ? "desc" : "asc";
        } else {
            this.sortField = field;
            this.sortDir = col.descFirst ? "desc" : "asc";
        }
        this._refresh();
    };

    LiteTable.prototype._col = function (field) {
        for (var i = 0; i < this.cols.length; i++) {
            if (this.cols[i].field === field) return this.cols[i];
        }
        return null;
    };

    LiteTable.prototype._refresh = function () {
        var rows = this.allRows;

        if (this.customFilter) {
            var cf = this.customFilter;
            rows = rows.filter(function (r) { return cf(r); });
        }

        var fEntries = Object.keys(this.filters);
        if (fEntries.length) {
            var filt = this.filters;
            rows = rows.filter(function (r) {
                for (var k = 0; k < fEntries.length; k++) {
                    var f = fEntries[k];
                    var v = r[f];
                    if (v == null) return false;
                    if (String(v).toLowerCase().indexOf(filt[f]) === -1) return false;
                }
                return true;
            });
        }

        if (this.sortField) {
            var col = this._col(this.sortField);
            var cmp;
            if (col && typeof col.sorter === "function") {
                cmp = col.sorter;
            } else {
                cmp = defaultCmp(col && col.sortField ? col.sortField : this.sortField);
            }
            var dir = this.sortDir === "desc" ? -1 : 1;
            rows = rows.slice().sort(function (a, b) { return dir * cmp(a, b); });
        }

        this.viewRows = rows;
        if (this.onCount) this.onCount(rows.length);

        var ths = this._titleRow.children;
        for (var i = 0; i < this.cols.length; i++) {
            var c = this.cols[i];
            var on = c.field === this.sortField;
            ths[i].classList.toggle("lt-sort-asc", on && this.sortDir === "asc");
            ths[i].classList.toggle("lt-sort-desc", on && this.sortDir === "desc");
            // Keep aria-sort in sync for sortable headers so screen readers
            // announce the current sort state.
            if (c.sortable !== false) {
                ths[i].setAttribute(
                    "aria-sort",
                    on ? (this.sortDir === "desc" ? "descending" : "ascending") : "none"
                );
            }
        }

        if (this.page * this.pageSize >= rows.length) this.page = 0;
        this._renderBody();
        this._renderPager();
    };

    LiteTable.prototype._renderBody = function () {
        var start = this.page * this.pageSize;
        var slice = this.viewRows.slice(start, start + this.pageSize);
        var cols = this.cols;
        var indexField = this.indexField;
        var selectedKey = this.selectedKey;
        var html = [];

        for (var i = 0; i < slice.length; i++) {
            var r = slice[i];
            var key = indexField ? r[indexField] : null;
            var sel = (selectedKey != null && key === selectedKey);
            html.push("<tr class=\"lt-row" + (sel ? " lt-selected" : "") + "\"" +
                (key != null ? " data-key=\"" + escHtml(String(key)) + "\"" : "") + ">");
            for (var j = 0; j < cols.length; j++) {
                var col = cols[j];
                var v = r[col.field];
                var content;
                if (col.formatter) {
                    content = col.formatter(v, r);
                    if (content == null) content = "";
                } else {
                    content = (v == null ? "" : escHtml(String(v)));
                }
                var alignAttr = col.align === "right" ? " class=\"lt-num\"" : "";
                html.push("<td" + alignAttr + ">" + content + "</td>");
            }
            html.push("</tr>");
        }

        this._tbody.innerHTML = html.join("");

        if (this.onRowClick) {
            var self = this;
            this._tbody.querySelectorAll("tr").forEach(function (tr, k) {
                tr.style.cursor = "pointer";
                tr.addEventListener("click", function () {
                    self.onRowClick(slice[k]);
                });
            });
        }
    };

    LiteTable.prototype._renderPager = function () {
        var total = this.viewRows.length;
        var pages = Math.max(1, Math.ceil(total / this.pageSize));
        var p = this.page;

        if (total <= this.pageSize) {
            this._pager.innerHTML = "";
            return;
        }

        var first = (p === 0) ? " disabled" : "";
        var last = (p >= pages - 1) ? " disabled" : "";
        var startN = total === 0 ? 0 : p * this.pageSize + 1;
        var endN = Math.min(total, (p + 1) * this.pageSize);

        this._pager.innerHTML =
            '<button type="button" class="btn btn-sm" data-act="first"' + first + ' aria-label="First page">«</button>' +
            '<button type="button" class="btn btn-sm" data-act="prev"' + first + ' aria-label="Previous page">‹</button>' +
            '<span class="lt-page-info">' + startN.toLocaleString() + "–" + endN.toLocaleString() +
            " of " + total.toLocaleString() + "</span>" +
            '<button type="button" class="btn btn-sm" data-act="next"' + last + ' aria-label="Next page">›</button>' +
            '<button type="button" class="btn btn-sm" data-act="last"' + last + ' aria-label="Last page">»</button>';

        var self = this;
        this._pager.querySelectorAll("button").forEach(function (btn) {
            btn.addEventListener("click", function () {
                var act = btn.dataset.act;
                if (act === "first") self.page = 0;
                else if (act === "prev") self.page = Math.max(0, self.page - 1);
                else if (act === "next") self.page = Math.min(pages - 1, self.page + 1);
                else if (act === "last") self.page = pages - 1;
                self._renderBody();
                self._renderPager();
                self._wrap.scrollTop = 0;
            });
        });
    };

    window.LiteTable = LiteTable;
})();
