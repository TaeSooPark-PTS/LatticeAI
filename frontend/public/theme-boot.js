(function () {
  try {
    var theme = localStorage.getItem("lattice.theme");
    document.documentElement.dataset.theme = theme === "light" || theme === "dark" ? theme : "dark";
  } catch (_error) {
    document.documentElement.dataset.theme = "dark";
  }
})();
