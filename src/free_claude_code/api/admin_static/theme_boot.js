/* Early theme bootstrap — keep tiny and side-effect free for first paint. */
(function () {
  try {
    var stored = localStorage.getItem("fcc.theme");
    var theme = stored === "light" || stored === "dark" ? stored : null;
    if (!theme && window.matchMedia) {
      theme = window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
    }
    document.documentElement.setAttribute("data-theme", theme || "dark");
  } catch (e) {
    document.documentElement.setAttribute("data-theme", "dark");
  }
})();
