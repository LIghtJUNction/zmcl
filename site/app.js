"use strict";
(() => {
  const rowTarget = document.getElementById("feature-rows");
  const metricTarget = document.getElementById("measurement-rows");
  const countTarget = document.getElementById("feature-count");
  const number = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 });
  let data;
  let filter = "all";

  function node(tag, text, className) {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = String(text);
    if (className) element.className = className;
    return element;
  }
  function safeLink(url) {
    const parsed = new URL(url, document.baseURI);
    if (parsed.protocol !== "https:" && parsed.origin !== location.origin) throw new Error("Unsupported link");
    return parsed.href;
  }
  function renderFeatures() {
    rowTarget.replaceChildren();
    const selected = data.features.filter(feature => filter === "all" || feature.state === filter);
    for (const feature of selected) {
      const row = node("tr");
      row.append(node("td", feature.name));
      const state = node("td");
      state.append(node("span", feature.label, "state " + feature.state));
      row.append(state);
      const detail = node("td");
      detail.append(node("p", feature.summary));
      const source = node("a", "实现与验收记录 ↗");
      source.href = safeLink(data.project.repository + "/blob/main/" + feature.evidence);
      detail.append(source);
      row.append(detail);
      rowTarget.append(row);
    }
    if (!selected.length) {
      const row = node("tr");
      const cell = node("td", "这一组暂时没有记录。", "empty-state");
      cell.colSpan = 3;
      row.append(cell);
      rowTarget.append(row);
    }
    countTarget.textContent = "显示 " + selected.length + " 个能力组 · 状态日期 " + data.project.updated;
  }
  function metricValue(item, unit) {
    const cell = node("td");
    if (item.value === null) {
      cell.textContent = item.label || "待测量";
      cell.classList.add("metric-unmeasured");
      if (item.note) cell.append(node("span", item.note, "metric-note"));
      return cell;
    }
    if (typeof item.value !== "number" || !Number.isFinite(item.value) || item.value < 0) throw new Error("Invalid measurement");
    const value = item.value;
    if (unit === "bytes") {
      const factor = value >= 1024 * 1024 ? 1024 * 1024 : 1024;
      cell.append(node("span", number.format(value / factor) + (factor === 1024 ? " KiB" : " MiB")));
      cell.append(node("span", value.toLocaleString("en-US") + " bytes", "metric-raw"));
    } else if (unit === "kib") {
      cell.append(node("span", number.format(value / 1024) + " MiB"));
      cell.append(node("span", value.toLocaleString("en-US") + " KiB", "metric-raw"));
    } else if (unit === "ms") {
      cell.textContent = number.format(value) + " ms";
    } else if (unit === "ticks") {
      cell.textContent = number.format(value) + " ticks";
    } else if (unit === "seconds") {
      cell.textContent = number.format(value) + " s";
    } else if (unit === "percent") {
      cell.textContent = number.format(value) + "%";
    } else {
      throw new Error("Unknown measurement unit");
    }
    if (item.note) cell.append(node("span", item.note, "metric-note"));
    return cell;
  }
  function renderMeasurements() {
    metricTarget.replaceChildren();
    for (const metric of data.measurements) {
      const row = node("tr");
      row.append(node("td", metric.label), metricValue(metric.zmcl, metric.unit), metricValue(metric.xmcl, metric.unit), node("td", metric.context));
      metricTarget.append(row);
    }
    document.getElementById("measurement-status").textContent = data.measurement.label;
    document.getElementById("measurement-context").textContent = data.measurement.environment + "。" + data.measurement.scope + data.measurement.comparison;
    document.getElementById("baseline-version").textContent = data.project.baseline;
  }
  document.querySelectorAll("[data-filter]").forEach(button => {
    button.addEventListener("click", () => {
      if (!data) return;
      filter = button.dataset.filter;
      document.querySelectorAll("[data-filter]").forEach(item => item.setAttribute("aria-pressed", String(item === button)));
      renderFeatures();
    });
  });
  document.querySelectorAll("[data-copy]").forEach(button => {
    button.addEventListener("click", async () => {
      const target = document.getElementById(button.dataset.copy);
      const result = document.getElementById("copy-result");
      try {
        if (!navigator.clipboard) throw new Error("Clipboard unavailable");
        await navigator.clipboard.writeText(target.textContent);
        result.textContent = "命令已复制。";
      } catch {
        const selection = window.getSelection();
        const range = document.createRange();
        range.selectNodeContents(target);
        selection.removeAllRanges();
        selection.addRange(range);
        result.textContent = "已选中命令，请手动复制。";
      }
    });
  });
  fetch(new URL("data.json", document.baseURI), { cache: "no-cache" })
    .then(response => {
      if (!response.ok) throw new Error("Data request failed");
      return response.json();
    })
    .then(value => {
      if (value.schema_version !== 1 || !Array.isArray(value.features) || !Array.isArray(value.measurements)) throw new Error("Unsupported data format");
      data = value;
      renderFeatures();
      renderMeasurements();
    })
    .catch(() => {
      countTarget.textContent = "数据文件暂时无法读取，请查看完整功能矩阵。";
      countTarget.classList.add("data-error");
      document.getElementById("measurement-status").textContent = "测量记录暂时无法读取；不显示推算值。";
    });
})();
