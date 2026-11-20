"use strict";
(() => {
  const result = document.getElementById("copy-result");
  let clearStatus;
  for (const button of document.querySelectorAll("[data-copy]")) {
    button.addEventListener("click", async () => {
      const target = document.getElementById(button.dataset.copy);
      if (!target || !result) return;
      try {
        if (!navigator.clipboard) throw new Error("Clipboard unavailable");
        await navigator.clipboard.writeText(target.textContent);
        result.textContent = "命令已复制。";
      } catch {
        const selection = window.getSelection();
        if (selection) {
          const range = document.createRange();
          range.selectNodeContents(target);
          selection.removeAllRanges();
          selection.addRange(range);
        }
        result.textContent = "已选中命令，请手动复制。";
      }
      clearTimeout(clearStatus);
      clearStatus = setTimeout(() => { result.textContent = ""; }, 4000);
    });
  }
})();
