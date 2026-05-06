// Shared helpers for uPlot charts: athletics-time formatter, theme bridge,
// and a tiny tooltip plugin.
//
// Globals: window.AthHelpers = { fmtMark, themeColors, makeTooltip,
//   epochSecondsFromIsoDate }.

(function () {
  const FIELD_FAMILIES = new Set([
    "field_distance",
    "field_distance_wind",
    "combined_points",
  ]);

  // Mirrors `_format_y_tick` in src/alltimeathletics/site.py.
  function fmtMark(v, family) {
    if (v == null || !Number.isFinite(v)) return "";
    if (family && FIELD_FAMILIES.has(family))
      return v.toFixed(2).replace(/\.?0+$/, "");
    if (v < 60) return v.toFixed(2);
    if (v < 3600) {
      const m = Math.floor(v / 60), s = v - m * 60;
      return `${m}:${s < 10 ? "0" : ""}${s.toFixed(2)}`;
    }
    const h = Math.floor(v / 3600);
    const rem = v - h * 3600;
    const m = Math.floor(rem / 60);
    const s = Math.round(rem - m * 60);
    return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  }

  // Read theme colors from CSS custom properties so charts honor light/dark.
  function themeColors() {
    const cs = getComputedStyle(document.documentElement);
    const get = (k, fallback) => (cs.getPropertyValue(k).trim() || fallback);
    return {
      fg: get("--fg", "#222"),
      muted: get("--fg-muted", "#666"),
      border: get("--border", "#ddd"),
      accent: get("--accent", "#0a58ca"),
      bg: get("--bg", "#fff"),
    };
  }

  function epochSecondsFromIsoDate(s) {
    if (!s) return null;
    const t = Date.parse(s);
    return Number.isFinite(t) ? Math.floor(t / 1000) : null;
  }

  // Tooltip plugin. `getText(u, dataIdx) -> string|null` returns innerHTML
  // (caller must escape). Returning null hides the tooltip for that point.
  function makeTooltip(getText) {
    let el = null;
    return {
      hooks: {
        init: (u) => {
          el = document.createElement("div");
          el.className = "uplot-tooltip";
          el.style.display = "none";
          u.over.appendChild(el);
        },
        setCursor: (u) => {
          if (!el) return;
          const { idx, left, top } = u.cursor;
          if (idx == null || idx < 0 || left < 0 || top < 0) {
            el.style.display = "none";
            return;
          }
          const text = getText(u, idx);
          if (!text) {
            el.style.display = "none";
            return;
          }
          el.innerHTML = text;
          el.style.display = "";
          // Position to the right of the cursor by default; flip if it would
          // overflow the plot area on the right.
          const pad = 12;
          const rect = u.over.getBoundingClientRect();
          let x = left + pad;
          if (x + el.offsetWidth > rect.width) x = left - pad - el.offsetWidth;
          let y = top - el.offsetHeight - 6;
          if (y < 0) y = top + 14;
          el.style.transform = `translate(${x}px, ${y}px)`;
        },
        destroy: () => {
          if (el && el.parentNode) el.parentNode.removeChild(el);
          el = null;
        },
      },
    };
  }

  window.AthHelpers = {
    fmtMark,
    themeColors,
    makeTooltip,
    epochSecondsFromIsoDate,
  };
})();
