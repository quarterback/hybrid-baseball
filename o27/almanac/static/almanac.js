/* o27.almanac — table sorting, percentile heatmap shading, theme toggle.
 *
 * No build step. No dependencies. Tables opt in via data-attributes;
 * column heatmap shading reads `data-heat="hi"|"lo"` on `<th>` headers
 * (hi = higher value is better → greener; lo = lower is better → also
 * greener at the low end). */

(function () {
  // ----------------------------------------------------------------- Theme
  const savedTheme = localStorage.getItem("almanac-theme") || "light";
  document.documentElement.setAttribute("data-theme", savedTheme);
  document.addEventListener("click", (e) => {
    const t = e.target.closest(".theme-toggle");
    if (!t) return;
    const cur = document.documentElement.getAttribute("data-theme") || "light";
    const next = cur === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    localStorage.setItem("almanac-theme", next);
    t.textContent = next === "dark" ? "Light" : "Dark";
  });
  document.querySelectorAll(".theme-toggle").forEach(
    (b) => (b.textContent = savedTheme === "dark" ? "Light" : "Dark"),
  );

  // ----------------------------------------------------------------- Heatmaps
  function shadeColumn(table, colIdx, polarity) {
    const rows = Array.from(table.tBodies[0]?.rows || []);
    const vals = rows.map((r) => parseFloat(r.cells[colIdx]?.dataset.val ?? r.cells[colIdx]?.textContent));
    const ok = vals.map((v) => (Number.isFinite(v) ? v : null));
    const finite = ok.filter((v) => v !== null);
    if (finite.length < 2) return;
    const min = Math.min(...finite);
    const max = Math.max(...finite);
    if (min === max) return;
    rows.forEach((r, i) => {
      const c = r.cells[colIdx];
      if (!c) return;
      const v = ok[i];
      if (v === null) return;
      let p = (v - min) / (max - min); // 0..1, higher value = higher p
      if (polarity === "lo") p = 1 - p;
      c.classList.add("heat");
      const r0 = 246, g0 = 196, b0 = 180; // soft red
      const r1 = 196, g1 = 236, b1 = 201; // soft green
      // blend with neutral cream at midpoint for less garish look
      const neutralR = 252, neutralG = 251, neutralB = 247;
      let rc, gc, bc;
      if (p < 0.5) {
        const t = p / 0.5;
        rc = Math.round(r0 + (neutralR - r0) * t);
        gc = Math.round(g0 + (neutralG - g0) * t);
        bc = Math.round(b0 + (neutralB - b0) * t);
      } else {
        const t = (p - 0.5) / 0.5;
        rc = Math.round(neutralR + (r1 - neutralR) * t);
        gc = Math.round(neutralG + (g1 - neutralG) * t);
        bc = Math.round(neutralB + (b1 - neutralB) * t);
      }
      c.style.setProperty("--heat-bg", `rgba(${rc}, ${gc}, ${bc}, 0.85)`);
      // adjust for dark theme by using a translucent darker tone
      if (document.documentElement.getAttribute("data-theme") === "dark") {
        const alpha = 0.30 + 0.45 * Math.abs(p - 0.5) * 2;
        c.style.setProperty(
          "--heat-bg",
          p > 0.5
            ? `rgba(78, 207, 114, ${alpha})`
            : `rgba(232, 90, 58, ${alpha})`,
        );
      }
    });
  }

  function applyHeatmaps(table) {
    const ths = Array.from(table.tHead?.rows[0]?.cells || []);
    ths.forEach((th, i) => {
      const pol = th.dataset.heat;
      if (pol === "hi" || pol === "lo") shadeColumn(table, i, pol);
    });
  }

  // ----------------------------------------------------------------- Sort
  function compare(a, b, type) {
    if (type === "num") {
      const na = parseFloat(a);
      const nb = parseFloat(b);
      const va = Number.isFinite(na) ? na : -Infinity;
      const vb = Number.isFinite(nb) ? nb : -Infinity;
      return va - vb;
    }
    return String(a).localeCompare(String(b));
  }
  function makeSortable(table) {
    const headers = Array.from(table.tHead?.rows[0]?.cells || []);
    headers.forEach((th, i) => {
      if (th.dataset.sortable === "false") return;
      th.addEventListener("click", () => {
        const tbody = table.tBodies[0];
        const rows = Array.from(tbody.rows);
        const asc = !th.classList.contains("sort-desc");
        headers.forEach((h) => h.classList.remove("sort-asc", "sort-desc"));
        th.classList.add(asc ? "sort-desc" : "sort-asc");
        const type = th.dataset.type || "num";
        rows.sort((r1, r2) => {
          const c1 = r1.cells[i];
          const c2 = r2.cells[i];
          const v1 = c1?.dataset.val ?? c1?.textContent ?? "";
          const v2 = c2?.dataset.val ?? c2?.textContent ?? "";
          return (asc ? -1 : 1) * compare(v1, v2, type);
        });
        rows.forEach((r) => tbody.appendChild(r));
      });
    });
  }

  // ----------------------------------------------------------------- CSV export from any visible table
  function tableToCSV(table) {
    const headers = Array.from(table.tHead?.rows[0]?.cells || []).map(
      (th) => (th.dataset.csv || th.textContent || "").trim(),
    );
    const lines = [headers.map(csvEscape).join(",")];
    Array.from(table.tBodies[0]?.rows || []).forEach((row) => {
      const cells = Array.from(row.cells).map((c) => {
        const v = c.dataset.val ?? c.textContent ?? "";
        return csvEscape(v);
      });
      lines.push(cells.join(","));
    });
    return lines.join("\n");
  }
  function csvEscape(v) {
    const s = String(v).replace(/ /g, " ").trim();
    if (/[",\n]/.test(s)) return `"${s.replace(/"/g, '""')}"`;
    return s;
  }
  function downloadCSV(filename, content) {
    const blob = new Blob([content], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  document.addEventListener("click", (e) => {
    const b = e.target.closest("[data-export-csv]");
    if (!b) return;
    e.preventDefault();
    const target = document.querySelector(b.dataset.exportCsv);
    if (!target) return;
    const name = b.dataset.filename || "almanac-export.csv";
    downloadCSV(name, tableToCSV(target));
  });

  // ----------------------------------------------------------------- Filters
  // Unified row filter: a table can have a text search box plus league /
  // division dropdowns; a row is shown only when it passes ALL active
  // controls. Controls point at their table via a selector in the dataset.
  function controlsFor(tbl) {
    const sel = "#" + tbl.id;
    return Array.from(document.querySelectorAll(
      `[data-filter-table="${sel}"],[data-league-filter="${sel}"],[data-division-filter="${sel}"]`
    ));
  }
  function applyTableFilters(tbl) {
    if (!tbl) return;
    const ctrls = controlsFor(tbl);
    Array.from(tbl.tBodies[0]?.rows || []).forEach((row) => {
      let visible = true;
      for (const c of ctrls) {
        if (c.dataset.filterTable) {
          const q = c.value.toLowerCase().trim();
          if (q) {
            const cols = (c.dataset.filterCols || "0").split(",").map((s) => parseInt(s, 10));
            let hit = false;
            for (const i of cols) {
              if ((row.cells[i]?.textContent || "").toLowerCase().includes(q)) { hit = true; break; }
            }
            if (!hit) visible = false;
          }
        } else if (c.dataset.leagueFilter) {
          if (c.value && c.value !== "all" && (row.dataset.league || "") !== c.value) visible = false;
        } else if (c.dataset.divisionFilter) {
          if (c.value && c.value !== "all" && (row.dataset.division || "") !== c.value) visible = false;
        }
      }
      row.style.display = visible ? "" : "none";
    });
  }
  function tableForControl(c) {
    const sel = c.dataset.filterTable || c.dataset.leagueFilter || c.dataset.divisionFilter;
    return sel ? document.querySelector(sel) : null;
  }
  document.addEventListener("input", (e) => {
    const c = e.target.closest("[data-filter-table]");
    if (c) applyTableFilters(tableForControl(c));
  });
  document.addEventListener("change", (e) => {
    const c = e.target.closest("[data-league-filter],[data-division-filter]");
    if (c) applyTableFilters(tableForControl(c));
  });

  // ----------------------------------------------------------------- Init
  document.querySelectorAll("table.stat-table").forEach((t) => {
    makeSortable(t);
    if (t.classList.contains("heatmap")) applyHeatmaps(t);
  });
})();
